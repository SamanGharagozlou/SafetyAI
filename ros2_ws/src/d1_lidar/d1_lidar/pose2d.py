"""Planar (x, y, yaw) transforms, no ROS imports. T_a_b = pose of frame b expressed in frame a."""
import math


def wrap(a):
    return (a + math.pi) % (2 * math.pi) - math.pi


def mul(a, b):
    """T_a_c = T_a_b * T_b_c"""
    c, s = math.cos(a[2]), math.sin(a[2])
    return (a[0] + c * b[0] - s * b[1], a[1] + s * b[0] + c * b[1], wrap(a[2] + b[2]))


def inv(a):
    c, s = math.cos(a[2]), math.sin(a[2])
    return (-(c * a[0] + s * a[1]), -(-s * a[0] + c * a[1]), wrap(-a[2]))


def blend(old, new, alpha):
    """Move `old` toward `new` by alpha (0..1), yaw on the circle."""
    return (old[0] + alpha * (new[0] - old[0]), old[1] + alpha * (new[1] - old[1]), wrap(old[2] + alpha * wrap(new[2] - old[2])))


def from_quat(x, y, z, w):
    """yaw of the body x-axis projected on the ground plane (robust to small roll/pitch)."""
    r00 = 1 - 2 * (y * y + z * z)
    r10 = 2 * (x * y + z * w)
    return math.atan2(r10, r00)
