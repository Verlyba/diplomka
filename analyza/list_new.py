import json, glob, os, re, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
ROOT = "C:/Users/green/diplomka/"
def model_of(p): return os.path.basename((p or "").replace("\\", "/"))
print("=== ORCHESTRACE (runs/2026092[56]-*) ===")
rows = []
for f in sorted(glob.glob(ROOT + "runs/202609[2][56]-*.json")):
    r = json.load(open(f, encoding="utf-8")); rid = os.path.basename(f)[:-5]
    cat = {x["slug"]: x.get("policy_path") for x in r.get("catalog", [])}
    cs = r.get("cost_summary") or {}
    st = r["steps"]
    seq = " ".join(f"{ {'catch_cube':'C','carry_cube':'K','homing':'H'}.get(s['step'], s['step'][:1])}{'+' if s['success'] else '-'}" for s in st)
    rows.append((rid, model_of(cat.get("catch_cube")), r["config"].get("task_slug"), r.get("success"), r.get("duration_s"),
                 max([s.get("replan", 0) for s in st] or [0]), cs.get("planner_calls"), cs.get("inspector_calls"), len(st), seq,
                 r["config"].get("temporal_ensemble"), (r.get("error") or "")[:40]))
for i, x in enumerate(rows):
    print(f"{i+1:>2} {x[0]}  {x[1]:<32} slug={x[2]:<10} sys={'ANO' if x[3] else 'ne '} {x[4]:>6}s replan={x[5]} CEO={x[6]} VLM={x[7]} kroků={x[8]:>2} ens={x[10]} | {x[9]} {x[11]}")
json.dump(rows, open("C:/Users/green/AppData/Local/Temp/claude/C--Users-green-diplomka/3092d090-a58d-4566-873f-9ee439d802b4/scratchpad/orch_new.json", "w", encoding="utf-8"), ensure_ascii=False)
