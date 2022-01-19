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
import multiprocessing

def setup_parser(subparser):
  subparser.add_argument(
    '--globalvar',
    dest='global_vars',
    action='append',
    required=False,
    help="""create global (key, val) pair"""
  )
  subparser.add_argument(
    '--spack-ref',
    dest='spack_ref',
    required=False,
    default="",
    help="""Spack Git SHA Ref"""
  )
  subparser.add_argument(
    '--mirror',
    dest='mirror',
    required=False,
    help="""mirror to check""")
  subparser.add_argument(
    '--output', '-o',
    dest='output',
    default="./dag.json",
    required=False,
    help="""output file""")

description = "generate a build manifest containing each spack job needed to install an environment"
section = "build"
level = "short"

def is_rebuild_required(o):
  h, s, m = o
  need_rebuild = False
  try:
    if bindist.needs_rebuild(s["spec"], m, True):
      need_rebuild = True
  except Exception as e:
    need_rebuild = True
  return (h, need_rebuild)

def get_jobs(parser, args, **kwargs):
  global_vars = {}
  if args.global_vars:
    for kv in args.global_vars:
      try:
        k, v = kv.split('=', 1)
        global_vars[k] = v
      except:
        tty.msg("Bad value for --globalvar: should be key=value format: {0} ...ignoring".format(kv))
        return 1
  
  blr = ["build", "link", "run"]

  e = env.active_environment()

  with spack.concretize.disable_compiler_existence_check():
    e.concretize()

  all_specs = {}

  root_spec_hashes = {
    cs.dag_hash(): abspec for (abspec, cs) in e.concretized_specs()
  }
  
  for (_, cs) in e.concretized_specs():
    
    for s in cs.traverse_edges(deptype=blr, direction="children", cover="nodes", order="pre"):
      h = s.spec.dag_hash()

      is_root = h in root_spec_hashes
      abspec = root_spec_hashes[h] if h in root_spec_hashes else ""

      all_specs[h] = {
        "spec": s.spec, 
        "dependency_hashes": [], 
        "dependent_hashes": [],
        "is_root": is_root,
        "abstract_spec": abspec
      }
      
      q = list(s.spec.dependencies(deptype=blr))
      while len(q) > 0:
        s2 = q.pop(0)
        all_specs[h]["dependency_hashes"].append(s2.dag_hash())
        q += list(s2.dependencies(deptype=blr))
      all_specs[h]["dependency_hashes"] = list(set(all_specs[h]["dependency_hashes"]))
        
      q = list(s.spec.dependents(deptype=blr))
      while len(q) > 0:
        s2 = q.pop(0)
        all_specs[h]["dependent_hashes"].append(s2.dag_hash())
        q += list(s2.dependents(deptype=blr))
      all_specs[h]["dependent_hashes"] = list(set(all_specs[h]["dependent_hashes"]))
  
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
    jobs = [(h, o, mirror_url) for h, o in all_specs.items()]
    pool = multiprocessing.Pool(multiprocessing.cpu_count())
    results = pool.map(is_rebuild_required, jobs)
    pool.close()
    pool.join()
    for (h, needs_rebuild) in results:
      if needs_rebuild:
        spec_hashes_rebuild.append(h)
        continue
      for dh in all_specs[h]["dependent_hashes"]:
        all_specs[dh]["dependency_hashes"].remove(h) 
  else:
    spec_hashes_rebuild = spec_hashes

  if len(spec_hashes_rebuild) <= 0:
    print("No specs need rebuilding!")
    return 0

  def job_name(s):
    return "{}@{}%{}-{} {}".format(s.name, s.version, s.compiler, s.dag_hash(7), s.architecture)

  def job_name_brief(s):
    return "{}-{}".format(s.name, s.dag_hash(7))
  
  def spec_filename(s):
    return "{}.spec.json".format(job_name_brief(s))

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

  y = {
    "stages": list(range(len(spec_stages))),
    "spack_ref": args.spack_ref,
    "variables": {k:v for k, v in global_vars.items()},
    "jobs": {}
  }
  for stage_i, hs in enumerate(spec_stages):
    for h in hs:
      s = all_specs[h]["spec"]
      is_root = all_specs[h]["is_root"]
      job = job_name(s)
      needs = [job_name(all_specs[dh]["spec"]) for dh in all_specs[h]["dependency_hashes"]]
      y["jobs"][job] = {
        "stage": stage_i,
        "spec_name": s.name,
        "is_root": is_root,
        "spec_file": spec_filename(s),
        "dag_hash": s.dag_hash(),
        "full_hash": s.full_hash(),
        "build_hash": s.build_hash(),
        "needs": needs
      }

      gpu = "none"
      abspec = all_specs[h]["abstract_spec"]
      if "+cuda" in abspec:
        gpu = "cuda"
      if "+rocm" in abspec or "+hip" in abspec:
        gpu = "rocm"
      
      y["jobs"][job]["gpu"] = gpu

      if is_root:
        s = all_specs[h]["spec"]
        abspec = all_specs[h]["abstract_spec"]
        container_name = "{}-{}".format(s.name, s.version)
        if "+cuda" in abspec:
          container_name += "-cuda"
        if "+rocm" in abspec or "+hip" in abspec:
          container_name += "-rocm"
        container_name += "-{}-{}".format(s.dag_hash(7), s.full_hash(7))
        y["jobs"][job]["variables"] = {
          "CONTAINER_NAME": container_name,
          "ABSTRACT_SPEC": str(abspec)
        }

  basename = os.path.basename(args.output)
  dirname = os.path.dirname(args.output)

  if len(dirname) == 0:
    dirname = "."

  os.makedirs(dirname, exist_ok=True)
  output_path = os.path.abspath(os.path.join(dirname, basename))
  
  with open(output_path, "w") as fs:
    fs.write(json.dumps(y, indent=1))
  
  # write spec files
  specs_dir_basename = "specs"
  specs_dir = os.path.abspath(os.path.join(dirname, specs_dir_basename))
  os.makedirs(specs_dir, exist_ok=True)

  ss = list(chain.from_iterable(spec_stages))
  for h in ss:
    s = all_specs[h]["spec"]
    f = os.path.join(specs_dir, spec_filename(s))
    with open(f, 'w') as fs:
      fs.write(s.to_json(hash=ht.build_hash) )

  return 0
