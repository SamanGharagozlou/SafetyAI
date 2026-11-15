"""AgiBot D1 Ultra-W (wheeled-leg quadruped, SDK model zsl-1w) bridge.

Three transports, selected by cfg["transport"] (env D1W_TRANSPORT overrides):
  * ros2 (default): through the SafetyAI team's ROS 2 node `d1_cmd_vel_bridge` (SafetyAI repo, branch develop,
    ros2_ws/src/agibot_d1_bridge). We publish `cmd_vel` (Twist) at 20 Hz while driving, a zero Twist at 5 Hz as a
    keepalive while we own the robot and it stands (idle or HOLD; so a bridge-side silence watchdog never damps it),
    and, once at connect, `gait` = "move" (logged, event gait.set). We subscribe odom / imu / battery / ctrl_mode /
    connected (all 10 Hz). Their node owns the ONE SDK connection.
  * sdk: the vendor Python binding mc_sdk_zsl_1w_py directly (Python 3.10 only, lib/zsl-1w/<arch>), with a 10 Hz
    keepalive move(0,0,0) while idle (no SDK traffic for 3 s -> the robot damps and lies down). Only when the
    operator sets D1W_TRANSPORT=sdk AND the SDK port is free on this host (nobody else owns the SDK).
  * mock: an in-process kinematic twin (FakeBridge maths, odometry frame + ctrl_mode + IMU + battery), so the whole
    edge stack runs on a laptop / the Spark.

Hard rules (tests/test_agibot_d1w_bridge.py):
  * caps: 0.4 m/s forward (0.3 guide pace comes from the behaviour policy), 0.15 m/s lateral, 0.6 rad/s; config can
    only lower them. NaN/inf -> 0. Below the SDK minimum (vx 0.05, vy 0.1, wz 0.1) a component is sent as exactly 0.
  * stop() = zero cmd_vel at once (repeated), then the robot holds its stance. NEVER lie_down / damp / passive from
    a skill or a stop path: no client for ~/lie_down or ~/passive is ever created, no publisher on robot_cmd or
    attitude_cmd. damp() calls ~/damp only with allow_damp: true (literal), default false.
  * stand() calls ~/stand_up only with auto_stand: true (literal); otherwise it only confirms the robot is standing
    (the operator stands it with the controller).
  * shake_hand() (greeting gesture) only with allow_greeting_gesture: true (literal), default false, only while
    stationary; through the team bridge's ~/shake_hand service (SDK shakeHand()), or, when greeting_robot_cmd is set
    (default none), that ONE string on robot_cmd (publisher created on first use; posture / locomotion words refused).
    skills.greet() adds safety NORMAL, nobody within 1.5 m, battery > 30 %. D1W_CMD_VEL renames the cmd_vel topic
    (behind twist_mux: /cmd_vel_edge, config/twist_mux.yaml).
  * gait locked to "move" (never crawl / climb).
  * tilt (IMU roll/pitch) over 15 deg -> zero velocity, refuse motion until level again for 1 s.
  * no state for 1 s (or connected == false) -> zero velocity + one `handover` event; no exception loop.
Pose: odometry (SDK getPosition via their node, frame odom -> base_link) through a calibrated 2D rigid transform
`odom_to_zone` [tx, ty, theta] (`python -m bridge.zones --calibrate --config config/d1w.yaml`, three fixes at named
landmarks). refix_at_landmark(name) re-zeroes drift when the robot stands at a known feature.
"""

from __future__ import annotations

import math
import os
import platform
import re
import socket
import sys
import threading
import time
import types
from dataclasses import dataclass
from typing import Any

from .base import NOT_STANDING_MSG, RobotBridge, RobotState, cfg_flag
from .paths import REPO_ROOT
from .zones import Zones

# ---------------------------------------------------------------- limits
MAX_VX = 0.4  # m/s hard cap (guide pace 0.3 is the behaviour policy's zone cap)
MAX_VY = 0.15
MAX_WZ = 0.6
SDK_MIN = (0.05, 0.1, 0.1)  # zsl-1w "move" gait minimums: below them the value must be exactly 0
TILT_MAX_DEG = 15.0
LEVEL_AGAIN_S = 1.0
STATE_TIMEOUT_S = 1.0
MIN_BATTERY_PCT = 25.0  # the SDK refuses under 20 %; we stop asking before that
CTRL_MODE = {0: "damped", 1: "standing", 3: "move", 51: "lying"}  # as observed by the SafetyAI team
UPRIGHT_MODES = (1, 3)
FORBIDDEN_SERVICES = ("lie_down", "passive")  # never created, under any flag
FORBIDDEN_TOPICS = ("robot_cmd", "attitude_cmd")  # never a target for cmd_vel / gait / say
# robot_cmd carries ONE thing from us: the configured greeting gesture string (greeting_robot_cmd, default none),
# published by greet() only. Anything that changes posture or locomotion is refused, whatever the config says.
GESTURE_DENY = (
    "lie", "damp", "passive", "stand", "sit", "crawl", "climb", "jump", "walk", "move", "run", "gait", "cancel",
    "stop", "recover", "roll", "flip", "dance", "spin", "trot", "attitude", "estop",
)  # fmt: skip


def gesture_cmd_ok(cmd: str | None) -> bool:
    c = str(cmd or "").strip().lower()
    return bool(re.fullmatch(r"[a-z][a-z_]{1,31}", c)) and not any(d in c for d in GESTURE_DENY)


DEFAULTS: dict[str, Any] = {
    "rate_hz": 20.0,
    "cmd_timeout_s": 0.3,  # our deadman: no move_velocity for this long -> zeros
    "zero_hold_s": 1.0,  # keep sending zeros this long after a stop, then the 5 Hz keepalive (ros2) takes over
    "odom_to_zone": [0.0, 0.0, 0.0],  # zone-frame pose of the odom origin  # VERIFY: calibrate on site
    "caps": {"vx": MAX_VX, "vy": MAX_VY, "wz": MAX_WZ},
    "confirm_timeout_s": 30.0,  # pick/place: wait this long for a person to confirm the load / hand-off
    "ros2": {
        "node_name": "leukerbad_d1w_edge",
        "cmd_vel": "/cmd_vel",
        "gait": "/gait",
        "robot_cmd": "/robot_cmd",  # only the configured greeting gesture (greeting_robot_cmd), never a posture
        "keepalive_hz": 5.0,  # zero Twist while standing and idle / held, so no bridge silence watchdog ever damps it
        "odom": "/odom",
        "imu": "/imu",
        "battery": "/battery",
        "ctrl_mode": "/ctrl_mode",
        "connected": "/connected",
        "say": "/say",
        "service_ns": "/d1_cmd_vel_bridge",
    },
    "sdk": {
        "local_ip": "172.24.0.239",
        "local_port": 43988,
        "robot_ip": "172.24.1.41",
        "keepalive_hz": 10.0,
    },
    "mock": {"rate_hz": 50.0, "confirm_auto_s": 0.5},
}


class CommandRefused(RuntimeError):
    """The bridge refused a command (hard rule, disabled flag, stale state, robot mode)."""


def shape(v: float, vmin: float, vmax: float) -> float:
    """Clamp to +-vmax; below vmin exactly 0 (SDK rule); NaN/inf -> 0."""
    if not math.isfinite(v):
        return 0.0
    v = max(-vmax, min(vmax, v))
    return 0.0 if abs(v) < vmin else v


def _wrap(a: float) -> float:
    return (a + math.pi) % (2 * math.pi) - math.pi


def _yaw_from_quat(w, x, y, z) -> float:
    return math.atan2(2 * (w * z + x * y), 1 - 2 * (y * y + z * z))


def _roll_pitch_from_quat(w, x, y, z) -> tuple[float, float]:
    roll = math.atan2(2 * (w * x + y * z), 1 - 2 * (x * x + y * y))
    pitch = math.asin(max(-1.0, min(1.0, 2 * (w * y - z * x))))
    return roll, pitch


def _merge(base: dict, over: dict | None) -> dict:
    out = dict(base)
    for k, v in (over or {}).items():
        out[k] = _merge(base[k], v) if isinstance(v, dict) and isinstance(base.get(k), dict) else v
    return out


@dataclass
class Raw:
    """One state sample from a transport, in the robot's odometry frame."""

    x: float = 0.0
    y: float = 0.0
    yaw: float = 0.0
    speed: float = 0.0  # |body velocity| m/s
    roll: float = 0.0
    pitch: float = 0.0
    battery_pct: float | None = None
    ctrl_mode: int | None = None
    connected: bool = False
    t: float = 0.0  # receive time (time.time()) of the newest odom sample
    t_imu: float = 0.0


# ======================================================================== transports
class Ros2Transport:
    """Through the team's d1_cmd_vel_bridge node. Publishes cmd_vel / gait (and /say); never robot_cmd / attitude."""

    name = "ros2"

    def __init__(self, cfg: dict, c: dict, ros=None):
        self.cfg, self.c = cfg, c
        self.ros = ros
        self.raw = Raw()
        self.lock = threading.Lock()
        self.pub_robot_cmd: Any = None  # created only when greet() sends a configured gesture
        self.gait_sent: tuple[str, str] | None = None
        self.keepalive_hz = 0.0
        self.node: Any = None
        self.clients: dict[str, Any] = {}

    @staticmethod
    def _import():
        try:
            import rclpy
            from geometry_msgs.msg import Twist
            from nav_msgs.msg import Odometry
            from sensor_msgs.msg import BatteryState, Imu
            from std_msgs.msg import Bool, Int32, String
            from std_srvs.srv import Trigger
        except ImportError as e:
            raise ImportError(
                "D1W ros2 transport needs ROS 2 Humble: `source /opt/ros/humble/setup.bash` and a venv with "
                f"--system-site-packages (scripts/setup_edge.sh). Or D1W_TRANSPORT=mock. (import failed: {e})"
            ) from e
        return types.SimpleNamespace(
            rclpy=rclpy,
            qos=10,
            Twist=Twist,
            Odometry=Odometry,
            Imu=Imu,
            BatteryState=BatteryState,
            Bool=Bool,
            Int32=Int32,
            String=String,
            Trigger=Trigger,
        )

    def start(self):
        r = self.ros = self.ros or self._import()
        if not r.rclpy.ok():
            r.rclpy.init()
        t = dict(self.c["ros2"])
        if os.environ.get("D1W_CMD_VEL"):  # behind twist_mux (config/twist_mux.yaml): /cmd_vel_edge
            t["cmd_vel"] = os.environ["D1W_CMD_VEL"]
        n = self.node = r.rclpy.create_node(t["node_name"])
        for k in FORBIDDEN_TOPICS:  # belt and braces: a config typo must not point cmd_vel at robot_cmd
            if any(str(t[x]).rstrip("/").endswith(k) for x in ("cmd_vel", "gait", "say")):
                raise CommandRefused(f"topic config points at {k}: refused")
        self.pub_cmd = n.create_publisher(r.Twist, t["cmd_vel"], r.qos)
        self.pub_gait = n.create_publisher(r.String, t["gait"], r.qos)
        self.pub_say = n.create_publisher(r.String, t["say"], r.qos)
        n.create_subscription(r.Odometry, t["odom"], self._on_odom, r.qos)
        n.create_subscription(r.Imu, t["imu"], self._on_imu, r.qos)
        n.create_subscription(r.BatteryState, t["battery"], self._on_battery, r.qos)
        n.create_subscription(r.Int32, t["ctrl_mode"], self._on_mode, r.qos)
        n.create_subscription(r.Bool, t["connected"], self._on_conn, r.qos)
        threading.Thread(target=r.rclpy.spin, args=(n,), daemon=True, name="d1w-ros2").start()
        g = r.String()
        g.data = "move"  # the only gait we ever send, once, here
        self.pub_gait.publish(g)
        self.gait_sent = (t["gait"], "move")
        print(f"[d1w] gait: published 'move' once on {t['gait']}")
        self.keepalive_hz = float(t.get("keepalive_hz") or 0.0)

    # --- callbacks
    def _on_odom(self, m):
        p, q = m.pose.pose.position, m.pose.pose.orientation
        v = m.twist.twist.linear
        with self.lock:
            self.raw.x, self.raw.y = float(p.x), float(p.y)
            self.raw.yaw = _yaw_from_quat(float(q.w), float(q.x), float(q.y), float(q.z))
            self.raw.speed = math.hypot(float(v.x), float(v.y))
            self.raw.t = time.time()
            self.raw.connected = True  # their node only publishes odom while the SDK is connected

    def _on_imu(self, m):
        q = m.orientation
        roll, pitch = _roll_pitch_from_quat(float(q.w), float(q.x), float(q.y), float(q.z))
        with self.lock:
            self.raw.roll, self.raw.pitch, self.raw.t_imu = roll, pitch, time.time()

    def _on_battery(self, m):
        pct = float(m.percentage)
        if math.isfinite(pct):
            with self.lock:
                self.raw.battery_pct = pct * 100.0 if pct <= 1.0 else pct  # their node publishes 0..1

    def _on_mode(self, m):
        with self.lock:
            self.raw.ctrl_mode = int(m.data)

    def _on_conn(self, m):
        with self.lock:
            self.raw.connected = bool(m.data)

    # --- commands
    def latest(self) -> Raw:
        with self.lock:
            return Raw(**self.raw.__dict__)

    def send(self, vx: float, vy: float, wz: float) -> None:
        tw = self.ros.Twist()
        tw.linear.x, tw.linear.y, tw.linear.z = float(vx), float(vy), 0.0
        tw.angular.x, tw.angular.y, tw.angular.z = 0.0, 0.0, float(wz)
        self.pub_cmd.publish(tw)

    def say(self, text: str) -> None:
        m = self.ros.String()
        m.data = text
        self.pub_say.publish(m)

    def _trigger(self, srv: str) -> bool:
        if srv in FORBIDDEN_SERVICES:
            raise CommandRefused(f"~/{srv} is never called by this bridge")
        name = f"{self.c['ros2']['service_ns']}/{srv}"
        cli = self.clients.get(name) or self.node.create_client(self.ros.Trigger, name)
        self.clients[name] = cli
        if not cli.wait_for_service(timeout_sec=2.0):
            return False
        fut = cli.call_async(self.ros.Trigger.Request())
        t0 = time.time()
        while not fut.done() and time.time() - t0 < 5.0:
            time.sleep(0.02)
        res = fut.result() if fut.done() else None
        return res is not None and getattr(res, "success", None) is True

    def stand_up(self) -> bool:
        return self._trigger("stand_up")

    def damp(self) -> bool:
        return self._trigger("damp")

    def shake_hand(self) -> bool:  # the team bridge's ~/shake_hand Trigger
        return self._trigger("shake_hand")

    def gesture(self, cmd: str) -> bool:
        """Publish ONE gesture string on robot_cmd (greet() only). The publisher is created on first use."""
        if not gesture_cmd_ok(cmd):
            raise CommandRefused(f"robot_cmd {cmd!r} refused: only a greeting gesture, never posture or locomotion")
        if self.pub_robot_cmd is None:
            self.pub_robot_cmd = self.node.create_publisher(self.ros.String, self.c["ros2"]["robot_cmd"], self.ros.qos)
        m = self.ros.String()
        m.data = str(cmd).strip().lower()
        self.pub_robot_cmd.publish(m)
        return True

    def close(self):
        try:
            if self.node is not None:
                self.node.destroy_node()
        except Exception:  # noqa: BLE001
            pass


class SdkTransport:
    """mc_sdk_zsl_1w_py directly. Refuses to start when the SDK port is already bound on this host (their ROS node
    or another client owns the one SDK connection)."""

    name = "sdk"
    always_send = True  # keepalive: no SDK traffic for 3 s -> the robot damps and lies down

    def __init__(self, cfg: dict, c: dict, sdk=None):
        self.cfg, self.c = cfg, c
        self.sdk = sdk
        self.robot: Any = None
        self.raw = Raw()
        self.lock = threading.Lock()
        self._poll = threading.Thread(target=self._poll_loop, daemon=True, name="d1w-sdk-state")
        self._run = True

    @staticmethod
    def lib_dir() -> str:
        arch = platform.machine().replace("amd64", "x86_64").replace("arm64", "aarch64")
        base = os.environ.get(
            "AGIBOT_SDK_DIR", str(REPO_ROOT.parent / "SafetyAI" / "third_party" / "agibot_D1_Edu-Ultra")
        )
        return os.path.join(base, "lib", "zsl-1w", arch)

    def _port_free(self, ip: str, port: int) -> bool:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.bind((ip, port))
            return True
        except OSError:
            return False
        finally:
            s.close()

    def start(self):
        s = self.c["sdk"]
        if os.environ.get("D1W_TRANSPORT") != "sdk" and not self.cfg.get("_test_sdk"):
            raise CommandRefused("sdk transport needs the operator to set D1W_TRANSPORT=sdk explicitly")
        if not self._port_free(s["local_ip"], int(s["local_port"])):
            raise CommandRefused(
                f"SDK port {s['local_ip']}:{s['local_port']} is in use: another SDK client (the team's ROS bridge?) "
                "owns the robot. Use transport ros2, or stop that client first. Only ONE SDK client may run."
            )
        if self.sdk is None:
            if sys.version_info[:2] != (3, 10):
                raise CommandRefused(f"the D1 SDK binding is CPython 3.10 only (this is {sys.version.split()[0]})")
            sys.path.insert(0, self.lib_dir())
            import importlib

            self.sdk = importlib.import_module("mc_sdk_zsl_1w_py")
        self.robot = self.sdk.HighLevel()
        self.robot.initRobot(s["local_ip"], int(s["local_port"]), s["robot_ip"])
        t0 = time.time()
        while time.time() - t0 < 5.0 and not self.robot.checkConnect():
            time.sleep(0.1)
        self._poll.start()

    def _poll_loop(self):
        while self._run:
            r = self.robot
            try:
                conn = bool(r.checkConnect())
                pos = list(r.getPosition())
                q = list(r.getQuaternion())  # [w, x, y, z]
                vel = list(r.getBodyVelocity())
                mode = int(r.getCurrentCtrlmode())
                batt = float(r.getBatteryPower())
            except Exception:  # noqa: BLE001  (SDK / network hiccup: state goes stale, the bridge hands over)
                time.sleep(0.1)
                continue
            roll, pitch = _roll_pitch_from_quat(*q)
            now = time.time()
            with self.lock:
                self.raw = Raw(
                    x=pos[0],
                    y=pos[1],
                    yaw=_yaw_from_quat(*q),
                    speed=math.hypot(vel[0], vel[1]),
                    roll=roll,
                    pitch=pitch,
                    battery_pct=batt,
                    ctrl_mode=mode,
                    connected=conn,
                    t=now if conn else self.raw.t,
                    t_imu=now,
                )
            time.sleep(0.1)

    def latest(self) -> Raw:
        with self.lock:
            return Raw(**self.raw.__dict__)

    def send(self, vx, vy, wz):
        self.robot.move(float(vx), float(vy), float(wz))

    def say(self, text):  # no speaker API in the SDK
        pass

    def stand_up(self) -> bool:
        return self.robot.standUp() == 0

    def damp(self) -> bool:
        return self.robot.passive() == 0  # vendor: "for emergency stop" (robot drops). allow_damp only.

    def shake_hand(self) -> bool:
        return self.robot.shakeHand() == 0

    def gesture(self, cmd: str) -> bool:  # the SDK has no robot_cmd topic; only its shakeHand() maps
        if str(cmd).strip().lower() in ("shake_hand", "shakehand"):
            return self.shake_hand()
        raise CommandRefused(f"gesture {cmd!r}: the SDK transport has no robot_cmd; only shake_hand")

    def close(self):
        self._run = False
        try:
            self.robot.move(0.0, 0.0, 0.0)
        except Exception:  # noqa: BLE001
            pass


class MockTransport:
    """Kinematic twin: integrates the (already shaped) command like FakeBridge, in an odometry frame that starts at
    0,0,0. ctrl_mode 1 (stood up by the operator) / 3 while moving; test hooks for tilt, link loss, battery."""

    name = "mock"

    def __init__(self, cfg: dict, c: dict):
        self.c = c
        self.raw = Raw(battery_pct=float(cfg.get("mock_battery_pct", 80.0)), ctrl_mode=1, connected=True, t=time.time())
        self.v = (0.0, 0.0, 0.0)
        self.lock = threading.Lock()
        self.frozen = False  # no state updates (link / node down)
        self.said: list[str] = []
        self.gestures: list[str] = []
        self.sent: list[tuple[float, float, float]] = []
        self._run = True
        self.speed_scale = float(cfg.get("mock_time_scale", 1.0))  # tests: the twin runs N x real time

    def start(self):
        threading.Thread(target=self._loop, daemon=True, name="d1w-mock").start()

    def _loop(self):
        dt_nom = 1.0 / float(self.c["mock"]["rate_hz"])
        last = time.time()
        while self._run:
            time.sleep(dt_nom)
            now = time.time()
            dt, last = now - last, now
            with self.lock:
                if self.frozen:
                    continue
                r = self.raw
                vx, vy, wz = (v * self.speed_scale for v in self.v)
                if r.ctrl_mode in UPRIGHT_MODES:
                    r.yaw = _wrap(r.yaw + wz * dt)
                    r.x += (vx * math.cos(r.yaw) - vy * math.sin(r.yaw)) * dt
                    r.y += (vx * math.sin(r.yaw) + vy * math.cos(r.yaw)) * dt
                    r.speed = math.hypot(vx, vy)
                    r.ctrl_mode = 3 if (vx or vy or wz) else 1
                    if r.battery_pct is not None and (vx or vy or wz):
                        r.battery_pct = max(0.0, r.battery_pct - 0.02 * dt)
                else:
                    r.speed = 0.0
                r.t = r.t_imu = now

    def latest(self) -> Raw:
        with self.lock:
            return Raw(**self.raw.__dict__)

    def send(self, vx, vy, wz):
        with self.lock:
            self.v = (vx, vy, wz)
            self.sent.append((vx, vy, wz))
            del self.sent[:-500]

    def say(self, text):
        self.said.append(text)

    def stand_up(self) -> bool:
        with self.lock:
            self.raw.ctrl_mode = 1
        return True

    def damp(self) -> bool:
        with self.lock:
            self.raw.ctrl_mode = 0
            self.v = (0.0, 0.0, 0.0)
        return True

    def shake_hand(self) -> bool:
        self.gestures.append("shake_hand")
        return True

    def gesture(self, cmd: str) -> bool:
        if not gesture_cmd_ok(cmd):
            raise CommandRefused(f"robot_cmd {cmd!r} refused: only a greeting gesture, never posture or locomotion")
        self.gestures.append(f"robot_cmd:{str(cmd).strip().lower()}")
        return True

    # --- test / demo hooks
    def set_tilt(self, roll_deg: float = 0.0, pitch_deg: float = 0.0):
        with self.lock:
            self.raw.roll, self.raw.pitch = math.radians(roll_deg), math.radians(pitch_deg)

    def set_frozen(self, frozen: bool):
        with self.lock:
            self.frozen = frozen

    def set_pose(self, x: float, y: float, yaw: float):
        with self.lock:
            self.raw.x, self.raw.y, self.raw.yaw = x, y, yaw

    def close(self):
        self._run = False


def make_transport(cfg: dict, c: dict, ros=None, sdk=None):
    kind = os.environ.get("D1W_TRANSPORT") or cfg.get("transport", "ros2")
    if kind == "ros2":
        return Ros2Transport(cfg, c, ros)
    if kind == "sdk":
        return SdkTransport(cfg, c, sdk)
    if kind == "mock":
        return MockTransport(cfg, c)
    raise ValueError(f"D1W transport must be ros2 | sdk | mock, not {kind!r}")


# ======================================================================== bridge
class AgibotD1WBridge(RobotBridge):
    body = "agibot_d1w"
    physical_stop = True
    mounted = "tray"  # quadruped flow: no arms; the tray (towels in it) rides on its back; pick/place = say + confirm

    def __init__(self, cfg: dict, transport=None, ros=None, sdk=None):
        super().__init__(cfg)
        self.c = _merge(DEFAULTS, cfg.get("d1w"))
        caps = self.c["caps"]
        # config can lower the caps, never raise them
        self.cap = (min(MAX_VX, float(caps["vx"])), min(MAX_VY, float(caps["vy"])), min(MAX_WZ, float(caps["wz"])))
        self.auto_stand = cfg_flag(cfg, "auto_stand")
        self.allow_damp = cfg_flag(cfg, "allow_damp")
        self.allow_greeting_gesture = cfg_flag(cfg, "allow_greeting_gesture")
        grc = cfg.get("greeting_robot_cmd")
        self.greeting_robot_cmd = None if grc in (None, "", "none", False) else str(grc).strip().lower()
        if self.greeting_robot_cmd is not None and not gesture_cmd_ok(self.greeting_robot_cmd):
            raise CommandRefused(f"greeting_robot_cmd {grc!r} refused: posture / locomotion commands are never sent")
        self.t = transport or make_transport(cfg, self.c, ros, sdk)
        if self.t.name == "mock":
            self.body = "agibot_d1w_mock"  # simulation on the Physical AI page, not hardware
        self.stop_adapter = f"zero cmd_vel + hold stance ({self.t.name}: d1_cmd_vel_bridge / SDK move(0,0,0))"
        self._zones = Zones(REPO_ROOT / cfg.get("zones_file", "policies/zones.yaml"))
        self.landmarks: dict = dict(self._zones.raw.get("landmarks") or {})
        tx, ty, th = (float(v) for v in self.c["odom_to_zone"])
        self._tf = [tx, ty, th]
        self._lock = threading.Lock()
        self._s = RobotState(body=self.body, mode="idle", arm_q=[])
        if self.t.name == "mock" and cfg.get("start_pose"):
            self._tf = [float(v) for v in cfg["start_pose"]]  # the twin's odom origin is its start pose
        self._target = (0.0, 0.0, 0.0)
        self._t_cmd = 0.0
        self._t_stop = 0.0
        self._stale = True
        self._handover_sent = False
        self._ever_fresh = False  # no handover event before the first state ever arrived (startup)
        self._tilt_until = 0.0  # refuse motion until this time (tilt breach)
        self._tilted = False
        self._loaded = True  # the tray starts strapped on (staff load it)
        self._confirm = threading.Event()
        self.person_distance_m: float | None = None  # set by the perception pipeline (perception/proximity.py)
        self.speech_hooks: list = []  # local TTS etc. (skills/voice.py)
        self._run = True

    # ------------------------------------------------------------ lifecycle
    def connect(self, iface: str, domain: int) -> None:
        self.t.start()
        gs = getattr(self.t, "gait_sent", None)
        if gs:
            self._event("gait.set", topic=gs[0], gait=gs[1], once=True)
        threading.Thread(target=self._stream, daemon=True, name="d1w-stream").start()

    def close(self) -> None:
        self.stop()
        time.sleep(0.15)
        self._run = False
        self.t.close()

    # ------------------------------------------------------------ frames
    def to_zone(self, x: float, y: float, yaw: float) -> tuple[float, float, float]:
        tx, ty, th = self._tf
        c, s = math.cos(th), math.sin(th)
        return tx + c * x - s * y, ty + s * x + c * y, _wrap(yaw + th)

    def set_odom_to_zone(self, tf) -> None:
        with self._lock:
            self._tf = [float(v) for v in tf]
        self._event("pose.calibrated", odom_to_zone=[round(v, 4) for v in self._tf])

    def refix_at_landmark(self, name: str, use_yaw: bool = False) -> dict:
        """The robot stands at landmark `name` (zones file `landmarks`): re-zero odometry drift so the current odom
        pose maps onto the landmark's position (and heading, with use_yaw and a landmark yaw). Returns the correction."""
        lm = self.landmarks.get(name)
        if lm is None:
            raise KeyError(f"unknown landmark {name!r}; known: {sorted(self.landmarks)}")
        r = self.t.latest()
        before = self.to_zone(r.x, r.y, r.yaw)
        th = self._tf[2]
        if use_yaw and lm.get("yaw") is not None:
            th = _wrap(float(lm["yaw"]) - r.yaw)
        c, s = math.cos(th), math.sin(th)
        lx, ly = (float(v) for v in lm["xy"])
        tf = [lx - (c * r.x - s * r.y), ly - (s * r.x + c * r.y), th]
        with self._lock:
            self._tf = tf
        err = math.hypot(before[0] - lx, before[1] - ly)
        self._event("pose.refix", landmark=name, drift_m=round(err, 3), odom_to_zone=[round(v, 4) for v in tf])
        return {"landmark": name, "drift_m": err, "odom_to_zone": tf}

    # ------------------------------------------------------------ state
    def _fresh(self, r: Raw, now: float) -> bool:
        return r.connected and r.t > 0 and now - r.t <= STATE_TIMEOUT_S

    @staticmethod
    def _tilt_deg(r: Raw) -> float:
        return math.degrees(max(abs(r.roll), abs(r.pitch)))

    def state(self) -> RobotState:
        r = self.t.latest()
        now = time.time()
        fresh = self._fresh(r, now)
        x, y, yaw = self.to_zone(r.x, r.y, r.yaw)
        tilt_ok = self._tilt_deg(r) < TILT_MAX_DEG
        if not fresh:
            mode = "no_state"
        else:
            name = CTRL_MODE.get(r.ctrl_mode if r.ctrl_mode is not None else -1, "unknown")
            if name == "move" or (name == "standing" and r.speed > 0.05):
                mode = "walking" if (r.speed > 0.05 or any(self._target)) else "standing"
            elif name == "lying":
                mode = "idle"
            else:
                mode = name
        with self._lock:
            s = self._s
            s.x, s.y, s.yaw = x, y, yaw
            s.zone = self._zones.zone_of(x, y)
            if r.battery_pct is not None:
                s.battery_pct = float(r.battery_pct)
            s.mode = mode
            s.balance_ok = fresh and tilt_ok and r.ctrl_mode in UPRIGHT_MODES
            s.ts = r.t or now
            return RobotState(**{**s.__dict__, "arm_q": []})

    def health(self) -> dict:
        r = self.t.latest()
        now = time.time()
        return {
            "transport": self.t.name,
            "connected": r.connected,
            "state_age_s": round(now - r.t, 2) if r.t else None,
            "fresh": self._fresh(r, now),
            "ctrl_mode": r.ctrl_mode,
            "tilt_deg": round(self._tilt_deg(r), 1),
            "battery_pct": r.battery_pct,
            "handover": self._handover_sent,
            "odom_to_zone": [round(v, 4) for v in self._tf],
            "caps": dict(zip(("vx", "vy", "wz"), self.cap, strict=True)),
        }

    def near_person(self) -> bool:
        d = self.person_distance_m
        return d is not None and d < 2.0

    # ------------------------------------------------------------ stream (20 Hz) + watchdogs
    def _stream(self):
        period = 1.0 / float(self.c["rate_hz"])
        last_send = 0.0
        while self._run:
            t0 = time.time()
            r = self.t.latest()
            fresh = self._fresh(r, t0)
            # state watchdog: hand over (one event), never loop on exceptions
            if fresh:
                self._ever_fresh = True
            if not fresh:
                with self._lock:
                    moving = any(self._target)
                    self._target = (0.0, 0.0, 0.0)
                if not self._handover_sent and self._ever_fresh:
                    self._handover_sent = True
                    why = "robot reports disconnected" if (r.t and not r.connected) else "no state for 1 s"
                    self._event("handover", reason=why, transport=self.t.name, was_moving=moving)
                    self._t_stop = t0
            elif self._handover_sent:
                self._handover_sent = False
                self._event("state_restored", transport=self.t.name)
            # tilt watchdog
            if fresh and self._tilt_deg(r) >= TILT_MAX_DEG:
                if not self._tilted:
                    self._tilted = True
                    self._event("tilt_breach", tilt_deg=round(self._tilt_deg(r), 1), cap_deg=TILT_MAX_DEG)
                with self._lock:
                    self._target = (0.0, 0.0, 0.0)
                    self._t_stop = t0
                self._tilt_until = t0 + LEVEL_AGAIN_S
            elif self._tilted and t0 >= self._tilt_until:
                self._tilted = False
                self._event("tilt_cleared")
            # deadman
            with self._lock:
                if any(self._target) and t0 - self._t_cmd > float(self.c["cmd_timeout_s"]):
                    self._target = (0.0, 0.0, 0.0)
                    self._t_stop = t0
                target = self._target
            active = any(target) or t0 - self._t_stop < float(self.c["zero_hold_s"])
            always = getattr(self.t, "always_send", False)
            # ros2 keepalive: while we own the robot (fresh state, no handover) and it stands (idle or HOLD), zero
            # Twist at keepalive_hz, so a bridge-side silence watchdog never damps a standing robot.
            ka = float(getattr(self.t, "keepalive_hz", 0.0) or 0.0)
            owned_standing = fresh and not self._handover_sent and r.ctrl_mode in UPRIGHT_MODES
            if ka > 0 and owned_standing and not any(target) and t0 - last_send >= 1.0 / ka:
                always = True
                keep = 0.0
            else:
                keep = 1.0 / float(self.c["sdk"]["keepalive_hz"])
            if active or (always and t0 - last_send >= keep):
                try:
                    self.t.send(*target)
                    last_send = t0
                except Exception as e:  # noqa: BLE001  (transport hiccup: next tick retries)
                    print(f"[d1w] send failed: {e}")
            time.sleep(max(0.0, period - (time.time() - t0)))

    # ------------------------------------------------------------ locomotion
    def stand(self) -> None:
        r = self.t.latest()
        if self._fresh(r, time.time()) and r.ctrl_mode in UPRIGHT_MODES and self._tilt_deg(r) < TILT_MAX_DEG:
            return
        if not self.auto_stand:
            print(f"[d1w] stand() refused: ctrl_mode={r.ctrl_mode} fresh={self._fresh(r, time.time())}")
            raise RuntimeError(NOT_STANDING_MSG)
        self._event("stand_up.requested", transport=self.t.name)
        if not self.t.stand_up():
            raise RuntimeError("stand_up failed")

    def move_velocity(self, vx: float, vy: float, vyaw: float) -> None:
        r = self.t.latest()
        now = time.time()
        why = None
        if not self._fresh(r, now):
            why = "no fresh robot state (handover)"
        elif r.ctrl_mode not in UPRIGHT_MODES:
            why = f"robot not standing (ctrl_mode {r.ctrl_mode}); {NOT_STANDING_MSG}"
        elif self._tilted or self._tilt_deg(r) >= TILT_MAX_DEG:
            why = f"tilt {self._tilt_deg(r):.0f} deg (max {TILT_MAX_DEG:.0f}); waiting until level"
        elif r.battery_pct is not None and r.battery_pct < MIN_BATTERY_PCT:
            why = f"battery {r.battery_pct:.0f} % < {MIN_BATTERY_PCT:.0f} %"
        if why:
            self.stop()
            self._event("command.refused", command="move_velocity", reason=why)
            raise CommandRefused(why)
        vxs = vx * self.speed_scale if math.isfinite(vx) else 0.0
        vys = vy * self.speed_scale if math.isfinite(vy) else 0.0
        cmd = (
            shape(vxs, SDK_MIN[0], self.cap[0]),
            shape(vys, SDK_MIN[1], self.cap[1]),
            shape(vyaw if math.isfinite(vyaw) else 0.0, SDK_MIN[2], self.cap[2]),
        )
        with self._lock:
            self._target = cmd
            self._t_cmd = now
            if not any(cmd):
                self._t_stop = now

    def stop(self) -> None:
        now = time.time()
        with self._lock:
            self._target = (0.0, 0.0, 0.0)
            self._t_stop = now
        for _ in range(3):  # at once, not at the next stream tick
            try:
                self.t.send(0.0, 0.0, 0.0)
            except Exception as e:  # noqa: BLE001
                print(f"[d1w] stop send failed: {e}")

    def damp(self) -> None:
        self.stop()
        if not self.allow_damp:
            self._event("damp.refused", reason="allow_damp is false: zero velocity only, the robot keeps its stance")
            return
        self._event("damp.requested", transport=self.t.name, note="emergency: the robot drops")
        self.t.damp()

    def is_stationary(self) -> bool:
        r = self.t.latest()
        return self._fresh(r, time.time()) and not any(self._target) and float(r.speed or 0.0) <= 0.05

    def shake_hand(self) -> bool:
        """Greeting gesture (the vendor's shake_hand). Only with allow_greeting_gesture: true (literal; default false)
        and only while stationary. skills.greet() adds the rest: safety NORMAL, nobody within 1.5 m, battery > 30 %."""
        if not self.allow_greeting_gesture:
            raise CommandRefused("allow_greeting_gesture is false: greet by voice only")
        if not self.is_stationary():
            raise CommandRefused("the robot is moving (or has no fresh state): no gesture")
        if self.greeting_robot_cmd:  # the configured robot_cmd gesture string instead of ~/shake_hand
            self._event("gesture.robot_cmd", cmd=self.greeting_robot_cmd, transport=self.t.name)
            return self.t.gesture(self.greeting_robot_cmd) is True
        self._event("gesture.shake_hand", transport=self.t.name)
        return self.t.shake_hand() is True

    def set_arm_targets(self, q, ramp_s: float = 2.0) -> None:
        if list(q):
            raise CommandRefused("the D1 Ultra-W has no arms")

    # ------------------------------------------------------------ quadruped flow: say + confirm
    def say(self, text: str) -> None:
        print(f"[robot says] {text}")
        try:
            self.t.say(text)
        except Exception as e:  # noqa: BLE001
            print(f"[d1w] /say publish failed: {e}")
        for h in list(self.speech_hooks):
            try:
                h(text)
            except Exception as e:  # noqa: BLE001
                print(f"[d1w] speech hook failed: {e}")

    def confirm(self) -> None:
        """A person confirmed the load / hand-off (POST /confirm on the edge API, or the console button)."""
        self._confirm.set()

    def _wait_confirm(self) -> bool:
        self._confirm.clear()
        if self.t.name == "mock":
            auto = float(self.c["mock"]["confirm_auto_s"])
            return self._confirm.wait(auto) or True
        return self._confirm.wait(float(self.c["confirm_timeout_s"]))

    def carries(self, obj: str) -> bool:
        return obj in ("tray", "towels")

    def mount_on(self) -> bool:
        if self._loaded:
            return True
        self.say("Please put the towels in the tray on my back, then press confirm.")
        self._loaded = self._wait_confirm()
        self._event("load.confirmed" if self._loaded else "load.unconfirmed")
        return self._loaded

    def unmount(self, obj: str, target: str) -> dict:
        self.say(f"Here are your {obj}. Please take them from my back, then press confirm.")
        ok = self._wait_confirm()
        self._event("handoff.confirmed" if ok else "handoff.unconfirmed", obj=obj, target=target)
        if ok:
            self._loaded = False
            return {"ok": True, "landed": None}
        return {"ok": False, "error": "nobody confirmed the hand-off"}


# ======================================================================== ladder CLI (venue rungs 1-5, local only)
def _ladder():  # pragma: no cover - needs the robot (or D1W_TRANSPORT=mock)
    """python -m bridge.agibot_d1w_bridge state | straight | lateral | turn   (run on the team PC, never remotely)

    Bounded moves through the same caps and exact-zero rule as the skills: straight 0.2 m/s x 1 s, lateral 0.1 m/s x
    1 s, turn 0.4 rad/s until 90 deg (max 6 s). Each ends with stop(): zero cmd_vel, the robot keeps its stance."""
    import argparse
    import json

    from . import load_config

    ap = argparse.ArgumentParser(description=_ladder.__doc__)
    ap.add_argument("rung", choices=["state", "straight", "lateral", "turn"])
    ap.add_argument("--config", default="config/d1w.yaml")
    a = ap.parse_args()
    cfg = load_config(a.config)
    b = AgibotD1WBridge(cfg)
    b.event_sink = lambda kind, **d: print(f"  event {kind} {d}")
    b.connect("", 0)
    time.sleep(1.5)
    s0 = b.state()
    print("state:", json.dumps(s0.dict()), json.dumps(b.health()))
    try:
        if a.rung == "state":
            return
        vx, vy, wz, dur = {"straight": (0.2, 0, 0, 1.0), "lateral": (0, 0.1, 0, 1.0), "turn": (0, 0, 0.4, 6.0)}[a.rung]
        t0 = time.time()
        while time.time() - t0 < dur:
            if a.rung == "turn" and abs(_wrap(b.state().yaw - s0.yaw)) >= math.pi / 2:
                break
            b.move_velocity(vx, vy, wz)  # refused (CommandRefused) unless fresh, standing, level, battery ok
            time.sleep(0.05)
    finally:
        b.stop()
        time.sleep(1.0)
        s1 = b.state()
        print(
            f"moved {math.hypot(s1.x - s0.x, s1.y - s0.y):.3f} m, turned {math.degrees(_wrap(s1.yaw - s0.yaw)):.1f} deg; "
            f"mode {s1.mode}"
        )
        b.close()


if __name__ == "__main__":
    _ladder()
