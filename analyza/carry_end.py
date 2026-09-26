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
print(f"{'soubor':16s} {'#':>2s} {'dur':>5s} {'konec':28s} {'load: start→max→konec':>22s} {'gripper°: start→max→konec':>26s} {'cíl gripperu na konci':>21s} {'|joint-cíl| na konci (k0..k5)':>34s} {'uvolněno?':>10s}")
for f in sorted(glob.glob("telemetry/2026091[9]*.jsonl")+glob.glob("telemetry/2026092*.jsonl")+glob.glob("telemetry/20260906*.jsonl")):
    ev=load(f)
    tk=[e for e in ev if e.get("event")=="tick" and e.get("joints")]
    dones=[e for e in ev if e.get("event")=="task_done"]
    n=0
    for s in [e for e in ev if e.get("event")=="task_started" and e.get("task")=="carry_cube"]:
        n+=1
        d=next((x for x in dones if x["t"]>=s["t"]),None)
        if not d: continue
        seg=[t for t in tk if s["t"]<=t["t"]<=d["t"]]
        if len(seg)<5: continue
        if all(seg[0]["joints"]==q["joints"] for q in seg[3:]): continue
        g=[t["joints"][5] for t in seg]; ld=[t["load"] for t in seg]
        last=seg[-1]
        diff=[abs(a-b) for a,b in zip(last["joints"],last["target"])] if last.get("target") else [0]*6
        # uvolněno = zátěž byla vysoká a pak klesla
        hi=[i for i,v in enumerate(ld) if v>=250]
        rel="ano" if hi and min(ld[hi[0]:])<120 and ld[-1]<200 else ("drží" if ld[-1]>=250 else "?")
        print(f"{os.path.basename(f)[:15]:16s} {n:>2d} {d['t']-s['t']:5.1f} {d['reason'][:28]:28s} {ld[0]:6.0f}→{max(ld):4.0f}→{ld[-1]:4.0f} {g[0]:8.1f}→{max(g):5.1f}→{g[-1]:5.1f} {last['target'][5]:21.1f} {' '.join(f'{x:5.1f}' for x in diff):>34s} {rel:>10s}")
