"""Vyrobi dilci datasety kroku a slouci je do cele ulohy (merge_datasets.py)."""
import json, os, shutil, subprocess, sys
from pathlib import Path

HOME = Path("test_lerobot_home").resolve()
if HOME.exists():
    shutil.rmtree(HOME)
os.environ["HF_LEROBOT_HOME"] = str(HOME)

import numpy as np
from lerobot.datasets.lerobot_dataset import LeRobotDataset

FPS = 30
FEATURES = {
    "observation.state": {"dtype": "float32", "shape": (2,), "names": ["a", "b"]},
    "action": {"dtype": "float32", "shape": (2,), "names": ["a", "b"]},
}
# krok -> pocet snimku v kazde ze dvou epizod
PLAN = {"grab_cube": 120, "move_to_box": 90, "release": 60}

for slug, n in PLAN.items():
    ds = LeRobotDataset.create(repo_id=f"local/test_task_{slug}", fps=FPS,
                               features=FEATURES, robot_type="so101_follower", use_videos=False)
    for ep in range(2):
        for i in range(n):
            ds.add_frame({"observation.state": np.array([ep, i], dtype=np.float32),
                          "action": np.array([ep, i], dtype=np.float32),
                          "task": slug.replace("_", " ")})
        ds.save_episode()
    if hasattr(ds, "finalize"):
        ds.finalize()
print("dilci datasety hotove:", PLAN)

# --- slouceni -> rozdeleni zpatky -> musi vyjit puvodni pocty --------------
PY = sys.executable
env = dict(os.environ, HF_LEROBOT_HOME=str(HOME))
run = lambda *a: subprocess.run([PY, *a], env=env, capture_output=True, text=True)

r = run("merge_datasets.py", "--task", "test_task",
        "--steps", "grab_cube,move_to_box,release", "--single-task", "vloz kostku")
assert r.returncode == 0, r.stderr[-2000:]

merged = LeRobotDataset("local/test_task")
expected_frames = sum(PLAN.values()) * 2
print(f"slouceno: {len(merged)} snimku (ceka se {expected_frames}), "
      f"{merged.meta.total_episodes} epizod")
assert len(merged) == expected_frames

marks = json.loads((HOME / "local" / "test_task.marks.json").read_text(encoding="utf-8"))
cuts = [PLAN["grab_cube"] / FPS, (PLAN["grab_cube"] + PLAN["move_to_box"]) / FPS]
print("svy:", marks["episodes"], "| cekane:", cuts)
assert all(v == cuts for v in marks["episodes"].values())

r = run("split_dataset.py", "--repo-id", "local/test_task",
        "--steps", "grab_cube,move_to_box,release")
assert r.returncode == 0, r.stderr[-2000:]
for slug, n in PLAN.items():
    ds = LeRobotDataset(f"local/test_task_{slug}")
    print(f"  po rozdeleni local/test_task_{slug}: {len(ds)} snimku (ceka se {n * 2})")
    assert len(ds) == n * 2
print("\nOK - slouceni i zpetne rozdeleni sedi na snimek")
