import json, glob, os, sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
def load(f):
    out=[]
    for l in open(f,encoding="utf-8",errors="replace"):
        l=l.strip()
        if l:
            try: out.append(json.loads(l))
            except Exception: pass
    return out
# každý carry_cube krok: zátěž po 0,6 s (a úhel gripperu), aby šlo vidět tvar
print("Zátěž gripperu (load) v čase kroku, vzorky po ~0,6 s.  Značky: G = úhel gripperu ≥ 30° (otevřený)")
for f in sorted(glob.glob("telemetry/20260906*.jsonl")+glob.glob("telemetry/20260919*.jsonl")+glob.glob("telemetry/2026092*.jsonl")):
    ev=load(f)
    tk=[e for e in ev if e.get("event")=="tick" and e.get("joints") and e.get("state")=="RUNNING"]
    dones=[e for e in ev if e.get("event")=="task_done"]
    n=0
    for s in [e for e in ev if e.get("event")=="task_started" and e.get("task")=="carry_cube"]:
        n+=1
        d=next((x for x in dones if x["t"]>=s["t"]),None)
        if not d: continue
        seg=[t for t in tk if s["t"]<=t["t"]<=d["t"]]
        if len(seg)<5 or all(seg[0]["joints"]==q["joints"] for q in seg[3:]): continue
        step=3
        row=[]
        for i in range(0,len(seg),step):
            t=seg[i]; row.append(f"{t['load']:3.0f}{'G' if t['joints'][5]>=30 else ' '}")
        print(f"{os.path.basename(f)[:15]} #{n} {d['t']-s['t']:4.1f}s {'A' if 'Protokol A' in d['reason'] else 'T'} | "+" ".join(row))
