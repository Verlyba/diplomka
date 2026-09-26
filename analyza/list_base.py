import json, glob, os, sys, io, time
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
ROOT = "C:/Users/green/diplomka/"
skill = {"catch_cube", "carry_cube", "homing"}
def load(f):
    out = []
    for l in open(f, encoding="utf-8", errors="replace"):
        l = l.strip()
        if l:
            try: out.append(json.loads(l))
            except Exception: pass
    return out
print("=== BASELINE-like telemetrie ze 26.9 (a pozdní 25.9) ===")
rows = []
for f in sorted(glob.glob(ROOT + "telemetry/202609[2][56]-*.jsonl")):
    ev = load(f)
    ds = next((e for e in ev if e.get("event") == "daemon_start"), {})
    pol = os.path.basename((ds.get("policy_path") or "").replace("\\", "/"))
    tasks = [e for e in ev if e.get("event") == "task_started"]
    if any(t.get("task") in skill for t in tasks) and len(tasks) > 0: continue          # krokové modely = orchestrace
    if "catch_cube" in pol or "carry_cube" in pol or "homing" in pol: continue
    dones = [e for e in ev if e.get("event") == "task_done"]
    name = os.path.basename(f)[:-6]
    print(f"{name}  {pol:<28} ens={ds.get('temporal_ensemble_coeff')} no_trig? úloh={len(tasks)}  task_done={len(dones)}  poslední událost t={ev[-1]['t']-tasks[0]['t'] if tasks else 0:.0f}s po startu" if tasks else f"{name}  {pol:<28} bez úlohy")
    segs = []
    seg = None
    for e in ev:
        if e.get("event") == "task_started":
            if seg is not None: segs.append((seg, e["t"], "další start"))
            seg = e["t"]
        elif e.get("event") == "task_done" and seg is not None:
            segs.append((seg, e["t"], e.get("reason", "")[:30])); seg = None
    if seg is not None: segs.append((seg, ev[-1]["t"], "konec souboru"))
    for i, (a, b, why) in enumerate(segs, 1):
        rows.append((name, i, pol, round(b - a, 1), why, a))
        print(f"    #{i}  {time.strftime('%H:%M:%S', time.localtime(a))}  {b-a:6.1f}s  {why}")
print("\nPokusů celkem:", len(rows))
json.dump(rows, open("C:/Users/green/AppData/Local/Temp/claude/C--Users-green-diplomka/3092d090-a58d-4566-873f-9ee439d802b4/scratchpad/base_new.json", "w", encoding="utf-8"), ensure_ascii=False)
