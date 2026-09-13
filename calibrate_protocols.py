#!/usr/bin/env python3
"""
Porovná NASTAVENÉ prahy ukončovacích protokolů A/B s tím, co je doopravdy
vidět v telemetrii z proběhlých běhů.

Proč to existuje: prahy v `config.json` (protocol_a_threshold_rad,
protocol_b_limit_ma, holding_limit_ma, protocol_b_stability_slope) jsou dnes
ručně nastavená čísla, která se v tomhle projektu opakovaně přelaďovala podle
toho, co se zrovna pokazilo. Přitom `inference_daemon.py` už do
`telemetry/*.jsonl` zapisuje přesně ty veličiny, proti kterým se ty prahy
porovnávají — jen je nikdo nečetl zpátky. Tenhle skript je čte.

CO TO DĚLÁ A CO NE
------------------
Skript **nic nezapisuje** — ani do `config.json`, ani do projektu. Jediné, co
vrací, je tabulka „naměřeno vs. nastaveno" a verdikt, jestli nastavená hodnota
leží v pásmu, ve kterém dává fyzikálně smysl. Aplikovat cokoliv je pořád ruční
rozhodnutí uživatele — prahy se nesmí měnit uprostřed měřené série, aniž by o
tom experimentátor věděl.

JAK SE ODVOZUJE NÁVRH
---------------------
U prahů, které mají oddělit dva režimy téže veličiny (klid vs. pohyb, plató
vs. stoupání, prázdné čelisti vs. držení), se z dat vezme spodní a horní okraj
mezery mezi režimy a návrh je jejich **geometrický průměr** — tedy střed té
mezery na logaritmické škále. Není to odhadnutá konstanta: mezera je ta, co
se naměřila, a střed je jediné místo, které je stejně daleko od obou chyb
(práh tak nízko, že protokol nikdy nespustí × tak vysoko, že spustí uprostřed
pohybu).

Co skript **neumí**: oddělit „proud vyskočil, protože čelisti něco sevřely" od
„proud vyskočil, protože se čelisti zavíraly naprázdno". Tyhle dvě rozdělení
se u tohohle hardwaru prokazatelně překrývají (viz komentáře u
PROTOCOL_B_GRACE_S v inference_daemon.py) a z telemetrie samotné je rozlišit
nejde — chybí k tomu nezávislý štítek „tenhle úchop se povedl", který zná až
inspektor. Proto se u `protocol_b_limit_ma` jen vypíše naměřené rozdělení a
verdikt, ale nenavrhuje se hodnota. Jakmile budou v `runs/*.json` časy kroků
(`t_start` / `t_end`), půjde běhy s telemetrií spárovat a tohle doplnit.

POUŽITÍ
-------
    python calibrate_protocols.py                 # tabulka do konzole
    python calibrate_protocols.py --json          # strojově čitelný výstup
    python calibrate_protocols.py --min-runs 5    # přísnější práh na počet běhů

Nepotřebuje LeRobota ani robota — jen soubory v `telemetry/`.
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONFIG_FILE = HERE / "config.json"
TELEMETRY_DIR = HERE / "telemetry"

# Nejmenší počet běhů, při kterém je medián doopravdy prostřední pozorování a
# ne jen průměr dvou krajních. Není to doporučená velikost vzorku — je to
# spodní hranice, pod kterou nemá smysl počítat vůbec nic. Reálně si ji
# uživatel nastaví výš (config.json → calibration_min_runs).
DEFAULT_MIN_RUNS = 3


# ── Statistika (stdlib, žádné numpy — tohle musí jít pustit i bez LeRobota) ──

def percentile(values: list[float], q: float) -> float | None:
    """Percentil metodou nearest-rank. q je 0..1."""
    if not values:
        return None
    ordered = sorted(values)
    idx = int(round(q * (len(ordered) - 1)))
    return ordered[max(0, min(len(ordered) - 1, idx))]


def summarize(values: list[float]) -> dict:
    return {
        "n": len(values),
        "p05": percentile(values, 0.05),
        "p50": percentile(values, 0.50),
        "p95": percentile(values, 0.95),
        "max": max(values) if values else None,
    }


def gap_midpoint(low: float | None, high: float | None) -> float | None:
    """Střed mezery mezi dvěma režimy na logaritmické škále.

    Aritmetický průměr by u veličin, jejichž dva režimy se liší o řád i víc
    (klidový šum vs. skutečný pohyb), skončil těsně pod horním okrajem —
    tedy prakticky na hranici pohybu. Geometrický průměr je stejně daleko od
    obou okrajů v poměru, což je způsob, jakým se ty dva režimy liší.
    """
    if low is None or high is None:
        return None
    if low <= 0 or high <= low:
        return None
    return math.sqrt(low * high)


def band_verdict(configured: float | None, low: float | None, high: float | None) -> str:
    """Leží nastavená hodnota v mezeře mezi dvěma naměřenými režimy?

    'too_low'  — protokol prakticky nikdy nespustí (hodnota je pod šumem)
    'too_high' — protokol spustí i uprostřed běžného pohybu

    Slitá mezera (horní okraj není nad dolním) žádný verdikt nedává: znamená
    to, že se oba režimy v datech vůbec nerozlišily, ne že je nastavená
    hodnota špatně. Vydat na to „příliš nízko" by uživatele poslalo ladit
    práh podle vzorku, který o něm nic neříká.
    """
    if configured is None or low is None or high is None or high <= low:
        return "unknown"
    if configured <= low:
        return "too_low"
    if configured >= high:
        return "too_high"
    return "ok"


# ── Čtení telemetrie ────────────────────────────────────────────────────────

def read_telemetry(directory: Path) -> list[list[dict]]:
    """Načte telemetrii po souborech. Jeden soubor = jeden daemon = jeden běh.

    Poškozené řádky se přeskakují: telemetrie se zapisuje průběžně a soubor z
    běhu, který skončil killem, má běžně useknutý poslední řádek. Zahodit
    kvůli tomu celý běh by bylo horší než přijít o jeden tik.
    """
    if not directory.exists():
        return []
    files: list[list[dict]] = []
    for path in sorted(directory.glob("*.jsonl")):
        rows: list[dict] = []
        try:
            with path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(row, dict):
                        rows.append(row)
        except OSError:
            continue
        if rows:
            files.append(rows)
    return files


def _new_step() -> dict:
    return {"runs": 0, "attempts": 0, "ticks": 0,
            "is_grasp": False, "is_reset": False,
            "joint_velocity": [], "target_error": [], "rise": [], "slope_abs": [],
            "rise_at_end": [], "end_reasons": {}}


def _reason_key(reason: str) -> str:
    """Zkrátí důvod ukončení na jeho typ ('Protokol A (klouby...' -> 'Protokol A')."""
    reason = (reason or "").strip()
    for known in ("Protokol A", "Protokol B", "Časový limit"):
        if reason.startswith(known):
            return known
    return reason.split("(")[0].strip() or "?"


def collect(files: list[list[dict]], grace_s: float = 0.0) -> tuple[dict, dict]:
    """Projde telemetrii a posbírá vzorky po krocích + vzorky ke stavu gripperu.

    Vrací (steps, holding), kde `holding` drží nárůst zátěže ve dvou situacích,
    o kterých se z pořadí událostí ví, co znamenají: než se v běhu vůbec
    poprvé něco spustilo (čelisti prázdné) a hned po kroku ukončeném
    protokolem B (čelisti něco drží). Tohle je jediné místo, kde jde
    „drží / nedrží" oštítkovat bez inspektora — proto se to sbírá zvlášť.
    """
    steps: dict[str, dict] = {}
    holding: dict[str, list[float]] = {"empty": [], "held": []}

    for rows in files:
        seen: set[str] = set()
        started_at: dict[str, float] = {}
        any_started = False
        just_grasped = False

        for row in rows:
            event = row.get("event")

            if event == "task_started":
                task = str(row.get("task") or "")
                if not task:
                    continue
                any_started = True
                just_grasped = False
                started_at[task] = float(row.get("t") or 0.0)
                acc = steps.setdefault(task, _new_step())
                acc["attempts"] += 1
                acc["is_grasp"] = acc["is_grasp"] or bool(row.get("is_grasp"))
                acc["is_reset"] = acc["is_reset"] or bool(row.get("is_reset"))
                seen.add(task)

            elif event == "task_done":
                task = str(row.get("task") or "")
                if not task:
                    continue
                acc = steps.setdefault(task, _new_step())
                key = _reason_key(str(row.get("reason") or ""))
                acc["end_reasons"][key] = acc["end_reasons"].get(key, 0) + 1
                rise = row.get("rise")
                if rise is not None:
                    acc["rise_at_end"].append(float(rise))
                if key == "Protokol B":
                    just_grasped = True
                seen.add(task)

            elif event == "tick":
                rise = row.get("rise")
                if row.get("state") != "RUNNING":
                    # Klidové tiky mezi kroky: zátěž tu neruší žádný pohyb,
                    # takže je to nejčistší vzorek "co čte čidlo, když se nic
                    # neděje" — a podle pozice v běhu se ví, jestli je v
                    # čelistech předmět.
                    if rise is not None:
                        if just_grasped:
                            holding["held"].append(float(rise))
                        elif not any_started:
                            holding["empty"].append(float(rise))
                    continue

                task = str(row.get("task") or "")
                if not task:
                    continue
                acc = steps.setdefault(task, _new_step())
                acc["ticks"] += 1
                seen.add(task)

                jv = row.get("joint_velocity")
                if jv is not None:
                    acc["joint_velocity"].append(float(jv))
                te = row.get("target_error")
                if te is not None:
                    acc["target_error"].append(float(te))
                slope = row.get("slope")
                if slope is not None:
                    acc["slope_abs"].append(abs(float(slope)))
                if rise is not None:
                    # Ochranná doba protokolu B se uplatní i tady: prvních pár
                    # desetin sekundy po startu kroku je proud gripperu daný
                    # přejezdem do výchozí polohy trajektorie, ne úlohou.
                    elapsed = float(row.get("t") or 0.0) - started_at.get(task, float(row.get("t") or 0.0))
                    if elapsed >= grace_s:
                        acc["rise"].append(float(rise))

        for task in seen:
            steps[task]["runs"] += 1

    return steps, holding


# ── Porovnání naměřeného s nastaveným ───────────────────────────────────────

def _check(key: str, label: str, scope: str, configured, low, high,
           measured: dict, runs: int, min_runs: int, suggest: bool = True,
           note: str = "") -> dict:
    enough = (runs >= min_runs and measured.get("n", 0) > 0
              and low is not None and high is not None and high > low)
    suggestion = gap_midpoint(low, high) if (suggest and enough) else None
    if suggestion is not None:
        suggestion = round(suggestion, 3 if abs(suggestion) < 10 else 0)
    return {
        "key": key,
        "label": label,
        "scope": scope,
        "configured": configured,
        "measured": measured,
        "band_low": low,
        "band_high": high,
        "suggested": suggestion,
        "verdict": band_verdict(configured, low, high) if enough else "insufficient_data",
        "runs": runs,
        "min_runs": min_runs,
        "note": note,
    }


def analyze(files: list[list[dict]], cfg: dict | None = None,
            min_runs: int = DEFAULT_MIN_RUNS) -> dict:
    """Hlavní vstupní bod — vrací report, který si bere i server pro UI."""
    cfg = cfg or {}
    grace = float(cfg.get("protocol_b_grace_s", 0) or 0)
    steps, holding = collect(files, grace_s=grace)

    checks: list[dict] = []
    per_step: list[dict] = []

    for slug in sorted(steps):
        acc = steps[slug]
        jv = [v for v in acc["joint_velocity"] if v > 0]
        jv_stats = summarize(acc["joint_velocity"])
        entry = {
            "step": slug,
            "runs": acc["runs"],
            "attempts": acc["attempts"],
            "ticks": acc["ticks"],
            "is_grasp": acc["is_grasp"],
            "is_reset": acc["is_reset"],
            "joint_velocity": jv_stats,
            "target_error": summarize(acc["target_error"]),
            "rise": summarize(acc["rise"]),
            "slope_abs": summarize(acc["slope_abs"]),
            "rise_at_end": summarize(acc["rise_at_end"]),
            "end_reasons": acc["end_reasons"],
        }
        per_step.append(entry)

        # Protokol A měří dvě různé veličiny pod jedním telemetrickým polem
        # (joint_velocity) podle typu kroku — viz PROTOCOL_A_TARGET_THRESHOLD
        # v inference_daemon.py. |grasp/|reset: skutečná rychlost mezi tiky,
        # práh musí ležet nad klidovým šumem a pod skutečným pohybem. Obyčejné
        # kroky: vzdálenost od aktuálně predikovaného cíle, jiné měřítko —
        # srovnávat proti sdílenému prahu by dávalo nesmyslný verdikt.
        if acc["is_grasp"] or acc["is_reset"]:
            checks.append(_check(
                "protocol_a_threshold_rad", "Práh pohybu mezi snímky (rychlost)", slug,
                cfg.get("protocol_a_threshold_rad"),
                percentile(jv, 0.05), percentile(acc["joint_velocity"], 0.95),
                jv_stats, acc["runs"], min_runs,
                note="Dolní okraj je klidový šum čidla, horní skutečný pohyb ramene."))
        else:
            checks.append(_check(
                "protocol_a_target_threshold_rad", "Práh vzdálenosti od cíle", slug,
                cfg.get("protocol_a_target_threshold_rad"),
                percentile(jv, 0.05), percentile(acc["joint_velocity"], 0.95),
                jv_stats, acc["runs"], min_runs,
                note="Dolní okraj je klidový šum, horní vzdálenost od cíle během pohybu — "
                     "jiná veličina než rychlost u |grasp/|reset kroků, viz "
                     "PROTOCOL_A_TARGET_THRESHOLD v inference_daemon.py."))

        if acc["is_grasp"]:
            # Protokol B, mez ustálení: plató po sevření vs. stoupající proud.
            checks.append(_check(
                "protocol_b_stability_slope", "Max. sklon pro „ustáleno\"", slug,
                cfg.get("protocol_b_stability_slope"),
                percentile(acc["slope_abs"], 0.05), percentile(acc["slope_abs"], 0.95),
                summarize(acc["slope_abs"]), acc["runs"], min_runs,
                note="Dolní okraj je plató, horní prudce stoupající proud."))

            # Protokol B, limit nárůstu: jen popis, bez návrhu — viz docstring.
            checks.append(_check(
                "protocol_b_limit_ma", "Nárůst zátěže — ukončení kroku", slug,
                cfg.get("protocol_b_limit_ma"),
                percentile(acc["rise"], 0.50), summarize(acc["rise"]).get("max"),
                summarize(acc["rise"]), acc["runs"], min_runs, suggest=False,
                note="Bez nezávislého štítku „úchop se povedl\" nejde oddělit sevření "
                     "od průjezdu proudu při zavírání naprázdno — proto jen popis."))

    total_runs = len(files)
    checks.append(_check(
        "holding_limit_ma", "Práh „něco drží\"", "(celá úloha)",
        cfg.get("holding_limit_ma"),
        percentile(holding["empty"], 0.95), percentile(holding["held"], 0.50),
        summarize(holding["held"]), total_runs, min_runs,
        note="Dolní okraj je zátěž v klidu s prázdnými čelistmi, horní zátěž v klidu "
             "hned po kroku ukončeném protokolem B."))

    return {
        "runs_total": total_runs,
        "min_runs": min_runs,
        "steps": per_step,
        "checks": checks,
        "holding": {"empty": summarize(holding["empty"]), "held": summarize(holding["held"])},
    }


def load_config() -> dict:
    if not CONFIG_FILE.exists():
        return {}
    try:
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}


# ── Výstup do konzole ───────────────────────────────────────────────────────

VERDICT_TEXT = {
    "ok": "OK — leží v naměřené mezeře",
    "too_low": "PŘÍLIŠ NÍZKO — protokol skoro nikdy nespustí",
    "too_high": "PŘÍLIŠ VYSOKO — protokol spustí i za pohybu",
    "insufficient_data": "málo dat",
    "unknown": "nelze posoudit",
}


def _num(value, digits: int = 2) -> str:
    if value is None:
        return "—"
    return f"{value:.{digits}f}"


def format_report(report: dict) -> str:
    out: list[str] = []
    out.append(f"Telemetrie: {report['runs_total']} běhů, "
               f"minimum pro výpočet: {report['min_runs']}")
    out.append("")

    if not report["steps"]:
        out.append("V telemetry/ nejsou žádná použitelná data. "
                   "Pusť aspoň jeden běh orchestrace a zkus to znovu.")
        return "\n".join(out)

    out.append("KROKY")
    out.append(f"{'krok':<16}{'běhů':>6}{'pokusů':>8}{'tiků':>7}  ukončení")
    for s in report["steps"]:
        reasons = ", ".join(f"{k} {v}x" for k, v in sorted(s["end_reasons"].items())) or "—"
        out.append(f"{s['step']:<16}{s['runs']:>6}{s['attempts']:>8}{s['ticks']:>7}  {reasons}")
    out.append("")

    out.append("NAMĚŘENO vs. NASTAVENO")
    header = f"{'krok':<16}{'veličina':<32}{'nastaveno':>10}{'mezera':>18}{'návrh':>9}  verdikt"
    out.append(header)
    out.append("-" * len(header))
    for c in report["checks"]:
        band = f"{_num(c['band_low'])} – {_num(c['band_high'])}"
        out.append(
            f"{c['scope']:<16}{c['label']:<32}{_num(c['configured']):>10}{band:>18}"
            f"{_num(c['suggested']):>9}  {VERDICT_TEXT.get(c['verdict'], c['verdict'])}")
    out.append("")
    out.append("Skript nic nezapisuje — hodnoty přepiš v Nastavení ručně, až se rozhodneš.")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Porovná nastavené prahy protokolů A/B s naměřenou telemetrií")
    ap.add_argument("--telemetry", default="", help=f"adresář s *.jsonl (výchozí {TELEMETRY_DIR})")
    ap.add_argument("--min-runs", type=int, default=0,
                    help="kolik běhů musí krok mít, než se jeho výpočet považuje za platný "
                         f"(výchozí: config.json → calibration_min_runs, jinak {DEFAULT_MIN_RUNS})")
    ap.add_argument("--json", action="store_true", help="strojově čitelný výstup")
    args = ap.parse_args()

    cfg = load_config()
    min_runs = args.min_runs or int(cfg.get("calibration_min_runs", DEFAULT_MIN_RUNS) or DEFAULT_MIN_RUNS)
    directory = Path(args.telemetry) if args.telemetry else TELEMETRY_DIR

    report = analyze(read_telemetry(directory), cfg, min_runs)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(format_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
