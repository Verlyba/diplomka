"""Inspektor se na jeden snimek ptá jednou (Orchestrator._verify).

Do 2026-09-26 se po [unclear] porizoval novy snimek a VLM se ptal podruhe. Test hlida,
ze se to uz nedeje: jedno volani modelu, zadny dotaz na nove snimky do daemona, a
[unclear] se vraci nezmeneny (rozhodne o nem pravidlo `uncertain`).

Nepotrebuje robota ani LM Studio — model i daemon jsou zastupci.

    python tests/test_verify_single_pass.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from orchestrator import Orchestrator, UNCLEAR_TAG

failures = []


def check(name, cond, detail=""):
    print(("ok   " if cond else "CHYBA"), name, ("" if cond else f"  -> {detail}"))
    if not cond:
        failures.append(name)


class FakeDaemon:
    last_load = None
    last_baseline = None
    load_ever_nonzero = False

    def __init__(self):
        self.snapshots = 0

    def snapshot(self, *a, **k):
        self.snapshots += 1
        return ["NOVY_SNIMEK"]


def make(reply):
    cfg = {"task_slug": "t", "task_description": "d", "vlm_model": "fake-vlm",
           "steps": [{"slug": "catch_cube", "description": "chyt kostku", "grasp": True}]}
    orch = Orchestrator(cfg, lambda *a, **k: None)
    orch.calls = []
    orch.daemon = FakeDaemon()

    def fake_chat(layer, purpose, **kw):
        orch.calls.append((layer, purpose, list(kw["images_b64"])))
        return reply

    orch._chat = fake_chat
    return orch


for reply, label in (("The photo is ambiguous.\n[unclear]", "nejasny verdikt"),
                     ("The cube is in the gripper.\nSUCCESS", "jasny verdikt")):
    orch = make(reply)
    ok, tag, why, used = orch._verify("catch_cube", ["STARY_SNIMEK"], plan=["catch_cube"], step_index=0, stop_reason="")
    check(f"{label}: prave jedno volani modelu", len(orch.calls) == 1, orch.calls)
    check(f"{label}: ucel volani je verify_step (ne resnapshot)", orch.calls[0][1] == "verify_step", orch.calls[0][1])
    check(f"{label}: nekdo se neptal daemona na novy snimek", orch.daemon.snapshots == 0, orch.daemon.snapshots)
    check(f"{label}: vraci se puvodni snimek", used == ["STARY_SNIMEK"], used)

orch = make("Cannot tell.\n[unclear]")
ok, tag, why, used = orch._verify("catch_cube", ["A"], plan=["catch_cube"], step_index=0, stop_reason="")
check("[unclear] se vraci beze zmeny a jako neuspech", (ok, tag) == (False, UNCLEAR_TAG), (ok, tag))

print()
if failures:
    print(f"NEPROSLO: {len(failures)} pripadu")
    sys.exit(1)
print("OK — inspektor se ptá jednou.")
