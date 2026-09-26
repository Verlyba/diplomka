"""Postavi/aktualizuje poznamky/zaznam_behu.xlsx ze skutecnych dat.

Baseline a orchestrace jsou DVE ODDELENE tabulky (nemaji stejne sloupce -
baseline nema CEO/inspektora/re-plany, orchestrace ma automaticky verdikt,
baseline ne). Soucet je rozdeleny podle (podminka x pocet epizod), protoze
srovnani se dela vzdy na stejnem poctu epizod pro obe vetve.

CUTOFF: vyvojove/testovaci behy pred zamrznutim metodiky se sem NECPOU -
napis do CUTOFF datum, od ktereho zacina ostre mereni; vse starsi se
pri generovani ignoruje (zustane to v runs//telemetry na disku, jen se
to nenacpe do sesitu).

Bezpecne opakovane spustitelne: rucne vyplnene sloupce (POCITA SE?,
VAS USUDEK - USPECH?, Poznamka) se pri kazdem spusteni nactou ze
stavajiciho souboru a spoji zpatky podle ID radku.
"""
import json
import re
from pathlib import Path
from datetime import datetime

import openpyxl
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.utils import get_column_letter

ROOT = Path("C:/Users/green/diplomka")
OUT = ROOT / "poznamky" / "zaznam_behu.xlsx"

# ─── UPRAV PODLE POTREBY ────────────────────────────────────────────────────
# Behy/telemetrie starsi tohoto data se do sesitu vubec nenactou (vyvojove
# behy pred zamrznutim metodiky). Format YYYYMMDD.
CUTOFF = "20260916"
# ─────────────────────────────────────────────────────────────────────────

FONT_NAME = "Arial"
HEADER_FILL = PatternFill("solid", fgColor="1F4E78")
HEADER_FONT = Font(name=FONT_NAME, size=10, bold=True, color="FFFFFF")
AUTO_FONT = Font(name=FONT_NAME, size=10, color="000000")
MANUAL_FONT = Font(name=FONT_NAME, size=10, bold=True, color="0000FF")
MANUAL_FILL = PatternFill("solid", fgColor="FFFFCC")
NORMAL_FONT = Font(name=FONT_NAME, size=10)
FORMULA_FONT = Font(name=FONT_NAME, size=10)
TITLE_FONT = Font(name=FONT_NAME, size=14, bold=True)
SECTION_FONT = Font(name=FONT_NAME, size=12, bold=True, color="1F4E78")
NOTE_FONT = Font(name=FONT_NAME, size=9, italic=True, color="595959")
THIN = Side(style="thin", color="D9D9D9")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
Z = 1.959963985  # z pro 95% oboustranny Wilsonuv interval

EP_RE = re.compile(r"_(\d+)ep_")


def ep_count(policy_path):
    if not policy_path:
        return ""
    m = EP_RE.search(policy_path.replace("\\", "/"))
    return m.group(1) if m else ""


def short_model(policy_path):
    if not policy_path:
        return ""
    return Path(policy_path.replace("\\", "/")).name


def run_stamp(run_id: str):
    m = re.match(r"(\d{8})-(\d{6})", run_id)
    if not m:
        return None, "", ""
    date_part, time_part = m.groups()
    return date_part, f"{date_part[:4]}-{date_part[4:6]}-{date_part[6:]}", \
        f"{time_part[:2]}:{time_part[2:4]}"


# ── 1) Precti existujici rucni sloupce (pokud sesit uz existuje) ───────────
def load_existing_manual(sheet, id_col_name="ID"):
    out = {}
    if not OUT.exists():
        return out
    try:
        wb_old = openpyxl.load_workbook(OUT)
        if sheet not in wb_old.sheetnames:
            return out
        ws_old = wb_old[sheet]
        header_row = None
        for r in range(1, 25):
            if ws_old.cell(row=r, column=1).value == id_col_name:
                header_row = r
                break
        if not header_row:
            return out
        col_map = {ws_old.cell(row=header_row, column=c).value: c
                  for c in range(1, ws_old.max_column + 1)}
        id_col = col_map.get(id_col_name)
        pocita_col = col_map.get("POCITA SE?")
        usudek_col = col_map.get("VAS USUDEK - USPECH?")
        pozn_col = col_map.get("Poznamka")
        if not id_col:
            return out
        for r in range(header_row + 1, ws_old.max_row + 1):
            rid = ws_old.cell(row=r, column=id_col).value
            if not rid:
                continue
            out[str(rid)] = {
                "pocita": ws_old.cell(row=r, column=pocita_col).value if pocita_col else None,
                "usudek": ws_old.cell(row=r, column=usudek_col).value if usudek_col else None,
                "pozn": ws_old.cell(row=r, column=pozn_col).value if pozn_col else None,
            }
    except Exception as e:
        print(f"({sheet}) predchozi rucni sloupce se nepodarilo precist: {e}")
    return out


EXIST_BASE = load_existing_manual("Baseline")
EXIST_ORCH = load_existing_manual("Orchestrace")

# ── 2) Orchestrace: jeden radek na jeden runs/*.json ────────────────────────
orch_rows = []
for f in sorted((ROOT / "runs").glob("*.json")):
    date_part, date_str, time_str = run_stamp(f.stem)
    if date_part is None or date_part < CUTOFF:
        continue
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        continue
    run_id = data.get("run_id") or f.stem
    cfg = data.get("config") or {}
    catalog = data.get("catalog") or []
    steps = data.get("steps") or []
    cost = data.get("cost_summary") or {}

    condition = "Orchestrace (bez re-planu)" if cfg.get("max_replans") == 0 else "Orchestrace"
    eps = sorted({e for e in (ep_count(s.get("policy_path")) for s in catalog) if e})
    models = sorted({short_model(s.get("policy_path")) for s in catalog if s.get("policy_path")})

    orch_rows.append({
        "id": run_id, "date": date_str, "time": time_str, "condition": condition,
        "model": ", ".join(models), "ep": "/".join(eps),
        "duration": data.get("duration_s"),
        "replans": max((s.get("replan") or 0) for s in steps) if steps else 0,
        "planner_calls": cost.get("planner_calls"), "inspector_calls": cost.get("inspector_calls"),
        "system_verdict": "ANO" if data.get("success") else "NE",
    })

# ── 3) Baseline: kazdy task_started->(task_done|dalsi start|EOF) usek       ──
# je samostatny pokus. Baseline se pozna podle policy_path v daemon_start,
# ktery neobsahuje jmeno zadneho kroku (baseline vzdy bezi na modelu pro
# CELOU ulohu, ne na modelu jednoho kroku).
step_slugs = set()
for pf in (ROOT / "projects").glob("*.json"):
    try:
        pdata = json.loads(pf.read_text(encoding="utf-8"))
        step_slugs.update(s.get("slug") for s in pdata.get("steps", []) if s.get("slug"))
    except Exception:
        pass

base_rows = []
for f in sorted((ROOT / "telemetry").glob("*.jsonl")):
    date_part, date_str, time_str = run_stamp(f.stem)
    if date_part is None or date_part < CUTOFF:
        continue
    try:
        lines = [json.loads(l) for l in f.read_text(encoding="utf-8").splitlines() if l.strip()]
    except Exception:
        continue
    if not lines or lines[0].get("event") != "daemon_start":
        continue
    policy_path = lines[0].get("policy_path") or ""
    if any(slug and slug in policy_path for slug in step_slugs):
        continue  # patri orchestrovanemu kroku, ne baseline

    segments = []
    seg_start = None
    for row in lines[1:]:
        ev = row.get("event")
        if ev == "task_started":
            if seg_start is not None:
                segments.append((seg_start, row["t"]))  # predchozi usek uzavren dalsim startem
            seg_start = row["t"]
        elif ev == "task_done" and seg_start is not None:
            segments.append((seg_start, row["t"]))
            seg_start = None
    if seg_start is not None and lines:
        segments.append((seg_start, lines[-1]["t"]))

    for i, (s, e) in enumerate(segments, start=1):
        base_rows.append({
            "id": f"{f.stem}#{i}", "date": date_str, "time": time_str,
            "model": short_model(policy_path), "ep": ep_count(policy_path),
            "duration": round(e - s, 1),
        })

orch_rows.sort(key=lambda r: (r["date"], r["time"], r["id"]))
base_rows.sort(key=lambda r: (r["date"], r["time"], r["id"]))

# Jen ciste jednohodnotove pocty epizod (napr. "60") - orchestracni beh
# s neshodnym poctem epizod mezi kroky (napr. "120/60") neni fer srovnavaci
# bod se stejnym poctem epizod na obe strany, takze do rozpadu Souhrnu
# nepatri (v tabulce Orchestrace zustane videt, jen mimo tenhle rozpad).
ALL_EPS = sorted({r["ep"] for r in orch_rows if r["ep"] and r["ep"].isdigit()}
                 | {r["ep"] for r in base_rows if r["ep"] and r["ep"].isdigit()},
                 key=int)
if not ALL_EPS:
    ALL_EPS = ["20", "60", "120"]  # rozumny default, dokud nejsou zadna data

ORCH_CONDITIONS = ["Orchestrace", "Orchestrace (bez re-planu)"]

# ── 4) Sestav sesit ─────────────────────────────────────────────────────────
wb = openpyxl.Workbook()


def style_header(ws, row, headers, col0=1):
    for i, h in enumerate(headers):
        c = ws.cell(row=row, column=col0 + i, value=h)
        c.font = HEADER_FONT
        c.fill = HEADER_FILL
        c.alignment = Alignment(wrap_text=True, vertical="center", horizontal="center")
        c.border = BORDER


def wilson_formulas(n_cell, p_cell):
    wc = f'(({p_cell}+({Z}^2)/(2*{n_cell}))/(1+({Z}^2)/{n_cell}))'
    wm = (f'(({Z}/(1+({Z}^2)/{n_cell}))*SQRT(({p_cell}*(1-{p_cell})/{n_cell})'
         f'+(({Z}^2)/(4*{n_cell}^2))))')
    lo = f'=IF({n_cell}=0,"",MAX(0,{wc}-{wm}))'
    hi = f'=IF({n_cell}=0,"",MIN(1,{wc}+{wm}))'
    return lo, hi


# ═══════════════════════════ List "Souhrn" ═════════════════════════════════
ws_sum = wb.active
ws_sum.title = "Souhrn"
ws_sum.sheet_view.showGridLines = False
ws_sum["A1"] = "Souhrn podle podminky x pocet epizod"
ws_sum["A1"].font = TITLE_FONT
ws_sum.merge_cells("A1:H1")
ws_sum["A2"] = ("Pocita se VZDY jen z radku s POCITA SE=ANO a z VLASTNIHO uspech/neuspech (ne "
               "z automatickeho verdiktu, ten u baseline stejne neexistuje). Srovnavej radky se "
               "stejnym poctem epizod mezi Baseline a Orchestrace.")
ws_sum["A2"].font = NOTE_FONT
ws_sum.merge_cells("A2:H2")

sum_headers = ["Podminka", "Epizod", "N (pocita se)", "Uspechu (tvuj usudek)",
              "Uspesnost", "Wilson 95% dolni", "Wilson 95% horni", "Prumerna doba (s)"]
SUM_HEADER_ROW = 4
style_header(ws_sum, SUM_HEADER_ROW, sum_headers)
widths_sum = [26, 9, 13, 16, 11, 15, 15, 16]
for col, w in enumerate(widths_sum, start=1):
    ws_sum.column_dimensions[get_column_letter(col)].width = w

sum_combos = [("Baseline", ep) for ep in ALL_EPS] + \
            [(cond, ep) for cond in ORCH_CONDITIONS for ep in ALL_EPS]

r = SUM_HEADER_ROW + 1
for cond, ep in sum_combos:
    ws_sum.cell(row=r, column=1, value=cond).font = NORMAL_FONT
    ws_sum.cell(row=r, column=2, value=ep).font = NORMAL_FONT
    if cond == "Baseline":
        sheet_ref, cond_col, dur_col, pocita_col, usudek_col, ep_col = "Baseline", None, "F", "G", "H", "E"
        rng_ep = f"{sheet_ref}!${ep_col}$999:${ep_col}$99999"
        rng_dur = f"{sheet_ref}!${dur_col}$999:${dur_col}$99999"
        rng_pocita = f"{sheet_ref}!${pocita_col}$999:${pocita_col}$99999"
        rng_usudek = f"{sheet_ref}!${usudek_col}$999:${usudek_col}$99999"
        n_formula = f'=COUNTIFS({rng_ep},$B{r},{rng_pocita},"ANO")'
        succ_formula = f'=COUNTIFS({rng_ep},$B{r},{rng_pocita},"ANO",{rng_usudek},"ANO")'
        dur_formula = (f'=IF(C{r}=0,"",AVERAGEIFS({rng_dur},{rng_ep},$B{r},'
                       f'{rng_pocita},"ANO"))')
    else:
        sheet_ref = "Orchestrace"
        rng_cond = f"{sheet_ref}!$D$999:$D$99999"
        rng_ep = f"{sheet_ref}!$F$999:$F$99999"
        rng_dur = f"{sheet_ref}!$G$999:$G$99999"
        rng_pocita = f"{sheet_ref}!$L$999:$L$99999"
        rng_usudek = f"{sheet_ref}!$M$999:$M$99999"
        n_formula = f'=COUNTIFS({rng_cond},$A{r},{rng_ep},$B{r},{rng_pocita},"ANO")'
        succ_formula = (f'=COUNTIFS({rng_cond},$A{r},{rng_ep},$B{r},{rng_pocita},"ANO",'
                        f'{rng_usudek},"ANO")')
        dur_formula = (f'=IF(C{r}=0,"",AVERAGEIFS({rng_dur},{rng_cond},$A{r},{rng_ep},$B{r},'
                       f'{rng_pocita},"ANO"))')
    ws_sum.cell(row=r, column=3, value=n_formula).font = FORMULA_FONT
    ws_sum.cell(row=r, column=4, value=succ_formula).font = FORMULA_FONT
    ws_sum.cell(row=r, column=5, value=f'=IF(C{r}=0,"",D{r}/C{r})').font = FORMULA_FONT
    ws_sum.cell(row=r, column=5).number_format = "0.0%"
    lo, hi = wilson_formulas(f"C{r}", f"E{r}")
    ws_sum.cell(row=r, column=6, value=lo).font = FORMULA_FONT
    ws_sum.cell(row=r, column=6).number_format = "0.0%"
    ws_sum.cell(row=r, column=7, value=hi).font = FORMULA_FONT
    ws_sum.cell(row=r, column=7).number_format = "0.0%"
    ws_sum.cell(row=r, column=8, value=dur_formula).font = FORMULA_FONT
    ws_sum.cell(row=r, column=8).number_format = "0.0"
    for col in range(1, 9):
        ws_sum.cell(row=r, column=col).border = BORDER
        if col > 2:
            ws_sum.cell(row=r, column=col).alignment = Alignment(horizontal="center")
    r += 1
    if cond == "Baseline" and ep == ALL_EPS[-1]:
        r += 1  # mezera pred orchestraci

note_r = r + 1
ws_sum.cell(row=note_r, column=1,
           value=("Min. 15-20 behu na kazdou pocitanou kombinaci (podminka x epizod), jinak je "
                  "Wilsonuv interval prilis siroky na obhajitelny zaver. Pocty epizod v radcich "
                  "(" + ", ".join(ALL_EPS) + ") jsou odvozene z toho, co je aktualne v datech "
                  "(nebo vychozi 20/60/120, dokud zadna data nejsou) - dopis si dalsi rucne, "
                  "kdybys pridal jiny pocet epizod.")).font = NOTE_FONT
ws_sum.merge_cells(f"A{note_r}:H{note_r}")

# ═══════════════════════════ List "Baseline" ═══════════════════════════════
ws_b = wb.create_sheet("Baseline")
ws_b.sheet_view.showGridLines = False
ws_b["A1"] = "Baseline - jednotlive pokusy"
ws_b["A1"].font = TITLE_FONT
ws_b.merge_cells("A1:H1")
ws_b["A2"] = ("Baseline nema zadny automaticky verdikt (--no-triggers vypina vsechny kontroly) - "
             "uspech/neuspech je VZDY tvuj vlastni usudek. Jeden radek = jeden pokus (mezi "
             "SET_TASK a koncem/dalsim SET_TASK), ne cely soubor - jeden telemetry soubor muze "
             "obsahovat vic pokusu za sebou.")
ws_b["A2"].font = NOTE_FONT
ws_b.merge_cells("A2:H2")

b_headers = ["ID", "Datum", "Cas", "Model / checkpoint", "Epizod", "Doba pokusu (s)",
            "POCITA SE?", "VAS USUDEK - USPECH?", "Poznamka"]
B_HEADER_ROW = 4
style_header(ws_b, B_HEADER_ROW, b_headers)
widths_b = [22, 11, 8, 28, 9, 13, 11, 16, 30]
for col, w in enumerate(widths_b, start=1):
    ws_b.column_dimensions[get_column_letter(col)].width = w

B_AUTO_COLS = {1, 2, 3, 4, 5, 6}
B_MANUAL_COLS = {7, 8, 9}
B_LAST_ROW = 998

for i, row in enumerate(base_rows):
    r = B_HEADER_ROW + 1 + i
    prev = EXIST_BASE.get(row["id"], {})
    vals = [row["id"], row["date"], row["time"], row["model"], row["ep"], row["duration"],
           prev.get("pocita"), prev.get("usudek"), prev.get("pozn")]
    for col, v in enumerate(vals, start=1):
        c = ws_b.cell(row=r, column=col, value=v)
        c.border = BORDER
        c.font = AUTO_FONT if col in B_AUTO_COLS else MANUAL_FONT
        if col in B_MANUAL_COLS:
            c.fill = MANUAL_FILL
        c.alignment = Alignment(horizontal="center" if col != 9 else "left")

for r in range(B_HEADER_ROW + 1 + len(base_rows), B_LAST_ROW + 1):
    for col in range(1, len(b_headers) + 1):
        c = ws_b.cell(row=r, column=col)
        c.border = BORDER
        c.font = AUTO_FONT if col in B_AUTO_COLS else MANUAL_FONT
        if col in B_MANUAL_COLS:
            c.fill = MANUAL_FILL
        c.alignment = Alignment(horizontal="center" if col != 9 else "left")

dv_yn_b = DataValidation(type="list", formula1='"ANO,NE"', allow_blank=True, showDropDown=False)
ws_b.add_data_validation(dv_yn_b)
dv_yn_b.add(f"G{B_HEADER_ROW+1}:H{B_LAST_ROW}")
dv_ep_b = DataValidation(type="list", formula1='"' + ",".join(ALL_EPS) + '"',
                         allow_blank=True, showDropDown=False)
ws_b.add_data_validation(dv_ep_b)
dv_ep_b.add(f"E{B_HEADER_ROW+1}:E{B_LAST_ROW}")
ws_b.freeze_panes = f"A{B_HEADER_ROW+1}"

# ═══════════════════════════ List "Orchestrace" ════════════════════════════
ws_o = wb.create_sheet("Orchestrace")
ws_o.sheet_view.showGridLines = False
ws_o["A1"] = "Orchestrace - jednotlive behy"
ws_o["A1"].font = TITLE_FONT
ws_o.merge_cells("A1:M1")
ws_o["A2"] = ("Jeden radek = jeden cely beh (runs/<ID>.json). Verdikt systemu je pole success "
             "z toho souboru - VAS USUDEK muze byt jiny, Souhrn pocita jen z VASEHO usudku.")
ws_o["A2"].font = NOTE_FONT
ws_o.merge_cells("A2:M2")

o_headers = ["ID behu", "Datum", "Cas", "Podminka", "Model(y)", "Epizod", "Doba behu (s)",
            "Re-planu", "Volani CEO", "Volani inspektora", "Verdikt systemu",
            "POCITA SE?", "VAS USUDEK - USPECH?", "Poznamka"]
O_HEADER_ROW = 4
style_header(ws_o, O_HEADER_ROW, o_headers)
widths_o = [16, 11, 8, 22, 34, 9, 12, 9, 10, 12, 13, 11, 16, 30]
for col, w in enumerate(widths_o, start=1):
    ws_o.column_dimensions[get_column_letter(col)].width = w

O_AUTO_COLS = set(range(1, 12))
O_MANUAL_COLS = {12, 13, 14}
O_LAST_ROW = 998

for i, row in enumerate(orch_rows):
    r = O_HEADER_ROW + 1 + i
    prev = EXIST_ORCH.get(row["id"], {})
    vals = [row["id"], row["date"], row["time"], row["condition"], row["model"], row["ep"],
           row["duration"], row["replans"], row["planner_calls"], row["inspector_calls"],
           row["system_verdict"], prev.get("pocita"), prev.get("usudek"), prev.get("pozn")]
    for col, v in enumerate(vals, start=1):
        c = ws_o.cell(row=r, column=col, value=v)
        c.border = BORDER
        c.font = AUTO_FONT if col in O_AUTO_COLS else MANUAL_FONT
        if col in O_MANUAL_COLS:
            c.fill = MANUAL_FILL
        c.alignment = Alignment(horizontal="center" if col not in (5, 14) else "left")

for r in range(O_HEADER_ROW + 1 + len(orch_rows), O_LAST_ROW + 1):
    for col in range(1, len(o_headers) + 1):
        c = ws_o.cell(row=r, column=col)
        c.border = BORDER
        c.font = AUTO_FONT if col in O_AUTO_COLS else MANUAL_FONT
        if col in O_MANUAL_COLS:
            c.fill = MANUAL_FILL
        c.alignment = Alignment(horizontal="center" if col not in (5, 14) else "left")

dv_cond_o = DataValidation(type="list", formula1='"' + ",".join(ORCH_CONDITIONS) + '"',
                           allow_blank=True, showDropDown=False)
ws_o.add_data_validation(dv_cond_o)
dv_cond_o.add(f"D{O_HEADER_ROW+1}:D{O_LAST_ROW}")
dv_yn_o = DataValidation(type="list", formula1='"ANO,NE"', allow_blank=True, showDropDown=False)
ws_o.add_data_validation(dv_yn_o)
dv_yn_o.add(f"L{O_HEADER_ROW+1}:M{O_LAST_ROW}")
dv_ep_o = DataValidation(type="list", formula1='"' + ",".join(ALL_EPS) + '"',
                         allow_blank=True, showDropDown=False)
ws_o.add_data_validation(dv_ep_o)
dv_ep_o.add(f"F{O_HEADER_ROW+1}:F{O_LAST_ROW}")
ws_o.freeze_panes = f"A{O_HEADER_ROW+1}"

# Prepis rozsahy v Souhrnu z placeholderu $999:$99999 na skutecne cislo
# posledniho radku, aby COUNTIFS/AVERAGEIFS nesahaly zbytecne daleko.
for row_cells in ws_sum.iter_rows(min_row=SUM_HEADER_ROW + 1):
    for c in row_cells:
        if isinstance(c.value, str) and "$999:" in c.value:
            c.value = (c.value.replace(f"Baseline!$E$999:$E$99999", f"Baseline!$E${B_HEADER_ROW+1}:$E${B_LAST_ROW}")
                      .replace(f"Baseline!$F$999:$F$99999", f"Baseline!$F${B_HEADER_ROW+1}:$F${B_LAST_ROW}")
                      .replace(f"Baseline!$G$999:$G$99999", f"Baseline!$G${B_HEADER_ROW+1}:$G${B_LAST_ROW}")
                      .replace(f"Baseline!$H$999:$H$99999", f"Baseline!$H${B_HEADER_ROW+1}:$H${B_LAST_ROW}")
                      .replace(f"Orchestrace!$D$999:$D$99999", f"Orchestrace!$D${O_HEADER_ROW+1}:$D${O_LAST_ROW}")
                      .replace(f"Orchestrace!$F$999:$F$99999", f"Orchestrace!$F${O_HEADER_ROW+1}:$F${O_LAST_ROW}")
                      .replace(f"Orchestrace!$G$999:$G$99999", f"Orchestrace!$G${O_HEADER_ROW+1}:$G${O_LAST_ROW}")
                      .replace(f"Orchestrace!$L$999:$L$99999", f"Orchestrace!$L${O_HEADER_ROW+1}:$L${O_LAST_ROW}")
                      .replace(f"Orchestrace!$M$999:$M$99999", f"Orchestrace!$M${O_HEADER_ROW+1}:$M${O_LAST_ROW}"))

wb.save(OUT)
print(f"saved {OUT} | orchestrace: {len(orch_rows)} | baseline pokusu: {len(base_rows)} "
     f"| epizody: {ALL_EPS} | cutoff: {CUTOFF}")
