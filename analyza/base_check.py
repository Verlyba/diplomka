import json, glob, os, sys, io, time
sys.path.insert(0, "C:/Users/green/diplomka")
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
from release_detect import ReleaseTracker
files = ["184956","185206","185318","185530","185704","185846","190024","190215","190328","190437","190546","190737","190910","191013","191132","191257","191504","191623"]
user = {5:("ANO","ANO"),6:("ANO","NE"),7:("ANO","NE"),8:("NE","NE"),9:("ANO","ANO"),10:("NE","NE"),11:("ANO","ANO"),12:("ANO","NE"),13:("ANO","NE"),14:("ANO","ANO"),
        15:("ANO","NE"),16:("ANO","NE"),17:("ANO","ANO"),18:("ANO","ANO"),19:("ANO","NE"),20:("ANO","ANO"),21:("ANO","NE"),22:("ANO","ANO")}
def load(f):
    out=[]
    for l in open(f,encoding="utf-8",errors="replace"):
        l=l.strip()
        if l:
            try: out.append(json.loads(l))
            except Exception: pass
    return out
LIMIT=300.0
print(f"{'řádek':>5s} {'soubor':>7s} {'doba':>6s} {'úsudek':>7s} | {'sevřel v':>9s} {'držel [s]':>10s} {'pustil v':>9s} {'zátěž konec':>11s} {'gripper konec':>13s} | shoda s úsudkem?")
res=[]
for i,fn in enumerate(files):
    row=5+i
    f=f"telemetry/20260926-{fn}.jsonl"
    ev=load(f)
    st=next(e for e in ev if e["event"]=="task_started")
    tk=[e for e in ev if e["event"]=="tick" and e.get("state")=="RUNNING" and e.get("joints")]
    seg=[t for t in tk if t["t"]>=st["t"]]
    tr=ReleaseTracker(0.66*LIMIT,0.5*LIMIT,1,2,3.0)
    g=r=None; prev=None; hold_ticks=0
    for t in seg:
        step=abs(t["joints"][5]-prev) if prev is not None else float("inf"); prev=t["joints"][5]
        if t["t"]-st["t"]<0.75: continue
        wg,wr=tr.gripped,tr.released
        tr.update(t["load"]-(t.get("baseline") or 0.0), step, abs(t.get("slope") or 0)<30)
        if tr.gripped and not tr.released: hold_ticks+=1
        if tr.gripped and not wg and g is None: g=t["t"]-st["t"]
        if tr.released and not wr and r is None: r=t["t"]-st["t"]
    dur=seg[-1]["t"]-st["t"]
    verdict=user[row][1]
    proxy="grasp+pustil" if r else ("grasp, drží do konce" if g else "bez sevření")
    ok = (verdict=="ANO" and r) or (verdict=="NE" and not r)
    print(f"{row:>5d} {fn:>7s} {dur:5.1f}s {verdict:>7s} | {('%.1f'%g) if g else '-':>9s} {hold_ticks*0.2:10.1f} {('%.1f'%r) if r else '-':>9s} {seg[-1]['load']:11.0f} {seg[-1]['joints'][5]:13.1f} | {proxy:<22s} {'shoda' if ok else '⚠ NESEDÍ (jen indicie)'}")
