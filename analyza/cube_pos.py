import json, glob, os, sys, io, math
import numpy as np
from PIL import Image
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
ROOT = "C:/Users/green/diplomka/"
def cube(p):
    a = np.array(Image.open(p).convert("RGB")).astype(int)
    m = (a[..., 1] > 80) & (a[..., 1] > a[..., 0] + 20) & (a[..., 1] > a[..., 2] + 20)
    ys, xs = np.nonzero(m)
    return None if len(xs) < 400 else (float(xs.mean()), float(ys.mean()), len(xs))
runs = sorted(os.path.basename(f)[:-5] for f in glob.glob(ROOT + "runs/20260926-*.json") if os.path.basename(f)[:-5] >= "20260926-164147")
pos = {}
for i, rid in enumerate(runs):
    r = json.load(open(ROOT + f"runs/{rid}.json", encoding="utf-8"))
    init = (r.get("initial_images") or [None])[0]
    c = cube(ROOT + init) if init else None
    pos[rid] = c
    print(f"řádek {5+i:>2d} {rid}  cs={'100' if 'diplomka_1' in json.dumps(r['catalog']) else '15 '}  kostka init: " + (f"({c[0]:.0f},{c[1]:.0f}) plocha {c[2]} px" if c else "nenalezena"))
json.dump({k: v for k, v in pos.items()}, open("C:/Users/green/AppData/Local/Temp/claude/C--Users-green-diplomka/3092d090-a58d-4566-873f-9ee439d802b4/scratchpad/cube_pos.json", "w"))
print("\nBlízké dvojice (< 25 px):")
ks = [k for k in runs if pos[k]]
for i in range(len(ks)):
    for j in range(i + 1, len(ks)):
        a, b = pos[ks[i]], pos[ks[j]]
        d = math.hypot(a[0] - b[0], a[1] - b[1])
        if d < 25: print(f"  řádek {5+runs.index(ks[i])} ↔ {5+runs.index(ks[j])}: {d:.0f} px")
