"""Estimate the camera->PC video latency by cross-correlating the camera's apparent yaw rate (phase correlation between
consecutive frames) with the IMU gyro z, over the moving part of the recorded run."""
import math
import sqlite3
import sys

import cv2
import numpy as np
from rclpy.serialization import deserialize_message
from sensor_msgs.msg import Image, Imu

BAG = sys.argv[1]
con = sqlite3.connect(BAG)
c = con.cursor()
tid = {n: i for n, i in c.execute('select name,id from topics')}

# --- gyro z (body yaw rate) from /imu
gt, gz = [], []
for ts, data in c.execute('select timestamp,data from messages where topic_id=? order by timestamp', (tid['/imu'],)):
    m = deserialize_message(bytes(data), Imu)
    gt.append(m.header.stamp.sec + m.header.stamp.nanosec * 1e-9)
    gz.append(m.angular_velocity.z)
gt, gz = np.array(gt), np.array(gz)
print(f'imu samples {len(gt)} over {gt[-1] - gt[0]:.0f} s; |gz| p95 = {np.percentile(np.abs(gz), 95):.2f} rad/s')

# --- camera yaw-rate proxy from phase correlation
rows = c.execute('select timestamp,data from messages where topic_id=? order by timestamp', (tid['/camera/image_raw'],)).fetchall()
F = 735.0 * 0.5          # focal length at 960 px width
prev, tprev = None, None
ti, wi = [], []
for ts, data in rows:
    m = deserialize_message(bytes(data), Image)
    t = m.header.stamp.sec + m.header.stamp.nanosec * 1e-9
    g = cv2.cvtColor(np.frombuffer(bytes(m.data), np.uint8).reshape(m.height, m.width, 3), cv2.COLOR_BGR2GRAY)
    g = cv2.resize(g, (320, 180)).astype(np.float32)
    if prev is not None and 0.05 < t - tprev < 1.0:
        (dx, dy), resp = cv2.phaseCorrelate(prev, g, cv2.createHanningWindow((320, 180), cv2.CV_32F))
        if resp > 0.05:
            ang = math.atan2(dx * 3.0, F)          # horizontal shift (px at 960 width) -> rotation about the camera's vertical axis
            ti.append(0.5 * (t + tprev))
            wi.append(-ang / (t - tprev))            # sign fixed below by the best correlation sign
    prev, tprev = g, t
ti, wi = np.array(ti), np.array(wi)
print(f'camera yaw-rate samples: {len(ti)}')

# restrict to the part of the run where the robot actually turned/moved
mask = np.zeros(len(ti), bool)
for i, t in enumerate(ti):
    k = (gt > t - 1.0) & (gt < t + 1.0)
    mask[i] = k.any() and np.abs(gz[k]).max() > 0.08
print(f'samples with real turning: {int(mask.sum())}')
if mask.sum() < 15:
    print('not enough turning in this run for a latency estimate')
    sys.exit(0)

best = []
for L in np.arange(-0.5, 3.01, 0.1):
    g_at = np.interp(ti[mask] - L, gt, gz)           # gyro at the time the motion really happened
    if np.std(g_at) < 1e-6 or np.std(wi[mask]) < 1e-9:
        continue
    r = np.corrcoef(g_at, wi[mask])[0, 1]
    best.append((L, r))
best = np.array(best)
print('lag L (s) | correlation of camera yaw-rate with gyro(t - L)')
for L, r in best[::2]:
    print(f'  {L:4.1f}  {r:+.3f}  ' + '#' * int(abs(r) * 40))
i = int(np.argmax(np.abs(best[:, 1])))
print(f'\nBEST: lag {best[i, 0]:.1f} s, correlation {best[i, 1]:+.3f} (sign {"as assumed" if best[i, 1] > 0 else "flipped"})')
# smooth peak refinement
top = best[np.argsort(-np.abs(best[:, 1]))[:5]]
print('top-5 lags:', [(round(a, 1), round(b, 2)) for a, b in top])
