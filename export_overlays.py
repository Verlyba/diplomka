#!/usr/bin/env python3
"""
Generate composite overlays from exported top camera frames:
- 20 frames with 15% opacity blending
- 60 frames with 15% opacity blending
- 120 frames with 15% opacity blending
- Additional min/average composites to provide maximum visibility of the green cube.
"""

from pathlib import Path
import cv2
import numpy as np

FRAMES_DIR = Path(r"c:\Users\green\diplomka\exported_frames\diplomka_1\top")
OUTPUT_DIR = Path(r"c:\Users\green\diplomka\exported_frames\overlays")

def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    all_files = sorted(list(FRAMES_DIR.glob("*.png")))
    
    if not all_files:
        raise FileNotFoundError(f"No frames found in {FRAMES_DIR}")

    total_n = len(all_files)
    print(f"Loaded total of {total_n} frames from {FRAMES_DIR}")

    def blend_alpha15(files):
        imgs = [cv2.imread(str(f)).astype(np.float32) for f in files]
        res = imgs[0].copy()
        for img in imgs[1:]:
            res = res * (1.0 - 0.15) + img * 0.15
        return np.clip(res, 0, 255).astype(np.uint8)

    def blend_average(files):
        imgs = [cv2.imread(str(f)).astype(np.float32) for f in files]
        return np.clip(np.mean(imgs, axis=0), 0, 255).astype(np.uint8)

    def blend_min(files):
        imgs = [cv2.imread(str(f)) for f in files]
        return np.minimum.reduce(imgs)

    subsets = {}
    if total_n >= 20:
        subsets[20] = all_files[:20]
    if total_n >= 60:
        subsets[60] = all_files[:60]
    subsets[total_n] = all_files

    for n_count, files_subset in subsets.items():
        print(f"\n--- Blending {n_count} frames ---")
        img_alpha15 = blend_alpha15(files_subset)
        img_min = blend_min(files_subset)
        img_avg = blend_average(files_subset)

        cv2.imwrite(str(OUTPUT_DIR / f"top_{n_count}_episodes_alpha15.png"), img_alpha15)
        cv2.imwrite(str(OUTPUT_DIR / f"top_{n_count}_episodes_min_composite.png"), img_min)
        cv2.imwrite(str(OUTPUT_DIR / f"top_{n_count}_episodes_average.png"), img_avg)
        print(f"  [OK] Saved alpha15, min_composite, average for {n_count} episodes.")

    print(f"\nAll overlay composites generated successfully in {OUTPUT_DIR}")

if __name__ == "__main__":
    main()
