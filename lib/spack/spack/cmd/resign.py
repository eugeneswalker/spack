import os
import argparse
import tarfile

import spack.binary_distribution
import spack.cmd.common.arguments as arguments
import spack.paths
import spack.util.gpg as gpg
import llnl.util.tty as tty
import spack.binary_distribution as bindist
from pathlib import Path
import spack.util.spack_yaml as syaml
import shutil
import yaml

import subprocess
import multiprocessing
import distutils.dir_util
import fnmatch
import stat
import string
import random
import tempfile

from tqdm import trange
from math import floor
from time import time
import sys


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
    chunkSize = len(ds)/n
    if len(ds)%n > 1:
        chunkSize += 1
    chunks = []
    lasti = 0
    for i in range(n):
        i0 = lasti
        i1 = floor(i0+chunkSize)
        if i == n-1 or i1 > len(ds):
            i1 = len(ds)
        chunks.append(ds[i0:i1])
        lasti = i1
    return chunks

def random_string(n):
    str = string.ascii_lowercase
    return ''.join(random.choice(str) for i in range(n))

def init_gpg_dir(src, dst):
    os.makedirs(dst, exist_ok=True)

    fs = ['pubring.kbx','trustdb.gpg']
    for f in fs:
        sf = os.path.join(src, f)
        df = os.path.join(dst, f)
        shutil.copyfile(sf, df)

    sf = os.path.join(src, 'private-keys-v1.d')
    df = os.path.join(dst, 'private-keys-v1.d')
    os.symlink(sf, df)

    for r, _, f in os.walk(dst):
        os.chmod(r, stat.S_IREAD | stat.S_IWRITE | stat.S_IEXEC)

def do_resign(baseworkdir, key, paths, strip_build_env, gpgdir):
    os.environ['SPACK_GNUPGHOME'] = gpgdir
    gpg.clear()
    gpg.init()

    for p in paths:
        srcdir, tarfn = os.path.split(p)

        print("Starting {0}".format(tarfn), flush=True)

        basefn = os.path.splitext(tarfn)[0]
        newtarfn = "{0}.new".format(tarfn)
        specfn = "{0}.spec.yaml".format(basefn)
        sigfn = "{0}.spec.yaml.asc".format(basefn)
        tgzfn = "{0}.tar.gz".format(basefn)
        newtgzfn = "{0}.new".format(tgzfn)
        tar_contents = [specfn, sigfn, tgzfn]

        donefile = os.path.join(srcdir, "{0}.done".format(basefn))

        if os.path.exists(donefile):
            print("Skipping, already done: {0}".format(tarfn), flush=True)
            continue

        workdir = os.path.join(baseworkdir, basefn)
        os.makedirs(workdir)
        os.chdir(workdir)

        def cleanup():
            shutil.rmtree(workdir)

        print("workdir = {0}".format(workdir), flush=True)

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
                print("aborting: extra files in archive: {0} \n{1}".format(tarfn, "\n".join(extra)), flush=True)
                abort = True

            if len(missing) > 0:
                print("aborting: missing files in archive: {0}\n{1}".format(tarfn, "\n".join(missing)), flush=True)
                abort = True

            if abort:
                print("skipping: archive has missing and/or extra files: {0}".format(tarfn), flush=True)
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
                        print("removing {}".format(n), flush=True)
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
                    print("error: binary_cache_checksum property not found: {0}"
                              .format(fullpath(specfn)), flush=True)
                    cleanup()
                    return 1

                spec_dict['binary_cache_checksum'] = {
                    'hash_algorithm': 'sha256',
                    'hash': checksum
                }

                with open(specfn, 'w') as f:
                    f.write(syaml.dump(spec_dict))

        try:
            keys = gpg.signing_keys(key)
            if len(keys) <= 0:
                raise spack.error.SpackError("signing key not found: {0}".format(key))
            elif len(keys) > 1:
                raise spack.error.SpackError("multiple matching keys, specify by key-id instead: {0}".format(", ".join(keys)))
            gpg.sign(keys[0], specfn, sigfn)
        except:
            print("signing failed", flush=True)
            cleanup()
            raise

        with tarfile.open(srcpath(newtarfn), 'w') as tf:
            for fn in tar_contents:
                tf.add(fn)

        os.remove(srcpath(tarfn))
        os.rename(srcpath(newtarfn), srcpath(tarfn))

        cleanup()
        open(donefile,'w').close()
        print("Finished {0}".format(tarfn), flush=True)

    print("All done!", flush=True)
    return 0

def resign(parser, args, **kwargs):
    archive = os.path.abspath(args.archive)
    baseworkdir = args.workdir
    key = args.key
    np = args.np
    strip_build_env = args.strip_build_env

    archives = []
    for r, _, fs in os.walk(archive):
        for f in fs:
            if f.endswith(".spack"):
                archives.append(os.path.join(r, f))

    random.shuffle(archives)
    archive_chunks = chunkify(archives, np)

    gpg.init()
    sgpg = gpg.GNUPGHOME
    tmpdir = tempfile.gettempdir()
    print("Temporary directory = {0}".format(tmpdir), flush=True)

    for i in range(np):
        gpgdir = os.path.join(tmpdir, ".gnupg-{0}".format(random_string(10)))
        init_gpg_dir(sgpg, gpgdir)
        p = multiprocessing.Process(target=do_resign, args=(baseworkdir, key, archive_chunks[i], strip_build_env, gpgdir))
        p.start()

    return 0
