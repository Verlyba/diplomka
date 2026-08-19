#!/usr/bin/env python3
"""
Opačný postup než split_dataset.py: z dílčích nahrávek kroků poskládá dataset
celé úlohy, ze kterého se trénuje monolitický baseline.

Kdy se to hodí: když se ti při teleoperaci nechce mačkat mezerník, nahraješ
každý krok jako samostatný dataset a celou úlohu z nich složíš tímhle.

    python merge_datasets.py --task pick_and_place ^
        --steps grab_cube,move_to_box,release

Vstup:  local/<úloha>_<krok> pro každý krok (přesně jak je nahraje setup)
Výstup: local/<úloha>            — epizoda i vznikne spojením epizody i všech kroků
        local/<úloha>.marks.json — hranice spojů (aby šel dataset zase rozdělit
                                   a aby bylo v datech vidět, kde švy jsou)

DŮLEŽITÉ pro férovost porovnání: epizoda i každého kroku musí navazovat na
epizodu i kroku předchozího — tedy nahrávat po epizodách (krok 1, 2, 3, pak
další epizoda), ne všechny epizody kroku 1 najednou. Jinak baseline dostane
„epizody", které v realitě nikdy takhle po sobě nešly.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import sys
import tempfile
from pathlib import Path

import numpy as np

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("merge")

AUTO_FEATURES = {"timestamp", "frame_index", "episode_index", "index", "task_index"}


def atomic_write_text(path: Path, text: str, encoding: str = "utf-8") -> None:
    """Write `text` to `path` via a temp-file + os.replace() so a crash or
    kill mid-write can never leave a truncated/corrupt sidecar behind."""
    path = Path(path)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        # mkstemp() creates the temp file mode 0600 (owner-only); os.replace()
        # would carry that onto the target, silently making it less readable
        # than the plain write_text() it replaces (which follows the umask).
        os.chmod(tmp_name, 0o644)
        with os.fdopen(fd, "w", encoding=encoding) as f:
            f.write(text)
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


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
    arr = value.numpy() if hasattr(value, "numpy") else np.asarray(value)
    if arr.ndim == 3 and arr.shape[0] in (1, 3, 4) and arr.shape[0] < arr.shape[-1]:
        arr = np.transpose(arr, (1, 2, 0))
    if arr.dtype != np.uint8:
        arr = (np.clip(arr, 0.0, 1.0) * 255.0).round().astype(np.uint8)
    return np.ascontiguousarray(arr)


def main() -> int:
    ap = argparse.ArgumentParser(description="Join per-step datasets into one full-task dataset")
    ap.add_argument("--task", required=True, help="slug úlohy, např. pick_and_place")
    ap.add_argument("--steps", required=True, help="slugy kroků v pořadí, oddělené čárkou")
    ap.add_argument("--single-task", default="", help="textová anotace úlohy (výchozí: slug s mezerami)")
    args = ap.parse_args()

    step_slugs = [s.strip() for s in args.steps.split(",") if s.strip()]
    if len(step_slugs) < 2:
        log.error("Potřeba aspoň dva kroky, dostal jsem %d", len(step_slugs))
        return 2
    task_text = args.single_task or args.task.replace("_", " ")

    from lerobot.datasets.lerobot_dataset import LeRobotDataset

    # ── Načtení dílčích datasetů a kontrola, že jdou spojit ──────────────
    sources = []
    for slug in step_slugs:
        repo_id = f"local/{args.task}_{slug}"
        log.info("Načítám %s ...", repo_id)
        sources.append(LeRobotDataset(repo_id))

    fps = int(sources[0].fps)
    features = {k: v for k, v in sources[0].meta.features.items() if k not in AUTO_FEATURES}
    image_keys = {k for k, v in features.items() if v.get("dtype") in ("video", "image")}

    for slug, src in zip(step_slugs[1:], sources[1:]):
        if int(src.fps) != fps:
            log.error("Krok '%s' má fps=%d, první krok %d — spojit nejdou.", slug, int(src.fps), fps)
            return 2
        other = {k for k in src.meta.features if k not in AUTO_FEATURES}
        if other != set(features):
            log.error("Krok '%s' má jiné featury než první krok (%s vs %s).",
                      slug, sorted(other), sorted(features))
            return 2

    counts = [src.meta.total_episodes for src in sources]
    episodes = min(counts)
    if len(set(counts)) != 1:
        log.warning("Kroky mají různý počet epizod %s — spojím jich %d a zbytek zahodím.",
                    dict(zip(step_slugs, counts)), episodes)
    if episodes == 0:
        log.error("Některý krok nemá žádnou epizodu.")
        return 2

    # ── Index snímků po epizodách, ať se nemusí datasety procházet vícekrát ──
    def frames_by_episode(src) -> dict[int, list[int]]:
        out: dict[int, list[int]] = {}
        for idx in range(len(src)):
            ep = int(src[idx]["episode_index"])
            out.setdefault(ep, []).append(idx)
        return out

    indices = [frames_by_episode(src) for src in sources]

    # ── Cílový dataset ───────────────────────────────────────────────────
    target_repo = f"local/{args.task}"
    target_dir = lerobot_home() / target_repo
    if target_dir.exists():
        log.info("Přepisuji %s", target_dir)
        shutil.rmtree(target_dir)
    dst = LeRobotDataset.create(
        repo_id=target_repo, fps=fps, features=features,
        robot_type=getattr(sources[0].meta, "robot_type", None),
        use_videos=bool(image_keys))

    marks: dict[str, list[float]] = {}
    total_frames = 0

    for ep in range(episodes):
        frames_written = 0
        boundaries: list[float] = []
        for step_i, (src, index) in enumerate(zip(sources, indices)):
            rows = index.get(ep, [])
            if not rows:
                log.warning("Krok '%s' nemá epizodu %d — vynechána.", step_slugs[step_i], ep)
            else:
                for idx in rows:
                    item = src[idx]
                    frame = {k: (to_image(item[k]) if k in image_keys else
                                 (item[k].numpy() if hasattr(item[k], "numpy") else np.asarray(item[k])))
                             for k in features}
                    frame["task"] = task_text
                    dst.add_frame(frame)
                    frames_written += 1
            # Hranice = konec tohohle kroku (u posledního kroku se nezapisuje).
            # Vždy se připíše, i když tenhle krok epizodu neměl (nulová délka
            # segmentu) — jinak by pro tuhle epizodu vzniklo míň hranic než
            # kroků a split_dataset.py by ji celou zahodil kvůli neshodě počtu.
            if step_i < len(sources) - 1:
                boundaries.append(round(frames_written / fps, 3))
        dst.save_episode()
        marks[str(ep)] = boundaries
        total_frames += frames_written
        log.info("Epizoda %d: %d snímků, spoje v %s s", ep, frames_written, boundaries)

    if hasattr(dst, "finalize"):
        try:
            dst.finalize()
        except Exception as e:
            log.warning("finalize selhalo: %s", e)

    # Sidecar se švy — stejný formát, jaký píše record_with_marks.py, takže
    # split_dataset.py umí dataset rozdělit zpátky na tytéž kroky.
    marks_path = target_dir.parent / f"{target_dir.name}.marks.json"
    atomic_write_text(marks_path, json.dumps({"episodes": marks}, indent=2))

    log.info("Hotovo: %s — %d epizod, %d snímků. Hranice uloženy do %s",
             target_repo, episodes, total_frames, marks_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
