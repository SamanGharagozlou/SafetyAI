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
import os
import subprocess
import sys
import time


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


def _grab(rtsp, path):
    cmd = ["ffmpeg", "-y", "-rtsp_transport", "tcp", "-i", rtsp,
           "-frames:v", "1", path]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    return r.returncode == 0


def _shift_px(before, after):
    """Horizontal pixel shift between two frames, by phase correlation.

    Measured on a band across the middle of the frame: the centre of a 122 deg
    lens is the least distorted part, and the floor and sky at top and bottom
    carry the least usable texture.
    """
    import cv2
    import numpy as np
    a = cv2.imread(before, cv2.IMREAD_GRAYSCALE)
    b = cv2.imread(after, cv2.IMREAD_GRAYSCALE)
    if a is None or b is None or a.shape != b.shape:
        return None, None
    h, w = a.shape
    y1, y2 = int(h * 0.35), int(h * 0.65)
    band_a = np.float32(a[y1:y2, :]) * np.hanning(w)
    band_b = np.float32(b[y1:y2, :]) * np.hanning(w)
    (dx, _dy), response = cv2.phaseCorrelate(band_a, band_b)
    return dx, response


def turnrate(a):
    """Command a known turn and measure what the camera says actually happened.

    move_robot takes a duration and a SPEED FRACTION, never an angle, and the
    bridge does not forward getRPY() - so without this the escort's bearings
    rest on a constant nobody measured. Turning the camera into the protractor
    closes that loop without extra hardware.
    """
    try:
        import cv2  # noqa: F401
    except ImportError:
        print("Needs OpenCV for this. Without it, measure by hand: chalk a "
              "line under the robot, command one turn, and read the angle off "
              "the floor with a protractor.", file=sys.stderr)
        return 1

    import json
    import tempfile
    import urllib.request

    tmp = tempfile.mkdtemp(prefix="d1turn-")
    rates = []
    print("Commanding %d turn(s) of %.1fs at speed %.2f.\n"
          "Give the robot clear space - it will rotate in place.\n"
          % (a.repeats, a.duration, a.speed))

    for i in range(a.repeats):
        before = os.path.join(tmp, "b%d.png" % i)
        after = os.path.join(tmp, "a%d.png" % i)
        if not _grab(a.rtsp, before):
            print("could not pull a frame from %s" % a.rtsp, file=sys.stderr)
            return 1

        body = json.dumps({"message": {"type": "tool-calls", "toolCallList": [
            {"id": "cal", "name": "move_robot",
             "arguments": {"direction": "turn_left",
                           "duration": a.duration,
                           "speed": a.speed}}]}}).encode()
        req = urllib.request.Request(
            a.robot.rstrip("/") + "/vapi", data=body, method="POST",
            headers={"Content-Type": "application/json",
                     "Authorization": "Bearer %s" % a.token})
        try:
            urllib.request.urlopen(req, timeout=a.duration + 20).read()
        except Exception as exc:
            print("turn command failed: %s" % exc, file=sys.stderr)
            return 1

        time.sleep(1.2)                      # let the body settle
        if not _grab(a.rtsp, after):
            print("could not pull the second frame", file=sys.stderr)
            return 1

        dx, response = _shift_px(before, after)
        if dx is None:
            print("  run %d: frames unusable" % (i + 1))
            continue
        # A turn to the LEFT sweeps the scene to the right in the image.
        w = cv2.imread(before, cv2.IMREAD_GRAYSCALE).shape[1]
        deg = abs(dx) / w * a.hfov
        rate = deg / a.duration
        rates.append(rate)
        flag = "" if (response or 0) > 0.05 else "   <- weak match, distrust"
        print("  run %d: %+.0f px  =  %.1f deg  ->  %.1f deg/s  (corr %.3f)%s"
              % (i + 1, dx, deg, rate, response or 0, flag))

    if not rates:
        print("\nNo usable measurements.", file=sys.stderr)
        return 1

    rates.sort()
    med = rates[len(rates) // 2]
    spread = max(rates) - min(rates)
    print("\n  median %.1f deg/s at speed %.2f   (spread %.1f)" %
          (med, a.speed, spread))
    print("  escort.py --turn-speed %.2f --turn-deg-per-sec %.1f"
          % (a.speed, med))
    if spread > med * 0.25:
        print("\n  The spread is wide. Phase correlation needs texture - a "
              "blank wall or an empty forecourt gives it nothing to lock onto. "
              "Re-run facing something with detail in it.")
    print("\n  This stays open-loop whatever the number says. The real fix is "
          "forwarding getRPY() from the agent so turns can close the loop.")
    return 0


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

    t = sub.add_parser("turnrate", help="measure how fast the robot actually "
                                       "turns, using the camera itself")
    t.add_argument("--rtsp", default="rtsp://192.168.168.168:8554/test")
    t.add_argument("--robot", default="http://127.0.0.1:8090")
    t.add_argument("--token", default="")
    t.add_argument("--duration", type=float, default=1.0)
    t.add_argument("--speed", type=float, default=0.25)
    t.add_argument("--hfov", type=float, default=122.0)
    t.add_argument("--repeats", type=int, default=3)

    a = ap.parse_args()

    if a.cmd == "turnrate":
        return turnrate(a)

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
