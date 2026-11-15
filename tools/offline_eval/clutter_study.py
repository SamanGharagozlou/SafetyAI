"""Why does the floor fit fail in the cluttered new environment? Test calibrations on the standing photo."""
import json
import math
import sys

import cv2
import numpy as np
import torch
from transformers import AutoImageProcessor, AutoModelForDepthEstimation

D = sys.argv[1]
MODEL = 'depth-anything/Depth-Anything-V2-Metric-Indoor-Small-hf'
img = cv2.imread(D + '/photo.png')
meta = json.load(open(D + '/meta.json'))
R, t = np.array(meta['R']), np.array(meta['t'])
h, w = img.shape[:2]
proc = AutoImageProcessor.from_pretrained(MODEL)
model = AutoModelForDepthEstimation.from_pretrained(MODEL).cuda().eval().half()
with torch.no_grad():
    pv = proc(images=cv2.cvtColor(img, cv2.COLOR_BGR2RGB), return_tensors='pt')['pixel_values'].cuda().half()
    d = proc.post_process_depth_estimation(model(pixel_values=pv), target_sizes=[(h, w)])[0]['predicted_depth'].float().cpu().numpy()
np.save(D + '/raw_depth.npy', d)
f, cx, cy = 735 * w / 1920, 960 * w / 1920, 540 * w / 1920
u = np.arange(w)[None, :].astype(float)
v = np.arange(h)[:, None].astype(float)
a, b = (u - cx) / f, (v - cy) / f
rz = R[2, 0] * a + R[2, 1] * b + R[2, 2]
with np.errstate(divide='ignore', invalid='ignore'):
    zf = np.where(rz < -1e-3, -t[2] / rz, np.nan)
horizon = cy + f * math.tan(math.asin(R[2, 2]))
print(f'camera h {t[2]:.3f} m, tilt {math.degrees(math.asin(R[2, 2])):+.1f} deg, horizon row ~{horizon:.0f}; raw depth {np.nanmin(d):.2f}..{np.nanmax(d):.2f} m')

# --- hand-marked TRUE floor patches (from the photo): between the boxes and bottom-left
floor_true = np.zeros((h, w), bool)
floor_true[430:535, 385:495] = True            # carpet between the stacked boxes and the big box
floor_true[455:535, 140:285] = True            # carpet bottom-left
ft = floor_true & np.isfinite(zf) & (zf > 0.4) & (zf < 4)
print('hand-marked floor pixels with valid analytic depth:', int(ft.sum()))
rt = zf[ft] / d[ft]
print(f'  ratio z_floor/d on TRUE floor: median {np.median(rt):.3f}  (p10 {np.percentile(rt, 10):.3f}, p90 {np.percentile(rt, 90):.3f})')

# --- candidate floor pixels used by the node: central columns, analytic depth window 0.4-3 m
c0, c1 = int(w * .2), int(w * .8)
cand = np.zeros((h, w), bool)
cand[:, c0:c1] = np.isfinite(zf[:, c0:c1]) & (zf[:, c0:c1] >= .4) & (zf[:, c0:c1] <= 3.0) & (d[:, c0:c1] > .05)
print(f'candidate pixels (what the node assumes is floor): {int(cand.sum())}; of those truly floor (hand-marked): {int((cand & floor_true).sum())} ({100 * (cand & floor_true).sum() / cand.sum():.0f}%)')


def err_on_true(zhat):
    k = ft
    return float(np.median(np.abs(zhat[k] - zf[k]) / zf[k]))


results = {}
# 1) current method: 2-param OLS + 3x trimming
x, y = d[cand], zf[cand]
keep = np.ones(len(x), bool)
for _ in range(3):
    A = np.c_[x[keep], np.ones(keep.sum())]
    co = np.linalg.lstsq(A, y[keep], rcond=None)[0]
    res = y - (co[0] * x + co[1])
    sig = max(1.4826 * np.median(np.abs(res[keep] - np.median(res[keep]))), 0.05)
    nk = np.abs(res) < 2.5 * sig
    if nk.sum() >= 3000:
        keep = nk
results['current (OLS + trimming)'] = (co, keep.mean())
# 2) RANSAC 2-param
rng = np.random.default_rng(0)
best = (None, -1)
idx = np.arange(len(x))
for _ in range(400):
    i, j = rng.choice(idx, 2, replace=False)
    if abs(x[i] - x[j]) < 0.3:
        continue
    aa = (y[i] - y[j]) / (x[i] - x[j])
    bb = y[i] - aa * x[i]
    if not (0.1 < aa < 0.8 and -0.6 < bb < 0.6):
        continue
    inl = np.abs(y - (aa * x + bb)) < 0.12 * y
    if inl.sum() > best[1]:
        best = ((aa, bb), inl.sum())
if best[0]:
    inl = np.abs(y - (best[0][0] * x + best[0][1])) < 0.12 * y
    A = np.c_[x[inl], np.ones(inl.sum())]
    co2 = np.linalg.lstsq(A, y[inl], rcond=None)[0]
    results['RANSAC 2-param (priors a 0.1-0.8, |b|<0.6)'] = (co2, inl.mean())
# 3) 1-param scale, b = 0: mode of the ratio z_floor/d (densest cluster)
ratio = y / x
hist, edges = np.histogram(ratio, bins=np.arange(0.05, 1.0, 0.01))
k = np.argmax(hist)
a_mode = 0.5 * (edges[k] + edges[k + 1])
inl = np.abs(ratio - a_mode) < 0.06 * a_mode + 0.01
results['1-param scale (mode of ratio), b=0'] = (np.array([a_mode, 0.0]), inl.mean())
# 4) prior from earlier runs (global)
results['fixed prior a=0.30, b=0 (earlier runs)'] = (np.array([0.30, 0.0]), float('nan'))
results['fixed prior a=0.29, b=-0.07 (replay fits)'] = (np.array([0.29, -0.07]), float('nan'))

print('\nmethod                                         a      b    inlier%   error on TRUE floor patches')
for name, (co, inl) in results.items():
    zhat = co[0] * d + co[1]
    print(f'{name:46s} {co[0]:6.3f} {co[1]:6.3f}   {100 * inl:5.0f}      {100 * err_on_true(zhat):5.1f} %')

# --- show where the current method's inliers are vs the best alternative
def overlay(co_inl, name, colour):
    m = np.zeros((h, w), bool)
    m[cand] = co_inl
    o = img.copy()
    o[m] = (0.35 * o[m] + 0.65 * np.array(colour)).astype(np.uint8)
    cv2.putText(o, name, (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
    return o


cur_co, cur_k = results['current (OLS + trimming)']
x2, y2 = d[cand], zf[cand]
k_cur = np.abs(y2 - (cur_co[0] * x2 + cur_co[1])) < 2.5 * max(1.4826 * np.median(np.abs((y2 - (cur_co[0] * x2 + cur_co[1])))), 0.05)
k_mode = np.abs(y2 / x2 - a_mode) < 0.06 * a_mode + 0.01
tp = img.copy()
tp[floor_true] = (0.4 * tp[floor_true] + 0.6 * np.array([0, 255, 0])).astype(np.uint8)
cv2.putText(tp, 'hand-marked true floor', (10, 24), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
cv2.imwrite(D + '/clutter_overlays.png', np.vstack([np.hstack([tp, overlay(k_cur, 'inliers: current method', (0, 0, 255))]),
                                                    np.hstack([overlay(k_mode, 'inliers: scale-mode method', (255, 160, 0)), overlay(np.ones(cand.sum(), bool), 'all candidate pixels', (0, 200, 255))])]))
print('saved overlays')
