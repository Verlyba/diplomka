"""Vyrobi maly skutecny LeRobotDataset a rozreze ho podle znacek."""
import json, os, shutil
from pathlib import Path

HOME = Path("test_lerobot_home").resolve()
if HOME.exists():
    shutil.rmtree(HOME)
os.environ["HF_LEROBOT_HOME"] = str(HOME)

import numpy as np
from lerobot.datasets.lerobot_dataset import LeRobotDataset

FPS = 30
features = {
    "observation.state": {"dtype": "float32", "shape": (2,), "names": ["a", "b"]},
    "action": {"dtype": "float32", "shape": (2,), "names": ["a", "b"]},
}
ds = LeRobotDataset.create(repo_id="local/test_task", fps=FPS, features=features,
                           robot_type="so101_follower", use_videos=False)

# dve epizody po 300 snimcich (10 s)
for ep in range(2):
    for i in range(300):
        ds.add_frame({
            "observation.state": np.array([ep, i], dtype=np.float32),
            "action": np.array([ep, i], dtype=np.float32),
            "task": "vloz kostku do krabice",
        })
    ds.save_episode()
if hasattr(ds, "finalize"):
    ds.finalize()

marks = {"episodes": {"0": [4.0, 7.0], "1": [3.0, 8.0]}}
Path(HOME / "local" / "test_task.marks.json").write_text(json.dumps(marks), encoding="utf-8")
print("zdrojovy dataset:", len(ds), "snimku,", ds.meta.total_episodes, "epizod")
