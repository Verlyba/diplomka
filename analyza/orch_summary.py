import json, glob, collections, re, os, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
rows=[]
for f in sorted(glob.glob("runs/*.json")):
    r=json.load(open(f,encoding="utf-8"))
    rid=os.path.basename(f)[:-5]
    c=r.get("config",{}); st=r.get("steps",[])
    cat={x["slug"]:x.get("policy_path") for x in r.get("catalog",[])}
    cc=(cat.get("catch_cube") or "").replace("\\","/")
    rows.append(dict(id=rid, ok=bool(r.get("success")), dur=r.get("duration_s") or 0, steps=st,
        model=os.path.basename(cc), slug=c.get("task_slug"), err=(r.get("error") or "")[:70],
        maxrep=c.get("max_replans"), skipi=c.get("skip_inspector"), ens=c.get("temporal_ensemble"),
        early=r.get("goal_early_exit")))
print("běhů celkem:", len(rows))
byday=collections.defaultdict(list)
for r in rows: byday[r["id"][:8]].append(r)
for d,rs in byday.items():
    print(d, f"běhů {len(rs):2d}  úspěšných {sum(r['ok'] for r in rs):2d}  s ≥1 krokem {sum(1 for r in rs if r['steps']):2d} | catch_cube model:",
          collections.Counter(r["model"] or "-" for r in rs).most_common(2))
json.dump(rows, open(os.environ.get("OUT","orch_rows.json"),"w",encoding="utf-8"), ensure_ascii=False)
