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
import json

def setup_parser(subparser): 
  subparser.add_argument(
    '--mirror',
    dest='mirror_path',
    required=False,
    help="""mirror path""") 

description = "generate dependency and dependent information for all specs in an environment"
section = "build"
level = "short"

def dd(parser, args, **kwargs):
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


  ask=list(all_specs.keys())
  k = ask[0]
  d=all_specs[k]
  print(k)
  print(d["spec"].name)
  print(json.dumps(d["dependent_hashes"], indent=1))
  print(json.dumps(d["dependency_hashes"], indent=1))
  return 0 

  # determine which specs are up-to-date at binary build cache
  mirror_url = args.mirror_path
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


  for ii, hs in enumerate(spec_stages):
    # ii = stage num
    # hs = spec hashes in stage
    continue
 
  # write .spec.yaml files
  specdir = os.path.abspath("./specs")
  os.makedirs(specdir, exist_ok=True)

  ss = list(chain.from_iterable(spec_stages))
  for h in ss:
    s = all_specs[h]["spec"]
    spec_yaml = s.to_yaml(hash=ht.build_hash) 
    spec_yaml_path = os.path.join(specdir, "{}.yaml".format(job_name(s)))
    with open(spec_yaml_path, 'w') as fs:
      fs.write(spec_yaml)
