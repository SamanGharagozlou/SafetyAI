#!/usr/bin/env python3
"""
One-time ground-plane calibration for the D1 camera.

Everything the guard believes about distance rests on two numbers: how high the
camera is off the floor, and how far it tilts down. Get them wrong and the
guard is not noisy, it is CONFIDENTLY WRONG BY A CONSTANT FACTOR - it will call
1.2 m "2 m" all day without a flicker of doubt. That is why guard.py refuses to
run at full speed until --calibrated is passed.

The spec sheet gives a standing height of 420 mm, but the camera is not at the
top of the body and the mounting tilt is not published, so it must be measured.

    # 1. stand the robot up on flat floor, put tape on the ground straight
    #    ahead at exactly 1.0 m and 3.0 m from the feet
    python3 calibrate.py grab --rtsp rtsp://192.168.168.168:8554/test

    # 2. open frame.png, read off the pixel ROW (y) of each tape mark
    # 3. solve
    python3 calibrate.py solve --near 1.0 460 --far 3.0 395

It prints the flags to paste into the service file. Use two marks far apart;
two close together make the fit ill-conditioned and the error blows up at range.
"""

import argparse
import math
import subprocess
import sys


def row_for(range_m, cam_h, tilt_deg, fy, img_h):
    """Pixel row where a floor point at `range_m` lands. Forward model."""
    below_horizon = math.atan(cam_h / range_m)
    below_axis = below_horizon - math.radians(tilt_deg)
    return img_h / 2 + math.tan(below_axis) * fy


def solve(near_m, near_y, far_m, far_y, vfov=70.0, img_h=720):
    """Recover (height, tilt) from two known floor points.

    Two unknowns, two observations. The residual is monotonic in tilt once
    height is eliminated, so a bisection is both sufficient and robust - no
    numpy, no optimiser, nothing to misconfigure.
    """
    fy = (img_h / 2) / math.tan(math.radians(vfov / 2))

    def height_from(tilt, r, y):
        below_axis = math.atan2(y - img_h / 2, fy)
        below_horizon = math.radians(tilt) + below_axis
        if below_horizon <= 1e-6:
            return None
        return r * math.tan(below_horizon)

    def residual(tilt):
        a = height_from(tilt, near_m, near_y)
        b = height_from(tilt, far_m, far_y)
        if a is None or b is None:
            return None
        return a - b

    # The tilt cannot be arbitrary: for a mark imaged ABOVE the optical axis
    # (negative below_axis - which the far mark usually is) the ray only points
    # at the floor once the tilt exceeds that angle. Below that bound the model
    # has no solution at all, so bracketing from -20 degrees finds nothing.
    floor_tilt = max(-math.degrees(math.atan2(y - img_h / 2, fy))
                     for y in (near_y, far_y))
    start = floor_tilt + 1e-3

    # There are TWO roots. The second is an artefact of tan() running away near
    # 90 degrees - it fits the marks with an absurd camera height several metres
    # up. Scanning upward and taking the FIRST sign change picks the physical
    # one; bracketing the whole range straddles both and finds neither.
    lo = hi = None
    prev_t, prev_r = start, residual(start)
    t = start
    while t < 60.0:
        t += 0.25
        r = residual(t)
        if r is None:
            prev_t, prev_r = t, r
            continue
        if prev_r is not None and prev_r * r <= 0:
            lo, hi, rlo = prev_t, t, prev_r
            break
        prev_t, prev_r = t, r
    if lo is None:
        return None, None, fy

    for _ in range(200):
        mid = (lo + hi) / 2
        rm = residual(mid)
        if rm is None:
            lo = mid
            continue
        if rm * rlo <= 0:
            hi = mid
        else:
            lo, rlo = mid, rm
    tilt = (lo + hi) / 2
    h = height_from(tilt, near_m, near_y)
    return h, tilt, fy


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("grab", help="save one frame from the robot camera")
    g.add_argument("--rtsp", default="rtsp://192.168.168.168:8554/test")
    g.add_argument("--out", default="frame.png")

    s = sub.add_parser("solve", help="fit height and tilt to two floor marks")
    s.add_argument("--near", nargs=2, metavar=("METRES", "PIXEL_ROW"),
                   required=True, type=float)
    s.add_argument("--far", nargs=2, metavar=("METRES", "PIXEL_ROW"),
                   required=True, type=float)
    s.add_argument("--vfov", type=float, default=70.0)
    s.add_argument("--img-height", type=int, default=720)

    a = ap.parse_args()

    if a.cmd == "grab":
        # ffmpeg rather than opencv: one less dependency, and it works on the
        # laptop as well as the Jetson.
        cmd = ["ffmpeg", "-y", "-rtsp_transport", "tcp", "-i", a.rtsp,
               "-frames:v", "1", "-q:v", "2", a.out]
        print("$ " + " ".join(cmd))
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            print(r.stderr[-800:], file=sys.stderr)
            print("\nFailed to pull a frame. Check the robot is standing, the "
                  "wired link is up, and the URL matches vendor doc 5.1.",
                  file=sys.stderr)
            return 1
        print("wrote %s - now read off the pixel row of each floor mark" % a.out)
        return 0

    near_m, near_y = a.near
    far_m, far_y = a.far
    if far_m <= near_m:
        print("--far must be further than --near", file=sys.stderr)
        return 2
    if near_y <= far_y:
        print("Suspicious input: the NEARER mark should sit LOWER in the frame "
              "(a larger pixel row) than the far one. Check you have not "
              "swapped them, or that your viewer measures y from the top.",
              file=sys.stderr)
        return 2

    h, tilt, fy = solve(near_m, near_y, far_m, far_y, a.vfov, a.img_height)
    if h is None or not (0.05 < h < 1.2):
        print("No plausible fit (height came out %s). Either a pixel row is "
              "misread, or the marks are too close together to constrain the "
              "fit." % ("%.2f m" % h if h else "undefined"), file=sys.stderr)
        return 3

    blind = h / math.tan(math.radians(tilt + a.vfov / 2))
    print()
    print("  camera height   %.3f m" % h)
    print("  tilt down       %.2f deg" % tilt)
    print("  focal length    %.0f px" % fy)
    print("  blind inside    %.2f m  <- nothing on the floor is visible here" % blind)
    print()
    print("  check: %.1f m should image at row %.0f (you measured %.0f)"
          % (near_m, row_for(near_m, h, tilt, fy, a.img_height), near_y))
    print("         %.1f m should image at row %.0f (you measured %.0f)"
          % (far_m, row_for(far_m, h, tilt, fy, a.img_height), far_y))
    print()
    print("  guard.py --cam-height %.3f --cam-tilt %.2f --calibrated" % (h, tilt))
    print()
    print("  Sanity-check it before you trust it: put a mark at a THIRD "
          "distance you did not fit to, and confirm the guard's nearest_m "
          "agrees to a few centimetres.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
