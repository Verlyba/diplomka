#!/usr/bin/env python3
"""
Advanced Green Cube Visualizations for 20, 60, and all 120 episodes:
1. Solid Cutout Overlay (all green cubes pasted at 100% saturation onto a crisp base image)
2. Semi-transparent Overlay (50% opacity cubes on clean background)
3. Centroid Points & Workspace Region (Scatter points + Convex Hull polygon)
4. Heatmap Density Map (Gaussian density heatmap overlaid on table)
5. Outlines / Bounding Contours
"""

from pathlib import Path
import cv2
import numpy as np

FRAMES_DIR = Path(r"c:\Users\green\diplomka\exported_frames\diplomka_1\top")
OUT_DIR = Path(r"c:\Users\green\diplomka\exported_frames\visualizations")
OUT_DIR.mkdir(parents=True, exist_ok=True)

# Wider HSV threshold to capture all lighting variations, specular reflections, and shadows
LOWER_GREEN = np.array([20, 20, 30])
UPPER_GREEN = np.array([95, 255, 255])

def get_cube_data(files):
    data = []
    for f in files:
        img = cv2.imread(str(f))
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        mask = cv2.inRange(hsv, LOWER_GREEN, UPPER_GREEN)
        
        # Restrict to workspace table bounds (eliminates edge artifacts/carpet)
        table_mask = np.zeros_like(mask)
        table_mask[40:435, 80:520] = 255
        mask = cv2.bitwise_and(mask, table_mask)
        
        cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        valid_cnts = [c for c in cnts if cv2.contourArea(c) > 300]
        
        if valid_cnts:
            best_cnt = max(valid_cnts, key=cv2.contourArea)
            # Convex hull guarantees complete cube geometry without missing chunks or holes
            hull = cv2.convexHull(best_cnt)
            
            M = cv2.moments(hull)
            if M["m00"] > 0:
                cx = int(M["m10"] / M["m00"])
                cy = int(M["m01"] / M["m00"])
                
                cube_mask = np.zeros(mask.shape, dtype=np.uint8)
                cv2.fillPoly(cube_mask, [hull], 255)
                
                data.append({
                    "file": f,
                    "img": img,
                    "mask": cube_mask,
                    "cnt": best_cnt,
                    "hull": hull,
                    "center": (cx, cy),
                    "rect": cv2.boundingRect(hull)
                })
        else:
            print(f"Warning: No valid cube contour found in {f.name}")
            
    return data

def make_cutout_composite(base_img, items, opacity=1.0):
    """Pastes only the solid green cube pixels from each frame onto the clean base frame."""
    result = base_img.copy()
    for item in items:
        cube_mask = (item["mask"] > 0)
        if opacity >= 1.0:
            result[cube_mask] = item["img"][cube_mask]
        else:
            result[cube_mask] = (result[cube_mask] * (1.0 - opacity) + item["img"][cube_mask] * opacity).astype(np.uint8)
    return result

def make_outline_composite(base_img, items, color=(0, 255, 255), thickness=2):
    """Draws crisp colored contours around each cube position."""
    result = base_img.copy()
    for item in items:
        cv2.polylines(result, [item["hull"]], True, color, thickness)
    return result

def make_points_and_hull(base_img, items):
    """Draws centroid markers, labels, and the convex boundary of all positions."""
    result = base_img.copy()
    pts = np.array([item["center"] for item in items], dtype=np.int32)
    
    # Draw convex hull region of all cube positions
    if len(pts) >= 3:
        hull = cv2.convexHull(pts)
        overlay = result.copy()
        cv2.fillPoly(overlay, [hull], (0, 255, 0))
        cv2.polylines(result, [hull], True, (0, 200, 0), 2)
        cv2.addWeighted(overlay, 0.25, result, 0.75, 0, result)

    # Draw centroid points with high contrast
    for cx, cy in pts:
        cv2.circle(result, (cx, cy), 5, (0, 0, 0), -1)      # outer black ring
        cv2.circle(result, (cx, cy), 3, (0, 255, 0), -1)    # green inner
        cv2.circle(result, (cx, cy), 1, (255, 255, 255), -1)# white center dot
        
    return result

def make_heatmap(base_img, items, radius=25):
    """Generates a smooth density heatmap of cube positions."""
    h, w = base_img.shape[:2]
    density = np.zeros((h, w), dtype=np.float32)
    
    for item in items:
        cx, cy = item["center"]
        y, x = np.ogrid[:h, :w]
        dist_sq = (x - cx)**2 + (y - cy)**2
        gaussian = np.exp(-dist_sq / (2 * (radius ** 2)))
        density += gaussian
        
    if density.max() > 0:
        density = density / density.max()
        
    density_uint8 = (density * 255).astype(np.uint8)
    heatmap = cv2.applyColorMap(density_uint8, cv2.COLORMAP_JET)
    
    mask = (density_uint8 > 10).astype(np.float32)[:, :, np.newaxis]
    alpha = 0.55 * mask
    blended = (base_img * (1.0 - alpha) + heatmap * alpha).astype(np.uint8)
    return blended

def main():
    files = sorted(list(FRAMES_DIR.glob("*.png")))
    if not files:
        print("No files found!")
        return

    items_all = get_cube_data(files)
    total_n = len(items_all)
    base_img = items_all[0]["img"].copy()
    print(f"Processed total of {total_n} cubes from {FRAMES_DIR}")

    subsets = {}
    if total_n >= 20:
        subsets[20] = items_all[:20]
    if total_n >= 60:
        subsets[60] = items_all[:60]
    subsets[total_n] = items_all

    for n_count, subset in subsets.items():
        print(f"\n--- Generating visualizations for {n_count} cubes ---")
        # 1. Solid Cutouts (100% visible solid cubes)
        cv2.imwrite(str(OUT_DIR / f"1_solid_cubes_{n_count}.png"), make_cutout_composite(base_img, subset, opacity=1.0))
        # 2. Semi-transparent Cutouts (50% opacity)
        cv2.imwrite(str(OUT_DIR / f"2_alpha50_cubes_{n_count}.png"), make_cutout_composite(base_img, subset, opacity=0.5))
        # 3. Points & Coverage Area (Convex Hull)
        cv2.imwrite(str(OUT_DIR / f"3_points_and_area_{n_count}.png"), make_points_and_hull(base_img, subset))
        # 4. Density Heatmap
        cv2.imwrite(str(OUT_DIR / f"4_density_heatmap_{n_count}.png"), make_heatmap(base_img, subset, radius=25))
        # 5. Outlines / Contours
        cv2.imwrite(str(OUT_DIR / f"5_cube_outlines_{n_count}.png"), make_outline_composite(base_img, subset, color=(0, 255, 255), thickness=2))
        print(f"  [OK] Saved all 5 variants for {n_count} cubes.")

    print(f"\nAll visualizations regenerated successfully in {OUT_DIR}")

if __name__ == "__main__":
    main()
