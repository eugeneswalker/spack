import argparse
import spack.environment as ev
import sys
import os
import json
import spack.hash_types as ht

description = "figure out every package needed to concretize an environment"
section = "build"
level = "short"

def setup_parser(subparser):
  subparser.add_argument(
    '--specs-dir',
    dest='specs_dir',
    required=True,
    help="""where to write specs""")
  subparser.add_argument(
    '--manifest-file',
    dest='manifest_file',
    required=True,
    help="""where to write manifest""")

def stage_ci(parser, args):

  manifest = os.path.abspath(args.manifest_file)
  specs_dir = os.path.abspath(args.specs_dir)

  env = ev.get_env(args, 'stage-ci', required=True)
  ss = env.concretize()
  rss = []
  i = []
  for idx,(_,s0) in enumerate(ss):
    rss.append(s0)
    i.append(s0)
    s0.nd = 0
    for s in s0.traverse():
      s.nd = 0
      i.append(s)

  for s in i:
    for d in s.dependents():
      d.nd += 1

  jobs = []
  stages = []
  n = 0
  i.sort(key=lambda x: x.nd)
  while len(i) > 0:
    x = 0
    stage = []
    for s in i:
      if s.nd == 0:
        stage.append(s)
      else:
        break
      x += 1
    i = i[x:]
    for s in stage:
      for z in s.dependents():
        z.nd -= 1
 
    stages.append(n)
    for s in sorted(list(set(stage)), key=lambda x: x.name):
      j = {"spec": s.name, "stage_num": n, "root": s.root.name, "yaml": s.to_yaml(hash=ht.build_hash), "dag_hash": s.dag_hash(), "build_hash": s.build_hash()}
      jobs.append(j)
    n += 1
    i.sort(key=lambda x: x.nd)

  jobs.sort(key=lambda j: j["stage_num"])
  tot = len(jobs)

  for i,j in enumerate(jobs):
    yf = "{}_{}.yaml".format(j["spec"],j["build_hash"][:7])
    specfile = "{}/{}".format(specs_dir,yf)
    jobs[i]["filename"] = specfile
    with open(specfile, 'w') as fs:
      fs.write(j['yaml'])

    del jobs[i]["yaml"]
  
  with open(manifest,"w") as manifest:
    manifest.write(json.dumps({"jobs":jobs},indent=1))
