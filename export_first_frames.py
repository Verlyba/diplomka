#!/usr/bin/env python3
"""
Export first frame of each episode for both cameras (top and wrist)
from LeRobot dataset 'local/diplomka_1' into separate folders.
Read-only access to dataset, does not alter any existing data.
"""

from pathlib import Path
import cv2

DATASET_ROOT = Path(r"C:\Users\green\.cache\huggingface\lerobot\local\diplomka_1")
OUTPUT_ROOT = Path(r"c:\Users\green\diplomka\exported_frames\diplomka_1")

CAMERAS = {
    "top": DATASET_ROOT / "videos" / "observation.images.top",
    "wrist": DATASET_ROOT / "videos" / "observation.images.wrist",
}

def main():
    print(f"Dataset root: {DATASET_ROOT}")
    print(f"Output root:  {OUTPUT_ROOT}")

    if not DATASET_ROOT.exists():
        raise FileNotFoundError(f"Dataset not found at {DATASET_ROOT}")

    for cam_name, cam_dir in CAMERAS.items():
        out_cam_dir = OUTPUT_ROOT / cam_name
        out_cam_dir.mkdir(parents=True, exist_ok=True)
        print(f"\n--- Processing camera: {cam_name} ---")

        video_files = sorted(list(cam_dir.rglob("*.mp4")))
        print(f"Found {len(video_files)} video files for camera '{cam_name}'")

        count = 0
        for video_path in video_files:
            # Determine episode index from filename, e.g. file-005.mp4 -> episode_005.png
            file_stem = video_path.stem # 'file-000'
            ep_num_str = file_stem.split("-")[-1] # '000'
            out_img_name = f"episode_{ep_num_str}.png"
            out_img_path = out_cam_dir / out_img_name

            cap = cv2.VideoCapture(str(video_path))
            if not cap.isOpened():
                print(f"  [ERROR] Could not open video: {video_path}")
                continue

            ret, frame = cap.read()
            cap.release()

            if not ret or frame is None:
                print(f"  [ERROR] Could not read first frame from: {video_path}")
                continue

            # cv2.imwrite expects BGR frame and writes PNG
            success = cv2.imwrite(str(out_img_path), frame)
            if success:
                count += 1
                if count <= 3 or count >= len(video_files) - 2 or count % 10 == 0:
                    print(f"  [OK] Saved {cam_name}/{out_img_name} (shape: {frame.shape})")
            else:
                print(f"  [ERROR] Failed writing image: {out_img_path}")

        print(f"Done camera '{cam_name}': {count} frames exported to {out_cam_dir}")

    print("\nExport completed successfully!")

if __name__ == "__main__":
    main()
