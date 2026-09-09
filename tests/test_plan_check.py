"""Kontrola planu proti stavu gripperu (orchestrator.plan_state_conflict).

Stejne jako test_fusion.py nepotrebuje robota ani LeRobota — kontrola je
cista funkce nad katalogem kroku a jednim bitem fyzickeho stavu, takze jde
overit cela, vcetne pripadu, ktere se na skutecnem robotu trefi jen zridka.

    python tests/test_plan_check.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from orchestrator import PLAN_ABORT, PLAN_DONE, plan_state_conflict

# Zamerne bezbarvy, neutralni katalog: kontrola nesmi znat nic o konkretni
# uloze — jen poradi kroku a priznaky grasp/reset.
CATALOG = [
    {"slug": "approach", "grasp": False, "reset": False},
    {"slug": "pick", "grasp": True, "reset": False},
    {"slug": "transport", "grasp": False, "reset": False},
    {"slug": "release", "grasp": False, "reset": False},
    {"slug": "home", "grasp": False, "reset": True},
]

NO_GRASP_CATALOG = [
    {"slug": "push_a", "grasp": False, "reset": False},
    {"slug": "push_b", "grasp": False, "reset": False},
]

# (popis, plan, katalog, holding) -> ceka se rozpor?
CASES = [
    # -- rozpory, ktere se maji chytit -----------------------------------
    ("uchop, kdyz uz robot neco drzi", ["pick", "transport"], CATALOG, True, True),
    ("prenos s prazdnymi celistmi", ["transport", "release"], CATALOG, False, True),
    ("polozeni s prazdnymi celistmi", ["release"], CATALOG, False, True),

    # -- konzistentni plany ----------------------------------------------
    ("uchop s prazdnymi celistmi", ["pick", "transport"], CATALOG, False, False),
    ("prenos, kdyz robot drzi", ["transport", "release"], CATALOG, True, False),
    ("prijezd k predmetu s prazdnymi celistmi", ["approach", "pick"], CATALOG, False, False),

    # RESET je z definice platny z libovolneho stavu — to je presne duvod,
    # proc ho plansovac smi zaradit kdykoli. Nesmi se hlasit jako rozpor
    # ani v jednom smeru.
    ("reset, kdyz robot drzi", ["home", "approach"], CATALOG, True, False),
    ("reset s prazdnymi celistmi", ["home", "approach"], CATALOG, False, False),

    # Zamerne nehlidany pripad: drzeni + predchozi faze. Katalogove poradi
    # na to neni dost presny podklad a spatne nastaveny prah "neco drzi" by
    # takhle shodil kazdy uvodni plan.
    ("prijezd, kdyz robot drzi (nehlida se)", ["approach", "pick"], CATALOG, True, False),

    # -- nelze nic tvrdit -> zadny rozpor --------------------------------
    ("neznamy stav gripperu", ["transport"], CATALOG, None, False),
    ("prazdny plan", [], CATALOG, True, False),
    ("uloha bez uchopu", ["push_b"], NO_GRASP_CATALOG, False, False),
    ("sentinel DONE", [PLAN_DONE], CATALOG, False, False),
    ("sentinel ABORT", [PLAN_ABORT], CATALOG, True, False),
    ("neznamy slug", ["neco_jineho"], CATALOG, False, False),
]

failures = []
for popis, plan, catalog, holding, want_conflict in CASES:
    conflict = plan_state_conflict(plan, catalog, holding)
    got = bool(conflict)
    status = "ok  " if got == want_conflict else "CHYBA"
    if got != want_conflict:
        failures.append((popis, got, want_conflict))
    print(f"{status} holding={str(holding):<5} plan={str(plan):<28} -> "
          f"{'rozpor' if got else 'v poradku'}   ({popis})")

# Hlaseny rozpor se posila zpatky planovaci do kontextu, takze musi vzdy
# pojmenovat konkretni krok — holy priznak "neco nesedi" mu nedava co opravit.
conflict = plan_state_conflict(["pick"], CATALOG, True)
if "pick" not in conflict:
    failures.append(("text rozporu nejmenuje krok", conflict, "obsahuje 'pick'"))

print()
if failures:
    print(f"NEPROSLO: {len(failures)} pripadu")
    for f in failures:
        print("  ", f)
    sys.exit(1)
print(f"OK — vsech {len(CASES)} pripadu kontroly planu sedi.")
