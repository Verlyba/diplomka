"""Odhad polohy kostky u baseline z pozice ramene v okamžiku, kdy začne zavírat gripper."""
import json, sys, io, math, random
import numpy as np
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
ROOT = "C:/Users/green/diplomka/"
SP = "C:/Users/green/AppData/Local/Temp/claude/C--Users-green-diplomka/3092d090-a58d-4566-873f-9ee439d802b4/scratchpad/"
d = json.load(open(SP + "demos_pose_pos.json")); P = np.array(d["P"]); Q = np.array(d["Q"])
W = np.array([1.0, 1.0, 0.6, 0.3, 0.0])
def predict(q, k=5):
    dd = np.sqrt((((Q - q) * W) ** 2).sum(1)); idx = np.argsort(dd)[:k]; w = 1 / (dd[idx] + 1.0)
    return (P[idx] * w[:, None]).sum(0) / w.sum(), float(dd[idx].mean())

def load(f):
    out = []
    for l in open(f, encoding="utf-8", errors="replace"):
        l = l.strip()
        if l:
            try: out.append(json.loads(l))
            except Exception: pass
    return out

FILES = ["184956","185206","185318","185530","185704","185846","190024","190215","190328","190437","190546","190737","190910","191013","191132","191257","191504","191623"]
ROWS = list(range(5, 23))
VERD = {5:"ANO",6:"NE",7:"NE",8:"NE",9:"ANO",10:"NE",11:"NE",12:"NE",13:"NE",14:"ANO",15:"NE",16:"NE",17:"ANO",18:"ANO",19:"NE",20:"ANO",21:"NE",22:"ANO"}   # řádek 11 podle opravy uživatele
COUNT = {r: r not in (8, 10) for r in ROWS}

def aim_pose(fn):
    ev = load(ROOT + f"telemetry/20260926-{fn}.jsonl")
    st = [e for e in ev if e["event"] == "task_started"][0]["t"]
    tk = [e for e in ev if e["event"] == "tick" and e.get("state") == "RUNNING" and e.get("joints")]
    seg = [e for e in tk if e["t"] >= st]
    j0 = np.array(seg[0]["joints"][:5])
    if max(np.abs(np.array(e["joints"][:5]) - j0).max() for e in seg) < 5: return None, None
    g = np.array([e["joints"][5] for e in seg]); t = np.array([e["t"] - st for e in seg])
    # začátek zavírání: gripper byl nad 30° a poprvé klesl o ≥ 4° pod svůj dosavadní vrchol
    peak = -1; peak_i = None
    for i, x in enumerate(g):
        if x > peak: peak, peak_i = x, i
        if peak >= 30 and x <= peak - 4: return np.array(seg[peak_i]["joints"][:5], float), float(t[peak_i])
    return None, None

est = {}
for row, fn in zip(ROWS, FILES):
    q, tc = aim_pose(fn)
    if q is None: est[row] = None; continue
    p, dist = predict(q)
    est[row] = (p, tc, dist, q)

orch = json.load(open(SP + "cube_pos.json"))
runs = sorted(orch)[:16]                                   # 16 započítaných běhů cs15 (164147 … 180139) v pořadí
opos = np.array([orch[r][:2] for r in runs])

print("baseline: odhad polohy z pozice ramene při zavírání gripperu")
print(f"{'řádek':>5s} {'úsudek':>7s} {'počítá':>7s} {'zavírá v':>9s} {'odhad polohy [px]':>18s} {'vzdálenost k demo v pozici':>26s}   nejbližší poloha orchestrace (řádek, px)")
for row in ROWS:
    e = est[row]
    if e is None: print(f"{row:>5d} {VERD[row]:>7s} {'ano' if COUNT[row] else 'ne':>7s}   (rameno se nehýbalo / žádné zavírání)"); continue
    p, tc, dist, q = e
    dd = np.hypot(opos[:, 0] - p[0], opos[:, 1] - p[1]); k = int(dd.argmin())
    print(f"{row:>5d} {VERD[row]:>7s} {'ano' if COUNT[row] else 'ne':>7s} {tc:8.1f}s ({p[0]:3.0f},{p[1]:3.0f})  {dist:26.1f}   řádek {5+k} ({dd[k]:.0f} px)")

# ── hypotéza: baseline procházel křížky ve stejném pořadí jako orchestrace cs15
cnt_rows = [r for r in ROWS if COUNT[r]]                    # 16 započítaných baseline pokusů v pořadí
pairs = [(i, r) for i, r in enumerate(cnt_rows) if est[r] is not None]
def mean_dist(order):                                        # order[i] = index polohy orchestrace přiřazený i-tému baseline pokusu
    return np.mean([math.hypot(*(est[r][0] - opos[order[i]])) for i, r in pairs])
same = mean_dist(list(range(16))); rev = mean_dist(list(range(15, -1, -1)))
random.seed(1); rnd = []
for _ in range(20000):
    o = list(range(16)); random.shuffle(o); rnd.append(mean_dist(o))
rnd = np.array(rnd)
print(f"\nPokusů s odhadem: {len(pairs)} z 16 započítaných")
print(f"průměrná vzdálenost odhadu od polohy orchestrace při STEJNÉM pořadí: {same:.0f} px | při opačném: {rev:.0f} px | náhodné pořadí: medián {np.median(rnd):.0f} px (5. percentil {np.percentile(rnd,5):.0f})")
print(f"podíl náhodných pořadí, která sedí aspoň tak dobře jako stejné pořadí: {(rnd <= same).mean():.3f}")
json.dump({str(r): (None if est[r] is None else {"pos": est[r][0].tolist(), "t_close": est[r][1], "dist": est[r][2]}) for r in ROWS},
          open(SP + "base_pos.json", "w"))
