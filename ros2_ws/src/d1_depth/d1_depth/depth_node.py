"""Monocular metric depth + point cloud for the D1 camera, calibrated on the floor.

Pipeline per image (rate-limited to `rate` Hz):
  1. Depth Anything V2 (metric, indoor, small) -> raw depth d (metres, but ~3.5x too far for this low camera).
  2. Camera pose from TF (floor frame `odom`, where z = 0 is the floor because the bridge publishes the SDK body
     height). Every pixel ray hits the floor plane at an analytic depth z_floor.
  3. Fit  1/z_floor = a * (1/d) + b  on floor pixels (robust, a few outlier-trimming passes), smooth a, b over time.
  4. corrected depth z = 1 / (a/d + b); publish a 32FC1 depth image and an XYZ PointCloud2 (camera optical frame).

Why per frame: the camera height changes between lying (~0.18 m) and standing (~0.40 m) and with body pitch, and the
raw model's scale depends on it (measured: see docs/costmap_plan.md section 7).

Torch and transformers live in a separate virtualenv (D1_DEPTH_VENV, default ~/d1_depth_venv, same Python 3.10); its
site-packages is put FIRST on sys.path so its NumPy 1.26 is used. ROS (rclpy) still comes from the ROS install.
"""
import os
import site
import sys

# ~/.local NumPy 2.x breaks ROS's NumPy-1 binaries; drop user site, then prefer the depth venv's packages.
try:
    _us = site.getusersitepackages()
    sys.path[:] = [p for p in sys.path if not p.startswith(_us)]
except Exception:  # pragma: no cover
    pass
_venv = os.path.expanduser(os.environ.get('D1_DEPTH_VENV', '~/d1_depth_venv'))
_sp = os.path.join(_venv, 'lib', 'python%d.%d' % sys.version_info[:2], 'site-packages')
if os.path.isdir(_sp):
    sys.path.insert(0, _sp)

import array  # noqa: E402
import math  # noqa: E402
import time  # noqa: E402

import numpy as np  # noqa: E402
import rclpy  # noqa: E402
import tf2_ros  # noqa: E402
import torch  # noqa: E402
from rclpy.node import Node  # noqa: E402
from rclpy.qos import HistoryPolicy, QoSProfile, ReliabilityPolicy  # noqa: E402
from rclpy.time import Time  # noqa: E402
from sensor_msgs.msg import CameraInfo, Image, PointCloud2, PointField  # noqa: E402
from transformers import AutoImageProcessor, AutoModelForDepthEstimation  # noqa: E402


def quat_to_matrix(x, y, z, w):
    xx, yy, zz = x * x, y * y, z * z
    return np.array([[1 - 2 * (yy + zz), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                     [2 * (x * y + z * w), 1 - 2 * (xx + zz), 2 * (y * z - x * w)],
                     [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (xx + yy)]])


class DepthNode(Node):
    def __init__(self):
        super().__init__('depth_node')
        p = self.declare_parameter
        p('model', 'depth-anything/Depth-Anything-V2-Metric-Indoor-Small-hf')
        p('rate', 5.0)                 # max inference rate (Hz)
        p('floor_frame', 'odom')       # frame whose z = 0 is the floor
        p('min_range', 0.15)           # m
        p('max_range', 3.0)            # m. Beyond ~3 m this model cannot tell a far wall (predicted 12-18 m, real 25 m+) from a
                                       # near obstacle after the floor calibration, so farther points are dropped (see docs)
        p('raw_max_valid', 19.0)       # raw model depth at/above this is the model's 20 m cap = 'far/unknown', not a surface
        p('downsample', 4)             # point cloud pixel stride
        p('fp16', True)
        p('floor_min', 0.4)            # analytic floor depth window used for the fit (m)
        p('floor_max', 3.0)            # = the working range; far floor is where the model saturates
        p('fit_model', 'linear')       # 'linear': z = a*d + b (best across postures, see docs) | 'inverse': 1/z = a/d + b
        p('floor_col_lo', 0.2)         # central image band used for the fit (fraction of width)
        p('floor_col_hi', 0.8)
        p('fit_ema', 0.25)             # smoothing of (a, b) over frames
        p('max_cal_age_s', 30.0)       # keep publishing with the last good calibration this long when fits fail, then stop
        p('min_floor_pts', 3000)       # consistent floor pixels needed for a trustworthy fit
        p('max_fit_error', 0.12)       # median relative floor error above which a fit is rejected (linear model: lying 6%, moving 6%, standing 9%)
        p('max_bad_frames', 5)         # stop publishing after this many consecutive rejected fits (fail-safe)
        p('default_a', 0.29)           # fallback calibration (linear model; measured global scale ~0.29)
        p('default_b', 0.0)
        p('publish_depth', True)
        p('publish_cloud', True)
        g = lambda n: self.get_parameter(n).value  # noqa: E731
        self.floor_frame = g('floor_frame')
        self.min_period = 1.0 / float(g('rate'))
        self.min_range, self.max_range = float(g('min_range')), float(g('max_range'))
        self.raw_max_valid = float(g('raw_max_valid'))
        self.ds = max(1, int(g('downsample')))
        self.f_min, self.f_max = float(g('floor_min')), float(g('floor_max'))
        self.c_lo, self.c_hi = float(g('floor_col_lo')), float(g('floor_col_hi'))
        self.ema = float(g('fit_ema'))
        self.min_pts = int(g('min_floor_pts'))
        self.max_fit_error = float(g('max_fit_error'))
        self.max_bad = int(g('max_bad_frames'))
        self.bad_frames = 0
        self.max_cal_age = float(g('max_cal_age_s'))
        self.last_good_t = 0.0
        self.pub_depth_on, self.pub_cloud_on = bool(g('publish_depth')), bool(g('publish_cloud'))
        self.ab = None
        self.default_ab = (float(g('default_a')), float(g('default_b')))
        self.fit_model = str(g('fit_model'))
        if self.fit_model not in ('linear', 'linear_ols', 'inverse'):
            raise ValueError("fit_model must be 'linear', 'linear_ols' or 'inverse'")
        self.K = None

        self.device = 'cuda' if torch.cuda.is_available() else 'cpu'
        self.dtype = torch.float16 if (self.device == 'cuda' and g('fp16')) else torch.float32
        t0 = time.time()
        self.proc = AutoImageProcessor.from_pretrained(g('model'))
        self.model = AutoModelForDepthEstimation.from_pretrained(g('model')).to(self.device).eval()
        if self.dtype == torch.float16:
            self.model = self.model.half()
        self.get_logger().info(f'model {g("model")} on {self.device} ({self.dtype}) loaded in {time.time() - t0:.1f}s')

        self.tf_buffer = tf2_ros.Buffer()
        self.tf_listener = tf2_ros.TransformListener(self.tf_buffer, self)

        qos = QoSProfile(history=HistoryPolicy.KEEP_LAST, depth=1, reliability=ReliabilityPolicy.BEST_EFFORT)
        self.create_subscription(CameraInfo, 'camera_info', self._on_info, qos)
        self.create_subscription(Image, 'image_raw', self._on_image, qos)
        self.pub_depth = self.create_publisher(Image, 'depth', qos)
        self.pub_cloud = self.create_publisher(PointCloud2, 'points', qos)

        self.last_t = 0.0
        self.n_done = 0
        self.stats_t = time.monotonic()
        self.stats_n = 0
        self.last_infer_ms = 0.0
        self.n_cb = 0
        self.cb_ms = 0.0
        self.stats_cb = 0
        self.last_fit = None
        self.create_timer(5.0, self._report)
        self.get_logger().info('waiting for /camera/image_raw and TF %s -> camera frame' % self.floor_frame)

    # ----------------------------------------------------------------------------------------------------------
    def _on_info(self, m):
        self.K = (m.k[0], m.k[4], m.k[2], m.k[5], m.width, m.height)

    def _intrinsics(self, w, h):
        if self.K is not None and self.K[4] == w and self.K[5] == h:
            return self.K[:4]
        s = w / 1920.0                       # fall back to the estimated D1 intrinsics, scaled
        return 735.0 * s, 735.0 * s, 960.0 * s, 540.0 * s

    @torch.no_grad()
    def _infer(self, rgb):
        h, w = rgb.shape[:2]
        pv = self.proc(images=rgb, return_tensors='pt')['pixel_values'].to(self.device, dtype=self.dtype)
        out = self.model(pixel_values=pv)
        d = self.proc.post_process_depth_estimation(out, target_sizes=[(h, w)])[0]['predicted_depth']
        return d.float().cpu().numpy()

    def _floor_depth(self, fx, fy, cx, cy, h, w, R, t):
        """Analytic depth along the optical axis of the floor plane (z = 0 in floor_frame) for every pixel."""
        u = np.arange(w, dtype=np.float64)[None, :]
        v = np.arange(h, dtype=np.float64)[:, None]
        a, b = (u - cx) / fx, (v - cy) / fy
        rz = R[2, 0] * a + R[2, 1] * b + R[2, 2]             # world-z component of the (a, b, 1) ray
        with np.errstate(divide='ignore', invalid='ignore'):
            zf = np.where(rz < -1e-3, -t[2] / rz, np.nan)
        zf[(zf <= 0) | (zf > 60)] = np.nan
        return zf

    def _fit(self, d, zf):
        """Calibrate the model depth against the analytic floor depth. Returns (a, b, n_inliers, rel_error) or None.

        'linear' (default, robust to clutter): the scale a0 is the MODE of z_floor/d over the candidate pixels, i.e. the
        scale that the largest consistent group of pixels agrees on. This survives scenes where most of the candidate
        "floor" pixels are really boxes or walls (measured: 31% true floor -> mode scale error 1.8% on the real floor,
        the old OLS + trimming fit locked onto the clutter and was 21% off). An optional 2-parameter refinement
        z = a*d + b is run on the inliers only.
        """
        h, w = d.shape
        c0, c1 = int(w * self.c_lo), int(w * self.c_hi)
        zs = zf[:, c0:c1][::2, ::2]
        ds = d[:, c0:c1][::2, ::2]
        k = np.isfinite(zs) & (zs >= self.f_min) & (zs <= self.f_max) & (ds > 0.05)
        if k.sum() < self.min_pts:
            return None
        if self.fit_model == 'linear':
            x, y = ds[k], zs[k]
            ratio = y / x
            hist, edges = np.histogram(ratio, bins=np.arange(0.05, 1.2, 0.01))
            sm = np.convolve(hist, np.ones(3) / 3.0, mode='same')          # light smoothing of the histogram
            i = int(np.argmax(sm))
            a0 = 0.5 * (edges[i] + edges[i + 1])
            inl = np.abs(ratio - a0) < 0.06 * a0 + 0.01
            if inl.sum() < self.min_pts or inl.mean() < 0.12:
                return None
            a, b = a0, 0.0
            A = np.c_[x[inl], np.ones(inl.sum())]
            co = np.linalg.lstsq(A, y[inl], rcond=None)[0]
            if 0.05 < co[0] < 1.2 and abs(co[1]) < 0.25:                    # keep the refinement only if sane
                a, b = float(co[0]), float(co[1])
            rel = float(np.median(np.abs(a * x[inl] + b - y[inl]) / y[inl]))
            return float(a), float(b), int(inl.sum()), rel
        lin = self.fit_model == 'linear_ols'
        x, y = (ds[k], zs[k]) if lin else (1.0 / ds[k], 1.0 / zs[k])     # z = a*d + b   or   1/z = a/d + b
        keep = np.ones(x.shape, bool)
        a = b = 0.0
        floor_sig = 0.05 if lin else 0.01
        for _ in range(3):
            A = np.c_[x[keep], np.ones(keep.sum())]
            a, b = np.linalg.lstsq(A, y[keep], rcond=None)[0]
            res = y - (a * x + b)
            sig = max(1.4826 * np.median(np.abs(res[keep] - np.median(res[keep]))), floor_sig)
            new_keep = np.abs(res) < 2.5 * sig
            if new_keep.sum() >= self.min_pts:
                keep = new_keep
        pred = a * x[keep] + b
        zfit = pred if lin else 1.0 / np.maximum(pred, 1e-3)
        ztrue = y[keep] if lin else 1.0 / y[keep]
        rel = float(np.median(np.abs(zfit - ztrue) / ztrue))
        ok = (0.02 < a < 3.0 and -2.0 < b < 2.0) if lin else (0.05 < a < 20.0 and -1.0 < b < 2.0)
        return (float(a), float(b), int(keep.sum()), rel) if ok else None

    def _on_image(self, msg):
        now = time.monotonic()
        self.n_cb += 1
        if now - self.last_t < 0.85 * self.min_period:     # tolerance: avoid aliasing against the input rate
            return
        self.last_t = now
        if msg.encoding != 'bgr8':
            self.get_logger().error(f'unsupported encoding {msg.encoding}', throttle_duration_sec=10.0)
            return
        h, w = msg.height, msg.width
        try:
            tf = self.tf_buffer.lookup_transform(self.floor_frame, msg.header.frame_id, Time())
        except Exception as e:  # noqa: BLE001
            self.get_logger().warn(f'no TF {self.floor_frame} -> {msg.header.frame_id}: {e}', throttle_duration_sec=5.0)
            return
        tr, q = tf.transform.translation, tf.transform.rotation
        R = quat_to_matrix(q.x, q.y, q.z, q.w)
        t = np.array([tr.x, tr.y, tr.z])
        fx, fy, cx, cy = self._intrinsics(w, h)

        bgr = np.frombuffer(msg.data, np.uint8).reshape(h, w, 3)
        t_inf = time.monotonic()
        d = self._infer(np.ascontiguousarray(bgr[:, :, ::-1]))
        self.last_infer_ms = (time.monotonic() - t_inf) * 1000.0

        zf = self._floor_depth(fx, fy, cx, cy, h, w, R, t)
        fit = self._fit(d, zf)
        tnow = time.monotonic()
        if fit is not None and fit[3] <= self.max_fit_error:
            a, b, n, rel = fit
            self.ab = (a, b) if self.ab is None else ((1 - self.ema) * self.ab[0] + self.ema * a,
                                                      (1 - self.ema) * self.ab[1] + self.ema * b)
            self.last_fit = (a, b, n, rel, True)
            self.bad_frames = 0
            self.last_good_t = tnow
        else:
            # No trustworthy floor in view (or an inconsistent fit): hold the last good calibration for a while, because
            # the monocular scale changes slowly; stop publishing only when that calibration has become too old.
            self.bad_frames += 1
            if fit is not None:
                self.last_fit = (fit[0], fit[1], fit[2], fit[3], False)
            elif self.ab is not None:
                self.last_fit = (self.ab[0], self.ab[1], 0, float('nan'), False)
            if self.ab is None or tnow - self.last_good_t > self.max_cal_age:
                self.get_logger().warn(
                    f'no usable floor calibration for {tnow - self.last_good_t:.0f}s ({self.bad_frames} frames): '
                    f'not publishing', throttle_duration_sec=5.0)
                return
        a, b = self.ab

        with np.errstate(divide='ignore', invalid='ignore'):
            if self.fit_model in ('linear', 'linear_ols'):
                z = (a * d + b).astype(np.float32)
            else:
                inv = a / np.maximum(d, 1e-3) + b
                z = np.where(inv > 1.0 / 30.0, 1.0 / inv, np.nan).astype(np.float32)
        z[(z < self.min_range)] = np.nan
        # The indoor model saturates at 20 m; the floor-fitted map would turn that cap into a fake surface ~3.5 m away.
        z[d >= self.raw_max_valid] = np.nan

        if self.pub_depth_on:
            dm = Image()
            dm.header = msg.header
            dm.height, dm.width, dm.encoding, dm.is_bigendian, dm.step = h, w, '32FC1', 0, w * 4
            buf = array.array('B')
            buf.frombytes(memoryview(np.ascontiguousarray(z).reshape(-1).view(np.uint8)))
            dm.data = buf
            self.pub_depth.publish(dm)
        if self.pub_cloud_on:
            self._publish_cloud(msg.header, z, fx, fy, cx, cy)
        self.n_done += 1
        self.stats_n += 1
        self.cb_ms = (time.monotonic() - now) * 1000.0

    def _publish_cloud(self, header, z, fx, fy, cx, cy):
        ds = self.ds
        h, w = z.shape
        zs = z[::ds, ::ds]
        uu, vv = np.meshgrid(np.arange(0, w, ds, dtype=np.float32), np.arange(0, h, ds, dtype=np.float32))
        k = np.isfinite(zs) & (zs <= self.max_range)
        Z = zs[k]
        pts = np.c_[((uu[k] - cx) * Z / fx), ((vv[k] - cy) * Z / fy), Z].astype(np.float32)
        m = PointCloud2()
        m.header = header
        m.height, m.width = 1, int(pts.shape[0])
        m.fields = [PointField(name=n, offset=4 * i, datatype=PointField.FLOAT32, count=1)
                    for i, n in enumerate('xyz')]
        m.is_bigendian, m.point_step, m.row_step, m.is_dense = False, 12, 12 * int(pts.shape[0]), True
        buf = array.array('B')
        buf.frombytes(memoryview(np.ascontiguousarray(pts).reshape(-1).view(np.uint8)))
        m.data = buf
        self.pub_cloud.publish(m)

    def _report(self):
        now = time.monotonic()
        dt = now - self.stats_t
        rate = self.stats_n / dt
        cb_rate = (self.n_cb - self.stats_cb) / dt
        self.stats_cb = self.n_cb
        self.stats_t, self.stats_n = now, 0
        if self.last_fit is None:
            self.get_logger().info('no frames processed yet (need image + TF)')
            return
        a, b, n, rel, ok = self.last_fit
        self.get_logger().info(
            f'in {cb_rate:.1f} Hz -> done {rate:.1f} Hz | callback {self.cb_ms:.0f} ms (model {self.last_infer_ms:.0f}) | floor fit a={a:.2f} b={b:.3f} '
            f'on {n} pts, median error {100 * rel:.1f}% {"" if ok else "(FIT REJECTED, holding last good calibration or not publishing)"}')


def main():
    rclpy.init()
    node = DepthNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
