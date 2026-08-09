#!/usr/bin/env python3
"""
Derive per-step inference timeouts from the actual recorded demonstrations,
instead of guessing one flat number for every step.

Why this exists: a step gets cut off once its time limit runs out, regardless
of how long that phase actually took during teleoperation. A single flat number
picked to fit the shortest step (e.g. pick_cube) will routinely truncate a
longer one (e.g. release) before it finishes — the step then ends via "časový
limit" instead of protocol A/B, and steps cut off mid-motion are the most
common source of [object_missed] / [object_slipped] failures. This script reads
the same <dataset>.marks.json sidecar split_dataset.py uses and reports, per
step, the real distribution of durations across every recorded episode.

The values it writes (config.json → steps[].timeout_s) are the ONLY per-step
limits the orchestrator uses; when a step has none, it falls back to
episode_time_s. There is no global step-timeout setting any more.

Run inside the LeRobot environment (needs the dataset to read exact per-episode
lengths):

    python compute_step_timeouts.py --repo-id local/pick_and_place \
        --steps grab_cube,pick_cube,move,release

    # write the suggested values straight into config.json's steps[].timeout_s
    python compute_step_timeouts.py --repo-id local/pick_and_place \
        --steps grab_cube,pick_cube,move,release --apply

The suggestion is max(observed durations) * --margin, rounded up to the
nearest --round-to seconds, floored at --min-timeout. Max rather than
mean/percentile on purpose: with a handful of demonstrations there is no
reliable tail to estimate a percentile from, and a step that gets truncated
even once during inference is a bigger problem for the thesis's success-rate
measurement than a step that occasionally runs a couple of seconds longer
than it strictly needed to.
"""

from __future__ import annotations

import argparse
import json
import logging
import math
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("timeouts")

HERE = Path(__file__).resolve().parent
CONFIG_FILE = HERE / "config.json"


def lerobot_home() -> Path:
    try:
        from lerobot.utils.constants import HF_LEROBOT_HOME
        return Path(HF_LEROBOT_HOME)
    except ImportError:
        pass
    import os
    hf_home = Path(os.getenv("HF_HOME", Path.home() / ".cache" / "huggingface"))
    return Path(os.getenv("HF_LEROBOT_HOME", hf_home / "lerobot"))


def episode_length_s(meta, ep_idx: int, fps: float) -> float:
    """Real duration of one episode in seconds (not the nominal --episode_time_s)."""
    ep = meta.episodes[ep_idx]
    length = ep.get("length")
    if length:
        return float(length) / fps
    return 0.0


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Compute per-step inference timeouts from recorded demonstration durations")
    ap.add_argument("--repo-id", required=True, help="source dataset, e.g. local/pick_and_place")
    ap.add_argument("--steps", required=True, help="comma-separated step slugs, in order (same as split_dataset.py)")
    ap.add_argument("--marks", default="",
                    help="JSON file with per-episode boundaries (default: the sidecar "
                         "<dataset>.marks.json written by record_with_marks.py)")
    ap.add_argument("--root", default="", help="local dir of the source dataset (when not in HF_LEROBOT_HOME)")
    ap.add_argument("--margin", type=float, default=1.25,
                    help="safety multiplier applied to the longest observed duration (default 1.25)")
    ap.add_argument("--min-timeout", type=float, default=3.0,
                    help="never suggest less than this many seconds (default 3.0)")
    ap.add_argument("--round-to", type=float, default=0.5,
                    help="round suggestions up to this granularity in seconds (default 0.5)")
    ap.add_argument("--apply", action="store_true",
                    help="write the suggested timeout_s into config.json's steps[] (matched by slug); "
                         "without this flag the script only prints the table")
    args = ap.parse_args()

    step_slugs = [s.strip() for s in args.steps.split(",") if s.strip()]
    if len(step_slugs) < 2:
        log.error("At least two steps are needed, got %d", len(step_slugs))
        return 2
    n_boundaries = len(step_slugs) - 1

    from lerobot.datasets.lerobot_dataset import LeRobotDataset

    log.info("Loading %s ...", args.repo_id)
    src = LeRobotDataset(args.repo_id, root=args.root) if args.root else LeRobotDataset(args.repo_id)
    fps = float(src.fps)

    marks_path = Path(args.marks) if args.marks else None
    if marks_path is None:
        root = Path(str(src.root))
        candidate = root.parent / f"{root.name}.marks.json"
        if candidate.exists():
            marks_path = candidate
            log.info("Using marks recorded during recording: %s", candidate)
    if marks_path is None or not marks_path.exists():
        log.error("No marks file found. Pass --marks explicitly, or record with "
                   "record_with_marks.py so a sidecar marks file exists.")
        return 2

    raw = json.loads(marks_path.read_text(encoding="utf-8")).get("episodes", {})
    per_episode = {int(k): sorted(float(t) for t in v) for k, v in raw.items()}

    durations: dict[str, list[float]] = {slug: [] for slug in step_slugs}
    skipped: list[int] = []
    for ep_idx, boundaries in sorted(per_episode.items()):
        if len(boundaries) != n_boundaries:
            skipped.append(ep_idx)
            continue
        ep_len = episode_length_s(src.meta, ep_idx, fps)
        if ep_len <= 0:
            skipped.append(ep_idx)
            continue
        cuts = [0.0, *boundaries, ep_len]
        for slug, start, end in zip(step_slugs, cuts, cuts[1:]):
            dur = end - start
            if dur > 0:
                durations[slug].append(dur)

    if skipped:
        log.warning("Skipped episodes without exactly %d marks or unknown length: %s",
                    n_boundaries, skipped)

    def round_up(x: float) -> float:
        return math.ceil(x / args.round_to) * args.round_to

    suggestions: dict[str, float] = {}
    log.info("%-14s %6s %6s %6s %6s   %s", "step", "n", "mean", "median", "max", "suggested timeout_s")
    for slug in step_slugs:
        vals = durations[slug]
        if not vals:
            log.warning("%-14s no episodes had usable marks for this step — leaving unset", slug)
            continue
        vals_sorted = sorted(vals)
        n = len(vals_sorted)
        mean = sum(vals_sorted) / n
        median = vals_sorted[n // 2] if n % 2 else (vals_sorted[n // 2 - 1] + vals_sorted[n // 2]) / 2
        longest = vals_sorted[-1]
        suggested = max(round_up(longest * args.margin), args.min_timeout)
        suggestions[slug] = suggested
        log.info("%-14s %6d %6.2f %6.2f %6.2f   %6.1f", slug, n, mean, median, longest, suggested)

    if not args.apply:
        log.info("Dry run — nothing written. Re-run with --apply to update config.json.")
        return 0

    if not CONFIG_FILE.exists():
        log.error("%s does not exist — nothing to update.", CONFIG_FILE)
        return 2
    cfg = json.loads(CONFIG_FILE.read_text(encoding="utf-8-sig"))
    changed = False
    for step in cfg.get("steps", []):
        slug = step.get("slug")
        if slug in suggestions:
            old = step.get("timeout_s")
            step["timeout_s"] = suggestions[slug]
            if old != suggestions[slug]:
                log.info("config.json: %s.timeout_s %s -> %s", slug, old, suggestions[slug])
                changed = True
    if changed:
        CONFIG_FILE.write_text(json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8")
        log.info("Written to %s", CONFIG_FILE)
    else:
        log.info("config.json already matched the suggestions — nothing changed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
