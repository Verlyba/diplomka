#!/usr/bin/env python3
"""
Generate Red Bowl Visualizations & Movement Tracking for Last 10 Episodes (episodes 057 to 066).
Detects red bowl position, measures shift, and creates composite overlays and high-contrast visual prints.
"""

from pathlib import Path
import cv2
import numpy as np

FRAMES_DIR = Path(r"c:\Users\green\diplomka\exported_frames\diplomka_1\top")
OUT_DIR = Path(r"c:\Users\green\diplomka\exported_frames\visualizations")
OUT_DIR.mkdir(parents=True, exist_ok=True)

# Red color HSV range (wraps around 0 / 180 in OpenCV)
LOWER_RED1 = np.array([0, 50, 50])
UPPER_RED1 = np.array([12, 255, 255])
LOWER_RED2 = np.array([165, 50, 50])
UPPER_RED2 = np.array([180, 255, 255])

def get_red_bowl_data(files):
    data = []
    table_mask = np.zeros((480, 640), dtype=np.uint8)
    table_mask[40:460, 60:580] = 255

    for f in files:
        img = cv2.imread(str(f))
        hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
        m1 = cv2.inRange(hsv, LOWER_RED1, UPPER_RED1)
        m2 = cv2.inRange(hsv, LOWER_RED2, UPPER_RED2)
        mask = cv2.bitwise_or(m1, m2)
        mask = cv2.bitwise_and(mask, table_mask)

        cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        valid_cnts = [c for c in cnts if cv2.contourArea(c) > 5000]

        if valid_cnts:
            best_cnt = max(valid_cnts, key=cv2.contourArea)
            hull = cv2.convexHull(best_cnt)
            M = cv2.moments(hull)
            if M["m00"] > 0:
                cx = int(M["m10"] / M["m00"])
                cy = int(M["m01"] / M["m00"])
                
                bowl_mask = np.zeros(mask.shape, dtype=np.uint8)
                cv2.fillPoly(bowl_mask, [hull], 255)

                ep_num = f.stem.replace("episode_", "ep")
                data.append({
                    "file": f,
                    "ep_name": ep_num,
                    "img": img,
                    "mask": bowl_mask,
                    "cnt": best_cnt,
                    "hull": hull,
                    "center": (cx, cy),
                    "rect": cv2.boundingRect(hull)
                })
        else:
            print(f"Warning: No valid red bowl contour found in {f.name}")

    return data

def make_cutout_composite(base_img, items, opacity=1.0):
    result = base_img.copy()
    for item in items:
        mask = (item["mask"] > 0)
        if opacity >= 1.0:
            result[mask] = item["img"][mask]
        else:
            result[mask] = (result[mask] * (1.0 - opacity) + item["img"][mask] * opacity).astype(np.uint8)
    return result

def make_outline_composite(base_img, items):
    result = base_img.copy()
    num_items = len(items)
    for idx, item in enumerate(items):
        # Color gradient: Ep 57-59 Cyan (0, 255, 255), Ep 60-66 Yellow/Magenta (0, 165, 255 / 255, 0, 255)
        if idx < 3:
            color = (255, 255, 0) # Cyan
        else:
            color = (0, 165, 255) # Orange/Yellow
        cv2.polylines(result, [item["hull"]], True, color, 2)
        cx, cy = item["center"]
        cv2.putText(result, item["ep_name"], (cx - 15, cy), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 255), 1, cv2.LINE_AA)
    return result

def make_points_and_hull(base_img, items):
    result = base_img.copy()
    pts = np.array([item["center"] for item in items], dtype=np.int32)
    
    if len(pts) >= 3:
        hull = cv2.convexHull(pts)
        overlay = result.copy()
        cv2.fillPoly(overlay, [hull], (0, 0, 255))
        cv2.polylines(result, [hull], True, (0, 0, 200), 2)
        cv2.addWeighted(overlay, 0.25, result, 0.75, 0, result)

    for idx, item in enumerate(items):
        cx, cy = item["center"]
        color = (255, 255, 0) if idx < 3 else (0, 165, 255)
        cv2.circle(result, (cx, cy), 5, (0, 0, 0), -1)
        cv2.circle(result, (cx, cy), 3, color, -1)
        
    return result

def make_heatmap(base_img, items, radius=35):
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
    alpha = 0.5 * mask
    blended = (base_img * (1.0 - alpha) + heatmap * alpha).astype(np.uint8)
    return blended

def make_shift_zoom_analysis(items):
    """Zoomed in detail view around the red bowl with measured displacement vector."""
    if len(items) < 10:
        return None

    ep57 = items[0]
    ep66 = items[-1]
    
    # Base image from ep57
    img = ep57["img"].copy()
    
    # Draw Ep 057 boundary (Cyan) and Ep 066 boundary (Magenta)
    cv2.polylines(img, [ep57["hull"]], True, (255, 255, 0), 2)
    cv2.polylines(img, [ep66["hull"]], True, (255, 0, 255), 2)

    c57 = ep57["center"]
    c66 = ep66["center"]

    # Draw arrow from c57 to c66
    cv2.arrowedLine(img, c57, c66, (0, 255, 0), 2, tipLength=0.3)
    cv2.circle(img, c57, 4, (255, 255, 0), -1)
    cv2.circle(img, c66, 4, (255, 0, 255), -1)

    dx = c66[0] - c57[0]
    dy = c66[1] - c57[1]

    # Zoom in region around bowl: x:240..420, y:120..300
    rx1, rx2 = 230, 430
    ry1, ry2 = 120, 300
    crop = img[ry1:ry2, rx1:rx2].copy()
    
    # Scale 2x for sharp detail printout
    crop_scaled = cv2.resize(crop, (400, 360), interpolation=cv2.INTER_NEAREST)

    # Info overlay panel
    panel = np.zeros((360, 280, 3), dtype=np.uint8) + 30
    cv2.putText(panel, "RED BOWL SHIFT ANALYSIS", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 2)
    cv2.putText(panel, "-----------------------", (10, 45), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (150, 150, 150), 1)
    
    cv2.putText(panel, "Ep 057-059 (Cyan):", (10, 75), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 255, 0), 1)
    cv2.putText(panel, f"  Center: {c57}", (10, 95), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (200, 200, 200), 1)

    cv2.putText(panel, "Ep 060-066 (Magenta):", (10, 130), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (255, 0, 255), 1)
    cv2.putText(panel, f"  Center: {c66}", (10, 150), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (200, 200, 200), 1)

    cv2.putText(panel, "Displacement (dx, dy):", (10, 190), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 0), 1)
    cv2.putText(panel, f"  dx = {dx:+d} px (LEFT)", (10, 215), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)
    cv2.putText(panel, f"  dy = {dy:+d} px (UP)", (10, 240), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)

    dist = np.sqrt(dx**2 + dy**2)
    cv2.putText(panel, f"Total Shift: {dist:.2f} px", (10, 275), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 2)
    cv2.putText(panel, "Shift occurred at Ep 060", (10, 310), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (180, 180, 255), 1)

    combined = np.hstack([crop_scaled, panel])
    return combined

def make_grid_collage(items):
    """2x5 grid showing all 10 individual frames side by side with headers."""
    resized_imgs = []
    for item in items:
        im = item["img"].copy()
        cv2.putText(im, item["ep_name"], (15, 35), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0, 255, 255), 2)
        r = cv2.resize(im, (320, 240))
        resized_imgs.append(r)
        
    row1 = np.hstack(resized_imgs[:5])
    row2 = np.hstack(resized_imgs[5:])
    grid = np.vstack([row1, row2])
    return grid

def main():
    files = sorted(list(FRAMES_DIR.glob("*.png")))[-10:]
    if not files:
        print("No files found!")
        return

    items_last10 = get_red_bowl_data(files)
    base_img = items_last10[0]["img"].copy()
    print(f"Loaded {len(items_last10)} red bowl entries for last 10 episodes ({files[0].stem} to {files[-1].stem})")

    # 1. Solid Cutouts (100% visible solid red bowl)
    cv2.imwrite(str(OUT_DIR / "1_red_bowl_solid_last10.png"), make_cutout_composite(base_img, items_last10, opacity=1.0))

    # 2. Semi-transparent Cutouts (50% opacity)
    cv2.imwrite(str(OUT_DIR / "2_red_bowl_alpha50_last10.png"), make_cutout_composite(base_img, items_last10, opacity=0.5))

    # 3. Outlines & Boundaries
    cv2.imwrite(str(OUT_DIR / "3_red_bowl_outlines_last10.png"), make_outline_composite(base_img, items_last10))

    # 4. Centroid Points & Coverage Hull
    cv2.imwrite(str(OUT_DIR / "4_red_bowl_centroids_hull_last10.png"), make_points_and_hull(base_img, items_last10))

    # 5. Density Heatmap
    cv2.imwrite(str(OUT_DIR / "5_red_bowl_heatmap_last10.png"), make_heatmap(base_img, items_last10, radius=30))

    # 6. Zoomed Shift Measurement Analysis
    zoom_img = make_shift_zoom_analysis(items_last10)
    if zoom_img is not None:
        cv2.imwrite(str(OUT_DIR / "6_red_bowl_shift_measurement_zoom.png"), zoom_img)

    # 7. 2x5 Grid Collage of All 10 Frames
    grid_img = make_grid_collage(items_last10)
    cv2.imwrite(str(OUT_DIR / "7_last10_episodes_grid.png"), grid_img)

    print(f"\nAll red bowl visualizations generated successfully in {OUT_DIR}")

if __name__ == "__main__":
    main()
