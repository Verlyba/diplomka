"""Lze z pozice ramene při úchopu odvodit, kde ležela kostka? Kalibrace na 120 demonstracích,
ověření na skutečných úspěšných úchopech orchestrace (poloha kostky známá ze snímku)."""
import json, glob, os, sys, io, math
import numpy as np, pandas as pd
from PIL import Image
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
ROOT = "C:/Users/green/diplomka/"
SP = "C:/Users/green/AppData/Local/Temp/claude/C--Users-green-diplomka/3092d090-a58d-4566-873f-9ee439d802b4/scratchpad/"

def cube(p):
    a = np.array(Image.open(p).convert("RGB")).astype(int)
    m = (a[..., 1] > 80) & (a[..., 1] > a[..., 0] + 20) & (a[..., 1] > a[..., 2] + 20)
    ys, xs = np.nonzero(m)
    return None if len(xs) < 400 else (float(xs.mean()), float(ys.mean()))

# ── 1) demonstrace: poloha kostky na začátku -> pozice ramene na konci catch_cube epizody
df = pd.read_parquet(r"C:\Users\green\.cache\huggingface\lerobot\local\diplomka_1_catch_cube\data\chunk-000\file-000.parquet",
                     columns=["episode_index", "frame_index", "observation.state"])
last = df.sort_values(["episode_index", "frame_index"]).groupby("episode_index").tail(1).set_index("episode_index")
P, Q = [], []
for ep, row in last.iterrows():
    c = cube(ROOT + f"exported_frames/diplomka_1/top/episode_{int(ep):03d}.png")
    if c: P.append(c); Q.append(np.array(row["observation.state"], float)[:5])
P, Q = np.array(P), np.array(Q)
print("demonstrací:", len(P))
W = np.array([1.0, 1.0, 0.6, 0.3, 0.0])          # váhy kloubů: pan a rameno/loket věrohodné, zápěstí ne (viz dřívější rozptyl)

def predict(q, Pk=P, Qk=Q, k=5, exclude=None):
    d = np.sqrt((((Qk - q) * W) ** 2).sum(1))
    if exclude is not None: d[exclude] = np.inf
    idx = np.argsort(d)[:k]
    w = 1 / (d[idx] + 1.0)
    return (Pk[idx] * w[:, None]).sum(0) / w.sum(), d[idx].mean()

# leave-one-out chyba na demonstracích
err = []
for i in range(len(P)):
    est, _ = predict(Q[i], exclude=i)
    err.append(math.hypot(*(est - P[i])))
err = np.array(err)
print(f"LOO chyba odhadu polohy z pozice ramene (demonstrace): medián {np.median(err):.0f} px, p75 {np.percentile(err,75):.0f}, p90 {np.percentile(err,90):.0f}  (kostka ~35 px, křížky od sebe 50–150 px)")

# ── 2) ověření na orchestraci: úspěšné úchopy (Protokol B) s kostkou, jejíž polohu znám ze snímku před krokem
def load(f):
    out = []
    for l in open(f, encoding="utf-8", errors="replace"):
        l = l.strip()
        if l:
            try: out.append(json.loads(l))
            except Exception: pass
    return out
tel = {}
for f in sorted(glob.glob(ROOT + "telemetry/20260926-*.jsonl")):
    ev = load(f)
    tk = [e for e in ev if e.get("event") == "tick" and e.get("joints")]
    tel[f] = tk
allticks = sorted((t["t"], t["joints"]) for tk in tel.values() for t in tk)
T = np.array([a[0] for a in allticks])
def pose_at(t):
    i = int(np.searchsorted(T, t)) - 1
    return np.array(allticks[max(i, 0)][1][:5], float)

runs = sorted(os.path.basename(f)[:-5] for f in glob.glob(ROOT + "runs/20260926-*.json") if os.path.basename(f)[:-5] >= "20260926-164147")
val = []
for rid in runs:
    r = json.load(open(ROOT + f"runs/{rid}.json", encoding="utf-8"))
    st = r["steps"]
    for i, s in enumerate(st):
        if s["step"] != "catch_cube" or s.get("phys") != "CONFIRM": continue
        src = (r.get("initial_images") or [None])[0] if i == 0 else (st[i - 1].get("images") or [None])[0]
        if i > 0 and st[i - 1]["step"] != "homing": continue        # jen když je kostka před krokem vidět a nedrží se
        c = cube(ROOT + src) if src else None
        if not c: continue
        est, dist = predict(pose_at(s["t_end"] - 0.15))
        val.append((rid, s["attempt"], c, est, math.hypot(*(np.array(est) - np.array(c)))))
print(f"\nověření na {len(val)} skutečných úspěšných úchopech orchestrace (poloha kostky ze snímku):")
for rid, att, c, est, e in val:
    print(f"  {rid[-6:]} #{att:<2d} skutečná ({c[0]:3.0f},{c[1]:3.0f})  odhad ({est[0]:3.0f},{est[1]:3.0f})  chyba {e:3.0f} px")
ve = np.array([v[4] for v in val])
print(f"chyba na orchestraci: medián {np.median(ve):.0f} px, max {ve.max():.0f} px")
json.dump({"P": P.tolist(), "Q": Q.tolist()}, open(SP + "demos_pose_pos.json", "w"))
