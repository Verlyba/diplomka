#!/usr/bin/env python3
"""
Zkontroluje, jestli se nastavení NEMĚNILO mezi jednotlivými běhy orchestrace.

Proč to existuje: v diplomce se porovnává baseline s orchestračním schématem.
Aby to srovnání něco znamenalo, musí všechny běhy orchestrační větve proběhnout
se **stejným nastavením** — jinak se neporovnávají dvě schémata, ale hromada
různě nastavených variant jednoho z nich. „Nastavení jsem neměnil" je ale
tvrzení o minulosti, které si nikdo nepamatuje přesně. Naštěstí ho není třeba
pamatovat: `Orchestrator._save_run()` zapisuje do každého `runs/*.json` celou
konfiguraci, se kterou ten běh proběhl, i katalog kroků. Tenhle skript je
přečte zpátky a řekne, co se mezi běhy lišilo.

Je to tedy táž disciplína, jakou drží zbytek projektu: o skutečnosti smí
mluvit jen záznam, ne vzpomínka.

CO JE „ROZHODNÉ"
----------------
Vypíšou se **všechny** klíče, které se mezi běhy liší — nic se neschovává.
Část z nich je navíc označená jako rozhodná pro měřené schéma, a to podle
jednoho pravidla: je to klíč, který se dostane buď (a) na příkazovou řádku
inferenčního daemona, (b) do řídicí smyčky orchestrátoru, nebo (c) do promptu
některého z modelů. Změna takového klíče uprostřed série znamená, že se běhy
před ní a po ní nesmí sčítat do jednoho čísla.

Klíče mimo tenhle seznam (cesty, port robota, věci kolem trénování) se taky
vypíšou, ale jako vedlejší — na chování měřené smyčky nemají vliv.

POUŽITÍ
-------
    python run_consistency.py                  # jen běhy aktuálního projektu
    python run_consistency.py --all            # napříč všemi projekty
    python run_consistency.py --last 20        # jen posledních 20 běhů
    python run_consistency.py --json

Nepotřebuje LeRobota, robota ani telemetrii — jen `runs/*.json`.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
RUNS_DIR = HERE / "runs"

# (a) klíče, které jdou na příkazovou řádku inferenčního daemona
#     (viz Daemon.start() a cameras_json() v orchestrator.py)
_DAEMON_KEYS = {
    "robot_type", "robot_id", "robot_port", "device", "fps",
    "protocol_a_enabled", "protocol_a_threshold_rad", "protocol_a_patience",
    "protocol_a_grasp_patience_extra",
    "protocol_b_enabled", "protocol_b_limit_ma", "protocol_b_patience",
    "protocol_b_grace_s", "protocol_b_stability_slope",
    "camera_name", "camera_index", "camera_width", "camera_height", "camera_fps",
    "camera2_name", "camera2_index", "camera2_width", "camera2_height", "camera2_fps",
}
# (b) klíče, podle kterých se větví řídicí smyčka orchestrátoru
_LOOP_KEYS = {
    "max_replans", "episode_time_s",
    "plan_state_check", "done_visual_check", "uncertain_retry",
    "skip_planner", "skip_inspector",
    "planner_vision", "planner_reasoning", "gripper_state_in_context",
    "protocol_b_deadband_frac", "holding_limit_ma",
}
# (c) klíče, které se dostanou do promptu některého z modelů
_PROMPT_KEYS = {
    "llm_model", "vlm_model", "llm_timeout_s", "lm_url",
    "task_slug", "task_description", "scene_description",
}
DECISIVE_KEYS = _DAEMON_KEYS | _LOOP_KEYS | _PROMPT_KEYS

# Vlastnosti kroků z `catalog`, které mají na měřenou smyčku stejný vliv jako
# konfigurační klíče (timeout_s ukončuje krok, grasp/reset volí protokol,
# description/verify_hint jdou do promptů).
DECISIVE_STEP_FIELDS = ("timeout_s", "grasp", "reset", "description", "verify_hint")

# Klíče, které se mění samy od sebe a o nastavení nic neříkají — nemá smysl
# je hlásit jako rozdíl. `calibration_min_runs` sem patří proto, že ovlivňuje
# jen kalibrační tabulku v Nastavení, ne jediný řádek toho, co dělá robot.
IGNORED_KEYS = {"calibration_min_runs"}


def load_runs(directory: Path) -> list[dict]:
    """Načte runs/*.json (nejnovější první). Poškozený soubor se přeskočí."""
    if not directory.exists():
        return []
    runs: list[dict] = []
    for path in sorted(directory.glob("*.json"), reverse=True):
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        runs.append({"name": path.name, "payload": payload})
    return runs


def take_last(runs: list[dict], last: int) -> list[dict]:
    """Posledních N běhů. Záměrně se volá až PO filtru podle projektu:
    „posledních 5 běhů téhle série" jinak vrátí méně, kdykoli se mezi ně
    vklíní běh jiného projektu."""
    return runs[:last] if last > 0 else runs


def settings_of(run: dict) -> dict:
    """Zploští konfiguraci běhu + katalog kroků na jeden slovník klíč -> hodnota.

    Kroky se zploští na `krok[slug].pole`, aby se per-step timeout_s porovnával
    stejně samozřejmě jako globální práh — je to hodnota, která rozhoduje o
    tom, kdy se krok usekne, takže její změna mezi běhy je úplně stejně
    zásadní.
    """
    payload = run["payload"]
    cfg = payload.get("config")
    flat: dict = {}
    if isinstance(cfg, dict):
        for key, value in cfg.items():
            if key not in IGNORED_KEYS:
                flat[key] = value
    catalog = payload.get("catalog")
    if isinstance(catalog, list):
        for step in catalog:
            if not isinstance(step, dict):
                continue
            slug = step.get("slug")
            if not slug:
                continue
            for field in DECISIVE_STEP_FIELDS:
                if field in step:
                    flat[f"krok[{slug}].{field}"] = step[field]
    return flat


def _is_decisive(key: str) -> bool:
    if key.startswith("krok["):
        return key.rsplit(".", 1)[-1] in DECISIVE_STEP_FIELDS
    return key in DECISIVE_KEYS


def _hashable(value):
    """Stabilní zástupce hodnoty pro porovnání (seznamy/slovníky nejsou hashable)."""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return value


def compare(runs: list[dict]) -> dict:
    """Porovná nastavení napříč běhy a vrátí, co se lišilo."""
    with_settings = [r for r in runs if isinstance(r["payload"].get("config"), dict)]
    without = [r["name"] for r in runs if not isinstance(r["payload"].get("config"), dict)]

    flats = {r["name"]: settings_of(r) for r in with_settings}
    all_keys: set[str] = set()
    for flat in flats.values():
        all_keys.update(flat)

    _MISSING = object()
    differences: list[dict] = []
    for key in sorted(all_keys):
        groups: dict = {}
        for name, flat in flats.items():
            value = flat.get(key, _MISSING)
            marker = "(klíč v záznamu chybí)" if value is _MISSING else _hashable(value)
            entry = groups.setdefault(marker, {"value": None if value is _MISSING else value,
                                               "missing": value is _MISSING, "runs": []})
            entry["runs"].append(name)
        if len(groups) <= 1:
            continue
        differences.append({
            "key": key,
            "decisive": _is_decisive(key),
            "values": sorted(groups.values(), key=lambda g: -len(g["runs"])),
        })

    differences.sort(key=lambda d: (not d["decisive"], d["key"]))
    decisive = [d for d in differences if d["decisive"]]
    return {
        "runs": [{"name": r["name"],
                  "success": r["payload"].get("success"),
                  "task_slug": (r["payload"].get("config") or {}).get("task_slug")}
                 for r in with_settings],
        "n": len(with_settings),
        "runs_without_settings": without,
        "differences": differences,
        "decisive_differences": len(decisive),
        # Stabilní = ani jeden rozhodný klíč se mezi běhy nezměnil. Běhy bez
        # zaznamenané konfigurace stabilitu nepotvrzují ani nevyvracejí, jen
        # se o nich nic neví — proto jsou vypsané zvlášť.
        "stable": len(decisive) == 0 and len(with_settings) > 0,
    }


def filter_by_task(runs: list[dict], task_slug: str) -> list[dict]:
    if not task_slug:
        return runs
    return [r for r in runs
            if (r["payload"].get("config") or {}).get("task_slug") == task_slug]


def load_config() -> dict:
    path = HERE / "config.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}


def format_report(report: dict) -> str:
    out: list[str] = []
    out.append(f"Porovnáno {report['n']} běhů se zaznamenanou konfigurací.")
    if report["runs_without_settings"]:
        out.append(f"Bez zaznamenané konfigurace (starší formát): "
                   f"{len(report['runs_without_settings'])} — o těch se nedá říct nic.")
    out.append("")

    if not report["n"]:
        out.append("V runs/ nejsou žádné použitelné záznamy.")
        return "\n".join(out)

    if report["stable"]:
        out.append("STABILNÍ — žádný klíč rozhodný pro měřené schéma se mezi běhy nezměnil.")
    else:
        out.append(f"POZOR — rozhodných rozdílů: {report['decisive_differences']}. "
                   "Běhy před změnou a po ní se nesmí sčítat do jednoho čísla.")
    out.append("")

    for diff in report["differences"]:
        mark = "!" if diff["decisive"] else " "
        out.append(f"{mark} {diff['key']}")
        for group in diff["values"]:
            value = "(klíč v záznamu chybí)" if group["missing"] else json.dumps(
                group["value"], ensure_ascii=False)
            runs = ", ".join(group["runs"][:4])
            more = f" (+{len(group['runs']) - 4} dalších)" if len(group["runs"]) > 4 else ""
            out.append(f"      {value}  —  {len(group['runs'])}x: {runs}{more}")
    if not report["differences"]:
        out.append("Žádný klíč se mezi běhy nelišil.")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Zkontroluje, jestli všechny běhy orchestrace proběhly se stejným nastavením")
    ap.add_argument("--runs", default="", help=f"adresář se záznamy běhů (výchozí {RUNS_DIR})")
    ap.add_argument("--all", action="store_true",
                    help="porovnat napříč všemi projekty (výchozí: jen aktuální task_slug)")
    ap.add_argument("--last", type=int, default=0, help="jen posledních N běhů (0 = všechny)")
    ap.add_argument("--json", action="store_true", help="strojově čitelný výstup")
    args = ap.parse_args()

    directory = Path(args.runs) if args.runs else RUNS_DIR
    runs = load_runs(directory)
    if not args.all:
        runs = filter_by_task(runs, load_config().get("task_slug", ""))
    runs = take_last(runs, args.last)

    report = compare(runs)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(format_report(report))
    # Nenulový návratový kód při nestabilitě, ať se to dá zapojit do skriptu.
    return 0 if report["stable"] or not report["n"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
