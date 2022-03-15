import spack.environment as env
import spack.mirror
import spack.hash_types as ht
import spack.binary_distribution as binary
import llnl.util.tty as tty
import yaml
import sys
import os
import copy
import json
from itertools import chain
import multiprocessing
from collections import defaultdict


def setup_parser(subparser): 
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
  name, spec, mirror = o
  needs_rebuild = False
  try:
    tty._msg_enabled = False
    tty._error_enabled = False
    if binary.needs_rebuild(spec, mirror, True):
      needs_rebuild = True
  except Exception as e:
    needs_rebuild = True

  return (name, needs_rebuild)


def zci(parser, args, **kwargs):

  def jobname(s):
    return "{}@{}%{}-{} {}".format(s.name, s.version, s.compiler, s.dag_hash(7), s.architecture)

  def specfilename(s):
    return "{}-{}.spec.json".format(s.name, s.dag_hash(7))

  tty._warn_enabled = False

  e = env.active_environment()
  with spack.concretize.disable_compiler_existence_check():
    with e.write_transaction():
      e.concretize()
      e.write()

  css = [cs for _, cs in e.concretized_specs()]


  m = defaultdict(list)
  rebuilds = {}
  roots = {}


  for cs in css:
    roots[jobname(cs)] = True
    for s in cs.traverse(deptype=all):
      for d in s.dependencies(deptype=all):
        djob = jobname(d)
        rjob = jobname(s)
        m[djob].append(rjob)
        rebuilds[djob] = d


  for k, v in m.items():
    m[k] = list(set(v))

  if args.mirror:
    jobs = [(k, v, args.mirror) for k, v in rebuilds.items()]
    pool = multiprocessing.Pool(multiprocessing.cpu_count())
    results = pool.map(is_rebuild_required, jobs)
    pool.close()
    pool.join()

    for (name, needs_rebuild) in results:
      if not needs_rebuild:
        del rebuilds[name]

  tty.msg("Needs rebuild = {}".format(len(list(rebuilds.keys()))))

  needsMap = {}
  staged = {}
  stages = []
  current_stage = 0
  while len(list(rebuilds.keys())) > 0:
    stage = []

    for k, v in rebuilds.items():
      deps = list(v.dependencies(deptype=all))

      cleared = 0
      needs = []
      for d in deps:
        n = jobname(d)
        if n not in rebuilds:
          cleared += 1
        if n in staged:
          needs.append(n)

      outstanding_needs = len(deps) - cleared
      assert outstanding_needs >= 0, "Needs < 0 for {}".format(k)
      if outstanding_needs == 0:
        stage.append(k)
        tty.msg("Mapping needs for {}".format(k))
        needsMap[k] = needs

    assert len(stage) > 0
    stages.append(stage)

    len0 = len(rebuilds)
    for n in stage:
      staged[n] = rebuilds[n]
      del rebuilds[n]
    len1 = len(rebuilds)
    assert len0 - len1 == len(stage), "{}, {}, expected decrease = {}".format(len0,len1,len(stage))

  y = {
    "stages": list(range(len(stages))),
    "jobs": {}
  }
  for ii, jobs in enumerate(stages):
    for j in jobs:
      spec = staged[j]

      y["jobs"][j] = {
        "stage": ii,
        "spec_name": spec.name,
        "is_root": j in roots,
        "spec_file": specfilename(spec),
        "dag_hash": spec.dag_hash(),
        "full_hash": spec.full_hash(),
        "build_hash": spec.build_hash(),
        "needs": needsMap[j]
      }

  basename = os.path.basename(args.output)
  dirname = os.path.dirname(args.output)
  if len(dirname) == 0:
    dirname = "."

  os.makedirs(dirname, exist_ok=True)
  output_path = os.path.abspath(os.path.join(dirname, basename))
  
  with open(output_path, "w") as fs:
    fs.write(json.dumps(y, indent=1))
  
  specs_dir_basename = "specs"
  specs_dir = os.path.abspath(os.path.join(dirname, specs_dir_basename))
  os.makedirs(specs_dir, exist_ok=True)

  ss = list(chain.from_iterable(stages))
  for j in ss:
    s = staged[j]
    f = os.path.join(specs_dir, specfilename(s))
    with open(f, 'w') as fs:
      fs.write(s.to_json(hash=ht.build_hash) )

  return 0
