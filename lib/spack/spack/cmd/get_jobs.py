import spack.environment as ev
import operator
import json
import copy
import spack.hash_types as ht

def setup_parser(subparser):
  subparser.add_argument(
    '--output-file',
    dest='output_file',
    required=True,
    help="""where to write jobs manifest""")

description = "generate a build manifest containing each spack job needed to install an environment"
section = "build"
level = "short"

def get_jobs(parser, args, **kwargs):
  env = ev.get_env(args, 'get_jobs', required=True)
  if not env:
    print("failed to get env")
    return

  ddd = {}
  topo = []
  specs = env.concretize()
  for i, s in enumerate(specs):
    us, cs = s[0], s[1]

    ts = [cs]
    while len(ts) != 0:
      x = ts.pop(0)
      hsh = x.build_hash()
      dependents = [t.build_hash() for t in x.dependents()]
      dependencies = [t.build_hash() for t in x.dependencies()]
      if hsh not in ddd:
        topo.append(x)
        ddd[hsh] = {"name": x.name, "dependents": dependents, "dependencies": dependencies}
      else:
        ddd[hsh]["dependents"] += dependents
        ddd[hsh]["dependencies"] += dependencies
      for z in x.dependencies():
        ts.append(z)

  for h in ddd.keys():
    nd = list(set(ddd[h]["dependencies"]))
    del ddd[h]["dependencies"]
    ddd[h]["dependencies"] = nd
    nd = list(set(ddd[h]["dependents"]))    
    del ddd[h]["dependents"]
    ddd[h]["dependents"] = nd
  
  for h, dd in ddd.items():
    # check dependents
    for h2 in dd["dependents"]:
      if h not in ddd[h2]["dependencies"]:
        print("ERROR: {} has dependent {} which does not reciprocate".format(dd["name"],ddd[h2]["name"]))
    for h2 in dd["dependencies"]:
      if h not in ddd[h2]["dependents"]:
        print("ERROR: {} has dependency {} which does not reciprocate".format(dd["name"],ddd[h2]["name"]))

  

  ddd2 = copy.deepcopy(ddd)
  topo2 = []

  topo.sort(key=lambda x: len(ddd2[x.build_hash()]["dependencies"]))
  while len(topo) > 0:
    n = 0
    for i,s in enumerate(topo):
      if len(ddd2[s.build_hash()]["dependencies"]) > 0:
        break
      n += 1
    ready, topo = topo[:n], topo[n:]
    #print(" ".join(["{} {}".format(ddd[x.build_hash()]["name"],len(ddd2[x.build_hash()]["dependencies"])) for x in ready]))

    for s in ready:
      for d in ddd2[s.build_hash()]["dependents"]:
        ddd2[d]["dependencies"].remove(s.build_hash())

    topo2 += ready
    topo.sort(key=lambda x: len(ddd2[x.build_hash()]["dependencies"])) 

  o = {"dag_dd": ddd, "specs": []}
  for s in topo2:
    o["specs"].append({"name": s.name, "hash": s.build_hash(), "yaml": s.to_yaml(hash=ht.build_hash)})

  open(args.output_file, 'w').write(json.dumps(o, indent=1))
