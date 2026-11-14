"""AgiBot D1 camera -> ROS 2.

Reads the robot's RTSP H.264 stream (default rtsp://172.24.1.41:8554/test, 1080p, lens-corrected
by the robot's ISP/FEC) and publishes sensor_msgs/Image (bgr8) and sensor_msgs/CameraInfo.

Design:
  * A background thread decodes continuously and keeps only the NEWEST frame, so a slow
    consumer never builds latency.
  * A timer publishes at `fps` (default 15 Hz) at `scale` x 1920x1080 (default 960x540).
  * Auto-reconnect if the stream stalls.
  * Image stamps are the PC receive time. The stream carries no hardware timestamp, so the
    stamp lags the real exposure by (encode + network + decode) latency. Use
    `stamp_offset_s` to subtract an estimate. This matters for visual-inertial SLAM.

Topics (under the node namespace, e.g. /camera):
  image_raw    sensor_msgs/Image
  camera_info  sensor_msgs/CameraInfo   (placeholder pinhole unless camera_info_file is given)
"""
import os
import site
import sys

# ~/.local NumPy 2.x shadows the system NumPy 1.x that ROS's OpenCV/cv_bridge are built against
# and makes `import cv2` fail. Drop user site-packages BEFORE anything imports numpy/cv2.
try:
    _user_site = site.getusersitepackages()
    sys.path[:] = [p for p in sys.path if not p.startswith(_user_site)]
except Exception:  # pragma: no cover
    pass

# Low-latency FFmpeg options, read when cv2 opens the stream. TCP avoids UDP loss;
# stimeout (microseconds) stops a dead stream from blocking read() forever.
os.environ.setdefault(
    'OPENCV_FFMPEG_CAPTURE_OPTIONS',
    'rtsp_transport;tcp|fflags;nobuffer|flags;low_delay|stimeout;5000000')

import array  # noqa: E402
import math  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402

import cv2  # noqa: E402
import numpy as np  # noqa: E402
import rclpy  # noqa: E402
import yaml  # noqa: E402
from rclpy.node import Node  # noqa: E402
from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy  # noqa: E402
from sensor_msgs.msg import CameraInfo, Image  # noqa: E402

NATIVE_W, NATIVE_H = 1920, 1080


class RtspCamera(Node):
    def __init__(self):
        super().__init__('rtsp_camera')
        p = self.declare_parameter
        p('url', 'rtsp://172.24.1.41:8554/test')   # also: a file path or a /dev/video index
        p('frame_id', 'camera_optical_frame')   # ROS optical convention: z forward, x right, y down
        p('fps', 15.0)                 # publish rate
        p('scale', 0.5)                # output size relative to 1920x1080
        p('hfov_deg', 122.0)           # used only for the placeholder intrinsics
        p('camera_info_file', '')      # ROS calibration YAML (overrides placeholder)
        p('stale_timeout', 1.0)        # s without a new frame -> warn, stop publishing
        p('reconnect_s', 2.0)
        p('stamp_offset_s', 0.0)       # subtract estimated pipeline latency from stamps
        g = lambda n: self.get_parameter(n).value  # noqa: E731
        self.url = str(g('url'))
        self.frame_id = g('frame_id')
        self.scale = float(g('scale'))
        self.stale = float(g('stale_timeout'))
        self.reconnect_s = float(g('reconnect_s'))
        self.stamp_offset = float(g('stamp_offset_s'))
        self.out_w = max(2, int(round(NATIVE_W * self.scale / 2)) * 2)
        self.out_h = max(2, int(round(NATIVE_H * self.scale / 2)) * 2)

        self._load_intrinsics(g('camera_info_file'), float(g('hfov_deg')))

        qos = QoSProfile(history=HistoryPolicy.KEEP_LAST, depth=2,
                         reliability=ReliabilityPolicy.BEST_EFFORT)
        self.pub_img = self.create_publisher(Image, 'image_raw', qos)
        self.pub_info = self.create_publisher(CameraInfo, 'camera_info', qos)

        self._lock = threading.Lock()
        self._frame = None
        self._frame_stamp = None
        self._seq = 0
        self._pub_seq = 0
        self._stop = threading.Event()
        self.frames_read = 0
        self.read_failures = 0
        self.reconnects = 0
        self.published = 0
        self._stats_t = time.monotonic()
        self._stats_read = 0
        self._stats_pub = 0
        self._stale_warned = False

        self._thread = threading.Thread(target=self._reader, daemon=True)
        self._thread.start()
        self.create_timer(1.0 / float(g('fps')), self._publish)
        self.create_timer(5.0, self._report)
        self.get_logger().info(
            f'camera url={self.url} -> {self.out_w}x{self.out_h} @ {g("fps")} Hz '
            f'(intrinsics: {"file" if g("camera_info_file") else "PLACEHOLDER, uncalibrated"})')

    # ----------------------------------------------------------------- intrinsics
    def _load_intrinsics(self, path, hfov_deg):
        """K/D/R/P for the NATIVE image, then scaled to the output size."""
        K = D = R = P = None
        src_w, src_h = NATIVE_W, NATIVE_H
        model = 'plumb_bob'
        self.calibrated = False
        if path:
            with open(path) as f:
                y = yaml.safe_load(f)
            src_w, src_h = int(y['image_width']), int(y['image_height'])
            K = [float(v) for v in y['camera_matrix']['data']]
            D = [float(v) for v in y['distortion_coefficients']['data']]
            R = [float(v) for v in y['rectification_matrix']['data']]
            P = [float(v) for v in y['projection_matrix']['data']]
            model = y.get('distortion_model', 'plumb_bob')
            self.calibrated = True
        else:
            f = (NATIVE_W / 2.0) / math.tan(math.radians(hfov_deg) / 2.0)
            K = [f, 0.0, NATIVE_W / 2.0, 0.0, f, NATIVE_H / 2.0, 0.0, 0.0, 1.0]
            D = [0.0] * 5
            R = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0]
            P = [f, 0.0, NATIVE_W / 2.0, 0.0, 0.0, f, NATIVE_H / 2.0, 0.0, 0.0, 0.0, 1.0, 0.0]
        sx, sy = self.out_w / float(src_w), self.out_h / float(src_h)
        K = [K[0] * sx, K[1], K[2] * sx, K[3], K[4] * sy, K[5] * sy, K[6], K[7], K[8]]
        P = [P[0] * sx, P[1], P[2] * sx, P[3] * sx, P[4], P[5] * sy, P[6] * sy, P[7],
             P[8], P[9], P[10], P[11]]
        info = CameraInfo()
        info.width, info.height = self.out_w, self.out_h
        info.distortion_model = model
        info.d, info.k, info.r, info.p = D, K, R, P
        info.header.frame_id = self.frame_id
        self.info = info

    # -------------------------------------------------------------------- reader
    def _open(self):
        src = int(self.url) if self.url.isdigit() else self.url
        cap = cv2.VideoCapture(src, cv2.CAP_FFMPEG)
        try:
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        except Exception:
            pass
        return cap if cap.isOpened() else None

    def _reader(self):
        while not self._stop.is_set():
            cap = self._open()
            if cap is None:
                self.get_logger().warn(f'cannot open {self.url}, retry in {self.reconnect_s}s',
                                       throttle_duration_sec=5.0)
                self._stop.wait(self.reconnect_s)
                continue
            fails = 0
            # Video FILES (replay/testing) are paced at their native frame rate and looped;
            # live streams are paced by the sender.
            is_file = os.path.isfile(self.url)
            period = 0.0
            if is_file:
                file_fps = cap.get(cv2.CAP_PROP_FPS)
                period = 1.0 / file_fps if file_fps and file_fps > 1.0 else 1.0 / 30.0
            next_t = time.monotonic()
            while not self._stop.is_set():
                ok, frame = cap.read()
                if not ok or frame is None:
                    if is_file:        # end of file: loop immediately
                        break
                    fails += 1
                    self.read_failures += 1
                    if fails >= 30:
                        break
                    continue
                fails = 0
                stamp = self.get_clock().now()
                with self._lock:
                    self._frame, self._frame_stamp = frame, stamp
                    self._seq += 1
                self.frames_read += 1
                if is_file:
                    next_t += period
                    delay = next_t - time.monotonic()
                    if delay > 0:
                        self._stop.wait(delay)
                    else:
                        next_t = time.monotonic()
            cap.release()
            if is_file:
                continue
            self.reconnects += 1
            self.get_logger().warn('stream lost, reconnecting', throttle_duration_sec=5.0)
            self._stop.wait(self.reconnect_s)

    # ----------------------------------------------------------------- publishing
    def _publish(self):
        with self._lock:
            frame, stamp, seq = self._frame, self._frame_stamp, self._seq
        if frame is None or seq == self._pub_seq:
            return
        age = (self.get_clock().now() - stamp).nanoseconds * 1e-9
        if age > self.stale:
            if not self._stale_warned:
                self.get_logger().warn(f'frame is {age:.1f}s old, not publishing')
                self._stale_warned = True
            return
        self._stale_warned = False
        self._pub_seq = seq
        if frame.shape[1] != self.out_w or frame.shape[0] != self.out_h:
            frame = cv2.resize(frame, (self.out_w, self.out_h), interpolation=cv2.INTER_AREA)
        st = stamp.to_msg()
        if self.stamp_offset:
            t = stamp.nanoseconds - int(self.stamp_offset * 1e9)
            st.sec, st.nanosec = divmod(max(t, 0), 1_000_000_000)
        msg = Image()
        msg.header.stamp, msg.header.frame_id = st, self.frame_id
        msg.height, msg.width = frame.shape[0], frame.shape[1]
        msg.encoding, msg.is_bigendian = 'bgr8', 0
        msg.step = frame.shape[1] * 3
        # array.array('B') is ~1000x faster than assigning bytes to Image.data in rclpy/Humble
        buf = array.array('B')
        buf.frombytes(memoryview(np.ascontiguousarray(frame).reshape(-1)))
        msg.data = buf
        self.info.header.stamp = st
        self.pub_img.publish(msg)
        self.pub_info.publish(self.info)
        self.published += 1

    def _report(self):
        now = time.monotonic()
        dt = now - self._stats_t
        rd, pb = self.frames_read - self._stats_read, self.published - self._stats_pub
        self._stats_t, self._stats_read, self._stats_pub = now, self.frames_read, self.published
        self.get_logger().info(
            f'decode {rd / dt:.1f} fps, publish {pb / dt:.1f} Hz, '
            f'read failures {self.read_failures}, reconnects {self.reconnects}')

    def shutdown(self):
        self._stop.set()


def main():
    rclpy.init()
    node = RtspCamera()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.shutdown()
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
