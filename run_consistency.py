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
Většina z nich je navíc označená jako rozhodná pro měřené schéma: změna
takového klíče uprostřed série znamená, že se běhy před ní a po ní nesmí
sčítat do jednoho čísla.

Rozhodné je **všechno kromě** vyjmenovaných výjimek (cesty, věci kolem
nahrávání a trénování, nastavení kalibrační tabulky) — viz NON_DECISIVE_KEYS,
kde je i vysvětlené, proč zrovna takhle a ne obráceně.

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

# Rozhodné je VŠECHNO KROMĚ těchhle klíčů — tedy obráceně, než by člověk čekal.
#
# Seznam rozhodných klíčů by byl kratší a čitelnější, ale má fatální vadu:
# musel by se ručně doplňovat pokaždé, když do schématu přibude přepínač. Ten,
# kdo ho zapomene doplnit, nedostane chybu — dostane tiché „STABILNÍ" o sérii,
# která stabilní nebyla. To je nejhorší možná chyba, jakou tenhle skript může
# udělat, protože se projeví až jako neplatné číslo v diplomce.
#
# Obrácený seznam selhává na bezpečnou stranu: nový klíč je rozhodný, dokud ho
# někdo vědomě neprohlásí za nepodstatný. Nejhorší následek je řádek navíc ve
# výpisu. (Reálný důkaz, že to není teoretická obava: `planner_memory` přibyl
# pár hodin po napsání tohohle skriptu a whitelistu by propadl. Totéž
# `policy_path` u kroku — tedy KTERÝ checkpoint se spustí.)
NON_DECISIVE_KEYS = {
    # prostředí a cesty — na chování měřené smyčky nemají vliv
    "python", "output_root",
    # nahrávání demonstrací a trénink: proběhlo dávno před během
    "data_strategy", "episodes", "resume_episodes", "reset_time_s",
    "policy_type", "train_steps", "batch_size", "save_freq",
    "teleop_type", "teleop_port", "teleop_id",
    "baseline_datasets",
    # ovlivňuje jen kalibrační tabulku v Nastavení, ne jediný řádek toho,
    # co dělá robot
    "calibration_min_runs",
}

# Totéž pro pole kroků v `catalog`. `slug` je identita kroku (je v názvu
# klíče), `train_steps` je záležitost tréninku.
NON_DECISIVE_STEP_FIELDS = {"slug", "train_steps"}


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
        flat.update(cfg)
    catalog = payload.get("catalog")
    if isinstance(catalog, list):
        for step in catalog:
            if not isinstance(step, dict):
                continue
            slug = step.get("slug")
            if not slug:
                continue
            for field, value in step.items():
                if field != "slug":
                    flat[f"krok[{slug}].{field}"] = value
    return flat


def _is_decisive(key: str) -> bool:
    if key.startswith("krok["):
        return key.rsplit(".", 1)[-1] not in NON_DECISIVE_STEP_FIELDS
    return key not in NON_DECISIVE_KEYS


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
