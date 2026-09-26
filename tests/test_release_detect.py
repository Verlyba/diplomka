"""Pouštění předmětu (release_detect.ReleaseTracker + orchestrator.release_evidence).

Nepotřebuje robota ani LeRobota. Vstupy jsou SKUTEČNÉ křivky zátěže gripperu z
telemetry/*.jsonl (5 Hz), které jsou přesně tou situací, kvůli které to vzniklo:
carry_cube končil protokolem A, když rameno dosedlo nad misku, ale gripper ještě
svíral kostku (2026-09-25).

Telemetrie je 5 Hz, démon jede 30 Hz, takže se tu patience a práh pohybu přepočítávají
na telemetrické tiky: 3 tiky (0,1 s) ~ 1, usazení 7 tiků (0,23 s) ~ 2, práh pohybu
0,5 °/tik ~ 3 °/0,2 s. Stejné přepočty používá přehrání celé telemetrie.

    python tests/test_release_detect.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from orchestrator import PHYS_CONFIRM, PHYS_DENY, PHYS_NONE, release_evidence
from release_detect import FREE_FRAC, HOLD_FRAC, ReleaseTracker

LIMIT = 300.0
HOLD, FREE = HOLD_FRAC * LIMIT, FREE_FRAC * LIMIT
INF = float("inf")
failures = []


def check(name, cond, detail=""):
    print(("ok   " if cond else "CHYBA"), name, ("" if cond else f"  -> {detail}"))
    if not cond:
        failures.append(name)


def tracker():
    return ReleaseTracker(HOLD, FREE, hold_patience=1, settle_ticks=2, still_delta=3.0)


def replay(loads, grips, plateaus=None, tr=None):
    """Vrátí (tracker, index prvního 'sevřel', index prvního 'pustil')."""
    tr = tr or tracker()
    g = r = None
    prev = None
    for i, (ld, gp) in enumerate(zip(loads, grips)):
        step = abs(gp - prev) if prev is not None else INF
        prev = gp
        was_g, was_r = tr.gripped, tr.released
        tr.update(ld, step, True if plateaus is None else plateaus[i])
        if tr.gripped and not was_g and g is None:
            g = i
        if tr.released and not was_r and r is None:
            r = i
    return tr, g, r


# ── 1) skutečné puštění: 20260925-215320 #1 (kostka držená přes diagonálu, gripper +1,2°) ──
loads = [44] + [500] * 18 + [72, 72, 51]
grips = [31.3] + [30.7] * 18 + [31.1, 31.1, 31.9]
tr, g, r = replay(loads, grips)
check("skutečné puštění: sevření zaznamenáno na plató zátěže 500", g is not None and g <= 2, f"g={g}")
check("skutečné puštění: pustil až po poklesu zátěže a zastavení gripperu",
      r is not None and r >= 19, f"r={r} (zátěž klesla na indexu 19)")
check("skutečné puštění: bez požadavku na otevření gripperu (posun jen 1,2°)", tr.released)
check("skutečné puštění: po puštění už nic nedrží", not tr.holding)

# ── 2) rameno dosedlo, kostka pořád v čelistech: 20260925-213514 #1 (končil 2,4 s protokolem A) ──
loads = [55, 431, 421, 428]
grips = [19.1, 19.1, 18.9, 18.6]
tr, g, r = replay(loads, grips)
check("předčasný konec: drží a nepustil -> protokol A je zakázán", tr.holding and r is None, f"g={g} r={r}")

# ── 3) pouštění v běhu: zátěž už klesá, ale gripper se ještě otevírá (204909/205253) ──
loads = [305, 308, 329, 365, 161, 140, 120, 90]
grips = [19.4, 19.6, 19.8, 19.9, 24.0, 32.0, 38.0, 41.3]      # čelisti jedou z 19° na 41°
tr, g, r = replay(loads, grips)
check("gripper se pořád hýbe -> ještě nepustil (zátěž sama nestačí)",
      g is not None and (r is None or r >= 7), f"g={g} r={r}")
loads += [60, 58]
grips += [41.3, 41.3]
tr, g, r = replay(loads, grips)
check("gripper se zastavil a zátěž je pryč -> pustil", tr.released, f"g={g} r={r}")

# ── 4) náběh zavírání na začátku kroku (20260906-130647 #1: 208 při zavírání 39,9° -> 18°) ──
# Démon takový tik nepošle jako plató (|slope| >= 30), tracker se neozbrojí.
loads = [114, 208, 119, 102, 49, 45, 69, 79, 62, 58]
grips = [39.9, 28.6, 24.0, 21.9, 20.5, 19.4, 18.8, 18.7, 18.4, 18.4]
tr, g, r = replay(loads, grips, plateaus=[False, False] + [True] * 8)
check("náběh bez plató se za sevření nepočítá", not tr.gripped and not tr.holding, f"g={g}")
tr, g, r = replay(loads, grips)          # kontrola, že by ho bez plató ozbrojil a pustil (proto plató)
check("bez plató by se tracker ozbrojil na náběhu (důvod, proč se plató vyžaduje)", g is not None)

# ── 5) nic nesvíral (předchozí úchop selhal): protokol A funguje jako dřív ──
loads = [51, 41, 30, 44, 40]
grips = [10.7] * 5
tr, g, r = replay(loads, grips)
check("bez sevření: holding zůstane False -> protokol A není blokován", not tr.holding and not tr.gripped)

# ── 6) hysterezní pás: zátěž mezi hranicemi nic nemění ──
tr = tracker()
tr.update(HOLD + 50, 0.0)
check("nad hold_rise se ozbrojí", tr.gripped)
for _ in range(5):
    tr.update((HOLD + FREE) / 2, 0.0)            # mezi 150 a 198
check("v hysterezním pásu zůstává 'drží'", tr.holding)
for _ in range(2):
    tr.update(FREE - 1, 0.0)
check("pod free_rise a se stojícím gripperem pustí (settle_ticks=2)", tr.released)
tr.update(HOLD + 100, 0.0)
check("po puštění se stav nemění (latch)", tr.released)

# ── 7) reset, neznámý pohyb gripperu, neplatné parametry ──
tr.reset()
check("reset() vrátí do výchozího stavu", not tr.gripped and not tr.released and not tr.holding)
tr = tracker()
tr.update(500, 0.0)
tr.update(10, INF)
tr.update(10, INF)
check("neměřitelný pohyb gripperu (inf) se nepočítá za 'stojí'", tr.holding and not tr.released)
for bad in (dict(hold_rise=100, free_rise=100), dict(hold_rise=100, free_rise=200)):
    try:
        ReleaseTracker(hold_patience=1, settle_ticks=1, still_delta=1.0, **bad)
        check(f"neplatné hranice {bad} vyhodí ValueError", False, "nevyhodilo")
    except ValueError:
        check(f"neplatné hranice {bad} vyhodí ValueError", True)

# ── 8) fyzický verdikt kroku (orchestrator.release_evidence) ──
UVOLNENI = ("Protokol B (uvolnění: zátěž gripperu klesla na 51, nárůst 51 pod 150 "
            "po sevření nad 198, gripper stojí 7/7 snímků)")
CASES = [
    ("uvolnění potvrzené démonem -> CONFIRM", (UVOLNENI, 51, 0, LIMIT, True), PHYS_CONFIRM),
    ("krok skončil a zátěž pořád drží -> DENY", ("Časový limit kroku", 488, 0, LIMIT, True), PHYS_DENY),
    ("protokol A, ale čelisti svírají -> DENY", ("Protokol A (klouby dosedly na predikci, ...)", 350, 0, LIMIT, True), PHYS_DENY),
    ("krok skončil a zátěž je nízká, ale uvolnění démon nepotvrdil -> NONE",
     ("Časový limit kroku", 60, 0, LIMIT, True), PHYS_NONE),
    ("zátěž mezi hranicemi (nárůst 170) -> NONE", ("Časový limit kroku", 170, 0, LIMIT, True), PHYS_NONE),
    ("nárůst se počítá nad klidovou hodnotou (zátěž 300, klid 250) -> NONE",
     ("Časový limit kroku", 300, 250, LIMIT, True), PHYS_NONE),
    ("čidlo nikdy nevrátilo nenulovou hodnotu -> NONE (i při 'uvolnění')", (UVOLNENI, 0, 0, LIMIT, False), PHYS_NONE),
    ("zátěž neznámá -> NONE", ("Časový limit kroku", None, None, LIMIT, True), PHYS_NONE),
]
for name, args, want in CASES:
    got, note = release_evidence(*args[:4], sensor_ok=args[4])
    check(f"release_evidence: {name}", got == want, f"{got} != {want} ({note})")

print()
if failures:
    print(f"NEPROSLO: {len(failures)} pripadu")
    for f in failures:
        print("  ", f)
    sys.exit(1)
print("OK — všechny případy pouštění sedí.")
