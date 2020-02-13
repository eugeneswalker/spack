import spack.environment as env
import spack.mirror
import spack.hash_types as ht
import spack.binary_distribution as bindist
import llnl.util.tty as tty
import yaml
import sys
import os
import copy
import json
from itertools import chain

def setup_parser(subparser): 
  subparser.add_argument(
    '--dd-only',
    dest='dd_only',
    action='store_true',
    default=False,
    required=False,
    help="""only dump dependency and dependent information""") 
  subparser.add_argument(
    '--no-needs',
    dest='use_needs',
    action='store_false',
    default=True,
    required=False,
    help="""where to write job specs yaml files to""") 
  subparser.add_argument(
    '--tags',
    dest='tags',
    required=False,
    type=str,
    help="""runner tags""")
  subparser.add_argument(
    '--mirror',
    dest='mirror',
    required=False,
    help="""mirror to check""")
  subparser.add_argument(
    '--runner-image',
    dest='runner_image',
    required=False,
    help="""runner image""")
  subparser.add_argument(
    '--output-file',
    dest='output_file',
    required=False,
    help="""output file""")
  subparser.add_argument(
    '--output-dir',
    dest='output_dir',
    required=False,
    help="""output dir""")
  subparser.add_argument(
    '--workdir',
    dest='workdir',
    required=False,
    help="""work dir""")
  subparser.add_argument(
    '--env-in-env',
    dest='env_in_env',
    required=False,
    default=False,
    action='store_true',
    help="""store spack environment as string in env var SPACK_ENV_YAML in downstream jobs"""
  )

description = "generate a build manifest containing each spack job needed to install an environment"
section = "build"
level = "short"

def get_jobs(parser, args, **kwargs):
  blr = ["build", "link", "run"]

  e = env.get_env(None, 'get_jobs', required=True)

  if args.env_in_env:
    spack_env_str = ""
    with open("./spack.yaml") as fs:
      spack_env_str = fs.read()

  with spack.concretize.disable_compiler_existence_check():
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
  mirror_url = args.mirror
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
    return 200

  if args.dd_only:
    j = {}
    for k in spec_hashes_rebuild:
      v = all_specs[k]
      nv = {
        "dependencies": v['dependency_hashes'],
        "dependents": v['dependent_hashes'],
        "name": v['spec'].name,
        "yaml": v['spec'].to_yaml(hash=ht.build_hash)
      }
      j[k] = nv

    with open('dd.json','w') as fs:
      fs.write(json.dumps(j, indent=1))

    return

  if not args.tags:
    print("error: missing required argument: --tags")
    return

  def job_name(s):
    return "{}@{}%{}-{} {}".format(s.name, s.version, s.compiler, s.build_hash()[:6], s.architecture)

  def job_name_brief(s):
    return "{}-{}".format(s.name, s.build_hash()[:6])

  def spec_name(s):
    return "{}".format(s.name)

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

  # group jobs into stages, where jobs in one stage only depend on jobs from previous stages
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
  y = {'variables': {}}
  if args.env_in_env:
    y['variables']['SPACK_ENV_YAML'] = spack_env_str.replace('$','_DOLLAR_')

  stages = []
  for ii, hs in enumerate(spec_stages):
    stage = "s{}".format(str(ii).zfill(2))
    stages.append(stage)
    for h in hs:
      s = all_specs[h]["spec"]
      job_brief = job_name_brief(s)
      job = job_name(s)
      y[job] = {
        "stage": stage,
        "artifacts": {
          "when": "always",
          "paths": ["artifacts/"]
        },
        "variables": {
          "SPEC_NAME": spec_name(s),
          "SPEC_YAML": "{}".format(s.to_yaml(hash=ht.build_hash)),
          "WORKDIR": args.workdir if args.workdir else ""
        },
        "script": [
          "./run-ci-job.sh"
        ] 
      }

      tags_split = args.tags.split(',')
      if len(tags_split) > 0:
        y[job]['tags'] = tags_split

      # docker image
      if args.runner_image:
        y[job]["image"] = args.runner_image

      # dag scheduling
      needs = [job_name(all_specs[dh]["spec"]) for dh in all_specs[h]["dependency_hashes"]]
      if args.use_needs and len(needs) > 0:
          y[job]["needs"] = needs

  # write .gitlab-ci.yml
  y["stages"] = stages

  if args.output_dir:
    os.makedirs(args.output_dir, exist_ok=True)

  ci_file_name = args.output_file if args.output_file else ".gitlab-ci.yml"
  ci_file_path = os.path.abspath(ci_file_name)
  if args.output_dir:
    ci_file_path = os.path.abspath(os.path.join(args.output_dir, ci_file_name))
  with open(ci_file_path,"w") as ymlfile:
    yaml.dump(y, ymlfile, default_flow_style=False)
  
  # write .spec.yaml files
  spec_dir_name = "specs"
  specs_dir = os.path.abspath(spec_dir_name)
  if args.output_dir:
    specs_dir = os.path.abspath(os.path.join(args.output_dir, spec_dir_name))
  os.makedirs(specs_dir, exist_ok=True)

  ss = list(chain.from_iterable(spec_stages))
  for h in ss:
    s = all_specs[h]["spec"]
    spec_yaml = s.to_yaml(hash=ht.build_hash) 
    spec_yaml_path = os.path.join(specs_dir, "{}.yaml".format(job_name_brief(s)))
    with open(spec_yaml_path, 'w') as fs:
      fs.write(spec_yaml)
