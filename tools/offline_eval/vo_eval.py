"""Objective check of the SDK odometry/IMU against visual odometry (ORB + PnP on the floor-calibrated depth).

For pairs of frames ~1 s apart in the moving part of the recorded run: estimate the base motion from images alone
(yaw, forward/lateral translation) and compare with the SDK-based odometry at the (latency-shifted) times.
Reports: best time offset, yaw-rate agreement and drift (IMU vs vision), and the speed scale of the SDK velocity.
"""
import math
import sqlite3
import sys

import cv2
import numpy as np
import torch
from rclpy.serialization import deserialize_message
from sensor_msgs.msg import Image
from tf2_msgs.msg import TFMessage
from transformers import AutoImageProcessor, AutoModelForDepthEstimation

BAG = sys.argv[1]
T0, T1 = float(sys.argv[2]), float(sys.argv[3])           # window (s since bag start) to analyse
MODEL = 'depth-anything/Depth-Anything-V2-Metric-Indoor-Small-hf'
con = sqlite3.connect(BAG)
c = con.cursor()
tid = {n: i for n, i in c.execute('select name,id from topics')}


def quat_m(x, y, z, w):
    return np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                     [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                     [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])


def rpy_m(r, p, yw):
    cr, sr, cp, sp, cy, sy = math.cos(r), math.sin(r), math.cos(p), math.sin(p), math.cos(yw), math.sin(yw)
    return np.array([[cy * cp, cy * sp * sr - sy * cr, cy * sp * cr + sy * sr],
                     [sy * cp, sy * sp * sr + cy * cr, sy * sp * cr - cy * sr],
                     [-sp, cp * sr, cp * cr]])


def T4(R, t):
    M = np.eye(4)
    M[:3, :3], M[:3, 3] = R, t
    return M


# ---- TF history odom->base_link
TT, TR, TP = [], [], []
for ts, data in c.execute('select timestamp,data from messages where topic_id=? order by timestamp', (tid['/tf'],)):
    for t in deserialize_message(bytes(data), TFMessage).transforms:
        if t.header.frame_id == 'odom' and t.child_frame_id == 'base_link':
            TT.append(t.header.stamp.sec + t.header.stamp.nanosec * 1e-9)
            r = t.transform.rotation
            TR.append(quat_m(r.x, r.y, r.z, r.w))
            TP.append([t.transform.translation.x, t.transform.translation.y, t.transform.translation.z])
TT, TP = np.array(TT), np.array(TP)
t_start = TT[0]


def base_T(t):
    i = int(np.clip(np.searchsorted(TT, t), 1, len(TT) - 1))
    i = i - 1 if abs(TT[i - 1] - t) < abs(TT[i] - t) else i
    return T4(TR[i], TP[i])


T_BO = T4(rpy_m(0, -0.28, 0) @ rpy_m(-math.pi / 2, 0, -math.pi / 2), np.array([0.28, 0, 0.02]))   # optical -> base
K_FULL = (735.0, 735.0, 960.0, 540.0)

proc = AutoImageProcessor.from_pretrained(MODEL)
model = AutoModelForDepthEstimation.from_pretrained(MODEL).cuda().eval().half()


@torch.no_grad()
def raw_depth(rgb):
    pv = proc(images=rgb, return_tensors='pt')['pixel_values'].cuda().half()
    return proc.post_process_depth_estimation(model(pixel_values=pv), target_sizes=[rgb.shape[:2]])[0]['predicted_depth'].float().cpu().numpy()


def calibrate(d, Tb):
    """floor-based mode scale (same estimator as the depth node), pose from the base transform."""
    h, w = d.shape
    s = w / 1920.0
    fx, fy, cx, cy = [k * s for k in K_FULL]
    Tc = Tb @ T_BO
    R, t = Tc[:3, :3], Tc[:3, 3]
    u = np.arange(w)[None, :].astype(float)
    v = np.arange(h)[:, None].astype(float)
    a, b = (u - cx) / fx, (v - cy) / fy
    rz = R[2, 0] * a + R[2, 1] * b + R[2, 2]
    with np.errstate(divide='ignore', invalid='ignore'):
        zf = np.where(rz < -1e-3, -t[2] / rz, np.nan)
    c0, c1 = int(w * .2), int(w * .8)
    zs, ds = zf[:, c0:c1][::2, ::2], d[:, c0:c1][::2, ::2]
    k = np.isfinite(zs) & (zs >= .4) & (zs <= 3.0) & (ds > .05)
    if k.sum() < 3000:
        return None
    ratio = zs[k] / ds[k]
    hist, edges = np.histogram(ratio, bins=np.arange(0.05, 1.2, 0.01))
    i = int(np.argmax(np.convolve(hist, np.ones(3) / 3, 'same')))
    a0 = 0.5 * (edges[i] + edges[i + 1])
    inl = np.abs(ratio - a0) < 0.06 * a0 + 0.01
    if inl.sum() < 3000:
        return None
    return a0 * d


rows = c.execute('select timestamp,data from messages where topic_id=? order by timestamp', (tid['/camera/image_raw'],)).fetchall()
orb = cv2.ORB_create(1500)
frames = []
for ts, data in rows:
    m = deserialize_message(bytes(data), Image)
    t = m.header.stamp.sec + m.header.stamp.nanosec * 1e-9
    if not (T0 <= t - t_start <= T1):
        continue
    img = np.frombuffer(bytes(m.data), np.uint8).reshape(m.height, m.width, 3)
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    kp, des = orb.detectAndCompute(gray, None)
    d = raw_depth(np.ascontiguousarray(img[:, :, ::-1]))
    z = calibrate(d, base_T(t))
    if z is None or des is None:
        continue
    frames.append({'t': t, 'kp': np.array([k.pt for k in kp], np.float32), 'des': des, 'z': z.astype(np.float16)})
print(f'frames with depth+features in window [{T0:.0f},{T1:.0f}] s: {len(frames)}')

s = 960 / 1920.0
fx, fy, cx, cy = [k * s for k in K_FULL]
Kc = np.array([[fx, 0, cx], [0, fy, cy], [0, 0, 1]], np.float64)
bf = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)
pairs = []   # (t_i, t_j, yaw_vis, t_vis(base xy), n_inliers)
for i in range(len(frames)):
    j = next((j for j in range(i + 1, len(frames)) if frames[j]['t'] - frames[i]['t'] >= 0.9), None)
    if j is None or frames[j]['t'] - frames[i]['t'] > 2.0:
        continue
    A, B = frames[i], frames[j]
    mt = bf.match(A['des'], B['des'])
    if len(mt) < 40:
        continue
    pa = A['kp'][[m.queryIdx for m in mt]]
    pb = B['kp'][[m.trainIdx for m in mt]]
    za = A['z'][pa[:, 1].astype(int).clip(0, 539), pa[:, 0].astype(int).clip(0, 959)].astype(np.float64)
    ok = np.isfinite(za) & (za > 0.3) & (za < 3.0)
    if ok.sum() < 30:
        continue
    X = np.c_[(pa[ok, 0] - cx) * za[ok] / fx, (pa[ok, 1] - cy) * za[ok] / fy, za[ok]]
    okp, rv, tv, inl = cv2.solvePnPRansac(X, pb[ok].astype(np.float64), Kc, None, iterationsCount=300, reprojectionError=3.0, confidence=0.99)
    if not okp or inl is None or len(inl) < 25:
        continue
    Rm = cv2.Rodrigues(rv)[0]
    T_ji = T4(Rm, tv.ravel())              # X_j = R X_i + t  (cam i coords -> cam j coords)
    T_ij_cam = np.linalg.inv(T_ji)         # cam j pose in cam i
    M = T_BO @ T_ij_cam @ np.linalg.inv(T_BO)    # base j pose in base i
    yaw = math.atan2(M[1, 0], M[0, 0])
    pairs.append((A['t'], B['t'], yaw, M[:2, 3].copy(), len(inl)))
print(f'frame pairs (~1 s apart) with a good visual pose: {len(pairs)}')
if len(pairs) < 20:
    sys.exit('not enough pairs')


def odom_rel(ti, tj, L):
    Ti, Tj = base_T(ti - L), base_T(tj - L)
    M = np.linalg.inv(Ti) @ Tj
    return math.atan2(M[1, 0], M[0, 0]), M[:2, 3]


yv = np.array([p[2] for p in pairs])
print('\n--- A. best time offset L (odometry at t - L vs vision): yaw RMS error between visual and SDK-IMU yaw increments')
best = None
for L in (0.0, 0.5, 0.9, 1.1, 1.35, 1.6, 1.9, 2.4):
    yo = np.array([odom_rel(p[0], p[1], L)[0] for p in pairs])
    err = np.degrees(np.unwrap(np.c_[yv, yo], axis=1)[:, 1] - yv)
    rms = float(np.sqrt(np.mean(err ** 2)))
    corr = float(np.corrcoef(yv, yo)[0, 1])
    print(f'  L={L:4.2f} s: yaw-increment RMS error {rms:5.2f} deg, correlation {corr:+.3f}')
    if best is None or rms < best[0]:
        best = (rms, L)
print(f'  -> best L = {best[1]} s')
L = 1.35

print('\n--- B. IMU heading vs vision, at L = 1.35 s')
yo = np.array([odom_rel(p[0], p[1], L)[0] for p in pairs])
dt = np.array([p[1] - p[0] for p in pairs])
turning = np.abs(yv) > math.radians(3)
print(f'  pairs {len(pairs)}, pairs with a real turn (>3 deg): {int(turning.sum())}')
A = np.c_[yv, dt]
sol, *_ = np.linalg.lstsq(A, yo, rcond=None)
print(f'  fit  yaw_imu = {sol[0]:.3f} * yaw_vision + {math.degrees(sol[1]):+.3f} deg/s * dt   (1.000 and 0 = perfect)')
resid = np.degrees(yo - yv)
cum = np.cumsum(resid)
print(f'  mean per-pair disagreement {resid.mean():+.2f} deg (sd {resid.std():.2f}); cumulative IMU-vision difference after {sum(dt):.0f} s of pairs: {cum[-1]:+.1f} deg')

print('\n--- C. speed scale of the SDK velocity (vision translation / odometry translation)')
rat, ang = [], []
for p in pairs:
    to = odom_rel(p[0], p[1], L)[1]
    if np.hypot(*to) > 0.08 and p[4] >= 30:
        rat.append(np.hypot(*p[3]) / np.hypot(*to))
        ang.append(math.degrees(math.atan2(p[3][1], p[3][0]) - math.atan2(to[1], to[0])))
rat = np.array(rat)
print(f'  segments with odometry motion > 8 cm: {len(rat)}')
if len(rat) >= 8:
    print(f'  ratio vision/odometry: median {np.median(rat):.2f} (p25 {np.percentile(rat, 25):.2f}, p75 {np.percentile(rat, 75):.2f})  -> SDK velocity is {"UNDER" if np.median(rat) > 1 else "OVER"}-reporting by {abs(1 - 1 / np.median(rat)) * 100:.0f}%')
    angw = (np.array(ang) + 180) % 360 - 180
    print(f'  direction difference (vision vs odometry): median {np.median(angw):+.1f} deg, p25..p75 {np.percentile(angw, 25):+.1f}..{np.percentile(angw, 75):+.1f}')
np.save('/tmp/vo_pairs.npy', np.array([[p[0], p[1], p[2], p[3][0], p[3][1], p[4]] for p in pairs]))
