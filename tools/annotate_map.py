#!/usr/bin/env python3
"""Draw a metre grid + map-frame coordinates on map.pgm so you can pick a robot placement (x, y)."""
import sys, re, numpy as np, cv2
d = sys.argv[1]
y = open(f"{d}/map.yaml").read()
res = float(re.search(r"resolution:\s*([\d.]+)", y).group(1))
ox, oy = map(float, re.search(r"origin:\s*\[([-\d.]+),\s*([-\d.]+)", y).groups())
g = cv2.imread(f"{d}/map.pgm", 0); H, W = g.shape
S = 2                                            # upscale for readability
img = cv2.cvtColor(cv2.resize(g, None, fx=S, fy=S, interpolation=cv2.INTER_NEAREST), cv2.COLOR_GRAY2BGR)
def px(x, yy): return int((x-ox)/res*S), int((H-1-(yy-oy)/res)*S)
for x in range(int(np.ceil(ox)), int(ox + W*res) + 1):
    X = px(x, oy)[0]; cv2.line(img, (X, 0), (X, img.shape[0]), (200, 150, 150) if x % 5 else (200, 80, 80), 1)
    if x % 5 == 0: cv2.putText(img, f"x={x}", (X+2, 14), 0, 0.45, (150, 0, 0), 1)
for yy in range(int(np.ceil(oy)), int(oy + H*res) + 1):
    Y = px(ox, yy)[1]; cv2.line(img, (0, Y), (img.shape[1], Y), (200, 150, 150) if yy % 5 else (200, 80, 80), 1)
    if yy % 5 == 0: cv2.putText(img, f"y={yy}", (2, Y-3), 0, 0.45, (150, 0, 0), 1)
cv2.circle(img, px(0, 0), 7, (0, 160, 0), -1); cv2.putText(img, "origin (0,0) = scan start", (px(0, 0)[0]+10, px(0, 0)[1]-8), 0, 0.5, (0, 120, 0), 1)
cv2.imwrite(f"{d}/map_grid.png", img); print("wrote", f"{d}/map_grid.png")
