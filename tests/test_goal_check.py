"""Krizova kontrola tvrzeni "cil je splneny" (orchestrator._settle_done).

Nepotrebuje robota ani LeRobota: parser odpovedi je cista funkce a samotny
dotaz na inspektora se da overit s podvrzenym klientem LM Studia. Prave tahle
cesta rozhoduje, jestli se beh zapise jako uspesny, takze si zaslouzi test i
bez hardwaru.

    python tests/test_goal_check.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import orchestrator as orch
from orchestrator import PLAN_DONE, parse_goal_flag

failures = []


# ── parser radku "GOAL: yes|no" ───────────────────────────────────────────
# None znamena "model se nevyjadril" a musi zustat odlisitelne od "ne" —
# na tom stoji cely zbytek: nevyjadreni se DONE nezpochybnuje, "ne" ano.
PARSE_CASES = [
    ("proste ano", "REASONING: vidim to\nGOAL: yes\nSUCCESS", True),
    ("proste ne", "REASONING: nevidim to\nGOAL: no", False),
    ("velka pismena", "GOAL: YES", True),
    ("tecka a hvezdicky", "**GOAL: no.**", False),
    ("odsazeni", "   GOAL: yes   ", True),
    ("delsi odpoved za yes", "GOAL: yes, the object is in place", True),
    ("chybejici radek", "REASONING: neco\nSUCCESS", None),
    ("prazdna odpoved", "", None),
    ("GOAL bez odpovedi", "GOAL:", None),
    ("nesmyslna odpoved", "GOAL: maybe", None),
    ("slovo goal v jine vete", "REASONING: the goal area is empty\nGOAL: no", False),
]

for popis, text, want in PARSE_CASES:
    got = parse_goal_flag(text)
    status = "ok  " if got is want else "CHYBA"
    if got is not want:
        failures.append((popis, got, want))
    print(f"{status} parse_goal_flag -> {str(got):<5} ({popis})")


# ── _settle_done: co se stane s tvrzenim DONE ─────────────────────────────
class FakeLM:
    """Vraci predpripravene odpovedi misto volani LM Studia."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = 0

    def chat_with_images(self, **kw):
        self.calls += 1
        if not self.replies:
            raise AssertionError("volani navic — mechanismus se ptal vic, nez mel")
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


CATALOG = [
    {"slug": "approach", "description": "a", "grasp": False, "reset": False},
    {"slug": "pick", "description": "p", "grasp": True, "reset": False},
]
orch.step_catalog = lambda cfg: CATALOG  # katalog bez projects/*.json

IMG = ["<snimek>"]


def settle(cfg_extra, replies, images=IMG):
    cfg = {"task_slug": "t", "task_description": "d", "steps": CATALOG,
           "gripper_state_in_context": False}
    cfg.update(cfg_extra)
    o = orch.Orchestrator(cfg, lambda ev, **kw: None)
    o.lm = FakeLM(replies)
    plan, _ = o._settle_done("KONTEXT", images)
    return plan, o.done_checks[-1], o.lm.calls


# (popis, cfg, odpovedi modelu, ocekavany plan, ocekavany verdikt, pocet volani)
SETTLE_CASES = [
    ("inspektor potvrdi cil",
     {}, ["REASONING: je to tam\nGOAL: yes"], [PLAN_DONE], "confirmed", 1),

    # Inspektor cil nevidi a planovac po upozorneni couvne -> beh pokracuje
    # misto toho, aby skoncil jako uspesny. To je jediny pripad, kdy se
    # chovani behu opravdu meni.
    ("inspektor popre, planovac couvne",
     {}, ["REASONING: kostka lezi vedle\nGOAL: no",
          'REASONING: dokoncim to\n["approach", "pick"]'],
     ["approach", "pick"], "denied", 2),

    # Zastaveni si porad drzi planovac: trva-li na DONE, beh skonci jako DONE
    # a rozpor se jen zapise. Zmateny VLM nesmi poslat robota manipulovat s
    # uz hotovou scenou.
    ("inspektor popre, planovac trva na svem",
     {}, ["REASONING: kostka lezi vedle\nGOAL: no", 'REASONING: hotovo\n["DONE"]'],
     [PLAN_DONE], "denied", 2),

    ("inspektor se nevyjadri",
     {}, ["REASONING: nevim\n(zadny radek GOAL)"], [PLAN_DONE], "unknown", 1),

    # Vypadek VLM nesmi shodit beh, ktery by jinak dobehl.
    ("volani VLM selze",
     {}, [RuntimeError("spojeni selhalo")], [PLAN_DONE], "unknown", 1),

    # Ablace a vypnuta kontrola se inspektora nesmi zeptat vubec.
    ("kontrola vypnuta", {"done_visual_check": False}, [], [PLAN_DONE], "off", 0),
    ("ablace bez inspektora", {"skip_inspector": True}, [], [PLAN_DONE], "skipped", 0),
]

for popis, cfg_extra, replies, want_plan, want_verdict, want_calls in SETTLE_CASES:
    plan, record, calls = settle(cfg_extra, replies)
    ok = plan == want_plan and record["verdict"] == want_verdict and calls == want_calls
    status = "ok  " if ok else "CHYBA"
    if not ok:
        failures.append((popis, (plan, record["verdict"], calls),
                         (want_plan, want_verdict, want_calls)))
    print(f"{status} _settle_done -> {record['verdict']:<9} plan={str(plan):<24} "
          f"volani={calls}  ({popis})")

# Bez snimku neni co posoudit — a hlavne se nesmi nic ptat.
plan, record, calls = settle({}, [], images=[])
if plan != [PLAN_DONE] or record["verdict"] != "unknown" or calls != 0:
    failures.append(("bez snimku", (plan, record["verdict"], calls),
                     ([PLAN_DONE], "unknown", 0)))
print(f"ok   _settle_done -> {record['verdict']:<9} plan={str(plan):<24} "
      f"volani={calls}  (bez snimku)")

# Rozpor musi zustat v datech behu i tehdy, kdyz beh dopadne jako uspesny —
# jinak by se nedal z runs/*.json vyfiltrovat.
_, record, _ = settle({}, ["REASONING: kostka lezi vedle\nGOAL: no",
                           'REASONING: hotovo\n["DONE"]'])
if record["verdict"] != "denied" or record["insisted"] is not True:
    failures.append(("zaznam o trvani na DONE", record, "verdict=denied, insisted=True"))
if "kostka" not in (record["inspector_reason"] or ""):
    failures.append(("zaznam neobsahuje oduvodneni inspektora",
                     record["inspector_reason"], "veta od inspektora"))

print()
if failures:
    print(f"NEPROSLO: {len(failures)} pripadu")
    for f in failures:
        print("  ", f)
    sys.exit(1)
print(f"OK — vsech {len(PARSE_CASES) + len(SETTLE_CASES) + 2} pripadu kontroly DONE sedi.")
