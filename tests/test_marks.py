"""Test znackovani: obalime record_loop presne jako naostro a zahrajeme si na
LeRobota - epizody, prace s klavesami, znovunahrani epizody."""
import json
from pathlib import Path

import lerobot.scripts.lerobot_record as rec
import lerobot.utils.keyboard_input as keys
import record_with_marks as rwm

ROOT = Path("test_ds_root/local/pick_and_place")
ROOT.mkdir(parents=True, exist_ok=True)
marks_file = ROOT.parent / f"{ROOT.name}.marks.json"
if marks_file.exists():
    marks_file.unlink()

class FakeDataset:
    def __init__(self, root, fps=30):
        self.root, self.fps, self.num_episodes = str(root), fps, 0
    def add_frame(self, frame, *a, **k):
        return None

# telo epizody, ktere si nastavi kazdy test krok zvlast
body = {"fn": lambda ds: None}

def fake_record_loop(*a, **k):
    """Zastupuje skutecnou nahravaci smycku LeRobotu."""
    ds = k.get("dataset")
    if ds is not None:
        body["fn"](ds)

rec.record_loop = fake_record_loop
rec.main = lambda: None
rwm.main()                       # instaluje patche
record_loop = rec.record_loop    # <- nas obal
listener, events = rec.init_keyboard_listener()
press = rwm.make_dispatch(keys, events)

def frames(ds, n):
    for _ in range(n):
        ds.add_frame({})

ds = FakeDataset(ROOT)

# --- epizoda 0: 4.0 s znacka, 7.0 s znacka --------------------------------
def episode0(d):
    frames(d, 120); press("space")   # 120/30 = 4.0 s
    frames(d, 90);  press("m")       # 210/30 = 7.0 s
body["fn"] = episode0
record_loop(robot=None, events=events, fps=30, dataset=ds)
ds.num_episodes = 1

# --- epizoda 1: znacka, omyl, undo, spravna znacka ------------------------
def episode1(d):
    frames(d, 90);  press("space")   # 3.0 s
    frames(d, 30);  press("space")   # 4.0 s (omyl)
    press("u")                       # zpet
    frames(d, 120); press("space")   # 8.0 s
body["fn"] = episode1
record_loop(robot=None, events=events, fps=30, dataset=ds)
ds.num_episodes = 2

# --- epizoda 2: znacka, pak uzivatel epizodu zahodi (r) -------------------
def episode2_bad(d):
    frames(d, 60); press("space")    # 2.0 s
    press("r")                       # zahodit
body["fn"] = episode2_bad
record_loop(robot=None, events=events, fps=30, dataset=ds)
print("po 'r': rerecord =", events["rerecord_episode"], "| exit_early =", events["exit_early"])

# znovu nahrana epizoda 2 (stejny index!)
def episode2_good(d):
    frames(d, 150); press("space")   # 5.0 s
body["fn"] = episode2_good
record_loop(robot=None, events=events, fps=30, dataset=ds)

# --- ovladaci klavesy LeRobotu -------------------------------------------
events["exit_early"] = False
press("n"); print("po 'n': exit_early =", events["exit_early"])
press("q"); print("po 'q': stop_recording =", events["stop_recording"])

data = json.loads(marks_file.read_text(encoding="utf-8"))
print("\nznacky:", json.dumps(data, indent=2))
ok = data["episodes"] == {"0": [4.0, 7.0], "1": [3.0, 8.0], "2": [5.0]}
print("\nOCEKAVANO {'0': [4.0, 7.0], '1': [3.0, 8.0], '2': [5.0]} ->", "OK" if ok else "CHYBA")
assert ok, f"neocekavane znacky: {data['episodes']}"
