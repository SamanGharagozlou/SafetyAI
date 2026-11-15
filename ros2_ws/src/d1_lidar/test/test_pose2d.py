import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from d1_lidar import pose2d as P  # noqa: E402


def close(a, b, tol=1e-9):
    return all(abs(x - y) < tol for x, y in zip(a[:2], b[:2])) and abs(P.wrap(a[2] - b[2])) < tol


def test_inverse_and_compose():
    a = (1.0, -2.0, 0.7)
    assert close(P.mul(a, P.inv(a)), (0, 0, 0))
    assert close(P.mul(P.inv(a), a), (0, 0, 0))
    b = (0.3, 0.1, -2.5)
    assert close(P.mul(P.mul(a, b), P.inv(b)), a)


def test_calibration_recovers_truth():
    """place at P with scanner at ext on the robot; later lidar sample L2 must give the true base pose."""
    T_map_mi = (-3.0, 2.0, 1.0)                       # unknown to the localizer
    ext = (0.1, 0.05, 0.2)                            # base -> scanner body
    truth = lambda base: P.mul(P.mul(P.inv(T_map_mi), base), ext)   # lidar sample for a base pose in the map
    place = (0.5, 0.3, 0.2)
    est_T_map_mi = P.mul(P.mul(place, ext), P.inv(truth(place)))
    assert close(est_T_map_mi, T_map_mi)
    later = (2.0, 1.5, -0.9)
    est_base = P.mul(P.mul(est_T_map_mi, truth(later)), P.inv(ext))
    assert close(est_base, later)


def test_blend_wraps():
    r = P.blend((0, 0, math.radians(179)), (0, 0, math.radians(-179)), 0.5)
    assert abs(abs(math.degrees(r[2])) - 180) < 1e-6
