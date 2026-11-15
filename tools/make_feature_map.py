#!/usr/bin/env python3
"""Feature maps (top-down views) from a MetaCam Lite recording. Needs only numpy + opencv.

Uses Preview.pcd (registered, coloured point cloud), odom-realtime.csv and the occupancy map
in <recording>/map2d (so every image shares the map's pixel grid and metre coordinates).

  python3 make_feature_map.py <recording_dir> [--scale 2] [--ceiling 2.5]

Writes <recording>/map_features/:
  features_rgb.png      camera colours seen from above (ceiling removed)
  features_height.png   height above floor (colour-coded), ceiling removed
  features_overlay.png  occupancy map + obstacles by height class + path + metre grid + legend
  Preview.ply           the same cloud as PLY (opens in CloudCompare, which has no PCD reader)
"""
import argparse, os, re
import numpy as np, cv2


def load_pcd(path):
    with open(path, "rb") as f:
        n = 0
        while True:
            l = f.readline()
            if l.startswith(b"POINTS"): n = int(l.split()[1])
            if l.startswith(b"DATA"): break
        a = np.frombuffer(f.read(), dtype=np.float32).reshape(-1, 4)
    a = a[np.isfinite(a[:, :3]).all(1)]
    rgb = a[:, 3].view(np.uint32)
    col = np.stack([rgb & 255, (rgb >> 8) & 255, (rgb >> 16) & 255], 1).astype(np.uint8)  # BGR for opencv
    return a[:, :3].copy(), col


def floor_height(z):
    h, e = np.histogram(z, bins=np.arange(z.min(), z.max() + 0.1, 0.1))
    ok = np.where(h >= 0.25 * h.max())[0]
    i = ok[0]                                   # lowest strong peak = floor
    j = i + np.argmax(h[i:i + 4])
    return e[j] + 0.05


def main():
    p = argparse.ArgumentParser(); p.add_argument("rec")
    p.add_argument("--scale", type=int, default=2); p.add_argument("--ceiling", type=float, default=2.5)
    a = p.parse_args(); rec = a.rec.rstrip("/"); S = a.scale
    y = open(f"{rec}/map2d/map.yaml").read()
    res = float(re.search(r"resolution:\s*([\d.]+)", y).group(1))
    ox, oy = map(float, re.search(r"origin:\s*\[([-\d.]+),\s*([-\d.]+)", y).groups())
    grid = cv2.imread(f"{rec}/map2d/map.pgm", 0); H, W = grid.shape
    xyz, col = load_pcd(f"{rec}/Preview.pcd")
    fl = floor_height(xyz[:, 2]); h = xyz[:, 2] - fl
    print(f"{len(xyz)} points, floor z = {fl:.2f} m, top z = {h.max():.1f} m above floor")
    out = f"{rec}/map_features"; os.makedirs(out, exist_ok=True)

    # --- PLY for CloudCompare
    v = np.empty(len(xyz), [("x", "<f4"), ("y", "<f4"), ("z", "<f4"), ("r", "u1"), ("g", "u1"), ("b", "u1")])
    v["x"], v["y"], v["z"] = xyz.T; v["r"], v["g"], v["b"] = col[:, 2], col[:, 1], col[:, 0]
    with open(f"{out}/Preview.ply", "wb") as f:
        f.write((f"ply\nformat binary_little_endian 1.0\nelement vertex {len(v)}\nproperty float x\nproperty float y\n"
                 "property float z\nproperty uchar red\nproperty uchar green\nproperty uchar blue\nend_header\n").encode())
        f.write(v.tobytes())

    # --- project into the map grid (scaled), image row 0 = max y like the occupancy map
    keep = (h > -0.15) & (h < a.ceiling)
    xyz, col, h = xyz[keep], col[keep], h[keep]
    cx = ((xyz[:, 0] - ox) / res * S).astype(int); cy = ((H - 1 - (xyz[:, 1] - oy) / res) * S).astype(int)
    ok = (cx >= 0) & (cx < W * S) & (cy >= 0) & (cy < H * S)
    cx, cy, col, h = cx[ok], cy[ok], col[ok], h[ok]
    order = np.argsort(h)                                    # highest point drawn last = on top
    cx, cy, col, h = cx[order], cy[order], col[order], h[order]
    k = np.ones((3, 3), np.uint8)

    def splat(values, bg):
        img = np.empty((H * S, W * S, 3), np.uint8); img[:] = bg
        img[cy, cx] = values
        mask = np.zeros((H * S, W * S), np.uint8); mask[cy, cx] = 255
        dil = cv2.dilate(img, k); dm = cv2.dilate(mask, k)
        img[(mask == 0) & (dm > 0)] = dil[(mask == 0) & (dm > 0)]   # close pin-holes between points
        return img

    def px(x, yy): return int((x - ox) / res * S), int((H - 1 - (yy - oy) / res) * S)

    def decorate(img, title):
        for x in range(int(np.ceil(ox)), int(ox + W * res) + 1):
            X = px(x, oy)[0]
            if x % 5 == 0: cv2.line(img, (X, 0), (X, 8), (60, 60, 60), 1); cv2.putText(img, f"{x}", (X + 2, 20), 0, 0.45, (40, 40, 40), 1)
        for yy in range(int(np.ceil(oy)), int(oy + H * res) + 1):
            Y = px(ox, yy)[1]
            if yy % 5 == 0: cv2.line(img, (0, Y), (8, Y), (60, 60, 60), 1); cv2.putText(img, f"{yy}", (10, Y + 4), 0, 0.45, (40, 40, 40), 1)
        bar = int(5 / res * S); cv2.line(img, (20, img.shape[0] - 20), (20 + bar, img.shape[0] - 20), (0, 0, 0), 3)
        cv2.putText(img, "5 m", (24, img.shape[0] - 28), 0, 0.5, (0, 0, 0), 1)
        cv2.putText(img, title, (12, img.shape[0] - 50), 0, 0.6, (0, 0, 0), 2)
        return img

    rgb = decorate(splat(col, (235, 235, 235)), "camera colours from above (ceiling removed)")
    cv2.imwrite(f"{out}/features_rgb.png", rgb)

    hn = np.clip(h / a.ceiling * 255, 0, 255).astype(np.uint8).reshape(-1, 1)
    hcol = cv2.applyColorMap(hn, cv2.COLORMAP_TURBO).reshape(-1, 3)
    hm = decorate(splat(hcol, (235, 235, 235)), f"height above floor, 0 to {a.ceiling:.1f} m")
    for i in range(0, 201):                                  # colour bar
        c = cv2.applyColorMap(np.array([[int(i / 200 * 255)]], np.uint8), cv2.COLORMAP_TURBO)[0, 0].tolist()
        cv2.line(hm, (hm.shape[1] - 30, 40 + 200 - i), (hm.shape[1] - 15, 40 + 200 - i), c, 1)
    cv2.putText(hm, f"{a.ceiling:.1f} m", (hm.shape[1] - 70, 46), 0, 0.45, (0, 0, 0), 1); cv2.putText(hm, "0 m", (hm.shape[1] - 55, 255), 0, 0.45, (0, 0, 0), 1)
    cv2.imwrite(f"{out}/features_height.png", hm)

    # --- overlay: occupancy + obstacles by height class + path
    base = cv2.cvtColor(cv2.resize(grid, None, fx=S, fy=S, interpolation=cv2.INTER_NEAREST), cv2.COLOR_GRAY2BGR)
    classes = [(0.15, 0.5, (60, 60, 230), "low 0.15-0.5 m (D1 bumps into it)"),
               (0.5, 1.5, (0, 140, 255), "mid 0.5-1.5 m (tables, people)"),
               (1.5, a.ceiling, (200, 120, 0), "high 1.5 m+ (walls, shelves, hanging)")]
    for lo, hi, c, _ in classes:
        m = (h >= lo) & (h < hi)
        layer = np.zeros((H * S, W * S), np.uint8); layer[cy[m], cx[m]] = 255
        base[layer > 0] = c
    path = np.loadtxt(f"{rec}/odom-realtime.csv", delimiter=",", skiprows=1, usecols=range(1, 4))
    pts = np.array([px(*q[:2]) for q in path], np.int32)
    cv2.polylines(base, [pts], False, (0, 160, 0), 2)
    cv2.circle(base, tuple(pts[0]), 8, (0, 200, 0), -1); cv2.circle(base, tuple(pts[-1]), 8, (0, 0, 0), -1)
    for i in range(0, len(pts) - 12, max(1, len(pts) // 12)):
        cv2.arrowedLine(base, tuple(pts[i]), tuple(pts[i + 12]), (0, 110, 0), 2, tipLength=0.5)
    ov = decorate(base, "occupancy + obstacle heights + scan path")
    y0 = 40
    for _, _, c, name in classes + [(0, 0, (0, 160, 0), "scan path (green dot = start, black = end)")]:
        cv2.rectangle(ov, (ov.shape[1] - 330, y0 - 10), (ov.shape[1] - 312, y0 + 4), c, -1)
        cv2.putText(ov, name, (ov.shape[1] - 305, y0 + 3), 0, 0.42, (0, 0, 0), 1); y0 += 22
    cv2.imwrite(f"{out}/features_overlay.png", ov)
    print("wrote", out)


if __name__ == "__main__":
    main()
