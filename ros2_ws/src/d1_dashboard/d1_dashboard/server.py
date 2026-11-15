"""D1 developer dashboard: a small web UI (stdlib http.server + Server-Sent Events, no extra packages).

  * Process control: start the robot link + perception, mapping, localization, Nav2 (each is a `ros2 launch` child).
  * Map workflow: start map -> drive -> save map (grid + RTAB-Map database) -> load map -> click to navigate (Nav2).
  * Robot control: stand / lie / damp / E-STOP and a deadman velocity pad (publishes /cmd_vel for agibot_d1_bridge).
  * Live view: occupancy grid / costmaps, robot pose, plan, goal, process state, logs.

Run:  ros2 run d1_dashboard dashboard --robot-ip 172.24.1.41 --local-ip 172.24.1.20   ->  http://127.0.0.1:8080
"""
import os
import site
import sys

try:  # ~/.local NumPy 2 breaks ROS binaries
    _us = site.getusersitepackages()
    sys.path[:] = [p for p in sys.path if not p.startswith(_us)]
except Exception:  # pragma: no cover
    pass

import argparse  # noqa: E402
import base64  # noqa: E402
import json  # noqa: E402
import math  # noqa: E402
import re  # noqa: E402
import shutil  # noqa: E402
import signal  # noqa: E402
import subprocess  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer  # noqa: E402
from urllib.parse import parse_qs, urlparse  # noqa: E402

import numpy as np  # noqa: E402
import rclpy  # noqa: E402
import tf2_ros  # noqa: E402
from action_msgs.msg import GoalStatus  # noqa: E402
from geometry_msgs.msg import PoseStamped, PoseWithCovarianceStamped, Twist  # noqa: E402
from nav2_msgs.action import NavigateToPose  # noqa: E402
from nav_msgs.msg import OccupancyGrid, Path  # noqa: E402
from rclpy.action import ActionClient  # noqa: E402
from rclpy.executors import MultiThreadedExecutor  # noqa: E402
from rclpy.node import Node  # noqa: E402
from rclpy.qos import DurabilityPolicy, HistoryPolicy, QoSProfile, ReliabilityPolicy  # noqa: E402
from rclpy.time import Time  # noqa: E402
from sensor_msgs.msg import BatteryState, PointCloud2  # noqa: E402
from std_msgs.msg import Bool, Int32, String  # noqa: E402

BAG_TOPICS = ['/camera/image_raw', '/camera/camera_info', '/camera/depth', '/odom', '/odom_sdk', '/imu', '/tf', '/tf_static',
              '/cmd_vel', '/ctrl_mode', '/connected', '/battery']
STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static')
MODE_NAMES = {0: 'damping', 1: 'standing', 3: 'moving', 18: 'moving', 51: 'lying'}
STANDING_MODES = (1, 3, 18)
MAX_V = (0.5, 0.3, 1.0)           # server-side clamps for the dashboard pad (vx, vy, wz)
TELEOP_TIMEOUT = 0.5               # s without a pad message -> stop


class Proc:
    """One managed `ros2 launch ...` child in its own process group, with a log file."""

    def __init__(self, name, cmd, log_dir, env):
        self.name, self.cmd, self.env = name, cmd, env
        self.log_path = os.path.join(log_dir, f'{name}.log')
        self.p = None
        self.t0 = 0.0

    def running(self):
        return self.p is not None and self.p.poll() is None

    def start(self):
        if self.running():
            return False
        with open(self.log_path, 'ab') as f:
            f.write(f'\n===== start {time.strftime("%F %T")}: {" ".join(self.cmd)}\n'.encode())
        logf = open(self.log_path, 'ab')
        self.p = subprocess.Popen(self.cmd, stdout=logf, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                  env=self.env, start_new_session=True)
        self.t0 = time.time()
        return True

    def stop(self, grace=10.0):
        if not self.running():
            return False
        pg = os.getpgid(self.p.pid)
        for sig, wait in ((signal.SIGINT, grace), (signal.SIGTERM, 4.0), (signal.SIGKILL, 2.0)):
            try:
                os.killpg(pg, sig)
            except ProcessLookupError:
                break
            t = time.time()
            while time.time() - t < wait:
                if self.p.poll() is not None:
                    return True
                time.sleep(0.1)
        return self.p.poll() is not None

    def tail(self, n=60):
        try:
            with open(self.log_path, 'rb') as f:
                f.seek(0, os.SEEK_END)
                size = f.tell()
                f.seek(max(0, size - 20000))
                return f.read().decode(errors='replace').splitlines()[-n:]
        except OSError:
            return []

    def info(self):
        return {'running': self.running(), 'pid': self.p.pid if self.running() else None,
                'uptime': round(time.time() - self.t0, 1) if self.running() else 0,
                'exit': self.p.poll() if (self.p is not None and not self.running()) else None}


class Dashboard(Node):
    def __init__(self, args):
        super().__init__('d1_dashboard')
        self.args = args
        self.external_robot = str(getattr(args, 'external_robot', 'false')).lower() in ('1', 'true', 'yes')
        # Monocular SLAM (camera -> depth -> RTAB-Map) is DEACTIVATED by default: the lidar map + static Nav2 path replaces it.
        # The code stays; start the dashboard with mono_slam:=true to bring Start map / Save map / camera obstacle layer back.
        self.mono = str(getattr(args, 'mono_slam', 'false')).lower() in ('1', 'true', 'yes')
        self.lock = threading.RLock()
        self.maps_dir = os.path.expanduser(args.maps_dir)
        self.log_dir = os.path.join(self.maps_dir, 'logs')
        os.makedirs(self.log_dir, exist_ok=True)

        # ---- ROS state
        self.connected, self.mode, self.battery = None, None, None
        self.pose, self.pose_frame, self.localized = None, None, False
        self.grids = {}                                     # name -> dict(version, w, h, res, ox, oy, data)
        self.plan = []
        self.trail, self.trail_frame = [], None          # robot path (x, y) in the pose frame, for the start marker + trail
        self.nav = {'status': 'idle', 'goal': None, 'distance_remaining': None, 'goal_handle': None}
        self.teleop = (0.0, 0.0, 0.0, 0.0)                   # vx, vy, wz, t
        self._teleop_active = False
        self.job = {'name': None, 'status': 'idle', 'message': ''}
        self.ui_mode = 'idle'                                # idle | mapping | navigation
        self.current_map = None                              # id of the loaded map
        self.current_entry = None                            # its list entry (dict)
        self.start_pose, self.pose_confirmed = None, False   # robot pose in the map set by the operator (lidar maps have no live localization)

        qos_latched = QoSProfile(history=HistoryPolicy.KEEP_LAST, depth=1, reliability=ReliabilityPolicy.RELIABLE,
                                 durability=DurabilityPolicy.TRANSIENT_LOCAL)
        self.create_subscription(Bool, '/connected', lambda m: self._set('connected', m.data), 10)
        self.create_subscription(Int32, '/ctrl_mode', lambda m: self._set('mode', m.data), 10)
        self.create_subscription(BatteryState, '/battery', lambda m: self._set('battery', m.percentage), 10)
        for name, topic in (('map', '/map'), ('global', '/global_costmap/costmap'), ('local', '/local_costmap/costmap')):
            self.create_subscription(OccupancyGrid, topic, lambda m, n=name: self._grid(n, m), qos_latched)
        self.create_subscription(Path, '/plan', self._on_plan, 10)
        # live obstacles straight from the depth cloud (works in every mode, independent of RTAB-Map / Nav2)
        if self.mono:
            self.create_subscription(PointCloud2, '/camera/points', self._on_cloud,
                                     QoSProfile(history=HistoryPolicy.KEEP_LAST, depth=1, reliability=ReliabilityPolicy.BEST_EFFORT))
        self._live_t = 0.0

        self.pub_vel = self.create_publisher(Twist, '/cmd_vel', 10)
        self.pub_cmd = self.create_publisher(String, '/robot_cmd', 10)
        self.pub_init = self.create_publisher(PoseWithCovarianceStamped, '/initialpose', 10)
        self.tf_buffer = tf2_ros.Buffer()
        self.tf_listener = tf2_ros.TransformListener(self.tf_buffer, self)
        self.nav_client = ActionClient(self, NavigateToPose, 'navigate_to_pose')
        self.create_timer(0.1, self._tick_pose)
        self.create_timer(0.05, self._tick_teleop)

        # ---- managed processes
        env = dict(os.environ)
        env['PYTHONNOUSERSITE'] = '1'
        env.setdefault('OPENCV_FFMPEG_CAPTURE_OPTIONS',
                       'rtsp_transport;tcp|fflags;nobuffer|flags;low_delay|stimeout;20000000')
        self.env = env
        self.procs = {}

    # ------------------------------------------------------------------ small helpers
    def _set(self, k, v):
        with self.lock:
            setattr(self, k, v)

    def _grid(self, name, m):
        with self.lock:
            old = self.grids.get(name, {'version': 0})
            self.grids[name] = {'version': old['version'] + 1, 'w': m.info.width, 'h': m.info.height,
                                'res': m.info.resolution, 'ox': m.info.origin.position.x,
                                'oy': m.info.origin.position.y, 'data': bytes(m.data)}

    def _on_plan(self, m):
        pts = [(p.pose.position.x, p.pose.position.y) for p in m.poses]
        step = max(1, len(pts) // 120)
        with self.lock:
            self.plan = pts[::step] + ([pts[-1]] if pts and (len(pts) - 1) % step else [])

    LIVE_MIN_H, LIVE_MAX_H = 0.10, 0.50        # obstacle band above the floor (same as the costmap config)
    LIVE_RES, LIVE_HALF, LIVE_INFL = 0.05, 4.0, 4   # 5 cm cells, 8 x 8 m window around the robot, 0.2 m halo

    def _on_cloud(self, m):
        """Rasterise the obstacle band of the live camera cloud around the robot -> grid 'live' (100 = obstacle, 45 = halo)."""
        now = time.monotonic()
        if now - self._live_t < 0.3 or self.pose is None:
            return
        self._live_t = now
        target = 'map' if self.localized else 'odom'
        try:   # the cloud is stamped with capture time (~1.4 s before it arrives): use the pose at that time
            tf = self.tf_buffer.lookup_transform(target, m.header.frame_id, Time.from_msg(m.header.stamp))
        except Exception:  # noqa: BLE001
            try:
                tf = self.tf_buffer.lookup_transform(target, m.header.frame_id, Time())
            except Exception:  # noqa: BLE001
                return
        q, t = tf.transform.rotation, tf.transform.translation
        x, y, z, w = q.x, q.y, q.z, q.w
        R = np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                      [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                      [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])
        P = np.frombuffer(bytes(m.data), np.float32).reshape(-1, 3).astype(np.float64)
        if len(P) == 0:
            return
        W = P @ R.T + np.array([t.x, t.y, t.z])
        sel = (W[:, 2] > self.LIVE_MIN_H) & (W[:, 2] < self.LIVE_MAX_H)
        res, half = self.LIVE_RES, self.LIVE_HALF
        cx, cy = self.pose[0], self.pose[1]
        ox, oy = math.floor((cx - half) / res) * res, math.floor((cy - half) / res) * res
        n = int(2 * half / res)
        ix, iy = ((W[sel, 0] - ox) / res).astype(int), ((W[sel, 1] - oy) / res).astype(int)
        ok = (ix >= 0) & (ix < n) & (iy >= 0) & (iy < n)
        counts = np.zeros((n, n), np.int32)
        np.add.at(counts, (iy[ok], ix[ok]), 1)
        obst = counts >= 3
        k = self.LIVE_INFL
        pad = np.pad(obst, k)
        infl = np.zeros_like(obst)
        for dy in range(-k, k + 1):
            for dx in range(-k, k + 1):
                if dx * dx + dy * dy <= k * k:
                    infl |= pad[k + dy:k + dy + n, k + dx:k + dx + n]
        data = np.zeros((n, n), np.int8)
        data[infl] = 45
        data[obst] = 100
        with self.lock:
            old = self.grids.get('live', {'version': 0})
            self.grids['live'] = {'version': old['version'] + 1, 'w': n, 'h': n, 'res': res, 'ox': ox, 'oy': oy,
                                  'data': data.tobytes()}

    def _tick_pose(self):
        try:
            tf = self.tf_buffer.lookup_transform('map', 'base_footprint', Time())
            frame, ok = 'map', True
        except Exception:  # noqa: BLE001
            try:
                tf = self.tf_buffer.lookup_transform('odom', 'base_footprint', Time())
                frame, ok = 'odom', False
            except Exception:  # noqa: BLE001
                return
        q, t = tf.transform.rotation, tf.transform.translation
        yaw = math.atan2(2 * (q.w * q.z + q.x * q.y), 1 - 2 * (q.y * q.y + q.z * q.z))
        with self.lock:
            self.pose, self.pose_frame, self.localized = (t.x, t.y, yaw), frame, ok
            if frame != self.trail_frame:                # frame switched (odom -> map): restart the trail in the new frame
                self.trail, self.trail_frame = [], frame
            if not self.trail or math.hypot(t.x - self.trail[-1][0], t.y - self.trail[-1][1]) > 0.08:
                self.trail.append((round(t.x, 3), round(t.y, 3)))
                if len(self.trail) > 3000:
                    self.trail = self.trail[::2]

    # ------------------------------------------------------------------ teleop and robot commands
    def set_teleop(self, vx, vy, wz):
        vx, vy, wz = (max(-m, min(m, v)) for v, m in zip((vx, vy, wz), MAX_V))
        with self.lock:
            self.teleop = (vx, vy, wz, time.monotonic())
            moving = any(abs(v) > 1e-3 for v in (vx, vy, wz))
        if moving and self.nav['status'] == 'navigating':
            self.cancel_goal('manual override')

    def _tick_teleop(self):
        with self.lock:
            vx, vy, wz, t = self.teleop
        fresh = time.monotonic() - t < TELEOP_TIMEOUT and any(abs(v) > 1e-3 for v in (vx, vy, wz))
        if fresh:
            m = Twist()
            m.linear.x, m.linear.y, m.angular.z = vx, vy, wz
            self.pub_vel.publish(m)
            self._teleop_active = True
        elif self._teleop_active:
            self.pub_vel.publish(Twist())              # one explicit stop when the pad is released / times out
            self._teleop_active = False

    def robot_cmd(self, cmd):
        if cmd not in ('stand_up', 'lie_down', 'damp', 'shake_hand'):
            return False
        self.set_teleop(0, 0, 0)
        self.pub_cmd.publish(String(data=cmd))
        return True

    def estop(self):
        self.cancel_goal('E-STOP')
        self.teleop = (0.0, 0.0, 0.0, 0.0)
        self.pub_vel.publish(Twist())
        self.pub_cmd.publish(String(data='damp'))

    # ------------------------------------------------------------------ navigation
    def send_goal(self, x, y, yaw):
        if not self.nav_client.wait_for_server(timeout_sec=1.0):
            return False, 'Nav2 is not running (use Load map)'
        if self.running('static_nav') and not self.pose_confirmed:
            return False, 'place the robot first: use Place robot and mark where it really stands (or "robot is at map origin")'
        if not self.localized:
            return False, 'robot is not localized in the map yet'
        goal = NavigateToPose.Goal()
        goal.pose = PoseStamped()
        goal.pose.header.frame_id = 'map'
        goal.pose.header.stamp = self.get_clock().now().to_msg()
        goal.pose.pose.position.x, goal.pose.pose.position.y = float(x), float(y)
        goal.pose.pose.orientation.z, goal.pose.pose.orientation.w = math.sin(yaw / 2), math.cos(yaw / 2)
        with self.lock:
            self.nav.update(status='sending', goal=(x, y, yaw), distance_remaining=None)
            self.plan = []
        fut = self.nav_client.send_goal_async(goal, feedback_callback=self._nav_feedback)
        fut.add_done_callback(self._nav_accepted)
        return True, 'goal sent'

    def _nav_feedback(self, fb):
        with self.lock:
            self.nav['distance_remaining'] = round(float(fb.feedback.distance_remaining), 2)

    def _nav_accepted(self, fut):
        gh = fut.result()
        with self.lock:
            if not gh.accepted:
                self.nav.update(status='rejected', goal_handle=None)
                return
            self.nav.update(status='navigating', goal_handle=gh)
        gh.get_result_async().add_done_callback(self._nav_done)

    def _nav_done(self, fut):
        st = fut.result().status
        names = {GoalStatus.STATUS_SUCCEEDED: 'succeeded', GoalStatus.STATUS_ABORTED: 'aborted',
                 GoalStatus.STATUS_CANCELED: 'canceled'}
        with self.lock:
            if self.nav['status'] in ('navigating', 'sending'):
                self.nav['status'] = names.get(st, f'ended({st})')
            self.nav['goal_handle'] = None

    def cancel_goal(self, why='cancel'):
        with self.lock:
            gh = self.nav.get('goal_handle')
            if self.nav['status'] in ('navigating', 'sending'):
                self.nav['status'] = 'canceled' if gh is not None else 'idle'
        if gh is not None:
            gh.cancel_goal_async()
        self.pub_vel.publish(Twist())

    # ------------------------------------------------------------------ processes and workflows
    def make_proc(self, name, cmd):
        p = self.procs.get(name)
        if p is None or p.cmd != cmd:
            p = Proc(name, cmd, self.log_dir, self.env)
            self.procs[name] = p
        return p

    def proc_cmds(self, name, db=None, bag=None):
        a = self.args
        if name == 'bag':
            return ['ros2', 'bag', 'record', '-o', bag] + BAG_TOPICS
        if name == 'robot':
            return ['ros2', 'launch', 'agibot_d1_bridge', 'bridge.launch.py', f'local_ip:={a.local_ip}', f'robot_ip:={a.robot_ip}']
        if name == 'perception':
            return ['ros2', 'launch', 'd1_depth', 'depth_demo.launch.py', 'rviz:=false', f'url:=rtsp://{a.robot_ip}:8554/test']
        if name == 'mapping':
            return ['ros2', 'launch', 'd1_slam', 'rgbd_rtabmap.launch.py', 'rviz:=false', f'database_path:={db}', 'delete_db:=true']
        if name == 'localization':
            return ['ros2', 'launch', 'd1_slam', 'rgbd_rtabmap.launch.py', 'rviz:=false', f'database_path:={db}',
                    'delete_db:=false', 'localization:=true']
        if name == 'nav2':
            return ['ros2', 'launch', 'd1_nav', 'navigation.launch.py']
        if name == 'static_nav':          # saved lidar map + hand-placed localization + Nav2
            sp = self.start_pose or (0.0, 0.0, 0.0)
            return ['ros2', 'launch', 'd1_nav', 'static_navigation.launch.py', f'map:={db}',
                    f'x:={sp[0]:.3f}', f'y:={sp[1]:.3f}', f'yaw:={sp[2]:.3f}']
        if name == 'rviz':
            return ['ros2', 'run', 'rviz2', 'rviz2', '-d', os.path.join(
                os.popen('ros2 pkg prefix d1_slam').read().strip(), 'share/d1_slam/rviz/mapping.rviz')]
        raise KeyError(name)

    def start(self, name, db=None, bag=None):
        if name == 'robot' and self.external_robot:      # the bridge is owned by someone else (SDK allows one client)
            return False
        return self.make_proc(name, self.proc_cmds(name, db, bag)).start()

    def stop(self, name):
        p = self.procs.get(name)
        return p.stop() if p else False

    def running(self, name):
        if name == 'robot' and self.external_robot:
            return True
        p = self.procs.get(name)
        return bool(p and p.running())

    def start_process(self):
        """Robot link + perception (camera, depth, camera TFs)."""
        started = []
        if self.start('robot'):
            started.append('robot')
        if self.mono:
            threading.Timer(2.5, lambda: self.start('perception')).start()
            if not self.running('perception'):
                started.append('perception')
        return {'ok': True, 'started': started}

    def start_map(self, name=None, record=True):
        if not self.mono:
            return {'ok': False, 'error': 'monocular SLAM is deactivated (start the dashboard with mono_slam:=true)'}
        if self.running('mapping'):
            return {'ok': False, 'error': 'mapping is already running'}
        for n in ('nav2', 'localization'):
            self.stop(n)
        name = name or time.strftime('map_%Y%m%d_%H%M%S')
        name = ''.join(ch for ch in name if ch.isalnum() or ch in '-_') or 'map'
        if not self.running('robot') or not self.running('perception'):
            self.start_process()
            time.sleep(3.0)
        with self.lock:
            for g in ('map', 'global', 'local'):
                self.grids.pop(g, None)
            self.plan = []
            self.current_map, self.ui_mode = name, 'mapping'
            self.trail = []
        self.start('mapping', db=os.path.join(self.maps_dir, name + '.db'))
        bag = None
        if record:
            bag = os.path.join(self.maps_dir, name + '_bag')
            self.stop('bag')
            self.start('bag', bag=bag)
        return {'ok': True, 'name': name, 'bag': bag}

    def _job(self, name, status, message=''):
        with self.lock:
            self.job = {'name': name, 'status': status, 'message': message}

    def _save_mono_map(self, name=None):
        if not self.running('mapping'):
            return {'ok': False, 'error': 'not mapping'}
        if 'map' not in self.grids:
            return {'ok': False, 'error': 'no map received yet'}
        if self.job['status'] == 'running':
            return {'ok': False, 'error': 'busy: ' + str(self.job['name'])}
        name = name or self.current_map or time.strftime('map_%Y%m%d_%H%M%S')
        name = ''.join(ch for ch in name if ch.isalnum() or ch in '-_') or 'map'
        threading.Thread(target=self._save_worker, args=(name,), daemon=True).start()
        return {'ok': True, 'name': name}

    def _save_worker(self, name):
        self._job('save_map', 'running', 'saving occupancy grid...')
        base = os.path.join(self.maps_dir, name)
        try:
            r = subprocess.run(['ros2', 'run', 'nav2_map_server', 'map_saver_cli', '-f', base + '_grid',
                                '--ros-args', '-p', 'save_map_timeout:=15000.0'], env=self.env, capture_output=True,
                               text=True, timeout=40)
            if not os.path.exists(base + '_grid.yaml'):
                raise RuntimeError('map_saver failed: ' + (r.stderr or r.stdout)[-200:])
            self._job('save_map', 'running', 'flushing the RTAB-Map database (stopping mapping)...')
            db = os.path.join(self.maps_dir, (self.current_map or name) + '.db')
            self.stop('mapping')
            self.stop('bag')                                   # SIGINT: rosbag2 finalises the bag
            if db != base + '.db' and os.path.exists(db):
                os.replace(db, base + '.db')
            bagdir = os.path.join(self.maps_dir, (self.current_map or name) + '_bag')
            meta = {'name': name, 'created': time.strftime('%F %T'), 'db': base + '.db', 'grid': base + '_grid.yaml',
                    'db_bytes': os.path.getsize(base + '.db') if os.path.exists(base + '.db') else 0}
            if os.path.isdir(bagdir):
                meta['bag'] = bagdir
            with open(base + '.json', 'w') as f:
                json.dump(meta, f, indent=1)
            with self.lock:
                self.ui_mode, self.current_map = 'idle', name
            self._job('save_map', 'done', f'saved "{name}"')
        except Exception as e:  # noqa: BLE001
            self._job('save_map', 'error', str(e))

    # ------------------------------------------------------------------ lidar maps (static navigation)
    def _repo_maps_dir(self):
        """<repo>/maps (one folder per lidar scan). D1_MAPS_DIR wins; else walk up from this file (works with --symlink-install)."""
        if os.environ.get('D1_MAPS_DIR'):
            return os.environ['D1_MAPS_DIR']
        here = os.path.dirname(os.path.realpath(__file__))
        while here != os.path.dirname(here):
            if os.path.isfile(os.path.join(here, 'maps', 'README.md')) or os.path.isdir(os.path.join(here, 'maps')):
                return os.path.join(here, 'maps')
            here = os.path.dirname(here)
        return ''

    def _static_maps(self):
        out = []
        for source, root in (('saved', os.path.join(self.maps_dir, 'maps')), ('repo', self._repo_maps_dir())):
            try:
                names = sorted(os.listdir(root)) if root else []
            except OSError:
                names = []
            for d in names:
                y = os.path.join(root, d, 'map.yaml')
                if not os.path.isfile(y):
                    continue
                meta = {}
                try:
                    meta = json.load(open(os.path.join(root, d, 'meta.json')))
                except (OSError, ValueError):
                    pass
                created = time.strftime('%Y-%m-%d %H:%M', time.localtime(os.path.getmtime(y)))
                out.append({'id': f'{source}:{d}', 'name': f'{d}', 'kind': 'lidar', 'source': source, 'yaml': y,
                            'created': created, 'start': meta.get('start_pose')})
        return out

    def list_maps(self):
        out = self._static_maps()
        if self.mono:
            out += self._list_mono_maps()
        return sorted(out, key=lambda m: (m.get('kind') != 'lidar', m.get('source') != 'saved', m.get('name', '')))

    def _find_map(self, mid):
        return next((m for m in self.list_maps() if m['id'] == mid or m['name'] == mid), None)

    def load_map(self, mid):
        m = self._find_map(mid)
        if m is None:
            return {'ok': False, 'error': f'unknown map "{mid}"'}
        if m['kind'] != 'lidar':
            if not self.mono:
                return {'ok': False, 'error': 'monocular SLAM is deactivated (start the dashboard with mono_slam:=true)'}
            return self._load_mono_map(m['name'])
        self.cancel_goal('load map')
        for n in ('mapping', 'bag', 'nav2', 'localization', 'static_nav'):
            self.stop(n)
        if not self.running('robot'):
            self.start_process()
            time.sleep(2.0)
        with self.lock:
            for g in ('map', 'global', 'local'):
                self.grids.pop(g, None)
            self.plan = []
            self.current_map, self.current_entry, self.ui_mode = m['id'], m, 'navigation'
            self.trail = []
            self.start_pose = tuple(m['start']) if m.get('start') else None
            self.pose_confirmed = bool(self.start_pose)       # a saved start pose counts as placed
            self.localized = False
        self.start('static_nav', db=m['yaml'])
        return {'ok': True, 'name': m['name'], 'start_pose': self.start_pose, 'placed': self.pose_confirmed}

    def set_pose(self, x, y, yaw):
        """Tell the robot where it is in the map (like RViz '2D Pose Estimate'); the localizer sets map->odom."""
        if not self.running('static_nav'):
            return {'ok': False, 'error': 'no map loaded (use Load map first)'}
        msg = PoseWithCovarianceStamped()
        msg.header.frame_id = 'map'
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.pose.pose.position.x, msg.pose.pose.position.y = float(x), float(y)
        msg.pose.pose.orientation.z, msg.pose.pose.orientation.w = math.sin(yaw / 2), math.cos(yaw / 2)
        msg.pose.covariance[0] = msg.pose.covariance[7] = 0.05
        msg.pose.covariance[35] = 0.1
        self.pub_init.publish(msg)
        with self.lock:
            self.start_pose, self.pose_confirmed = (float(x), float(y), float(yaw)), True
            self.trail, self.trail_frame = [], None
        return {'ok': True, 'pose': [x, y, yaw]}

    def save_map(self, name=None):
        if self.mono and self.running('mapping'):
            return self._save_mono_map(name)
        e = self.current_entry
        if not e or e.get('kind') != 'lidar' or not self.running('static_nav'):
            return {'ok': False, 'error': 'load a map first'}
        name = ''.join(ch for ch in (name or '') if ch.isalnum() or ch in '-_') or (e['name'] + time.strftime('_%H%M%S'))
        dst = os.path.join(self.maps_dir, 'maps', name)
        try:
            os.makedirs(dst, exist_ok=True)
            src = os.path.dirname(e['yaml'])
            y = open(e['yaml']).read()
            img = re.search(r'^image:\s*(\S+)', y, re.M).group(1)
            shutil.copy(os.path.join(src, img), os.path.join(dst, img))
            shutil.copy(e['yaml'], os.path.join(dst, 'map.yaml'))
            json.dump({'name': name, 'source_map': e['id'], 'start_pose': list(self.start_pose) if self.pose_confirmed else None,
                       'created': time.strftime('%F %T')}, open(os.path.join(dst, 'meta.json'), 'w'))
        except Exception as ex:  # noqa: BLE001
            return {'ok': False, 'error': f'save failed: {ex}'}
        return {'ok': True, 'name': name, 'start_pose': self.start_pose if self.pose_confirmed else None,
                'message': f'saved map "{name}"' + (' with its start pose' if self.pose_confirmed else ' (no start pose set yet)')}

    def _list_mono_maps(self):
        out = []
        try:
            for fn in sorted(os.listdir(self.maps_dir)):
                if fn.endswith('.json'):
                    try:
                        m = json.load(open(os.path.join(self.maps_dir, fn)))
                        if os.path.exists(m.get('db', '')):
                            m.update(id=m.get('name'), kind='mono')
                            out.append(m)
                    except (OSError, ValueError):
                        pass
        except OSError:
            pass
        return sorted(out, key=lambda m: m.get('created', ''), reverse=True)

    def _load_mono_map(self, name):
        db = os.path.join(self.maps_dir, name + '.db')
        if not os.path.exists(db):
            return {'ok': False, 'error': f'no database for "{name}"'}
        self.cancel_goal('load map')
        for n in ('mapping', 'bag', 'nav2', 'localization'):
            self.stop(n)
        if not self.running('robot') or not self.running('perception'):
            self.start_process()
            time.sleep(3.0)
        with self.lock:
            for g in ('map', 'global', 'local'):
                self.grids.pop(g, None)
            self.plan = []
            self.current_map, self.ui_mode = name, 'navigation'
            self.trail = []
        self.start('localization', db=db)
        threading.Timer(4.0, lambda: self.start('nav2')).start()
        return {'ok': True, 'name': name}

    def clear_all(self, stop_robot=False):
        self.cancel_goal('clear all')
        self.teleop = (0.0, 0.0, 0.0, 0.0)
        for n in ('nav2', 'localization', 'static_nav', 'mapping', 'bag', 'rviz', 'perception'):
            self.stop(n)
        kept_robot = False
        standing = self.mode in STANDING_MODES
        if stop_robot and not standing:
            self.stop('robot')
        else:
            kept_robot = self.running('robot')
        with self.lock:
            self.grids.clear()
            self.plan = []
            self.nav.update(status='idle', goal=None, distance_remaining=None, goal_handle=None)
            self.ui_mode, self.current_map, self.current_entry = 'idle', None, None
            self.start_pose, self.pose_confirmed = None, False
            self.trail = []
            self.job = {'name': None, 'status': 'idle', 'message': ''}
        msg = 'all stopped'
        if kept_robot:
            msg += ' (robot link kept running' + (': the robot is standing, lie it down before stopping the link' if standing else '') + ')'
        return {'ok': True, 'message': msg}

    def shutdown_all(self):
        for n in ('nav2', 'localization', 'static_nav', 'mapping', 'bag', 'rviz', 'perception'):
            self.stop(n)
        if self.mode not in STANDING_MODES:
            self.stop('robot')

    # ------------------------------------------------------------------ state for the UI
    def state(self):
        with self.lock:
            nav = {k: v for k, v in self.nav.items() if k != 'goal_handle'}
            return {
                't': time.time(), 'ui_mode': self.ui_mode, 'current_map': self.current_map,
                'robot': {'connected': self.connected, 'mode': self.mode, 'mode_name': MODE_NAMES.get(self.mode, '?'),
                          'battery': None if self.battery is None else round(self.battery * 100),
                          'standing': self.mode in STANDING_MODES},
                'pose': self.pose, 'pose_frame': self.pose_frame, 'localized': self.localized,
                'nav': nav, 'plan': self.plan, 'trail': self.trail[-600:] if len(self.trail) <= 600 else self.trail[::max(1, len(self.trail) // 600)],
                'procs': {n: p.info() for n, p in self.procs.items()},
                'job': self.job,
                'grids': {n: g['version'] for n, g in self.grids.items()},
                'features': {'mono_slam': self.mono, 'lidar_nav': True}, 'start_pose': self.start_pose, 'pose_confirmed': self.pose_confirmed,
            }

    def grid_payload(self, name):
        with self.lock:
            g = self.grids.get(name)
            if not g:
                return None
            return {'name': name, 'version': g['version'], 'w': g['w'], 'h': g['h'], 'res': g['res'], 'ox': g['ox'],
                    'oy': g['oy'], 'data': base64.b64encode(g['data']).decode()}


# ---------------------------------------------------------------------------------------------------- HTTP
def make_handler(dash):
    class H(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def log_message(self, *a):  # quiet
            pass

        def _send(self, code, body, ctype='application/json'):
            data = body if isinstance(body, bytes) else json.dumps(body).encode()
            self.send_response(code)
            self.send_header('Content-Type', ctype)
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(data)

        def _json(self):
            n = int(self.headers.get('Content-Length') or 0)
            try:
                return json.loads(self.rfile.read(n) or b'{}')
            except ValueError:
                return {}

        def do_GET(self):  # noqa: N802
            u = urlparse(self.path)
            q = parse_qs(u.query)
            if u.path in ('/', '/index.html'):
                with open(os.path.join(STATIC, 'index.html'), 'rb') as f:
                    return self._send(200, f.read(), 'text/html; charset=utf-8')
            if u.path == '/api/state':
                return self._send(200, dash.state())
            if u.path == '/api/grid':
                g = dash.grid_payload((q.get('name') or ['map'])[0])
                return self._send(200 if g else 404, g or {'error': 'no such grid'})
            if u.path == '/api/maps':
                return self._send(200, dash.list_maps())
            if u.path == '/api/log':
                name = (q.get('name') or [''])[0]
                p = dash.procs.get(name)
                return self._send(200, {'name': name, 'lines': p.tail(80) if p else []})
            if u.path == '/api/events':
                return self._events()
            self._send(404, {'error': 'not found'})

        def _events(self):
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Connection', 'keep-alive')
            self.end_headers()
            seen = {}
            try:
                while True:
                    st = dash.state()
                    self.wfile.write(b'event: state\ndata: ' + json.dumps(st).encode() + b'\n\n')
                    for name, ver in st['grids'].items():
                        if seen.get(name) != ver:
                            g = dash.grid_payload(name)
                            if g:
                                self.wfile.write(b'event: grid\ndata: ' + json.dumps(g).encode() + b'\n\n')
                                seen[name] = ver
                    for name in list(seen):
                        if name not in st['grids']:
                            self.wfile.write(b'event: gridclear\ndata: ' + json.dumps({'name': name}).encode() + b'\n\n')
                            del seen[name]
                    self.wfile.flush()
                    time.sleep(0.25)
            except (BrokenPipeError, ConnectionResetError, OSError):
                return

        def do_POST(self):  # noqa: N802
            u, b = urlparse(self.path).path, self._json()
            try:
                if u == '/api/cmd_vel':
                    dash.set_teleop(float(b.get('vx', 0)), float(b.get('vy', 0)), float(b.get('wz', 0)))
                    return self._send(200, {'ok': True})
                if u == '/api/robot':
                    return self._send(200, {'ok': dash.robot_cmd(b.get('cmd', ''))})
                if u == '/api/estop':
                    dash.estop()
                    return self._send(200, {'ok': True})
                if u == '/api/workflow':
                    a = b.get('action')
                    if a == 'start_process':
                        return self._send(200, dash.start_process())
                    if a == 'start_map':
                        return self._send(200, dash.start_map(b.get('name'), bool(b.get('record', True))))
                    if a == 'save_map':
                        return self._send(200, dash.save_map(b.get('name')))
                    if a == 'load_map':
                        return self._send(200, dash.load_map(b.get('name', '')))
                    if a == 'clear_all':
                        return self._send(200, dash.clear_all(bool(b.get('stop_robot'))))
                    return self._send(400, {'ok': False, 'error': 'unknown action'})
                if u == '/api/process':
                    name, act = b.get('name', ''), b.get('action', '')
                    if name not in ('robot', 'perception', 'mapping', 'bag', 'localization', 'nav2', 'static_nav', 'rviz'):
                        return self._send(400, {'ok': False, 'error': 'unknown process'})
                    if act == 'start':
                        db = os.path.join(dash.maps_dir, (dash.current_map or 'manual') + '.db')
                        bag = os.path.join(dash.maps_dir, time.strftime('manual_%Y%m%d_%H%M%S') + '_bag')
                        return self._send(200, {'ok': dash.start(name, db=db, bag=bag)})
                    if act == 'stop':
                        if name == 'robot' and dash.mode in STANDING_MODES:
                            return self._send(200, {'ok': False, 'error': 'robot is standing: lie it down first'})
                        return self._send(200, {'ok': dash.stop(name)})
                    return self._send(400, {'ok': False, 'error': 'unknown action'})
                if u == '/api/pose':
                    return self._send(200, dash.set_pose(float(b['x']), float(b['y']), float(b.get('yaw', 0))))
                if u == '/api/goal':
                    ok, msg = dash.send_goal(float(b['x']), float(b['y']), float(b.get('yaw', 0)))
                    return self._send(200, {'ok': ok, 'message': msg})
                if u == '/api/cancel':
                    dash.cancel_goal('user')
                    return self._send(200, {'ok': True})
            except Exception as e:  # noqa: BLE001
                return self._send(500, {'ok': False, 'error': str(e)})
            self._send(404, {'error': 'not found'})

    return H


def main():
    ap = argparse.ArgumentParser(description='D1 developer dashboard')
    ap.add_argument('--robot-ip', default='172.24.1.41')
    ap.add_argument('--local-ip', default='172.24.1.20')
    ap.add_argument('--host', default='127.0.0.1')
    ap.add_argument('--port', type=int, default=8080)
    ap.add_argument('--maps-dir', default='~/d1_maps')
    ap.add_argument('--mono-slam', default='false', help='true: enable the (deactivated) monocular SLAM workflow')
    ap.add_argument('--external-robot', default='false', help='true: a bridge already runs elsewhere; never start/stop one')
    args, ros_args = ap.parse_known_args()
    rclpy.init(args=ros_args)
    dash = Dashboard(args)
    ex = MultiThreadedExecutor(num_threads=4)
    ex.add_node(dash)
    threading.Thread(target=ex.spin, daemon=True).start()
    srv = ThreadingHTTPServer((args.host, args.port), make_handler(dash))
    srv.daemon_threads = True
    dash.get_logger().info(f'dashboard on http://{args.host}:{args.port}  (maps: {dash.maps_dir})')
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        dash.get_logger().info('shutting down managed processes')
        dash.shutdown_all()
        srv.server_close()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
