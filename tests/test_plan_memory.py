"""Pamet planovace (orchestrator.plan_repeat_index, format_planner_memory).

Stejne jako test_plan_check.py nepotrebuje robota, LeRobota ani LM Studio —
obe funkce jsou ciste nad seznamem plany, ktere planovac sam navrhl. Prave
proto jdou overit uplne, vcetne pripadu, ktere na skutecnem behu nastanou
zridka (plan, pod kterym se nic nespustilo; plan bez odduvodneni).

    python tests/test_plan_memory.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from orchestrator import format_planner_memory, plan_repeat_index

failures: list[tuple] = []


def check(popis: str, got, want) -> None:
    ok = got == want
    if not ok:
        failures.append((popis, got, want))
    print(f"{'ok  ' if ok else 'CHYBA'} {popis}")


def contains(popis: str, text: str, needle: str, want: bool = True) -> None:
    ok = (needle in text) == want
    if not ok:
        failures.append((popis, needle in text, want))
    print(f"{'ok  ' if ok else 'CHYBA'} {popis}")


def entry(plan, reasoning="", first_attempt=1, repeat_of=None) -> dict:
    return {"plan": plan, "reasoning": reasoning,
            "first_attempt": first_attempt, "repeat_of": repeat_of}


# ── plan_repeat_index ──────────────────────────────────────────────────────
# Zamerne bezbarve slugy: pamet planovace nesmi znat nic o konkretni uloze.
H = [entry(["a", "b"], first_attempt=1), entry(["reset", "a"], first_attempt=3)]

check("prazdna historie -> nic", plan_repeat_index(["a"], []), None)
check("novy plan -> nic", plan_repeat_index(["b", "a"], H), None)
check("shoda s prvnim plánem -> 1", plan_repeat_index(["a", "b"], H), 1)
check("shoda s druhym plánem -> 2", plan_repeat_index(["reset", "a"], H), 2)
check("poradi kroku rozhoduje", plan_repeat_index(["a", "reset"], H), None)
check("prefix neni shoda", plan_repeat_index(["a"], H), None)
check("prazdny plan proti neprazdne historii", plan_repeat_index([], H), None)
check("prvni shoda vyhrava",
      plan_repeat_index(["a", "b"], [entry(["a", "b"]), entry(["a", "b"])]), 1)


# ── format_planner_memory ──────────────────────────────────────────────────
check("prazdna historie -> prazdny blok", format_planner_memory([], 0), "")

block = format_planner_memory(
    [entry(["a", "b", "c"], "Predmet lezi vlevo od cile.", first_attempt=1),
     entry(["reset", "a"], "Rameno skoncilo v divne poloze.", first_attempt=3)],
    total_attempts=4)
print("\n" + block + "\n")

contains("prvni plan pokryva pokusy 1-2", block, "plan 1 (attempts 1-2)")
contains("druhy plan pokryva pokusy 3-4", block, "plan 2 (attempts 3-4)")
contains("odduvodneni planovace se prenasi", block, "Predmet lezi vlevo od cile.")
contains("plan je JSON pole", block, '["a", "b", "c"]')
# Vysledky kroku se do bloku nekopiruji — od toho je PROGRESS THIS RUN a
# cisla pokusu, ktera oba soupisy parují. Duplikovat je by slo proti tomu,
# proc tenhle blok existuje (dat pomale vrstve jen to, co jeste nema).
contains("bez opisovani verdiktu (SUCCESS)", block, "SUCCESS", want=False)
contains("bez opisovani verdiktu (FAILED)", block, "FAILED", want=False)
# Opakovani planu se nezakazuje, jen se po nem chce zduvodneni — zmenena
# scena je legitimni duvod zkusit tentyz plan znovu (viz revert z 2026-09-08).
contains("zada zduvodneni misto zakazu", block, "what has changed since")

single = format_planner_memory([entry(["a"], first_attempt=1)], total_attempts=1)
contains("jediny pokus se pise jednotne", single, "plan 1 (attempt 1)")
contains("chybejici odduvodneni nevyrobi prazdne uvozovky", single, '""', want=False)

# Plan prijaty tesne pred koncem behu (napr. planovac odvolal DONE a beh
# skoncil driv, nez se cokoli spustilo) nesmi vyrobit zaporny rozsah pokusu.
none_run = format_planner_memory(
    [entry(["a"], first_attempt=1), entry(["b"], first_attempt=2)], total_attempts=1)
contains("nespusteny plan", none_run, "plan 2 (not executed)")

print()
if failures:
    print(f"NEPROSLO: {len(failures)} pripadu")
    for f in failures:
        print("  ", f)
    sys.exit(1)
print("OK — pamet planovace sedi ve vsech kontrolovanych pripadech.")
