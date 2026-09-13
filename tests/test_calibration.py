"""Kalibrace prahu protokolu A/B z telemetrie (calibrate_protocols.py).

Stejne jako test_fusion.py nepotrebuje ani robota, ani LeRobota — telemetrie
se tu vyrobi synteticky, takze se da overit i kombinace, ktere se na
skutecnem hardwaru trefi jen zridka (prilis nizky prah, prazdna data,
useknuty soubor).

    python tests/test_calibration.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from calibrate_protocols import (DEFAULT_MIN_RUNS, analyze, band_verdict,
                                 collect, gap_midpoint, percentile, summarize)

failures = []


def check(name, got, want):
    ok = got == want
    print(f"{'ok  ' if ok else 'CHYBA'} {name}: {got!r}" + ("" if ok else f"  (cekano {want!r})"))
    if not ok:
        failures.append((name, got, want))


# ── Statisticke primitivy ───────────────────────────────────────────────────
check("percentil prazdneho vzorku", percentile([], 0.5), None)
check("percentil p50", percentile([1.0, 2.0, 3.0], 0.5), 2.0)
check("percentil p05 bere nejnizsi", percentile([1.0, 2.0, 3.0], 0.05), 1.0)
check("percentil p95 bere nejvyssi", percentile([1.0, 2.0, 3.0], 0.95), 3.0)
check("summarize prazdneho", summarize([])["n"], 0)

# Geometricky prumer musi lezet mezi okraji, a u rezimu vzdalenych o rad
# vyrazne niz nez aritmeticky prumer — o to tady jde.
mid = gap_midpoint(1.0, 100.0)
check("stred mezery 1..100", mid, 10.0)
check("stred mezery je pod aritmetickym prumerem", mid < (1.0 + 100.0) / 2, True)
check("stred mezery bez dolniho okraje", gap_midpoint(None, 10.0), None)
check("stred mezery pri nulovem dolnim okraji", gap_midpoint(0.0, 10.0), None)
check("stred mezery pri prevracenych okrajich", gap_midpoint(10.0, 1.0), None)

# ── Verdikt ─────────────────────────────────────────────────────────────────
check("verdikt uvnitr mezery", band_verdict(5.0, 1.0, 10.0), "ok")
check("verdikt na dolnim okraji", band_verdict(1.0, 1.0, 10.0), "too_low")
check("verdikt na hornim okraji", band_verdict(10.0, 1.0, 10.0), "too_high")
check("verdikt bez nastavene hodnoty", band_verdict(None, 1.0, 10.0), "unknown")
# Slita mezera znamena "oba rezimy vypadaji stejne", ne "prah je spatne" —
# sebejisty verdikt by uzivatele poslal ladit podle vzorku, ktery o prahu
# nic nerika.
check("verdikt pri slite mezere", band_verdict(5.0, 10.0, 10.0), "unknown")
check("verdikt pri prevracene mezere", band_verdict(5.0, 10.0, 1.0), "unknown")


# ── Syntenticka telemetrie ──────────────────────────────────────────────────

def run_rows(t0=1000.0, grasp_fires=True):
    """Jeden bezny beh: klid s prazdnymi celistmi -> najezd -> uchop -> klid s predmetem."""
    rows = [{"event": "daemon_start", "t": t0}]
    # Klidove tiky pred prvnim krokem = prazdne celisti.
    for i in range(3):
        rows.append({"event": "tick", "state": "WAITING", "t": t0 + i * 0.2,
                     "load": 100.0, "baseline": 100.0, "rise": 2.0})
    # Krok bez uchopu: chvili pohyb, pak klid.
    rows.append({"event": "task_started", "task": "approach", "is_grasp": False,
                 "is_reset": False, "t": t0 + 1.0})
    for i, vel in enumerate([20.0, 18.0, 15.0, 0.2, 0.1]):
        rows.append({"event": "tick", "state": "RUNNING", "task": "approach",
                     "is_grasp": False, "t": t0 + 1.2 + i * 0.2,
                     "joint_velocity": vel, "target_error": 1.5,
                     "load": 100.0, "baseline": 100.0, "rise": 3.0, "slope": 1.0})
    rows.append({"event": "task_done", "task": "approach", "t": t0 + 2.4,
                 "reason": "Protokol A (klouby se prestaly hybat, max pohyb 0.10000)",
                 "rise": 3.0})
    # Uchopovy krok: proud stoupa, pak plato.
    rows.append({"event": "task_started", "task": "grab", "is_grasp": True,
                 "is_reset": False, "t": t0 + 3.0})
    for i, (vel, rise, slope) in enumerate([(12.0, 40.0, 300.0), (9.0, 160.0, 250.0),
                                            (0.3, 300.0, 8.0), (0.2, 305.0, 4.0)]):
        rows.append({"event": "tick", "state": "RUNNING", "task": "grab",
                     "is_grasp": True, "t": t0 + 3.2 + i * 0.2,
                     "joint_velocity": vel, "target_error": 2.0,
                     "load": 100.0 + rise, "baseline": 100.0, "rise": rise, "slope": slope})
    if grasp_fires:
        rows.append({"event": "task_done", "task": "grab", "t": t0 + 4.0,
                     "reason": "Protokol B (celisti registruji sevreni)", "rise": 305.0})
        # Klidove tiky po potvrzenem uchopu = neco se drzi.
        for i in range(3):
            rows.append({"event": "tick", "state": "WAITING", "t": t0 + 4.2 + i * 0.2,
                         "load": 380.0, "baseline": 100.0, "rise": 280.0})
    else:
        rows.append({"event": "task_done", "task": "grab", "t": t0 + 4.0,
                     "reason": "Casovy limit kroku", "rise": 305.0})
    return rows


FILES = [run_rows(1000.0), run_rows(2000.0), run_rows(3000.0)]

steps, holding = collect(FILES, grace_s=0.0)
check("nasel oba kroky", sorted(steps), ["approach", "grab"])
check("pocet behu u kroku", steps["approach"]["runs"], 3)
check("pocet pokusu u kroku", steps["grab"]["attempts"], 3)
check("uchopovy krok je oznaceny", steps["grab"]["is_grasp"], True)
check("duvody ukonceni", steps["grab"]["end_reasons"], {"Protokol B": 3})
# Klidove tiky se rozdelily podle toho, kde v behu lezi.
check("klidove vzorky s prazdnymi celistmi", len(holding["empty"]), 9)
check("klidove vzorky s drzenym predmetem", len(holding["held"]), 9)

# Ochranna doba musi zahodit tiky z rozjezdu kroku (tady prvni dva tiky uchopu).
_, _ = collect(FILES, grace_s=0.0)
steps_grace, _ = collect(FILES, grace_s=0.5)
check("ochranna doba zahodila rozjezdove tiky",
      len(steps_grace["grab"]["rise"]) < len(steps["grab"]["rise"]), True)

# ── Cely report ─────────────────────────────────────────────────────────────
# "approach" (bez |grasp/|reset) je kontrolovan proti target_threshold, ne
# proti threshold — viz PROTOCOL_A_TARGET_THRESHOLD v inference_daemon.py:
# jina fyzikalni velicina (vzdalenost od cile) nez rychlost u |grasp/|reset.
cfg = {"protocol_a_threshold_rad": 0.5, "protocol_a_target_threshold_rad": 0.5,
       "protocol_b_stability_slope": 30.0,
       "protocol_b_limit_ma": 250.0, "holding_limit_ma": 20.0,
       "protocol_b_grace_s": 0.0}
report = analyze(FILES, cfg, min_runs=3)
check("report zna pocet behu", report["runs_total"], 3)

by_key = {}
for c in report["checks"]:
    by_key.setdefault(c["key"], []).append(c)

# Prah pohybu 0.5 lezi mezi klidem (0.1-0.2) a pohybem (desitky) -> OK.
approach_a = next(c for c in by_key["protocol_a_target_threshold_rad"] if c["scope"] == "approach")
check("prah protokolu A je v mezere", approach_a["verdict"], "ok")
check("prah protokolu A ma navrh", approach_a["suggested"] is not None, True)

# Prah "neco drzi" 20 lezi mezi klidem naprazdno (2) a drzenim (280) -> OK.
hold = by_key["holding_limit_ma"][0]
check("prah drzeni je v mezere", hold["verdict"], "ok")

# U limitu protokolu B se zamerne nenavrhuje hodnota.
limit_b = next(c for c in by_key["protocol_b_limit_ma"] if c["scope"] == "grab")
check("limit protokolu B se nenavrhuje", limit_b["suggested"], None)

# Spatne nastavene hodnoty se musi poznat.
bad_low = analyze(FILES, {**cfg, "protocol_a_target_threshold_rad": 0.01}, min_runs=3)
bad_low_check = next(c for c in bad_low["checks"]
                     if c["key"] == "protocol_a_target_threshold_rad" and c["scope"] == "approach")
check("prilis nizky prah se pozna", bad_low_check["verdict"], "too_low")

bad_high = analyze(FILES, {**cfg, "protocol_a_target_threshold_rad": 999.0}, min_runs=3)
bad_high_check = next(c for c in bad_high["checks"]
                      if c["key"] == "protocol_a_target_threshold_rad" and c["scope"] == "approach")
check("prilis vysoky prah se pozna", bad_high_check["verdict"], "too_high")

# ── Prah na pocet behu ──────────────────────────────────────────────────────
# Tohle je uzivatelem pozadovana pojistka: dokud neni dost behu, nesmi z
# toho vylezt zadny verdikt ani navrh, aby se prahy neladily podle jednoho
# nahodneho pokusu.
strict = analyze(FILES, cfg, min_runs=10)
for c in strict["checks"]:
    if c["verdict"] != "insufficient_data" or c["suggested"] is not None:
        failures.append((f"min_runs mel zablokovat {c['key']}/{c['scope']}",
                         (c["verdict"], c["suggested"]), ("insufficient_data", None)))
print(f"ok   pri min_runs=10 je vsech {len(strict['checks'])} kontrol zablokovanych")

single = analyze([run_rows(1000.0)], cfg, min_runs=DEFAULT_MIN_RUNS)
single_a = next(c for c in single["checks"]
                if c["key"] == "protocol_a_target_threshold_rad" and c["scope"] == "approach")
check("jeden beh nestaci na verdikt", single_a["verdict"], "insufficient_data")

# ── Degenerovane vstupy ─────────────────────────────────────────────────────
empty = analyze([], cfg, min_runs=3)
check("prazdna telemetrie nespadne", empty["steps"], [])
check("prazdna telemetrie hlasi nula behu", empty["runs_total"], 0)

# Beh, kde uchop nikdy nespustil protokol B -> nemame vzorek "drzi",
# takze prah drzeni nesmi dostat verdikt.
no_grasp = analyze([run_rows(1000.0, grasp_fires=False),
                    run_rows(2000.0, grasp_fires=False),
                    run_rows(3000.0, grasp_fires=False)], cfg, min_runs=3)
no_hold = next(c for c in no_grasp["checks"] if c["key"] == "holding_limit_ma")
check("bez potvrzeneho uchopu neni verdikt na drzeni", no_hold["verdict"], "insufficient_data")

# Ochranna doba muze u kratkeho uchopu nechat jen tiky z plata — mezera se
# pak slije do jedne hodnoty a nesmi z toho vypadnout zadny verdikt.
flat = analyze(FILES, {**cfg, "protocol_b_grace_s": 0.75}, min_runs=3)
flat_b = next(c for c in flat["checks"]
              if c["key"] == "protocol_b_limit_ma" and c["scope"] == "grab")
check("slita mezera nedava verdikt", flat_b["verdict"], "insufficient_data")

print()
if failures:
    print(f"NEPROSLO: {len(failures)} pripadu")
    for f in failures:
        print("  ", f)
    sys.exit(1)
print("OK — kalibrace prahu z telemetrie sedi.")
