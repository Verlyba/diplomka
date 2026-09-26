"""Přehraje ReleaseTracker přes skutečnou telemetrii všech carry_cube kroků (5 Hz)."""
import json, glob, os, sys, io
sys.path.insert(0, r"C:\Users\green\diplomka")
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
from release_detect import ReleaseTracker
LIMIT = 300.0                      # protocol_b_limit_ma v projektu
HOLD, FREE = 0.66 * LIMIT, 0.5 * LIMIT
# telemetrie je 5 Hz, démon jede 30 Hz: patience 3 tiky (0,1 s) ~ 1 telemetrický tik;
# usazení gripperu 7 tiků (0,23 s) ~ 2 tiky; práh pohybu 0,5°/tik ~ 3°/0,2 s
mk = lambda: ReleaseTracker(HOLD, FREE, hold_patience=1, settle_ticks=2, still_delta=3.0)
def load(f):
    out=[]
    for l in open(f,encoding="utf-8",errors="replace"):
        l=l.strip()
        if l:
            try: out.append(json.loads(l))
            except Exception: pass
    return out
print(f"hold ≥ {HOLD:.0f}, free ≤ {FREE:.0f}\n")
print(f"{'soubor':16s} {'#':>2s} {'skutečný konec':>16s} {'sevřel v [s]':>13s} {'pustil v [s]':>13s} {'gripper při pustění':>20s} {'při skut. konci: drží?':>24s}  poznámka")
tot={"drží_při_konci":0,"pustil_před_koncem":0,"nikdy_nedržel":0,"pustil_po_konci":0}
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
        if len(seg)<3 or all(seg[0]["joints"]==q["joints"] for q in seg[3:]): continue
        tr=mk(); t_g=t_r=None; g_at=None; prev=None
        for t in seg:
            rise=t["load"]-(t.get("baseline") or 0.0)
            step=abs(t["joints"][5]-prev) if prev is not None else float("inf")
            prev=t["joints"][5]
            was_g, was_r = tr.gripped, tr.released
            if t["t"]-s["t"] < 0.75:                    # PROTOCOL_B_GRACE_S: náběh na startu kroku se nevyhodnocuje
                continue
            tr.update(rise, step, plateau=abs(t.get("slope") or 0.0) < 30.0)
            if tr.gripped and not was_g: t_g=t["t"]-s["t"]
            if tr.released and not was_r: t_r=t["t"]-s["t"]; g_at=t["joints"][5]
        end=d["t"]-s["t"]
        note=""
        if not tr.gripped: tot["nikdy_nedržel"]+=1; note="nic nedržel"
        elif not tr.released: tot["drží_při_konci"]+=1; note="←→ konec s kostkou v čelistech (protokol A by byl zablokován)"
        else: tot["pustil_před_koncem"]+=1
        prot = "A" if "Protokol A" in d["reason"] else "T"
        print(f"{os.path.basename(f)[:15]:16s} {n:>2d} {end:8.1f}s ({prot})   {('%.1f'%t_g) if t_g is not None else '-':>13s} {('%.1f'%t_r) if t_r is not None else '-':>13s} {('%.1f°'%g_at) if g_at is not None else '-':>20s} {'ANO' if tr.holding else 'ne':>24s}  {note}")
print("\n", tot)
