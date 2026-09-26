import json, sys, os
from PIL import Image, ImageDraw, ImageFont
ROOT = "C:/Users/green/diplomka/"
OUT = "C:/Users/green/AppData/Local/Temp/claude/C--Users-green-diplomka/3092d090-a58d-4566-873f-9ee439d802b4/scratchpad/"
def montage(rid, cols=5, scale=0.42):
    r = json.load(open(ROOT + f"runs/{rid}.json", encoding="utf-8"))
    tiles = []
    init = (r.get("initial_images") or [None])[0]
    if init: tiles.append(("init", ROOT + init))
    for s in r["steps"]:
        p = (s.get("images") or [None])[0]
        if p: tiles.append((f"#{s['attempt']} {s['step'][:5]} {'OK' if s['success'] else 'FAIL'}", ROOT + p))
    if not tiles: return None
    w, h = int(640 * scale), int(480 * scale)
    rows = (len(tiles) + cols - 1) // cols
    im = Image.new("RGB", (cols * w, rows * (h + 16)), "white")
    d = ImageDraw.Draw(im)
    for i, (lab, p) in enumerate(tiles):
        try: t = Image.open(p).convert("RGB").resize((w, h))
        except Exception: t = Image.new("RGB", (w, h), "gray")
        x, y = (i % cols) * w, (i // cols) * (h + 16)
        im.paste(t, (x, y + 16)); d.text((x + 3, y + 2), lab, fill="black")
    fn = OUT + f"montage_{rid}.png"
    im.save(fn); return fn
for rid in sys.argv[1:]:
    print(montage(rid))
