import os
import random
import shutil
import stat
import string
import tarfile
import time
from math import floor
from multiprocessing import Process, Queue
from pathlib import Path

import yaml
from tqdm import trange

import llnl.util.tty as tty
import spack.binary_distribution
import spack.binary_distribution as bindist
import spack.paths
import spack.util.gpg as gpg
import spack.util.spack_yaml as syaml


description = "re-sign .spack archive"
section = "packaging"
level = "long"


def setup_parser(subparser):
    subparser.add_argument(
        '--strip-build-env',
        action='store_true',
        default=False,
        dest='strip_build_env',
        help='strip spack-build-env.txt from archive, if present')

    subparser.add_argument(
        '--workdir',
        type=str,
        required=True,
        help='directory to use for staging work-in-progress')

    subparser.add_argument(
        '--debug', '-d',
        action='store_true',
        required=False,
        help='show debug messages (incompatible with progress bar)')

    subparser.add_argument(
        '--progress', '-p',
        action='store_true',
        required=False,
        default=False,
        help='display progress bar')

    subparser.add_argument(
        '--key',
        type=str,
        required=True,
        help='key to use for re-signing')

    subparser.add_argument(
        "--np",
        type=int,
        required=False,
        default=1,
        help='number of threads')

    subparser.add_argument(
        'archive',
        metavar='archive',
        type=str,
        help='.spack archive to re-sign')


def chunkify(ds, n):
    chunkSize = len(ds) / n
    if len(ds) % n != 0:
        chunkSize += 1
    chunks = []
    lasti = 0
    for i in range(n):
        i0 = lasti
        i1 = floor(i0 + chunkSize)
        if i == n - 1 or i1 > len(ds):
            i1 = len(ds)
        chunks.append(ds[i0:i1])
        lasti = i1
    return chunks


def random_string(n):
    str = string.ascii_lowercase
    return ''.join(random.choice(str) for i in range(n))


debug_on = False


def debug(*args, **kwargs):
    if debug_on:
        print(*args, flush=True, **kwargs)


def worker_gpghome(src, tmpdir):
    dst = None
    for _ in range(5):
        dst = os.path.join(tmpdir, ".gnupg-{0}".format(random_string(10)))
        if not os.path.exists(dst):
            break
        else:
            dst = None
    if not dst:
        raise

    os.makedirs(dst, exist_ok=True)

    for f in ['pubring.kbx', 'trustdb.gpg']:
        sf = os.path.join(src, f)
        df = os.path.join(dst, f)
        shutil.copyfile(sf, df)

    sf = os.path.join(src, 'private-keys-v1.d')
    df = os.path.join(dst, 'private-keys-v1.d')
    os.symlink(sf, df)

    for r, _, f in os.walk(dst):
        os.chmod(r, stat.S_IREAD | stat.S_IWRITE | stat.S_IEXEC)

    return dst


def progress_bar(n, q):
    for _ in trange(n):
        q.get()


def resign_worker(baseworkdir, key, paths, strip_build_env, gpghome, q):
    os.environ['SPACK_GNUPGHOME'] = gpghome
    gpg.clear()
    gpg.init()

    for p in paths:
        srcdir, tarfn = os.path.split(p)

        debug("starting {0}".format(tarfn))

        basefn = os.path.splitext(tarfn)[0]
        newtarfn = "{0}.new".format(tarfn)
        specfn = "{0}.spec.yaml".format(basefn)
        sigfn = "{0}.spec.yaml.asc".format(basefn)
        tgzfn = "{0}.tar.gz".format(basefn)
        newtgzfn = "{0}.new".format(tgzfn)
        tar_contents = [specfn, sigfn, tgzfn]

        donefile = os.path.join(srcdir, "{0}.done".format(basefn))

        if os.path.exists(donefile):
            debug("skipping, already done: {0}".format(tarfn))
            if q:
                q.put(0)
            continue

        workdir = os.path.join(baseworkdir, basefn)
        os.makedirs(workdir)
        os.chdir(workdir)

        def cleanup():
            debug("finished {0}".format(tarfn))
            shutil.rmtree(workdir)
            if q:
                q.put(0)

        debug("workdir = {0}".format(workdir))

        def srcpath(f):
            return os.path.join(srcdir, f)

        def wrkpath(f):
            return os.path.join(workdir, f)

        with tarfile.open(p, 'r') as tf:
            actual = set(tf.getnames())
            expected = set(tar_contents)
            extra = actual.difference(expected)
            missing = expected.difference(actual)

            abort = False
            if len(extra) > 0:
                debug("extra files in archive: {0} \n{1}".format(tarfn, "\n".join(extra)))
                abort = True

            if len(missing) > 0:
                debug("missing files in archive: {0}\n{1}".format(tarfn, "\n".join(missing)))
                abort = True

            if abort:
                debug("skipping: archive has missing and/or extra files: {0}".format(tarfn))
                cleanup()
                continue

            tf.extractall()

        os.remove(sigfn)

        if strip_build_env:
            delete, add = [], []
            with tarfile.open(tgzfn, 'r') as tf:
                for n in tf.getnames():
                    add.append(Path(n).parts[0])
                    if 'spack-build-env.txt' in n:
                        delete.append(n)
                add = list(set(add))

                if len(delete) > 0:
                    tf.extractall()

            if len(delete) > 0:
                for n in delete:
                    if os.path.exists(n):
                        debug("removing {}".format(n))
                        os.remove(n)

                with tarfile.open(newtgzfn, 'w:gz') as tf:
                    for n in add:
                        tf.add(n)

                for p in add + [tgzfn]:
                    if os.path.isdir(p):
                        shutil.rmtree(p, ignore_errors=True)
                    else:
                        os.remove(p)

                os.rename(newtgzfn, tgzfn)

                checksum = bindist.checksum_tarball(tgzfn)

                with open(specfn, 'r') as f:
                    spec_dict = yaml.load(f.read(), Loader=yaml.FullLoader)

                if 'binary_cache_checksum' not in spec_dict:
                    debug("error: binary_cache_checksum property not found: {0}"
                          .format(srcpath(specfn)))
                    cleanup()
                    continue

                spec_dict['binary_cache_checksum'] = {
                    'hash_algorithm': 'sha256',
                    'hash': checksum
                }

                with open(specfn, 'w') as f:
                    f.write(syaml.dump(spec_dict))

        try:
            gpg.sign(key, specfn, sigfn)
        except Exception:
            debug("signing failed")
            cleanup()
            raise

        with tarfile.open(srcpath(newtarfn), 'w') as tf:
            for fn in tar_contents:
                tf.add(fn)

        os.remove(srcpath(tarfn))
        os.rename(srcpath(newtarfn), srcpath(tarfn))

        open(donefile, 'w').close()
        cleanup()

    debug("worker done!")
    return 0


def resign(parser, args, **kwargs):
    archivedir = os.path.abspath(args.archive)
    workdir = args.workdir
    key = args.key
    np = args.np
    strip = args.strip_build_env
    progress = args.progress

    global debug_on
    debug_on = args.debug

    if debug_on and progress:
        tty.error("--debug and --progress are mutually exclusive.")
        return 1

    archives = []
    for r, _, fs in os.walk(archivedir):
        for f in fs:
            if f.endswith(".spack"):
                archives.append(os.path.join(r, f))

    if len(archives) <= 0:
        tty.msg("Nothing to do! No .spack archives found in {0}".format(archivedir))
        return 0

    uniqdir = None
    for i in range(5):
        d = os.path.join(workdir, "spack-resign-{0}".format(random_string(10)))
        if not os.path.exists(d):
            uniqdir = d
            break
    if uniqdir is None:
        tty.error("Could not create unique work dir under {0}".format(workdir))
        return 1
    workdir = uniqdir

    random.shuffle(archives)
    archive_chunks = chunkify(archives, np)

    keys = gpg.signing_keys(key)
    if len(keys) <= 0:
        raise spack.error.SpackError("signing key not found: {0}".format(key))
    elif len(keys) > 1:
        msg = "Multiple keys available for signing\n"
        for i, k in enumerate(keys):
            msg += "\n"
            msg += "{0: <3} User ID: {1}\n".format("{0}.".format(i), k.uid)
            msg += "{0: <3} Fingerprint: {1}\n".format("", k.fingerprint)
        msg += "\nKey selection must be made unambiguously using the associated fingerprint or user-id."
        raise spack.error.SpackError(msg)
    key = keys[0].fingerprint

    os.makedirs(workdir)

    def cleanup():
        shutil.rmtree(workdir)

    debug("Working directory = {0}".format(workdir))

    q = Queue() if progress else None
    procs = []
    for i in range(np):
        debug("WORKDIR {0} = {1}".format(i,workdir))
        gpgdir = worker_gpghome(gpg.GNUPGHOME, workdir)
        debug("GPGHOME {0} = {1}".format(i,gpgdir))
        work = archive_chunks[i]
        p = Process(target=resign_worker, args=(workdir, key, work, strip, gpgdir, q))
        p.start()
        procs.append(p)

    pbar = None
    if progress:
        pbar = Process(target=progress_bar, args=(len(archives), q))
        pbar.start()

    cnt = 0
    while cnt < np:
        for p in procs:
            if not p.is_alive():
                cnt += 1
        time.sleep(0.2)

    debug("DONE!")

    if pbar:
        pbar.join(2)

    debug("REALLY DONE!")
    return 0
