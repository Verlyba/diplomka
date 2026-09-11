#!/usr/bin/env python3
"""
Vygeneruje tři schémata orchestrace do SVG:

  web/schema-komponenty.svg  — CO je s ČÍM propojené a jakým kanálem
                               (procesy, roury, HTTP, SSE, sériový port, soubory)
  web/schema-vyvojovy.svg    — CO SE DĚJE KDY: klasický vývojový diagram
                               s drahami podle vrstev (nejčitelnější z těch tří)
  web/schema-sekvence.svg    — V JAKÉM POŘADÍ si ty komponenty posílají zprávy
                               během jednoho běhu, s doslovnými řetězci protokolu

Proč generátor a ne ručně kreslené SVG: schéma má přes čtyřicet zpráv a
přesné souřadnice se ručně neudržují. Takhle se přidání zprávy nebo přejmenování
komponenty zapíše do seznamu níž a schéma se překreslí — což je podstatné,
protože zastaralé schéma v diplomce je horší než žádné.

Všechna jsou záměrně **na bílém pozadí s tmavým textem**, ne v tmavém
vzhledu aplikace: cílem je tisk v diplomce. Ve stránce se proto zobrazují na
bílé kartě.

    python make_diagrams.py            # přepíše všechna SVG ve web/
    python make_diagrams.py --check    # jen ověří, že se popisky vejdou

Nepotřebuje nic než standardní knihovnu.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from xml.sax.saxutils import escape

HERE = Path(__file__).resolve().parent
WEB = HERE / "web"

FONT = "DejaVu Sans, Segoe UI, Arial, sans-serif"
MONO = "DejaVu Sans Mono, Consolas, monospace"

INK = "#1a1d22"        # hlavní text a rámečky
MUTED = "#5c6672"      # popisky, vysvětlivky
LINE = "#aab3bd"       # čáry životnosti, slabé rámečky
ACCENT = "#b5651d"     # doslovné řetězce protokolu
PANEL = "#f2f4f7"      # výplň rámečků
BAND = "#e7ebf0"       # pruh oddělující fáze

# Odhad šířky textu — na kontrolu, jestli se popisek vejde mezi dvě životnosti.
# Přesné měření by znamenalo tahat sem font, což pro kontrolu překryvu nestojí za to.
W_SANS, W_MONO = 0.52, 0.60


def text_width(s: str, size: float, mono: bool = False) -> float:
    return len(s) * size * (W_MONO if mono else W_SANS)


def esc(s: str) -> str:
    return escape(s, {'"': "&quot;"})


# ── Malé SVG primitivy ──────────────────────────────────────────────────────

def rect(x, y, w, h, fill=PANEL, stroke=LINE, rx=5, sw=1, dash=""):
    d = f' stroke-dasharray="{dash}"' if dash else ""
    return (f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" rx="{rx}" '
            f'fill="{fill}" stroke="{stroke}" stroke-width="{sw}"{d}/>')


def line(x1, y1, x2, y2, stroke=LINE, sw=1, dash="", marker=""):
    d = f' stroke-dasharray="{dash}"' if dash else ""
    m = f' marker-end="url(#{marker})"' if marker else ""
    return (f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
            f'stroke="{stroke}" stroke-width="{sw}"{d}{m}/>')


def text(x, y, s, size=11, fill=INK, anchor="start", mono=False, weight="normal"):
    fam = MONO if mono else FONT
    return (f'<text x="{x:.1f}" y="{y:.1f}" font-family="{fam}" font-size="{size}" '
            f'fill="{fill}" text-anchor="{anchor}" font-weight="{weight}">{esc(s)}</text>')


def defs() -> str:
    return (
        '<defs>'
        f'<marker id="a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
        f'orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="{INK}"/></marker>'
        f'<marker id="ao" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" '
        f'orient="auto-start-reverse"><path d="M0,1 L10,5 L0,9" fill="none" stroke="{INK}" '
        f'stroke-width="1.4"/></marker>'
        '</defs>')


def svg(width, height, body: list[str]) -> str:
    return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
            f'viewBox="0 0 {width} {height}" role="img">'
            f'{defs()}<rect width="{width}" height="{height}" fill="#ffffff"/>'
            + "".join(body) + "</svg>")


# ── Schéma 1: komponenty a kanály ───────────────────────────────────────────
# Rozvržení je ruční, ale drží jedno pravidlo: mezi sloupci je mezera 300 px,
# aby se do ní vešly popisky kanálů. Popisek, který přeteče do sousedního
# rámečku, dělá schéma nečitelným — a přesně to se stalo první verzi.
COL_A, COL_B, COL_C = 40, 640, 1240
W_STD, W_C = 300, 280

# (id, x, y, šířka, výška, nadpis, [řádky popisu], styl)
BOXES = [
    ("browser", COL_A, 30, W_STD, 62, "Prohlížeč — web/",
     ["index.html (Setup) · orchestrace.html"], "proc"),
    ("server", COL_A, 150, W_STD, 76, "server.py",
     ["HTTP server + SSE (jen stdlib)", "vlákno běhu, EventBus"], "proc"),
    ("cfg", COL_B, 157, W_STD, 62, "config.json · projects/",
     ["nastavení, se kterým běh poběží"], "file"),
    ("orch", COL_A, 290, W_STD, 76, "orchestrator.py",
     ["Orchestrator.run() — řídicí smyčka", "fúze důkazů, re-plánování"], "proc"),
    ("lm", COL_B, 282, W_STD, 92, "LM Studio",
     ["CEO — llm_model (pomalý, zřídka)",
      "inspektor — vlm_model (rychlý, často)",
      "jeden server, dva různé modely"], "ext"),
    ("runs", COL_A, 450, W_STD, 74, "runs/<id>.json + images/<id>/",
     ["záznam běhu a snímky k pokusům —", "obojí pod týmž run_id"], "file"),
    ("daemon", COL_B, 450, W_STD, 76, "inference_daemon.py",
     ["podproces, drží ACT policy kroků", "měří protokoly A/B"], "proc"),
    ("robot", COL_C, 450, W_C, 76, "Robot SO-101 + kamery",
     ["sériový port (lerobot)", "OpenCV kamery"], "hw"),
    ("tele", COL_B, 580, W_STD, 62, "telemetry/<čas>.jsonl",
     ["tik po tiku: klouby, zátěž, sklon"], "file"),
]

# (z, do, popisek, [další řádky], druh)
#   "v"     svislá šipka ve stejném sloupci
#   "h"     vodorovná šipka ve stejné řadě
#   "elbow" zalomená (sloupec i řada se liší) — vede se pod překážkou
LINKS = [
    ("browser", "server", "HTTP: POST /api/run · /api/stop · GET /api/status",
     ["SSE: GET /api/events → log, plan, step, snapshot, state, finished"], "v"),
    ("server", "cfg", "čte a zapisuje (POST /api/config)",
     ["odsud čte nastavení i orchestrátor"], "h"),
    ("server", "orch", "přímé volání ve vlákně: Orchestrator.run(instrukce)",
     ["zpět: emit() → EventBus → SSE"], "v"),
    ("orch", "lm", "HTTP (urllib): POST <lm_url>/chat/completions",
     ["{model, messages, image_url: base64 JPEG}"], "h"),
    ("orch", "runs", "záznam na konci, snímky průběžně", [], "v"),
    ("orch", "daemon", "roury podprocesu (stdin/stdout, řádkově)",
     ["→ SET_POLICY · SET_TASK · SNAP · STOP · QUIT",
      "← [STATUS] · [SNAPSHOT] · [TELEMETRY]"], "elbow"),
    ("daemon", "robot", "sériový port + OpenCV",
     ["akce na fps Hz ↔ klouby, zátěž, snímky"], "h"),
    ("daemon", "tele", "zapisuje průběžně", [], "v"),
]


def diagram_components() -> str:
    body: list[str] = []
    by_id = {b[0]: b for b in BOXES}

    body.append(text(COL_A, 20, "Komponenty a kanály", 14, INK, weight="bold"))

    for _id, x, y, w, h, title, lines_, style in BOXES:
        fill = {"proc": PANEL, "ext": "#fdf3e7", "hw": "#eef6ef", "file": "#ffffff"}[style]
        dash = "4 3" if style == "file" else ""
        body.append(rect(x, y, w, h, fill=fill, stroke=INK if style == "proc" else LINE, dash=dash))
        body.append(text(x + 12, y + 21, title, 12, INK, mono=(style == "file"), weight="bold"))
        for i, ln in enumerate(lines_):
            body.append(text(x + 12, y + 38 + i * 14, ln, 10, MUTED))

    for src, dst, label, extra, kind in LINKS:
        a, b = by_id[src], by_id[dst]
        ax, ay, aw, ah = a[1], a[2], a[3], a[4]
        bx, by, bw, bh = b[1], b[2], b[3], b[4]

        if kind == "v":
            # Svislé šipky vycházejí z různých míst spodní hrany, aby se dvě
            # odbočky z téhož rámečku nepřekryly.
            x = ax + aw * (0.28 if dst == "runs" else 0.5)
            y1, y2 = ay + ah, by
            body.append(line(x, y1, x, y2, stroke=INK, marker="a"))
            ty = (y1 + y2) / 2 - len(extra) * 6 + 2
            body.append(text(x + 10, ty, label, 9.5, INK))
            for i, e in enumerate(extra):
                body.append(text(x + 10, ty + 12 + i * 12, e, 9.5, MUTED, mono=True))

        elif kind == "h":
            y = ay + ah / 2
            x1, x2 = ax + aw, bx
            body.append(line(x1, y, x2, y, stroke=INK, marker="a"))
            ty = y - 10 - len(extra) * 12
            body.append(text((x1 + x2) / 2, ty, label, 9.5, INK, anchor="middle"))
            for i, e in enumerate(extra):
                body.append(text((x1 + x2) / 2, ty + 12 + i * 12, e, 9.5, MUTED,
                                 anchor="middle", mono=True))

        else:  # elbow: dolů, doprava pod překážkou, a shora do cíle
            x0 = ax + aw * 0.78
            x2 = bx + bw / 2
            y_mid = by - 52
            body.append(line(x0, ay + ah, x0, y_mid, stroke=INK))
            body.append(line(x0, y_mid, x2, y_mid, stroke=INK))
            body.append(line(x2, y_mid, x2, by, stroke=INK, marker="a"))
            body.append(text(x0 + 8, y_mid - 6, label, 9.5, INK))
            for i, e in enumerate(extra):
                body.append(text(x0 + 8, y_mid + 12 + i * 12, e, 9.5, MUTED, mono=True))

    legend_y = 690
    body.append(text(COL_A, legend_y, "Plný rámeček = proces, čárkovaný = soubor na disku. "
                     "Barva: šedá = vlastní kód, oranžová = externí služba, "
                     "zelená = hardware.", 9.5, MUTED))
    return svg(COL_C + W_C + 40, legend_y + 20, body)


# ── Schéma 2: sekvence jednoho běhu ─────────────────────────────────────────

PARTS = [
    ("B", "Prohlížeč", "web/"),
    ("S", "server.py", "HTTP + SSE"),
    ("O", "Orchestrator", "orchestrator.py"),
    ("C", "LM Studio · CEO", "llm_model"),
    ("V", "LM Studio · inspektor", "vlm_model"),
    ("D", "inference_daemon.py", "podproces"),
    ("R", "Robot + kamery", "SO-101"),
    ("F", "Soubory", "runs/ · telemetry/"),
]

# ("phase", nadpis) | ("msg", z, do, popisek, styl) | ("self", kdo, popisek)
# | ("note", text) | ("loop", nadpis) | ("endloop",)
# styl: "" plná (volání), "ret" čárkovaná (odpověď), "opt" volitelná větev
SEQ = [
    ("phase", "FÁZE 0 — start běhu a výchozí snímek"),
    ("msg", "B", "S", "POST /api/run {instruction}", ""),
    ("msg", "S", "O", "run(instrukce) — nové vlákno", ""),
    ("note", "Každý emit() z orchestrátoru putuje přes EventBus serveru na SSE do prohlížeče "
             "(log, plan, step, snapshot, state). Dál se už kvůli přehlednosti nekreslí."),
    ("msg", "O", "D", "spawn: --policy.path · --protocol-a.* · --protocol-b.* · --fps", ""),
    ("msg", "D", "R", "connect (sériový port, kamery)", ""),
    ("msg", "D", "O", "[STATUS] DAEMON_READY: mode=HARDWARE", "ret"),
    ("msg", "O", "D", "SNAP", ""),
    ("msg", "D", "O", "[SNAPSHOT] <base64 JPEG>", "ret"),
    ("msg", "O", "F", "images/<id>/init_1.jpg — výchozí scéna", ""),

    ("phase", "FÁZE 1 — plán a jeho audit"),
    ("msg", "O", "C", "POST /chat/completions {llm_model}", ""),
    ("msg", "C", "O", "REASONING + pole kroků | DONE | ABORT", "ret"),
    ("self", "O", "_resolve_plan() — zahodí halucinovaná ID, rozbalí cíl na kroky"),
    ("self", "O", "plan_state_conflict(plán, katalog, drží gripper něco?)"),
    ("msg", "O", "C", "při rozporu: PLAN_STATE_CORRECTION", "opt"),
    ("msg", "O", "V", "při [\"DONE\"]: GOAL_CHECK_RULES + snímky {vlm_model}", "opt"),
    ("msg", "V", "O", "REASONING + GOAL: yes | no", "opt"),

    ("loop", "FÁZE 2 — smyčka přes kroky plánu"),
    ("msg", "O", "D", "SET_POLICY:<cesta k modelu kroku>", ""),
    ("msg", "D", "O", "[STATUS] POLICY_LOADED: <cesta>", "ret"),
    ("msg", "O", "D", "SET_TASK:<krok>|grasp|reset|timeout=N", ""),
    ("msg", "D", "O", "[STATUS] TASK_STARTED: <krok>", "ret"),
    ("msg", "D", "R", "akce na fps Hz, dokud krok neskončí", ""),
    ("msg", "R", "D", "polohy kloubů, zátěž gripperu, snímky", "ret"),
    ("msg", "D", "F", "telemetry/<čas>.jsonl — jeden řádek na tik", ""),
    ("msg", "D", "O", "[STATUS] TASK_DONE: <krok> | Protokol A | B | časový limit", "ret"),
    ("msg", "O", "D", "SNAP", ""),
    ("msg", "D", "O", "[SNAPSHOT] <base64 JPEG>", "ret"),
    ("self", "O", "fyzický kanál z důvodu ukončení → CONFIRM / DENY / UNCLEAR / NONE"),
    ("msg", "O", "V", "VERIFY_PROMPT_RULES + snímky + fyzické důkazy {vlm_model}", ""),
    ("msg", "V", "O", "REASONING + GOAL: yes|no + značka (SUCCESS / [unclear] / …)", "ret"),
    ("msg", "O", "D", "při [unclear]: SNAP znovu a dotaz zopakovat jednou", "opt"),
    ("self", "O", "fuse_evidence() → success · outcome · rozpor"),
    ("msg", "O", "F", "images/<id>/a003_1.jpg — snímky, na kterých verdikt stojí", ""),
    ("self", "O", "uncertain → krok zopakovat bez volání CEO (reflex_retry_decision)"),
    ("endloop",),

    ("phase", "FÁZE 3 — re-plán po selhání (do vyčerpání max_replans)"),
    ("msg", "O", "C", "kontext selhání + paměť plánovače + snímek", ""),
    ("msg", "C", "O", "nový plán → zpět do smyčky kroků", "ret"),

    ("phase", "KONEC BĚHU"),
    ("msg", "O", "D", "QUIT", ""),
    ("msg", "D", "R", "disconnect (port a kamery)", ""),
    ("msg", "O", "F", "runs/<id>.json — kroky, fúze, audity, cesty ke snímkům", ""),
    ("msg", "O", "S", "emit(\"finished\", success, duration_s, …)", "ret"),
    ("msg", "S", "B", "SSE: finished → výsledek v prohlížeči", "ret"),
]

COL_W = 230
MARGIN_X = 30
HEAD_H = 44
ROW = 30
PHASE_H = 38
NOTE_H = 40
SELF_H = 34


def diagram_sequence(check_only: bool = False) -> str:
    idx = {p[0]: i for i, p in enumerate(PARTS)}
    width = MARGIN_X * 2 + COL_W * len(PARTS)

    def cx(pid: str) -> float:
        return MARGIN_X + COL_W * idx[pid] + COL_W / 2

    # Nejdřív spočítat výšku, ať se dá nakreslit čára životnosti přes celé schéma.
    y = HEAD_H + 54
    rows: list[tuple] = []
    warnings: list[str] = []
    for item in SEQ:
        kind = item[0]
        if kind == "phase":
            y += 10
            rows.append((y, item)); y += PHASE_H
        elif kind == "note":
            rows.append((y, item)); y += NOTE_H
        elif kind == "self":
            rows.append((y, item)); y += SELF_H
        elif kind == "loop":
            y += 8
            rows.append((y, item)); y += PHASE_H
        elif kind == "endloop":
            rows.append((y, item)); y += 20
        else:
            rows.append((y, item)); y += ROW
            _, src, dst, label, _style = item
            span = max(abs(idx[src] - idx[dst]), 1)
            need = text_width(label, 9.5)
            if need > span * COL_W - 16:
                warnings.append(f"popisek se nevejde ({need:.0f}px do {span * COL_W - 16:.0f}px): {label}")
    height = y + 30

    if check_only:
        for w in warnings:
            print("  ", w)
        print(f"{len(warnings)} popisků přesahuje svůj úsek; rozměr {width}x{height}")
        return ""

    body: list[str] = []
    body.append(text(MARGIN_X, 20, "Sekvence jednoho běhu orchestrace", 14, INK, weight="bold"))
    body.append(text(MARGIN_X, 34, "Plná šipka = příkaz/volání, čárkovaná = odpověď, "
                                   "šedá = větev, která proběhne jen za určité podmínky.",
                     9.5, MUTED))

    # Životnosti
    for pid, title, sub in PARTS:
        x = cx(pid)
        body.append(rect(x - COL_W / 2 + 8, 46, COL_W - 16, HEAD_H, fill=PANEL, stroke=INK))
        body.append(text(x, 64, title, 10.5, INK, anchor="middle", weight="bold"))
        body.append(text(x, 78, sub, 9, MUTED, anchor="middle", mono=True))
        body.append(line(x, 46 + HEAD_H, x, height - 20, stroke=LINE, dash="3 4"))

    for y0, item in rows:
        kind = item[0]
        if kind in ("phase", "loop"):
            label = item[1]
            body.append(rect(MARGIN_X, y0 - 14, width - MARGIN_X * 2, 24,
                             fill=BAND, stroke="none", rx=3))
            body.append(text(MARGIN_X + 10, y0 + 2, label, 10, INK, weight="bold"))
            if kind == "loop":
                body.append(text(width - MARGIN_X - 10, y0 + 2, "opakuje se", 9, MUTED,
                                 anchor="end"))
        elif kind == "endloop":
            body.append(line(MARGIN_X, y0 - 6, width - MARGIN_X, y0 - 6, stroke=LINE, dash="2 4"))
        elif kind == "note":
            body.append(rect(MARGIN_X + 30, y0 - 14, width - MARGIN_X * 2 - 60, 30,
                             fill="#fffaf0", stroke=LINE, rx=3))
            body.append(text(MARGIN_X + 42, y0 + 4, item[1], 9.5, MUTED))
        elif kind == "self":
            _, pid, label = item
            x = cx(pid)
            body.append(line(x, y0 - 6, x + 26, y0 - 6, stroke=INK))
            body.append(line(x + 26, y0 - 6, x + 26, y0 + 6, stroke=INK))
            body.append(line(x + 26, y0 + 6, x, y0 + 6, stroke=INK, marker="a"))
            body.append(text(x + 34, y0 + 9, label, 9.5, MUTED))
        else:
            _, src, dst, label, style = item
            x1, x2 = cx(src), cx(dst)
            stroke = MUTED if style == "opt" else INK
            dash = "5 3" if style in ("ret", "opt") else ""
            marker = "ao" if style == "ret" else "a"
            body.append(line(x1, y0, x2, y0, stroke=stroke, sw=1.2, dash=dash, marker=marker))
            mono = any(t in label for t in ("SET_", "SNAP", "QUIT", "[STATUS]", "[SNAPSHOT]",
                                            "POST", "REASONING", "GOAL:"))
            body.append(text((x1 + x2) / 2, y0 - 6, label, 9.5,
                             stroke if style != "opt" else MUTED,
                             anchor="middle", mono=mono))
    return svg(width, height, body)


# ── Schéma 3: vývojový diagram s drahami podle vrstev ───────────────────────
# Tohle je ta nejčitelnější podoba pro někoho, kdo nečte UML: klasické
# "co se stane, a co když ne", jen rozdělené do sloupců podle toho, KTERÁ
# VRSTVA to dělá. Sekvenční diagram říká totéž přesněji, ale hůř se čte.

FLOW_LANES = [
    ("l1", "Vrstva 1 — plánovač (CEO)", "pomalý, volá se jen na startu a při re-plánu"),
    ("orch", "Orchestrátor", "řídicí logika, fúze důkazů, rozpočty"),
    ("l2", "Vrstva 2 — policy kroku + čidla", "rychlá, běží po celou dobu kroku"),
    ("l3", "Vrstva 3 — inspektor (VLM)", "rychlý, volá se po každém kroku"),
]

# (id, dráha, řádek, druh, [řádky textu])
#   druh: start | end-ok | end-bad | proc | dec
FLOW_NODES = [
    ("start", "orch", 0, "start", ["Instrukce od uživatele"]),
    ("boot", "l2", 1, "proc", ["Start daemona,", "snímek výchozí scény"]),
    ("plan", "l1", 2, "proc", ["Naplánuj kroky", "z katalogu dovedností"]),
    ("state", "orch", 3, "dec", ["Odporuje plán čidlu zátěže?"]),
    ("fix", "l1", 4, "proc", ["Jedna cílená výzva", "k opravě plánu"]),
    ("ans", "orch", 5, "dec", ["Co plánovač vrátil?"]),
    ("goal", "l3", 6, "dec", ["Vidíš na snímku splněný cíl?"]),
    ("swap", "l2", 7, "proc", ["Přepni váhy na model", "tohoto kroku (hot-swap)"]),
    ("exec", "l2", 8, "proc", ["Spusť krok —", "policy řídí robota"]),
    ("term", "l2", 9, "dec", ["Čím krok skončil?"]),
    ("snap", "l2", 10, "proc", ["Pořiď snímek scény"]),
    ("phys", "orch", 11, "proc", ["Fyzický důkaz podle", "typu kroku a důvodu konce"]),
    ("insp", "l3", 12, "proc", ["Posuď krok ze snímku", "(+ dostaneš fyzický důkaz)"]),
    ("fuse", "orch", 13, "proc", ["Fúze obou důkazů"]),
    ("res", "orch", 14, "dec", ["Výsledek kroku?"]),
    ("budget", "l1", 15, "dec", ["Zbývá rozpočet re-plánů?"]),
    ("retry", "l2", 15, "proc", ["Zopakuj krok", "bez volání CEO"]),
    ("replan", "l1", 16, "proc", ["Nový plán z kontextu", "selhání + vlastní paměti"]),
    ("ok", "orch", 17, "end-ok", ["Konec — úspěch"]),
    ("bad", "l1", 17, "end-bad", ["Konec — neúspěch"]),
]

# (z, do, popisek, druh trasy, parametr)
#   "down"  svisle v téže dráze
#   "elbow" dolů, vodorovně do cílové dráhy, dolů do jejího horního okraje
#   "side"  bokem přes postranní kanál — pro zpětné skoky a dlouhé přeskoky;
#           parametr je (strana, číslo kanálu), aby se dvě takové trasy
#           nepřekryly
FLOW_EDGES = [
    ("start", "boot", "", "elbow", None),
    ("boot", "plan", "", "elbow", None),
    ("plan", "state", "", "elbow", None),
    ("state", "fix", "ano — rozpor", "elbow", None),
    ("fix", "ans", "plán se stejně spustí", "elbow", None),
    ("state", "ans", "ne", "down", None),
    ("ans", "goal", "[\"DONE\"]", "elbow", None),
    ("ans", "swap", "plán kroků", "elbow", None),
    ("ans", "bad", "[\"ABORT\"]", "side", ("left", 0)),
    ("goal", "ok", "ano (nebo plánovač na DONE trvá)", "side", ("right", 2)),
    ("goal", "state", "ne — plánovač DONE odvolá", "side", ("left", 2)),
    ("swap", "exec", "", "down", None),
    ("exec", "term", "", "down", None),
    ("term", "snap", "protokol A · protokol B · časový limit", "down", None),
    ("snap", "phys", "", "elbow", None),
    ("phys", "insp", "", "elbow", None),
    ("insp", "fuse", "", "elbow", None),
    ("fuse", "res", "", "down", None),
    ("res", "retry", "neprůkazné (poprvé)", "elbow", None),
    ("retry", "exec", "zpět na spuštění kroku", "side", ("right", 1)),
    ("res", "swap", "úspěch — zbývá krok", "side", ("right", 0)),
    ("res", "ok", "úspěch — plán dojel", "down", None),
    ("res", "budget", "selhání (i druhé neprůkazné v řadě)", "elbow", None),
    ("budget", "replan", "ano", "down", None),
    ("budget", "bad", "ne — vyčerpáno", "side", ("left", 1)),
    ("replan", "state", "nový plán projde týmiž kontrolami", "side", ("left", 3)),
]

LANE_W = 270
FLOW_ROW = 100
FLOW_NODE_H = 54
SIDE_W = 34          # rozteč postranních kanálů
FLOW_LEFT = 30 + 4 * SIDE_W
FLOW_TOP = 108


def diagram_flow() -> str:
    lane_i = {l[0]: i for i, l in enumerate(FLOW_LANES)}
    nodes = {n[0]: n for n in FLOW_NODES}
    max_row = max(n[2] for n in FLOW_NODES)
    width = FLOW_LEFT + LANE_W * len(FLOW_LANES) + 4 * SIDE_W + 30
    height = FLOW_TOP + (max_row + 1) * FLOW_ROW + 50
    node_w = LANE_W - 40

    def cx(node_id: str) -> float:
        return FLOW_LEFT + LANE_W * lane_i[nodes[node_id][1]] + LANE_W / 2

    def top(node_id: str) -> float:
        return FLOW_TOP + nodes[node_id][2] * FLOW_ROW

    def bottom(node_id: str) -> float:
        return top(node_id) + FLOW_NODE_H

    body: list[str] = []
    body.append(text(30, 22, "Vývojový diagram běhu — co se děje kdy a kdo to dělá",
                     14, INK, weight="bold"))
    body.append(text(30, 38, "Sloupce jsou vrstvy schématu. Obdélník = akce, "
                             "kosočtverec = rozhodnutí, zaoblený tvar = začátek/konec.",
                     9.5, MUTED))

    # Dráhy
    for i, (_id, title, sub) in enumerate(FLOW_LANES):
        x = FLOW_LEFT + LANE_W * i
        body.append(rect(x, 56, LANE_W, height - 76, fill="#fafbfc" if i % 2 == 0 else "#ffffff",
                         stroke=LINE, rx=4))
        body.append(rect(x, 56, LANE_W, 40, fill=PANEL, stroke=LINE, rx=4))
        body.append(text(x + LANE_W / 2, 73, title, 10.5, INK, anchor="middle", weight="bold"))
        body.append(text(x + LANE_W / 2, 87, sub, 8.5, MUTED, anchor="middle"))

    # Hrany se kreslí pod uzly, aby šipka nešla přes text
    for src, dst, label, kind, param in FLOW_EDGES:
        x1, x2 = cx(src), cx(dst)
        if kind == "down":
            y1, y2 = bottom(src), top(dst)
            body.append(line(x1, y1, x1, y2, stroke=INK, sw=1.2, marker="a"))
            if label:
                body.append(text(x1 + 8, (y1 + y2) / 2 + 3, label, 9, MUTED))
        elif kind == "elbow":
            y1, y2 = bottom(src), top(dst)
            y_mid = y2 - 22
            body.append(line(x1, y1, x1, y_mid, stroke=INK, sw=1.2))
            body.append(line(x1, y_mid, x2, y_mid, stroke=INK, sw=1.2))
            body.append(line(x2, y_mid, x2, y2, stroke=INK, sw=1.2, marker="a"))
            if label:
                anchor = "start" if x2 > x1 else "end"
                body.append(text(x1 + (8 if x2 > x1 else -8), y_mid - 6, label, 9, MUTED,
                                 anchor=anchor))
        else:  # side
            side, slot = param
            ya = top(src) + FLOW_NODE_H / 2
            yb = top(dst) + FLOW_NODE_H / 2
            if side == "left":
                xa = cx(src) - node_w / 2
                xb = cx(dst) - node_w / 2
                chan = FLOW_LEFT - 12 - slot * SIDE_W
            else:
                xa = cx(src) + node_w / 2
                xb = cx(dst) + node_w / 2
                chan = FLOW_LEFT + LANE_W * len(FLOW_LANES) + 12 + slot * SIDE_W
            body.append(line(xa, ya, chan, ya, stroke=MUTED, sw=1.2))
            body.append(line(chan, ya, chan, yb, stroke=MUTED, sw=1.2))
            body.append(line(chan, yb, xb, yb, stroke=MUTED, sw=1.2, marker="a"))
            if label:
                anchor = "end" if side == "left" else "start"
                off = -8 if side == "left" else 8
                body.append(text(xa + off, ya - 8, label, 9, MUTED, anchor=anchor))

    # Uzly
    for nid, lane, row, kind, lines_ in FLOW_NODES:
        x, y = cx(nid) - node_w / 2, top(nid)
        cxx, cyy = cx(nid), y + FLOW_NODE_H / 2
        if kind == "dec":
            pts = f"{cxx:.1f},{y:.1f} {x + node_w:.1f},{cyy:.1f} {cxx:.1f},{y + FLOW_NODE_H:.1f} {x:.1f},{cyy:.1f}"
            body.append(f'<polygon points="{pts}" fill="#fdf6e3" stroke="{INK}" stroke-width="1.2"/>')
        elif kind in ("start", "end-ok", "end-bad"):
            fill = {"start": PANEL, "end-ok": "#eaf5ec", "end-bad": "#fbecec"}[kind]
            body.append(rect(x, y, node_w, FLOW_NODE_H, fill=fill, stroke=INK,
                             rx=FLOW_NODE_H / 2, sw=1.2))
        else:
            body.append(rect(x, y, node_w, FLOW_NODE_H, fill="#ffffff", stroke=INK, rx=4, sw=1.2))
        first = cyy + 4 - (len(lines_) - 1) * 7
        for i, ln in enumerate(lines_):
            body.append(text(cxx, first + i * 14, ln, 10, INK, anchor="middle",
                             weight="bold" if kind in ("start", "end-ok", "end-bad") else "normal"))

    return svg(width, height, body)


def main() -> int:
    ap = argparse.ArgumentParser(description="Vygeneruje SVG schémata orchestrace")
    ap.add_argument("--check", action="store_true",
                    help="jen ohlásí popisky, které se nevejdou mezi životnosti")
    args = ap.parse_args()

    if args.check:
        diagram_sequence(check_only=True)
        return 0

    WEB.mkdir(exist_ok=True)
    for name, content in (("schema-komponenty.svg", diagram_components()),
                          ("schema-vyvojovy.svg", diagram_flow()),
                          ("schema-sekvence.svg", diagram_sequence())):
        path = WEB / name
        path.write_text(content, encoding="utf-8")
        print(f"zapsáno {path} ({len(content)} B)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
