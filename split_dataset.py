#!/usr/bin/env python3
"""
Cut one recorded LeRobot dataset into per-step sub-datasets.

Why this exists (thesis methodology): the monolithic ACT baseline trains on the
FULL recording, the orchestration's per-step policies train on slices of the
SAME recording. Neither side gets extra demonstrations, so the difference in
results is a difference between the two schemes, not between two datasets.

Run it inside the LeRobot environment (it imports lerobot, nothing else from
this project):

    # boundaries marked live during recording (record_with_marks.py) — the
    # sidecar file next to the dataset is found automatically:
    python split_dataset.py --repo-id local/pick_and_place ^
        --steps grab_cube,move_to_box,release

    # or one set of boundaries applied to every episode:
    python split_dataset.py --repo-id local/pick_and_place ^
        --steps grab_cube,move_to_box,release --boundaries 4.0,9.0

Marks file format — seconds from the start of each episode:

    {"episodes": {"0": [4.1, 9.3], "1": [3.8, 8.9]}}

Output: local/<task>/<step> for every step, replacing existing directories.
"""

from __future__ import annotations

import argparse
import bisect
import json
import logging
import shutil
import sys
from pathlib import Path

import numpy as np

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("split")

# Columns LeRobotDataset manages itself — never copied over
AUTO_FEATURES = {"timestamp", "frame_index", "episode_index", "index", "task_index"}


def lerobot_home() -> Path:
    try:
        from lerobot.utils.constants import HF_LEROBOT_HOME
        return Path(HF_LEROBOT_HOME)
    except ImportError:
        pass
    import os
    hf_home = Path(os.getenv("HF_HOME", Path.home() / ".cache" / "huggingface"))
    return Path(os.getenv("HF_LEROBOT_HOME", hf_home / "lerobot"))


def to_image(value) -> np.ndarray:
    """Decoded video frame (torch CHW float) -> HWC uint8."""
    arr = value.numpy() if hasattr(value, "numpy") else np.asarray(value)
    if arr.ndim == 3 and arr.shape[0] in (1, 3, 4) and arr.shape[0] < arr.shape[-1]:
        arr = np.transpose(arr, (1, 2, 0))
    if arr.dtype != np.uint8:
        arr = (np.clip(arr, 0.0, 1.0) * 255.0).round().astype(np.uint8)
    return np.ascontiguousarray(arr)


def main() -> int:
    ap = argparse.ArgumentParser(description="Split a LeRobot dataset into per-step datasets")
    ap.add_argument("--repo-id", required=True, help="source dataset, e.g. local/pick_and_place")
    ap.add_argument("--steps", required=True, help="comma-separated step slugs, in order")
    ap.add_argument("--boundaries", default="", help="comma-separated seconds, same for every episode")
    ap.add_argument("--marks", default="",
                    help="JSON file with per-episode boundaries (default: the sidecar "
                         "<dataset>.marks.json written by record_with_marks.py)")
    ap.add_argument("--root", default="", help="local dir of the source dataset (when not in HF_LEROBOT_HOME)")
    args = ap.parse_args()

    step_slugs = [s.strip() for s in args.steps.split(",") if s.strip()]
    if len(step_slugs) < 2:
        log.error("At least two steps are needed, got %d", len(step_slugs))
        return 2
    n_boundaries = len(step_slugs) - 1

    shared: list[float] = []
    if args.boundaries:
        shared = sorted(float(x) for x in args.boundaries.split(",") if x.strip())
    from lerobot.datasets.lerobot_dataset import LeRobotDataset

    log.info("Loading %s ...", args.repo_id)
    src = LeRobotDataset(args.repo_id, root=args.root) if args.root else LeRobotDataset(args.repo_id)

    # Marks: explicit file, else the sidecar record_with_marks.py leaves next to
    # the dataset, else the shared --boundaries.
    marks_path = Path(args.marks) if args.marks else None
    if marks_path is None and not shared:
        root = Path(str(src.root))
        candidate = root.parent / f"{root.name}.marks.json"
        if candidate.exists():
            marks_path = candidate
            log.info("Using marks recorded during recording: %s", candidate)

    per_episode: dict[int, list[float]] = {}
    if marks_path:
        raw = json.loads(marks_path.read_text(encoding="utf-8")).get("episodes", {})
        per_episode = {int(k): sorted(float(t) for t in v) for k, v in raw.items()}
    if not shared and not per_episode:
        log.error("No boundaries: pass --boundaries, or --marks, or record with "
                  "record_with_marks.py so a sidecar marks file exists.")
        return 2
    fps = int(src.fps)
    features = {k: v for k, v in src.meta.features.items() if k not in AUTO_FEATURES}
    image_keys = {k for k, v in features.items() if v.get("dtype") in ("video", "image")}
    parent = args.repo_id.split("/")[-1]
    log.info("Source: %d frames, %d episodes, fps=%d", len(src), src.meta.total_episodes, fps)

    home = lerobot_home()
    targets = []
    for slug in step_slugs:
        # One slash only: the hub validates repo_id as `namespace/name`, so
        # `local/<task>/<step>` would be rejected at dataset creation.
        repo_id = f"local/{parent}_{slug}"
        target_dir = home / repo_id
        if target_dir.exists():
            log.info("Replacing %s", target_dir)
            shutil.rmtree(target_dir)
        targets.append(LeRobotDataset.create(
            repo_id=repo_id, fps=fps, features=features,
            robot_type=getattr(src.meta, "robot_type", None),
            use_videos=bool(image_keys)))

    stats = {slug: {"episodes": 0, "frames": 0} for slug in step_slugs}
    skipped: list[int] = []
    current_ep, current_seg, seg_frames = -1, -1, 0

    def close_segment() -> None:
        nonlocal seg_frames
        if current_seg >= 0 and seg_frames > 0:
            targets[current_seg].save_episode()
            stats[step_slugs[current_seg]]["episodes"] += 1
            stats[step_slugs[current_seg]]["frames"] += seg_frames
        seg_frames = 0

    total = len(src)
    for idx in range(total):
        item = src[idx]
        ep = int(item["episode_index"])
        ts = float(item["timestamp"])

        if ep != current_ep:
            close_segment()
            current_ep, current_seg = ep, -1

        boundaries = per_episode.get(ep, shared)
        if len(boundaries) != n_boundaries:
            if ep not in skipped:
                skipped.append(ep)
                log.warning("Skipping episode %d: %d boundaries (expected %d)",
                            ep, len(boundaries), n_boundaries)
            continue

        seg = min(bisect.bisect_right(boundaries, ts), len(step_slugs) - 1)
        if seg != current_seg:
            close_segment()
            current_seg = seg

        frame = {k: (to_image(item[k]) if k in image_keys else
                     (item[k].numpy() if hasattr(item[k], "numpy") else np.asarray(item[k])))
                 for k in features}
        frame["task"] = step_slugs[seg].replace("_", " ")
        targets[seg].add_frame(frame)
        seg_frames += 1

        if idx % 500 == 0:
            log.info("frame %d/%d (episode %d, segment %d)", idx, total, ep, seg)

    close_segment()
    for dst in targets:
        if hasattr(dst, "finalize"):
            try:
                dst.finalize()
            except Exception as e:
                log.warning("finalize failed for %s: %s", dst.repo_id, e)

    log.info("Done: %s", json.dumps(stats, ensure_ascii=False))
    if skipped:
        log.warning("Skipped episodes without complete boundaries: %s", skipped)
    return 0


if __name__ == "__main__":
    sys.exit(main())
