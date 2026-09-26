import json, sys, os, glob
from PIL import Image, ImageDraw
ROOT = "C:/Users/green/diplomka/"
OUT = "C:/Users/green/AppData/Local/Temp/claude/C--Users-green-diplomka/3092d090-a58d-4566-873f-9ee439d802b4/scratchpad/"
runs = sorted(os.path.basename(f)[:-5] for f in glob.glob(ROOT + "runs/20260926-*.json"))
runs = [r for r in runs if r >= "20260926-164147"]
def tile(p, w, h):
    try: return Image.open(p).convert("RGB").resize((w, h))
    except Exception: return Image.new("RGB", (w, h), "gray")
per = 4
sheets = []
for k in range(0, len(runs), per):
    group = runs[k:k + per]
    w, h = 400, 300
    im = Image.new("RGB", (3 * w, len(group) * (h + 18)), "white"); d = ImageDraw.Draw(im)
    for i, rid in enumerate(group):
        r = json.load(open(ROOT + f"runs/{rid}.json", encoding="utf-8"))
        row = 5 + runs.index(rid)
        last = r["steps"][-1] if r["steps"] else {}
        imgs = last.get("images") or [None, None]
        init = (r.get("initial_images") or [None])[0]
        y = i * (h + 18)
        d.text((3, y + 3), f"řádek {row}  {rid}  systém={'ANO' if r['success'] else 'ne'}  poslední krok: {last.get('step')} {'ok' if last.get('success') else 'FAIL'}   |  init | poslední krok top | poslední krok zápěstí", fill="black")
        for j, p in enumerate((init, imgs[0], imgs[1] if len(imgs) > 1 else None)):
            im.paste(tile(ROOT + p, w, h) if p else Image.new("RGB", (w, h), "gray"), (j * w, y + 18))
    fn = OUT + f"finals_{k // per + 1}.png"; im.save(fn); sheets.append(fn); print(fn, [f"{5+runs.index(r)}:{r[-6:]}" for r in group])
