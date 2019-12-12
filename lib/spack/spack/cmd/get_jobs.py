import spack.environment as env
import spack.mirror
import spack.hash_types as ht
import spack.binary_distribution as bindist
import llnl.util.tty as tty
import boto3
import yaml
import sys
import os
import copy
from itertools import chain
import traceback

def setup_parser(subparser):
  subparser.add_argument(
    '--specs-dir',
    dest='specs_dir',
    default="./specs",
    required=False,
    help="""where to write job specs yaml files to""")
  subparser.add_argument(
    '--runner-tags',
    dest='runner_tags',
    required=True,
    type=str,
    help="""CI runner tags""")
  subparser.add_argument(
    '--rebuild-all',
    dest='rebuild_all',
    default=False,
    required=False,
    action="store_true",
    help="""If True, rebuild all specs""")
  subparser.add_argument(
    '--s3-path',
    dest='s3_path',
    required=True,
    help="""S3 mirror path""")
  subparser.add_argument(
    '--multiple-os',
    dest='multiple_os',
    default="",
    required=False,
    help="""Comma-separated list of operating systems to build for""")
  subparser.add_argument(
    '--arch',
    dest='arch',
    default="x86_64",
    required=False,
    help="""Target architecture""")

description = "generate a build manifest containing each spack job needed to install an environment"
section = "build"
level = "short"

def get_jobs(parser, args, **kwargs):
  runner_tags = []
  try:
    runner_tags = args.runner_tags.split(',')
  except:
    sys.stderr.write("Runner tags must be comma separated. Failed to process: {}\n".format(args.runner_tags))
    return 1

  os_to_runner_map = {
    "ubuntu18.04": "ecpe4s/ubuntu18.04-spack-runner-x86-64:0.13.2",
    "centos7": "ecpe4s/centos7-spack-runner-x86-64:0.13.2",
    "rhel7": "ecpe4s/ubi7-spack-runner-x86-64:0.13.2"
  }

  valid_osss = set(list(os_to_runner_map.keys())+[''])
  osss = []
  try:
    osss = args.multiple_os.split(',')
  except:
    pass

  if not set(osss).issubset(valid_osss):
    sys.stderr.write("Multiple operating system list must be comma separated and confined to centos7, rhel7, and/or ubuntu18.04\n")
    return 1

  blr = ["build", "link", "run"]

  chosen_oss = ""
  e = env.get_env(None, 'get_jobs', required=True)
  e.concretize()
  css = [cs for (us,cs) in e.concretized_specs()]
  for oss in osss:
    all_specs = {}
    if oss != '':
      for cs in css:
        for s in cs.traverse_edges(deptype=blr, direction="children", cover="nodes", order="pre"):
          s.spec._full_hash = None
          s.spec._build_hash = None
          s.spec._hash = None
          s.spec.architecture = spack.spec.ArchSpec(("linux",oss,"x86_64"))
    else:
      for s in css[0].traverse_edges(deptype=blr, direction="children", cover="nodes", order="pre"):
        chosen_oss = s.spec.architecture.os
        break

    for cs in css:
      for s in cs.traverse_edges(deptype=blr, direction="children", cover="nodes", order="pre"):
        h = s.spec.build_hash()
        if h not in all_specs:
          all_specs[h] = {"spec": s.spec, "dependency_hashes": [], "dependent_hashes": []}
        
          q = list(s.spec.dependencies(deptype=blr))
          ds = all_specs[h]["dependency_hashes"]
          while len(q) > 0:
            s2 = q.pop(0)
            ds.append(s2.build_hash())
            q += list(s2.dependencies(deptype=blr))
          all_specs[h]["dependency_hashes"] = list(set(ds))
          
          q = list(s.spec.dependents(deptype=blr))
          ds = all_specs[h]["dependent_hashes"]
          while len(q) > 0:
            s2 = q.pop(0)
            ds.append(s2.build_hash())
            q += list(s2.dependents(deptype=blr))
          all_specs[h]["dependent_hashes"] = list(set(ds))

    # accumulate dependents + dependencies
    for rh, ro in all_specs.items():
      for dh in ro["dependent_hashes"]:
        if rh not in all_specs[dh]["dependency_hashes"]:
          all_specs[dh]["dependency_hashes"].append(rh)

      for dh in ro["dependency_hashes"]:
        if rh not in all_specs[dh]["dependent_hashes"]:
          all_specs[dh]["dependent_hashes"].append(rh)

    # sanity check: ensure symmetry of dependent/dependency relationships
    for h,o in all_specs.items():
      for dh in o["dependent_hashes"]:
        assert all_specs[dh]["dependency_hashes"].count(h) == 1
      for dh in o["dependency_hashes"]:
        assert all_specs[dh]["dependent_hashes"].count(h) == 1

    # determine which specs are up-to-date at binary build cache

    mirror_url = args.s3_path
    spec_hashes = list(all_specs.keys())
    spec_hashes_rebuild = []
    tty._msg_enabled = False
    tty._error_enabled = False
    tty._warn_enabled = False
    if not args.rebuild_all:
      for h, o in all_specs.items():
        s = o["spec"]

        try:
          if bindist.needs_rebuild(s, mirror_url, True):
            spec_hashes_rebuild.append(h)
            continue
        except Exception as e:
          spec_hashes_rebuild.append(h)
          continue

        # if here, spec does not need rebuild
        for dh in o["dependent_hashes"]:
          all_specs[dh]["dependency_hashes"].remove(h)
    else:
      spec_hashes_rebuild = spec_hashes

    if len(spec_hashes_rebuild) <= 0:
      print("No specs need rebuilding!")
      return

    def job_name(s):
      return "{}-{}".format(s.name, s.build_hash()[:6])

    def ndeps(h):
      return len(aspecs[h]["dependency_hashes"])

    aspecs = {}
    for h in all_specs.keys():
      o = all_specs[h]
      aspecs[h] = {
        "spec": o["spec"],
        "dependency_hashes": copy.deepcopy(o["dependency_hashes"]),
        "dependent_hashes": copy.deepcopy(o["dependent_hashes"])
      }

    # group jobs into stages, which each stage only depends on jobs from prior stages
    tty._debug = True
    spec_stages = []
    spec_hashes_rebuild.sort(key=ndeps)
    assert ndeps(spec_hashes_rebuild[0]) == 0 
    total_specs = len(spec_hashes_rebuild)
    stage_n = 0
    cnt = 0 
    while cnt < total_specs:
      this_stage = []
      for ii, h in enumerate(spec_hashes_rebuild):
        if ii == 0:
          assert ndeps(h) == 0

        if ndeps(h) > 0:
          break

        this_stage.append(h)

      # `this_stage` is complete; save it
      spec_stages.append(this_stage)
      cnt += len(this_stage)
      stage_n += 1
      
      # mark each spec in this_stage as a satisfied dependency of its dependents
      for h in this_stage: 
        for dh in aspecs[h]["dependent_hashes"]:
          aspecs[dh]["dependency_hashes"].remove(h)

      spec_hashes_rebuild = spec_hashes_rebuild[ii:]
      spec_hashes_rebuild.sort(key=ndeps)
      this_stage = []

    # generate .gitlab-ci.yml
    y = {}
    stages = []
    for ii, hs in enumerate(spec_stages):
      stage = "s{}".format(str(ii).zfill(2))
      stages.append(stage)
      for h in hs:
        job = job_name(all_specs[h]["spec"])
        needs = [job_name(all_specs[dh]["spec"]) for dh in all_specs[h]["dependency_hashes"]]
        y[job] = {
          "stage": stage,
          "variables": {
            "SPEC_YAML_FILE": "{}.yaml".format(job)
          },
          "before_script": [ 
            "export SPACK_CLONE_DIR=$(mktemp -d)",
            "pushd ${SPACK_CLONE_DIR}",
            "git clone ${SPACK_REPO} --branch ${SPACK_REF} --single-branch --depth=1",
            "popd",
            ". \"${SPACK_CLONE_DIR}/spack/share/spack/setup-env.sh\"",
            "echo \"${SIGNING_KEY}\" > key.priv",
            "spack gpg trust key.priv",
            "spack mirror add s3 ${S3_MIRROR}"
          ],
          "script": [
            "time spack -d install --cache-only --only dependencies ./specs/${SPEC_YAML_FILE}",
            "time spack -d install --no-cache --only package ./specs/${SPEC_YAML_FILE}",
            "spack -d buildcache create -d s3 -afr --key ${SIGNING_KEY_ID} --no-deps -y ./specs/${SPEC_YAML_FILE}"
          ],
          "tags": list(runner_tags),
        }
        if oss != '':
          y[job]["image"] = {
            "name": os_to_runner_map[oss],
            "entrypoint": ['']
          }
        if len(needs) > 0:
          y[job]["needs"] = needs

    # write .gitlab-ci.yml
    y["stages"] = stages
    ci_file = ".gitlab-ci.yml.{}".format(oss) if oss != '' else ".gitlab-ci.yml.{}".format(chosen_oss)
    with open(ci_file,"w") as ymlfile:
      yaml.dump(y, ymlfile, default_flow_style=False)
    
    # write .spec.yaml files
    specs_dir = os.path.abspath(args.specs_dir)
    if oss != '':
      specs_dir += "-{}".format(oss)
    else:
      specs_dir += "-{}".format(chosen_oss)
    os.makedirs(specs_dir, exist_ok=True)
    ss = list(chain.from_iterable(spec_stages))
    for h in ss:
      s = all_specs[h]["spec"]
      spec_yaml = s.to_yaml(hash=ht.build_hash) 
      spec_yaml_path = os.path.join(specs_dir, "{}.yaml".format(job_name(s)))
      with open(spec_yaml_path, 'w') as fs:
        fs.write(spec_yaml)
