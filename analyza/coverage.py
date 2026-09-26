import json, glob, os, sys, io, math
import numpy as np
from PIL import Image
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
ROOT = "C:/Users/green/diplomka/"
SP = "C:/Users/green/AppData/Local/Temp/claude/C--Users-green-diplomka/3092d090-a58d-4566-873f-9ee439d802b4/scratchpad/"
def cube(p):
    a = np.array(Image.open(p).convert("RGB")).astype(int)
    m = (a[..., 1] > 80) & (a[..., 1] > a[..., 0] + 20) & (a[..., 1] > a[..., 2] + 20)
    ys, xs = np.nonzero(m)
    return None if len(xs) < 400 else (float(xs.mean()), float(ys.mean()))
demos = []
for f in sorted(glob.glob(ROOT + "exported_frames/diplomka_1/top/episode_*.png")):
    c = cube(f)
    if c: demos.append(c)
demos = np.array(demos); print("demonstrací s nalezenou kostkou:", len(demos), "z", len(glob.glob(ROOT + "exported_frames/diplomka_1/top/episode_*.png")))
pos = json.load(open(SP + "cube_pos.json"))
verdict = {5:"ANO",6:"ANO",7:"ANO",8:"ANO",9:"ANO",10:"ANO",11:"NE",12:"NE",13:"ANO",14:"NE",15:"ANO",16:"NE",17:"NE",18:"ANO",19:"ANO",20:"ANO"}
runs = sorted(pos)
out = {}
print(f"{'řádek':>5s} {'poloha':>10s} {'nejbližší demo [px]':>20s} {'demo do 40 px':>14s} {'demo do 60 px':>14s}  úsudek  1.pokus?")
for i, rid in enumerate(runs):
    row = 5 + i
    c = pos[rid]
    d = np.hypot(demos[:, 0] - c[0], demos[:, 1] - c[1])
    r = json.load(open(ROOT + f"runs/{rid}.json", encoding="utf-8"))
    first_try = bool(r["steps"]) and r["steps"][0]["step"] == "catch_cube" and r["steps"][0]["success"]
    out[row] = dict(nearest=float(d.min()), n40=int((d < 40).sum()), n60=int((d < 60).sum()))
    print(f"{row:>5d} ({c[0]:3.0f},{c[1]:3.0f}) {d.min():20.0f} {int((d<40).sum()):14d} {int((d<60).sum()):14d}  {verdict.get(row,'(cs100)'):>6s}  {'ano' if first_try else 'ne'}")
json.dump(out, open(SP + "coverage.json", "w"))
# popisně: prvních-pokus úspěch vs pokrytí (jen cs15, 16 poloh)
cs15 = [(5+i, out[5+i]["n60"]) for i, rid in enumerate(runs) if 5+i <= 20]
ft = {5+i: (json.load(open(ROOT + f"runs/{rid}.json", encoding="utf-8"))["steps"][0]["success"] and json.load(open(ROOT + f"runs/{rid}.json", encoding="utf-8"))["steps"][0]["step"]=="catch_cube") for i, rid in enumerate(runs) if 5+i <= 20}
srt = sorted(cs15, key=lambda x: x[1])
lo, hi = srt[:8], srt[8:]
print("\nnejhůř pokrytých 8 poloh (n60):", [(r, n) for r, n in lo], "-> úspěch 1. pokusem:", sum(ft[r] for r, _ in lo), "/ 8")
print("nejlépe pokrytých 8 poloh (n60):", [(r, n) for r, n in hi], "-> úspěch 1. pokusem:", sum(ft[r] for r, _ in hi), "/ 8")
