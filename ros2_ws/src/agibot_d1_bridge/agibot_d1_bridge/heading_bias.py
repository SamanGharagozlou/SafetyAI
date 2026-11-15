"""Gyro/heading bias removal with zero-velocity updates (no ROS dependency).

Measured on the D1: the SDK yaw drifts at a constant -9.4 deg/min (gyro z bias -0.0029 rad/s, stable over 230 s) even when
the robot is perfectly still. Whenever the robot is stationary its true yaw rate is zero, so the slope of the SDK yaw over
a stationary stretch IS the bias. We estimate it and subtract its integral from the yaw.

  hb = HeadingBias(); yaw_corrected = hb.update(t, yaw_sdk, gyro_z, body_speed)

yaw_sdk may wrap; it is unwrapped internally. The returned yaw is continuous (unwrapped) minus the integrated bias.
"""
import math


class HeadingBias:
    def __init__(self, min_still_s=4.0, window_s=12.0, speed_thr=0.02, gyro_thr=0.02, alpha=0.3):
        self.min_still, self.window = min_still_s, window_s
        self.speed_thr, self.gyro_thr, self.alpha = speed_thr, gyro_thr, alpha
        self.bias = 0.0                     # rad/s of yaw drift (SDK yaw rate when truly still)
        self.have_bias = False
        self.B = 0.0                        # integral of the bias since start (rad)
        self._last_raw = None
        self._unwrapped = 0.0
        self._last_t = None
        self._seg = []                      # (t, unwrapped yaw) samples of the current stationary stretch
        self.updates = 0

    def update(self, t, yaw_raw, gyro_z, speed):
        # unwrap
        if self._last_raw is None:
            self._unwrapped = yaw_raw
        else:
            d = yaw_raw - self._last_raw
            d = (d + math.pi) % (2 * math.pi) - math.pi
            self._unwrapped += d
        self._last_raw = yaw_raw
        # apply the bias integral so far
        if self._last_t is not None:
            dt = t - self._last_t
            if 0.0 < dt < 1.0:
                self.B += self.bias * dt
        self._last_t = t
        # zero-velocity detection and bias estimation
        still = speed < self.speed_thr and abs(gyro_z) < self.gyro_thr
        if still:
            self._seg.append((t, self._unwrapped))
            while self._seg and t - self._seg[0][0] > self.window:
                self._seg.pop(0)
            dur = self._seg[-1][0] - self._seg[0][0]
            if dur >= self.min_still and len(self._seg) >= 20:
                n = len(self._seg)
                mt = sum(s[0] for s in self._seg) / n
                my = sum(s[1] for s in self._seg) / n
                num = sum((s[0] - mt) * (s[1] - my) for s in self._seg)
                den = sum((s[0] - mt) ** 2 for s in self._seg)
                if den > 1e-6:
                    slope = num / den
                    if abs(slope) < 0.02:   # sanity: < 1.1 deg/s
                        w = 1.0 if not self.have_bias else self.alpha
                        # the segment yaw is the RAW unwrapped yaw: its slope is the raw drift, i.e. the full bias
                        self.bias = (1 - w) * self.bias + w * slope if self.have_bias else slope
                        self.have_bias = True
                        self.updates += 1
                        self._seg = self._seg[-max(5, len(self._seg) // 3):]
        else:
            self._seg = []
        return self._unwrapped - self.B
