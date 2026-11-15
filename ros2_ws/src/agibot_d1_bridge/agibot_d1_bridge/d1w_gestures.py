#!/usr/bin/env python3
"""Gesture control for the AgiBot D1 Ultra-W (SDK model ZSL-1W) via HighLevel.attitudeControl.

attitudeControl(roll_vel, pitch_vel, yaw_vel, height_vel) takes body *rates*
(rad/s and m/s, each limited to +/-0.5 by the SDK), not target poses. So a gesture
here is a list of timed rate segments whose integrals cancel on every axis: the body
ends where it started. Each segment follows a half-sine profile so rates start and
end at zero (no jerk at segment boundaries).

Usage:
  python3 d1w_gestures.py --list
  python3 d1w_gestures.py --dry-run nod shake          # no robot, mock backend
  ros2 run agibot_d1_bridge d1w_gestures --local-ip 192.168.234.100 [--stand] [--scale 1.0] nod
  (SDK found via --sdk-lib, $AGIBOT_SDK_DIR, or the third_party submodule.
   Do not run while cmd_vel_bridge is connected - both open the SDK link.)
  (no gesture names -> interactive prompt)

SDK bindings are built for CPython 3.10 (cpython-310 .so files).
"""
import argparse
import math
import os
import platform
import sys
import time
from dataclasses import dataclass, replace

SDK_LIMIT = 0.5      # per-axis limit from api_zsl-1w.md
SAFE_LIMIT = 0.45    # our cap, margin under the SDK limit
GESTURE_RATE = 0.44  # peak rate used to time angle-based gestures (<= SAFE_LIMIT)
RATE_HZ = 50
MODE_PASSIVE, MODE_STAND, MODE_MOVE = 0, 1, 3   # getCurrentCtrlmode() values
DEFAULT_PORT = 43988                            # port used in the vendor demos

# Sign convention is NOT verified on hardware. Assumed ROS-style body frame
# (x fwd, y left, z up) => +pitch = nose down. Flip to -1 if "bow" looks up.
PITCH_NOSE_DOWN = +1


@dataclass(frozen=True)
class Seg:
    """One gesture segment: peak rates, shaped by a half-sine over `duration` seconds."""
    duration: float
    roll: float = 0.0
    pitch: float = 0.0
    yaw: float = 0.0
    height: float = 0.0

    @property
    def peaks(self):
        return (self.roll, self.pitch, self.yaw, self.height)

    def rates_at(self, t):
        s = math.sin(math.pi * t / self.duration)
        return tuple(p * s for p in self.peaks)

    def displacement(self):
        # integral of p*sin(pi t / T) over [0, T] = 2pT/pi
        k = 2.0 * self.duration / math.pi
        return tuple(p * k for p in self.peaks)

    def scaled(self, f):
        return replace(self, roll=self.roll * f, pitch=self.pitch * f,
                       yaw=self.yaw * f, height=self.height * f)


def hold(d):
    return Seg(d)


def turn(duration=None, **amounts):
    """Segment that moves the body by `amounts` (roll/pitch/yaw in rad, height in m).

    A half-sine of peak p over T moves 2pT/pi, so p = amount*pi/(2T). With no
    `duration`, T is the shortest that keeps the largest *angle* at GESTURE_RATE.
    """
    if duration is None:
        biggest = max(abs(v) for k, v in amounts.items() if k != "height")
        duration = math.pi * biggest / (2 * GESTURE_RATE)
    return Seg(duration, **{k: v * math.pi / (2 * duration) for k, v in amounts.items()})


def _alternate(axis, amp, cycles):
    """Oscillate on one axis by +-amp rad: out, (-,+) full swings, back to centre."""
    segs = [turn(**{axis: amp})]
    for i in range(2 * cycles - 1):
        segs.append(turn(**{axis: -2 * amp if i % 2 == 0 else 2 * amp}))
    segs.append(turn(**{axis: -amp if (2 * cycles - 1) % 2 == 0 else amp}))
    return segs


# Amplitudes are body angles (rad) / height (m). Reference: the vendor demo holds
# attitudeControl(0.1, 0.1, 0.1, 0.1) for 4 s, i.e. ~0.4 rad (23 deg) per axis.
# Untested on hardware: the SDK may clamp posture internally below these.
P = PITCH_NOSE_DOWN
GESTURES = {
    # "yes": nose down/up twice, 14 deg
    "nod": [turn(pitch=0.25 * P), turn(pitch=-0.25 * P)] * 2,
    # "no": yaw +-14 deg, left-right-left, back to centre
    "shake": _alternate("yaw", 0.25, 2),
    # curious head tilt: roll 17 deg, hold, return
    "tilt": [turn(roll=0.3), hold(1.0), turn(roll=-0.3)],
    # bow: nose down 20 deg + lower body 6 cm, hold, return
    "bow": [turn(pitch=0.35 * P, height=-0.06), hold(1.0),
            turn(pitch=-0.35 * P, height=0.06)],
    # squat: lower body 10 cm, hold, rise
    "squat": [turn(0.8, height=-0.10), hold(0.8), turn(0.8, height=0.10)],
    # side-to-side wiggle, roll +-9 deg
    "wiggle": _alternate("roll", 0.15, 2),
    # look left 23 deg, then right, then centre
    "look_around": [turn(yaw=0.4), hold(0.6), turn(yaw=-0.8), hold(0.6), turn(yaw=0.4)],
    # body traces a loop in roll/pitch, 14 deg
    "sway_circle": [turn(roll=0.25), turn(pitch=0.25), turn(roll=-0.25),
                    turn(pitch=-0.25)],
}

# Sequences mixing our attitude gestures with built-in SDK actions.
#   ("gesture", name)          -> play a GESTURES entry
#   ("sdk", method, wait_s)    -> call HighLevel.<method>() and wait for it to finish
# shakeHand() is the SDK's own greeting (doc: 打招呼 "say hello"). The 4 s wait is
# taken from the vendor demo; actual duration unverified.
SEQUENCES = {
    "greet": [("gesture", "bow"), ("sdk", "shakeHand", 4.0)],
}
SEQUENCES["welcome"] = SEQUENCES["greet"]


def validate(segs, limit=SAFE_LIMIT, tol=1e-9):
    """Raise ValueError unless every peak is within `limit` and net motion is zero."""
    net = [0.0] * 4
    for s in segs:
        if s.duration <= 0:
            raise ValueError(f"non-positive duration in {s}")
        if any(abs(p) > limit for p in s.peaks):
            raise ValueError(f"peak above {limit} in {s}")
        net = [n + d for n, d in zip(net, s.displacement())]
    if any(abs(n) > tol for n in net):
        raise ValueError(f"gesture does not return to start, net={net}")


def all_names():
    return list(GESTURES) + list(SEQUENCES)


def validate_sequences():
    for name, steps in SEQUENCES.items():
        for kind, *arg in steps:
            if kind == "gesture" and arg[0] not in GESTURES:
                raise ValueError(f"sequence {name!r} references unknown gesture {arg[0]!r}")
            if kind == "sdk" and not hasattr(MockHighLevel, arg[0]):
                raise ValueError(f"sequence {name!r} calls unknown SDK method {arg[0]!r}")
            if kind not in ("gesture", "sdk"):
                raise ValueError(f"sequence {name!r} has bad step kind {kind!r}")


def describe(name):
    """Approximate duration in seconds (sequence durations exclude standUp resets)."""
    if name in GESTURES:
        return sum(s.duration for s in GESTURES[name])
    return sum(describe(a[0]) if k == "gesture" else a[1] for k, *a in SEQUENCES[name])


class SdkError(RuntimeError):
    pass


class GestureController:
    def __init__(self, robot, rate_hz=RATE_HZ, clock=time.monotonic, sleep=time.sleep,
                 reset_with_standup=True, log=print):
        self.robot = robot
        self.period = 1.0 / rate_hz
        self.clock, self.sleep, self.log = clock, sleep, log
        # The vendor demo calls standUp() after attitudeControl to "return to a stable
        # state". Assumed to re-centre posture; unverified on hardware.
        self.reset_with_standup = reset_with_standup

    def _send(self, r, p, y, h):
        ret = self.robot.attitudeControl(float(r), float(p), float(y), float(h))
        if ret not in (0, None):
            raise SdkError(f"attitudeControl returned {ret}")

    def _require_standing(self):
        mode = self.robot.getCurrentCtrlmode()
        if mode != MODE_STAND:
            raise SdkError(f"robot must be in standing mode (1), current mode={mode}")

    def greet(self, scale=1.0):
        """Welcome gesture: small bow, then the SDK's built-in shakeHand greeting."""
        self.play("greet", scale)

    def call_sdk(self, method, wait_s):
        self._require_standing()
        self.log(f"sdk: {method}()")
        ret = getattr(self.robot, method)()
        if ret not in (0, None):
            raise SdkError(f"{method} returned {ret}")
        self.sleep(wait_s)
        if self.reset_with_standup:
            self.robot.standUp()
            self.sleep(1.0)

    def play(self, name, scale=1.0):
        if not 0.0 < scale <= 1.0:
            raise ValueError("scale must be in (0, 1]")
        if name in SEQUENCES:
            self.log(f"sequence: {name}")
            for kind, *arg in SEQUENCES[name]:
                if kind == "gesture":
                    self.play(arg[0], scale)
                else:
                    self.call_sdk(*arg)
            return
        if name not in GESTURES:
            raise KeyError(f"unknown gesture {name!r}; known: {', '.join(all_names())}")
        self._require_standing()
        segs = [s.scaled(scale) for s in GESTURES[name]]
        validate(segs)

        self.log(f"gesture: {name} (scale {scale})")
        try:
            for seg in segs:
                t0 = self.clock()
                while (t := self.clock() - t0) < seg.duration:
                    self._send(*seg.rates_at(t))
                    self.sleep(self.period)
        finally:
            try:
                self._send(0, 0, 0, 0)   # always stop body motion, even on Ctrl+C
            except Exception as e:      # don't mask the original error
                self.log(f"WARNING: failed to send zero attitude: {e}")
        if self.reset_with_standup:
            self.robot.standUp()
            self.sleep(1.0)


class MockHighLevel:
    """Stand-in for mc_sdk_zsl_1w_py.HighLevel for offline runs."""
    def __init__(self):
        self.mode = MODE_STAND
        self.calls = []

    def initRobot(self, local_ip, local_port, dog_ip="192.168.234.1"):
        pass

    def checkConnect(self):
        return True

    def getCurrentCtrlmode(self):
        return self.mode

    def getBatteryPower(self):
        return 100

    def standUp(self):
        self.mode = MODE_STAND
        return 0

    def lieDown(self):
        self.mode = MODE_PASSIVE
        return 0

    def passive(self):
        self.mode = MODE_PASSIVE
        return 0

    def shakeHand(self):
        self.calls.append("shakeHand")
        return 0

    def attitudeControl(self, r, p, y, h):
        self.calls.append((r, p, y, h))
        return 0


SDK_SUBMODULE = os.path.join("third_party", "agibot_D1_Edu-Ultra")


def find_sdk_lib(override):
    """Same lookup order as cmd_vel_bridge: override, $AGIBOT_SDK_DIR, then the
    third_party submodule found by walking up from this file."""
    arch = platform.machine().replace("amd64", "x86_64").replace("arm64", "aarch64")
    candidates = [override] if override else []
    if os.environ.get("AGIBOT_SDK_DIR"):
        candidates.append(os.path.join(os.environ["AGIBOT_SDK_DIR"], "lib", "zsl-1w", arch))
    here = os.path.dirname(os.path.realpath(__file__))
    while here != os.path.dirname(here):
        candidates.append(os.path.join(here, SDK_SUBMODULE, "lib", "zsl-1w", arch))
        here = os.path.dirname(here)
    for c in candidates:
        if os.path.isdir(c):
            return c
    sys.exit("AgiBot SDK lib not found. Run `git submodule update --init`, or set "
             "AGIBOT_SDK_DIR / --sdk-lib. Tried: " + ", ".join(candidates))


def load_sdk(lib_dir):
    if sys.version_info[:2] != (3, 10):
        sys.exit(f"SDK bindings are cpython-310; this is Python "
                 f"{sys.version_info.major}.{sys.version_info.minor}. Use a 3.10 env.")
    sys.path.insert(0, find_sdk_lib(lib_dir))
    try:
        import mc_sdk_zsl_1w_py
    except ImportError as e:
        sys.exit(f"cannot import mc_sdk_zsl_1w_py ({e})")
    return mc_sdk_zsl_1w_py.HighLevel()


def connect(args):
    if args.dry_run:
        return MockHighLevel()
    if not args.local_ip:
        sys.exit("--local-ip (this PC's IP on the robot network) is required")
    robot = load_sdk(args.sdk_lib)
    robot.initRobot(args.local_ip, args.port, args.robot_ip)
    deadline = time.monotonic() + 5.0
    while not robot.checkConnect():
        if time.monotonic() > deadline:
            sys.exit("checkConnect() false after 5 s - check IPs and robot-side SDK config")
        time.sleep(0.2)
    print(f"connected; battery {robot.getBatteryPower()}%, "
          f"mode {robot.getCurrentCtrlmode()}")
    return robot


def interactive(ctl, robot, scale):
    print("commands: <gesture> | list | stand | lie | estop (passive/damping) | quit")
    while True:
        try:
            cmd = input("> ").strip()
        except EOFError:
            return
        if cmd in ("quit", "q", "exit"):
            return
        if cmd == "list":
            print(", ".join(all_names()))
        elif cmd == "stand":
            print("standUp ->", robot.standUp())
        elif cmd == "lie":
            print("lieDown ->", robot.lieDown())
        elif cmd == "estop":
            print("passive ->", robot.passive())
        elif cmd:
            try:
                ctl.play(cmd, scale)
            except (KeyError, SdkError, ValueError) as e:
                print("error:", e)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("gestures", nargs="*", help="gesture names to play in order")
    ap.add_argument("--list", action="store_true", help="list gestures and exit")
    ap.add_argument("--dry-run", action="store_true", help="use mock robot, no SDK")
    ap.add_argument("--local-ip", help="this PC's IP on the robot network")
    ap.add_argument("--robot-ip", default="192.168.234.1")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--sdk-lib", help="dir containing mc_sdk_zsl_1w_py*.so (default: "
                                      "$AGIBOT_SDK_DIR or third_party submodule)")
    ap.add_argument("--scale", type=float, default=1.0,
                    help="rate multiplier in (0,1], default 1.0")
    ap.add_argument("--stand", action="store_true", help="call standUp() before starting")
    ap.add_argument("--no-reset", action="store_true",
                    help="skip standUp() re-centre after each gesture")
    args = ap.parse_args(argv)

    for name, segs in GESTURES.items():
        validate(segs)   # refuse to run with a bad gesture table
    validate_sequences()

    if args.list:
        for name in all_names():
            tag = "  (sequence)" if name in SEQUENCES else ""
            print(f"{name:12s} {describe(name):.1f} s{tag}")
        return 0

    robot = connect(args)
    if args.stand:
        robot.standUp()
        time.sleep(3)
    ctl = GestureController(robot, reset_with_standup=not args.no_reset,
                            sleep=(lambda dt: None) if args.dry_run else time.sleep,
                            clock=_fake_clock() if args.dry_run else time.monotonic)
    try:
        if args.gestures:
            for g in args.gestures:
                ctl.play(g, args.scale)
        else:
            interactive(ctl, robot, args.scale)
    except KeyboardInterrupt:
        print("\ninterrupted; zero attitude sent")
    if args.dry_run:
        sdk = [c for c in robot.calls if isinstance(c, str)]
        print(f"dry run: {len(robot.calls) - len(sdk)} attitudeControl calls, "
              f"SDK actions: {sdk or 'none'}")
    return 0


def _fake_clock():
    """Clock for dry runs that advances one control period per call."""
    state = {"t": 0.0}

    def clock():
        state["t"] += 1.0 / RATE_HZ
        return state["t"]
    return clock


if __name__ == "__main__":
    sys.exit(main())
