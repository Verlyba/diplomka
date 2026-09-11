"""Kontrola stability nastaveni mezi behy (run_consistency.py).

Nepotrebuje robota ani LeRobota — zaznamy behu se vyrobi synteticky.

    python tests/test_run_consistency.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from run_consistency import (NON_DECISIVE_KEYS, compare, filter_by_task,
                             settings_of, take_last)

failures = []


def check(name, got, want):
    ok = got == want
    print(f"{'ok  ' if ok else 'CHYBA'} {name}: {got!r}" + ("" if ok else f"  (cekano {want!r})"))
    if not ok:
        failures.append((name, got, want))


BASE_CFG = {
    "task_slug": "pick_and_place",
    "protocol_b_limit_ma": 250,
    "max_replans": 5,
    "uncertain_retry": True,
    "python": "/home/user/.venv/bin/python",
    "calibration_min_runs": 3,
}
CATALOG = [
    {"slug": "approach", "description": "najezd", "timeout_s": 6.0},
    {"slug": "grab", "description": "uchop", "grasp": True, "timeout_s": 8.0},
]


def run(name, cfg_overrides=None, catalog=None, success=True, with_config=True):
    payload = {"success": success, "steps": []}
    if with_config:
        payload["config"] = {**BASE_CFG, **(cfg_overrides or {})}
        payload["catalog"] = catalog if catalog is not None else CATALOG
    return {"name": name, "payload": payload}


# ── Zplosteni ───────────────────────────────────────────────────────────────
flat = settings_of(run("a.json"))
check("zplosteni zna globalni klic", flat["protocol_b_limit_ma"], 250)
check("zplosteni zna per-step timeout", flat["krok[grab].timeout_s"], 8.0)
check("zplosteni zna priznak uchopu", flat["krok[grab].grasp"], True)
# calibration_min_runs ovlivnuje jen tabulku v Nastaveni, ne chovani robota.
# Vypise se (nic se neschovava), ale stabilitu shodit nesmi — jinak by kazde
# prenastaveni kalibracni tabulky vypadalo jako zmena experimentu.
check("kalibracni klic je videt", "calibration_min_runs" in flat, True)
calib_only = compare([run("1.json"), run("2.json", {"calibration_min_runs": 8})])
check("kalibracni klic stabilitu neshodi", calib_only["stable"], True)
check("kalibracni klic se presto vypise", len(calib_only["differences"]), 1)


# ── Stabilni serie ──────────────────────────────────────────────────────────
stable = compare([run("1.json"), run("2.json"), run("3.json")])
check("stejne nastavene behy jsou stabilni", stable["stable"], True)
check("stabilni serie nema rozdily", stable["differences"], [])
check("stabilni serie zna pocet behu", stable["n"], 3)


# ── Zmena rozhodneho klice ──────────────────────────────────────────────────
drift = compare([run("1.json"), run("2.json"),
                 run("3.json", {"protocol_b_limit_ma": 300})])
check("zmena prahu shodi stabilitu", drift["stable"], False)
check("zmena prahu je rozhodna", drift["decisive_differences"], 1)
diff = drift["differences"][0]
check("nahlaseny spravny klic", diff["key"], "protocol_b_limit_ma")
check("obe hodnoty jsou videt", sorted(str(g["value"]) for g in diff["values"]), ["250", "300"])
# Nejcastejsi hodnota je prvni, at je poznat, ktery beh vybocuje.
check("vetsinova hodnota je prvni", diff["values"][0]["value"], 250)
check("vybocujici beh je jmenovany", diff["values"][1]["runs"], ["3.json"])


# ── Zmena per-step timeoutu je stejne zasadni jako globalni prah ────────────
step_drift = compare([
    run("1.json"),
    run("2.json", catalog=[CATALOG[0], {**CATALOG[1], "timeout_s": 12.0}]),
])
check("zmena timeoutu kroku shodi stabilitu", step_drift["stable"], False)
check("zmena timeoutu kroku je rozhodna",
      step_drift["differences"][0]["key"], "krok[grab].timeout_s")


# ── Nerozhodny klic se hlasi, ale stabilitu neshodi ─────────────────────────
cosmetic = compare([run("1.json"), run("2.json", {"python": "/jiny/python"})])
check("zmena cesty stabilitu neshodi", cosmetic["stable"], True)
check("zmena cesty se presto vypise", len(cosmetic["differences"]), 1)
check("zmena cesty neni rozhodna", cosmetic["differences"][0]["decisive"], False)

# Rozhodne rozdily se radi pred nerozhodne — jinak by to podstatne zapadlo.
mixed = compare([run("1.json"),
                 run("2.json", {"python": "/jiny/python", "max_replans": 9})])
check("rozhodny rozdil je vypsany prvni", mixed["differences"][0]["key"], "max_replans")


# ── Chybejici klic v jednom behu ────────────────────────────────────────────
# Beh z doby pred pridanim prepinace ho v konfiguraci nema. To je rozdil,
# ktery se musi ohlasit — ne tise povazovat za shodu s vychozi hodnotou.
missing = compare([run("1.json"), run("2.json", {"uncertain_retry": None})])
partial = compare([
    run("1.json"),
    {"name": "2.json", "payload": {"success": True,
                                   "config": {k: v for k, v in BASE_CFG.items()
                                              if k != "uncertain_retry"},
                                   "catalog": CATALOG}},
])
check("chybejici klic se ohlasi", partial["decisive_differences"], 1)
check("chybejici klic je oznaceny",
      any(g["missing"] for g in partial["differences"][0]["values"]), True)
check("hodnota None je jina hodnota nez 250", missing["stable"], False)


# ── Behy bez zaznamenane konfigurace ────────────────────────────────────────
# Starsi format neumi stabilitu ani potvrdit, ani vyvratit — musi se vypsat
# zvlast, ne tise zapocitat mezi shodne.
legacy = compare([run("1.json"), run("2.json"), run("3.json", with_config=False)])
check("beh bez konfigurace se nepocita", legacy["n"], 2)
check("beh bez konfigurace je vypsany", legacy["runs_without_settings"], ["3.json"])
check("zbytek serie muze byt stabilni", legacy["stable"], True)

only_legacy = compare([run("1.json", with_config=False)])
check("samy stary format neni stabilni", only_legacy["stable"], False)
check("prazdny vstup neni stabilni", compare([])["stable"], False)


# ── Filtr podle projektu ────────────────────────────────────────────────────
# Bez nej by se jako "zmena nastaveni" hlasil kazdy jiny projekt, coz je
# sum, ne nalez.
mixed_projects = [run("1.json"), run("2.json", {"task_slug": "jina_uloha"})]
check("filtr nechá jen aktualni projekt",
      [r["name"] for r in filter_by_task(mixed_projects, "pick_and_place")], ["1.json"])
check("prazdny slug filtr nevyhazuje", len(filter_by_task(mixed_projects, "")), 2)

# "Poslednich N behu teto serie" = filtrovat, teprve pak orezat. Opacne
# poradi vrati min nez N, kdykoli se mezi ne vklini beh jineho projektu.
series = [run("5.json"), run("4.json", {"task_slug": "jina_uloha"}),
          run("3.json"), run("2.json"), run("1.json")]
check("orez az po filtru vrati pozadovany pocet",
      [r["name"] for r in take_last(filter_by_task(series, "pick_and_place"), 2)],
      ["5.json", "3.json"])
check("nulovy orez nechava vse", len(take_last(series, 0)), 5)
check("orez vetsi nez pocet behu nevadi", len(take_last(series, 99)), 5)


# ── Rozhodne je vsechno krome vyjimek ───────────────────────────────────────
for key in ("protocol_a_threshold_rad", "protocol_b_limit_ma", "protocol_b_grace_s",
            "max_replans", "uncertain_retry", "plan_state_check", "done_visual_check",
            "skip_planner", "skip_inspector", "llm_model", "vlm_model",
            "planner_memory", "baseline_policy_path"):
    if key in NON_DECISIVE_KEYS:
        failures.append((f"klic {key} je chybne mezi nerozhodnymi", True, False))
print(f"ok   vsechny kontrolovane klice jsou rozhodne ({len(NON_DECISIVE_KEYS)} vyjimek celkem)")

# Tohle je ta vlastnost, kvuli ktere je seznam obraceny: prepinac, ktery v
# dobe psani skriptu jeste neexistoval, MUSI byt rozhodny sam od sebe. Jinak
# by serie, do ktere nekdo uprostred pridal novy prepinac, vyhlasila
# "STABILNI" — a to je chyba, ktera se projevi az jako neplatne cislo.
future = compare([run("1.json"), run("2.json", {"uplne_novy_prepinac_2027": True})])
check("neznamy budouci prepinac je rozhodny", future["decisive_differences"], 1)
check("neznamy budouci prepinac shodi stabilitu", future["stable"], False)

# Kterou politiku krok spustil, je to nejrozhodnejsi vubec — a je to pole
# katalogu, ne konfigurace.
policy = compare([
    run("1.json"),
    run("2.json", catalog=[CATALOG[0], {**CATALOG[1], "policy_path": "outputs/jiny_model"}]),
])
check("zmena checkpointu kroku je rozhodna", policy["decisive_differences"], 1)
check("zmena checkpointu kroku je pojmenovana",
      policy["differences"][0]["key"], "krok[grab].policy_path")

# train_steps u kroku je naopak zalezitost treninku, ne behu.
train = compare([
    run("1.json"),
    run("2.json", catalog=[CATALOG[0], {**CATALOG[1], "train_steps": 30000}]),
])
check("train_steps kroku neni rozhodny", train["stable"], True)
check("train_steps kroku se presto vypise", len(train["differences"]), 1)

print()
if failures:
    print(f"NEPROSLO: {len(failures)} pripadu")
    for f in failures:
        print("  ", f)
    sys.exit(1)
print("OK — kontrola stability nastaveni sedi.")
