import spack.environment as env
import spack.mirror
import spack.hash_types as ht
import spack.binary_distribution as bindist
import llnl.util.tty as tty
import sys
import os
import copy
from itertools import chain
import json

description = "generate a build manifest containing each spack job needed to install an environment"
section = "build"
level = "short"

def dd(parser, args, **kwargs):
  blr = ["build", "link", "run"]
  e = env.get_env(None, 'get_jobs', required=True)
  e.concretize()
  css = [cs for (us,cs) in e.concretized_specs()]
  all_specs={}
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

  for k in all_specs:
    all_specs[k]["yaml"] = all_specs[k]["spec"].to_yaml(hash=ht.build_hash)
    del all_specs[k]["spec"]

  with open("dd.json","w") as fs:
    fs.write(json.dumps(all_specs,indent=1))
