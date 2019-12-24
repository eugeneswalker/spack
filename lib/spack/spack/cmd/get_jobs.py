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
    '--no-needs',
    dest='disable_needs',
    action='store_true',
    default=False,
    required=False,
    help="""where to write job specs yaml files to""") 
  subparser.add_argument(
    '--tags',
    dest='tags',
    required=True,
    type=str,
    help="""CI runner tags""")
  subparser.add_argument(
    '--s3',
    dest='s3',
    required=True,
    help="""S3 mirror path""") 

description = "generate a build manifest containing each spack job needed to install an environment"
section = "build"
level = "short"

def get_jobs(parser, args, **kwargs):
  tags = []
  try:
    tags = args.tags.split(',')
  except:
    sys.stderr.write("Runner tags must be comma separated. Failed to process: {}\n".format(args.tags))
    return 1

  if 'TARGET_OS' not in os.environ or 'TARGET_ARCH' not in os.environ:
    sys.stderr.write("Required environment variables are missing: TARGET_OS, TARGET_ARCH\n")
    return 1

  target_os = os.environ["TARGET_OS"]
  target_arch = os.environ["TARGET_ARCH"]
  os_arch_tag = "{}-{}".format(target_os, target_arch)

  os_to_runner_map = {
    "ubuntu18.04": "ecpe4s/ubuntu18.04-runner:0.13.2",
    "centos7": "ecpe4s/centos7-runner:0.13.3",
    "centos8": "ecpe4s/centos8-runner:0.13.2",
    "rhel7": "ecpe4s/rhel7-runner:0.13.2",
    "rhel8": "ecpe4s/rhel8-runner:0.13.2"
  }

  if target_os not in os_to_runner_map:
    sys.stderr.write("Error: no entry in os_to_runner_map for TARGET_OS='{}'\n".format(target_os))
    return 1

  blr = ["build", "link", "run"]

  e = env.get_env(None, 'get_jobs', required=True)
  e.concretize()
  css = [cs for (us,cs) in e.concretized_specs()]
  all_specs = {}  

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

  mirror_url = args.s3
  spec_hashes = list(all_specs.keys())
  spec_hashes_rebuild = []
  tty._msg_enabled = False
  tty._error_enabled = False
  tty._warn_enabled = False
  if mirror_url:
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
      y[job] = {
        "stage": stage,
        "variables": {
          "SPEC_YAML_FILE": "./specs/{}.yaml".format(job)
        },
        "script": [
          "./do-ci-job.sh ${SPEC_YAML_FILE}"
        ],
        "tags": list(tags),
      }

      # docker image
      y[job]["image"] = {
        "name": os_to_runner_map[target_os],
        "entrypoint": ['']
      }

      # dag scheduling
      needs = [job_name(all_specs[dh]["spec"]) for dh in all_specs[h]["dependency_hashes"]]
      if len(needs) > 0 and not args.disable_needs:
        y[job]["needs"] = needs

  # write .gitlab-ci.yml
  y["stages"] = stages

  ci_file = ".gitlab-ci.yml.{}".format(os_arch_tag)
  with open(ci_file,"w") as ymlfile:
    yaml.dump(y, ymlfile, default_flow_style=False)
  
  # write .spec.yaml files
  specs_dir = os.path.abspath("./specs-{}".format(os_arch_tag))
  os.makedirs(specs_dir, exist_ok=True)

  ss = list(chain.from_iterable(spec_stages))
  for h in ss:
    s = all_specs[h]["spec"]
    spec_yaml = s.to_yaml(hash=ht.build_hash) 
    spec_yaml_path = os.path.join(specs_dir, "{}.yaml".format(job_name(s)))
    with open(spec_yaml_path, 'w') as fs:
      fs.write(spec_yaml)
