"""Ucetnictvi vrstev: co ktera vrstva v behu doopravdy stala.

Cele schema stoji na predpokladu, ze planovac je pomaly (a proto se vola
zridka) a inspektor rychly (a proto casto). Dosud se ten predpoklad nikdy
nezmeril. Testuje se ciste ucetnictvi — souctova funkce summarize_costs() a
obalka Orchestrator._chat(), ktera kazde volani modelu zmeri.

Bez robota, LeRobota i LM Studia: _chat() se testuje proti podvrzenemu
klientovi, zadne sitove volani se nedeje.

    python tests/test_layer_costs.py
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from orchestrator import (LAYER_INSPECTOR, LAYER_PLANNER, Orchestrator,
                          summarize_costs)

failures: list[tuple] = []


def check(label: str, got, want) -> None:
    ok = got == want
    if not ok:
        failures.append((label, got, want))
    print(f"{'ok  ' if ok else 'CHYBA'} {label}")


# ── summarize_costs: prazdny beh ────────────────────────────────────────────
# Beh, ktery skoncil driv, nez se cokoli zavolalo, musi dat nuly a ne vyjimku.
empty = summarize_costs([], [], [], 0)
check("prazdny beh: nula volani planovace", empty["planner_calls"], 0)
check("prazdny beh: nula volani inspektora", empty["inspector_calls"], 0)
check("prazdny beh: nula prepnuti modelu", empty["policy_swaps"], 0)
check("prazdny beh: nulova rezie", empty["orchestration_s"], 0)

# ── summarize_costs: deleni podle vrstev ────────────────────────────────────
CALLS = [
    {"layer": LAYER_PLANNER, "purpose": "initial_plan", "s": 12.0, "ok": True},
    {"layer": LAYER_INSPECTOR, "purpose": "verify_step", "s": 2.0, "ok": True},
    {"layer": LAYER_INSPECTOR, "purpose": "verify_step_resnapshot", "s": 2.5, "ok": True},
    {"layer": LAYER_PLANNER, "purpose": "replan", "s": 15.0, "ok": True},
    {"layer": LAYER_INSPECTOR, "purpose": "scene_change", "s": 1.5, "ok": True},
]
SWAPS = [
    {"step": "a", "phase": "preload", "s": 3.0},
    {"step": "b", "phase": "preload", "s": 4.0},
    {"step": "b", "phase": "step", "s": 0.1},
]
STEPS = [
    {"attempt": 1, "t_start": 1000.0, "t_end": 1020.0},
    {"attempt": 2, "t_start": 1050.0, "t_end": 1065.0},
]
c = summarize_costs(CALLS, SWAPS, STEPS, run_s=100.0)
check("planovac: pocet volani", c["planner_calls"], 2)
check("planovac: cas", c["planner_s"], 27.0)
check("inspektor: pocet volani", c["inspector_calls"], 3)
check("inspektor: cas", c["inspector_s"], 6.0)
check("prepnuti modelu: pocet", c["policy_swaps"], 3)
check("prepnuti modelu: cas", c["policy_swap_s"], 7.1)
check("cas dovednosti z t_start/t_end", c["skill_s"], 35.0)
# Rezie orchestrace = presne to, co monoliticka policy neplati vubec.
check("rezie orchestrace je soucet tri polozek", c["orchestration_s"], 40.1)
# Zbytek (start daemona, snimkovani, zapis na disk) se hlasi, ne zamlcuje.
check("zbytek se dopocita do celku", c["unaccounted_s"], 24.9)
check("celkovy cas behu se prenasi", c["run_s"], 100.0)

# Neuspesne volani je taky utrata — cas shorel, i kdyz odpoved neprisla.
c_fail = summarize_costs(
    [{"layer": LAYER_PLANNER, "purpose": "replan", "s": 60.0, "ok": False}], [], [], 60.0)
check("neuspesne volani se pocita do casu", c_fail["planner_s"], 60.0)
check("neuspesne volani se pocita do poctu", c_fail["planner_calls"], 1)

# Starsi zaznamy behu nemaji t_start/t_end — nesmi se domyslet nula delky.
c_old = summarize_costs([], [], [{"attempt": 1, "success": True}], 10.0)
check("krok bez casovych znacek se do skill_s nepocita", c_old["skill_s"], 0)
# Neznama vrstva (kdyby nekdo pridal dalsi) nespadne do zadneho ze dvou kbelicku.
c_other = summarize_costs([{"layer": "neco_jineho", "s": 5.0}], [], [], 5.0)
check("nezname vrstve se nepricte ani planovac, ani inspektor",
      (c_other["planner_calls"], c_other["inspector_calls"]), (0, 0))


# ── _chat: kazde volani modelu se zmeri a zapise ────────────────────────────
class FakeLM:
    """Podvrzeny klient LM Studia — nic nevolá po siti."""

    def __init__(self, reply: str = "ok", raises: Exception | None = None):
        self.reply = reply
        self.raises = raises
        self.seen: list[dict] = []

    def chat_with_images(self, **kwargs):
        self.seen.append(kwargs)
        if self.raises:
            raise self.raises
        return self.reply


def make_orch(lm: FakeLM) -> Orchestrator:
    o = Orchestrator({}, lambda *a, **k: None)
    o.lm = lm
    return o


lm = FakeLM(reply="REASONING: x\nSUCCESS")
orch = make_orch(lm)
out = orch._chat(LAYER_INSPECTOR, "verify_step", model="vlm-1",
                 user_prompt="p", images_b64=["aaa", "bbb"], temperature=0.1)
check("_chat vraci odpoved modelu beze zmeny", out, "REASONING: x\nSUCCESS")
check("_chat zapsal prave jedno volani", len(orch.llm_calls), 1)
rec = orch.llm_calls[0]
check("_chat zapsal vrstvu", rec["layer"], LAYER_INSPECTOR)
check("_chat zapsal ucel", rec["purpose"], "verify_step")
check("_chat zapsal model", rec["model"], "vlm-1")
check("_chat oznacil volani jako uspesne", rec["ok"], True)
check("_chat spocital snimky", rec["images"], 2)
check("_chat zmeril nezaporny cas", rec["s"] >= 0, True)
check("_chat zapsal absolutni cas zacatku", rec["t"] > 0, True)
# Argumenty musi projit beze zmeny — obalka nesmi tise prepsat teplotu ani prompt.
check("_chat preda argumenty klientovi", lm.seen[0]["user_prompt"], "p")
check("_chat nemeni teplotu", lm.seen[0]["temperature"], 0.1)

# Volani bez snimku (planovac bez obrazu) — nula, ne None.
orch2 = make_orch(FakeLM())
orch2._chat(LAYER_PLANNER, "replan", model="llm-1", user_prompt="p", images_b64=None)
check("volani bez snimku ma images = 0", orch2.llm_calls[0]["images"], 0)
# Prazdne retezce ve snimcich nejsou snimky.
orch3 = make_orch(FakeLM())
orch3._chat(LAYER_PLANNER, "replan", model="llm-1", user_prompt="p", images_b64=["", "x"])
check("prazdny snimek se nepocita", orch3.llm_calls[0]["images"], 1)

# Spadle volani: vyjimka musi projit ven (volajici se na ni spolehaji),
# ale cas uz shorel, takze se zaznamena jako neuspesne.
orch4 = make_orch(FakeLM(raises=RuntimeError("timeout")))
raised = False
try:
    orch4._chat(LAYER_PLANNER, "initial_plan", model="llm-1", user_prompt="p")
except RuntimeError:
    raised = True
check("_chat nepolyka vyjimku", raised, True)
check("spadle volani se presto zaznamena", len(orch4.llm_calls), 1)
check("spadle volani je oznacene ok=False", orch4.llm_calls[0]["ok"], False)

# Vic volani za sebou se scita, ne prepisuje.
orch5 = make_orch(FakeLM())
orch5._chat(LAYER_PLANNER, "initial_plan", model="llm-1", user_prompt="p")
orch5._chat(LAYER_INSPECTOR, "verify_step", model="vlm-1", user_prompt="p")
orch5._chat(LAYER_INSPECTOR, "goal_check", model="vlm-1", user_prompt="p")
summed = summarize_costs(orch5.llm_calls, [], [], 1.0)
check("posloupnost volani se secte do souhrnu",
      (summed["planner_calls"], summed["inspector_calls"]), (1, 2))

# ── _record_swap ────────────────────────────────────────────────────────────
orch6 = make_orch(FakeLM())
orch6._record_swap("grab", "preload", time.time())
check("_record_swap zapsal prave jeden prehoz", len(orch6.policy_swaps), 1)
check("_record_swap zapsal krok", orch6.policy_swaps[0]["step"], "grab")
check("_record_swap zapsal fazi", orch6.policy_swaps[0]["phase"], "preload")
check("_record_swap zmeril nezaporny cas", orch6.policy_swaps[0]["s"] >= 0, True)

print()
if failures:
    print(f"NEPROSLO: {len(failures)} pripadu")
    for f in failures:
        print("  ", f)
    sys.exit(1)
print("OK — ucetnictvi vrstev (souhrn, obalka volani, prehozy modelu) sedi.")
