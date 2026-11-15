"""RobotPort on ROS 2: goals -> Nav2 NavigateToPose, intents -> the D1 bridge services, state <- bridge topics + TF."""
import math
import threading
import time

import rclpy
from action_msgs.msg import GoalStatus
from geometry_msgs.msg import PoseStamped, Twist
from nav2_msgs.action import NavigateToPose
from rclpy.action import ActionClient
from rclpy.node import Node
from rclpy.time import Time
from sensor_msgs.msg import BatteryState
from std_msgs.msg import Bool, Int32, String
from std_srvs.srv import Trigger
import tf2_ros

MODE_NAMES = {0: 'damping', 1: 'standing', 3: 'moving', 18: 'moving', 51: 'lying'}
STANDING = ('standing', 'moving')
SERVICE_FOR = {'stand_up': 'stand_up', 'lie_down': 'lie_down', 'shake_hand': 'shake_hand', 'say_hi': 'shake_hand'}


def _yaw_quat(yaw):
    return math.sin(yaw / 2), math.cos(yaw / 2)


def _quat_yaw(q):
    return math.atan2(2 * (q.w * q.z + q.x * q.y), 1 - 2 * (q.y * q.y + q.z * q.z))


class RosRobot(Node):
    def __init__(self):
        super().__init__('d1_gateway')
        p = self.declare_parameter
        p('bridge_node', 'd1_cmd_vel_bridge')
        p('map_frame', 'map')
        p('odom_frame', 'odom')
        p('base_frame', 'base_footprint')
        p('cmd_vel_topic', 'cmd_vel')
        p('say_topic', 'say')
        p('follow_node', 'd1_follow')
        p('progress_period_s', 0.5)
        g = lambda n: self.get_parameter(n).value  # noqa: E731
        self.map_frame, self.odom_frame, self.base_frame = g('map_frame'), g('odom_frame'), g('base_frame')
        self.progress_period = float(g('progress_period_s'))
        self._emit = lambda m: None
        self._lock = threading.RLock()
        self._goal = None                    # {'id', 'handle', 'last_progress'}
        self.mode, self.connected, self.battery = 'unknown', False, None

        self.nav = ActionClient(self, NavigateToPose, 'navigate_to_pose')
        self.tfb = tf2_ros.Buffer()
        self.tfl = tf2_ros.TransformListener(self.tfb, self)
        self.pub_vel = self.create_publisher(Twist, g('cmd_vel_topic'), 10)
        self.pub_say = self.create_publisher(String, g('say_topic'), 10)
        self.pub_cmd = self.create_publisher(String, 'robot_cmd', 10)
        self.create_subscription(Int32, 'ctrl_mode', self._on_mode, 10)
        self.create_subscription(Bool, 'connected', self._on_conn, 10)
        self.create_subscription(BatteryState, 'battery', self._on_batt, 10)
        bn = g('bridge_node')
        self.trig = {s: self.create_client(Trigger, f'/{bn}/{s}') for s in set(SERVICE_FOR.values())}
        fn = g('follow_node')
        self.follow = {'follow_lock': self.create_client(Trigger, f'/{fn}/lock'),
                       'follow_release': self.create_client(Trigger, f'/{fn}/release')}

    # ---- RobotPort
    def start(self, emit):
        self._emit = emit

    def state(self):
        return {'pose': self._pose(), 'battery': self.battery, 'mode': self.mode, 'connected': self.connected,
                'nav_ready': self.nav.server_is_ready()}

    def navigate(self, gid, pose):
        if not self.connected:
            return self._emit({'type': 'goal_rejected', 'id': gid, 'reason': 'robot link is down (bridge not connected to the SDK)'})
        if self.mode not in STANDING:
            return self._emit({'type': 'goal_rejected', 'id': gid, 'reason': f'robot is {self.mode}; send intent stand_up first'})
        if not self.nav.server_is_ready():
            return self._emit({'type': 'goal_rejected', 'id': gid, 'reason': 'Nav2 is not running (navigate_to_pose unavailable)'})
        with self._lock:
            old = self._goal
            self._goal = {'id': gid, 'handle': None, 'last_progress': 0.0}
        if old:
            self._cancel_handle(old, 'preempted by a new goal')
        msg = NavigateToPose.Goal()
        msg.pose = PoseStamped()
        msg.pose.header.frame_id = pose.get('frame', self.map_frame)
        msg.pose.pose.position.x, msg.pose.pose.position.y = pose['x'], pose['y']
        msg.pose.pose.orientation.z, msg.pose.pose.orientation.w = _yaw_quat(pose['yaw'])
        fut = self.nav.send_goal_async(msg, feedback_callback=lambda fb, gid=gid: self._on_feedback(gid, fb))
        fut.add_done_callback(lambda f, gid=gid: self._on_accept(gid, f))

    def cancel_goal(self, gid, reason='canceled'):
        with self._lock:
            g = self._goal
        if g and g['id'] == gid:
            self._cancel_handle(g, reason)

    def stop(self):
        with self._lock:
            g = self._goal
        if g:
            self._cancel_handle(g, 'stop')
        for _ in range(3):
            self.pub_vel.publish(Twist())

    def estop(self):
        self.stop()
        self.pub_cmd.publish(String(data='damp'))

    def intent(self, iid, name, args):
        if name == 'say':
            self.pub_say.publish(String(data=str(args.get('text', ''))))
            self._emit({'type': 'intent_started', 'id': iid, 'name': name})
            return self._emit({'type': 'intent_done', 'id': iid, 'name': name})
        cli = self.follow.get(name) or self.trig[SERVICE_FOR[name]]
        if not cli.service_is_ready():
            why = 'follow node not running' if name in self.follow else 'robot bridge service not available'
            return self._emit({'type': 'intent_failed', 'id': iid, 'name': name, 'reason': why})
        self._emit({'type': 'intent_started', 'id': iid, 'name': name})
        if name == 'say_hi' and args.get('text'):
            self.pub_say.publish(String(data=str(args['text'])))
        fut = cli.call_async(Trigger.Request())

        def done(f):
            try:
                r = f.result()
                if r.success:
                    self._emit({'type': 'intent_done', 'id': iid, 'name': name, 'detail': r.message})
                else:
                    self._emit({'type': 'intent_failed', 'id': iid, 'name': name, 'reason': r.message})
            except Exception as e:  # noqa: BLE001
                self._emit({'type': 'intent_failed', 'id': iid, 'name': name, 'reason': str(e)})
        fut.add_done_callback(done)

    # ---- Nav2 callbacks
    def _on_accept(self, gid, fut):
        try:
            h = fut.result()
        except Exception as e:  # noqa: BLE001
            return self._finish(gid, {'type': 'goal_failed', 'id': gid, 'reason': f'send failed: {e}'})
        with self._lock:
            g = self._goal
            if not g or g['id'] != gid:               # replaced while the request was in flight
                if h.accepted:
                    h.cancel_goal_async()
                return
            g['handle'] = h
        if not h.accepted:
            return self._finish(gid, {'type': 'goal_rejected', 'id': gid, 'reason': 'Nav2 rejected the goal (outside map, or in an obstacle?)'})
        self._emit({'type': 'goal_accepted', 'id': gid})
        h.get_result_async().add_done_callback(lambda f, gid=gid: self._on_result(gid, f))

    def _on_feedback(self, gid, fb):
        with self._lock:
            g = self._goal
            if not g or g['id'] != gid or time.monotonic() - g['last_progress'] < self.progress_period:
                return
            g['last_progress'] = time.monotonic()
        f = fb.feedback
        self._emit({'type': 'goal_progress', 'id': gid, 'distance_remaining': round(float(f.distance_remaining), 2),
                    'recoveries': int(f.number_of_recoveries),
                    'elapsed_s': round(f.navigation_time.sec + f.navigation_time.nanosec * 1e-9, 1)})

    def _on_result(self, gid, fut):
        with self._lock:
            g = self._goal
            if not g or g['id'] != gid:               # already answered (canceled / preempted)
                return
        try:
            st = fut.result().status
        except Exception as e:  # noqa: BLE001
            return self._finish(gid, {'type': 'goal_failed', 'id': gid, 'reason': str(e)})
        if st == GoalStatus.STATUS_SUCCEEDED:
            self._finish(gid, {'type': 'goal_reached', 'id': gid})
        elif st == GoalStatus.STATUS_CANCELED:
            self._finish(gid, {'type': 'goal_canceled', 'id': gid, 'reason': 'canceled'})
        else:
            self._finish(gid, {'type': 'goal_failed', 'id': gid, 'reason': 'Nav2 could not reach the goal (blocked or timed out)'})

    def _finish(self, gid, msg):
        with self._lock:
            if self._goal and self._goal['id'] == gid:
                self._goal = None
        self._emit(msg)

    def _cancel_handle(self, g, reason):
        gid = g['id']
        with self._lock:
            if self._goal and self._goal['id'] == gid:
                self._goal = None
        if g['handle'] is not None and g['handle'].accepted:
            g['handle'].cancel_goal_async()
        self._emit({'type': 'goal_canceled', 'id': gid, 'reason': reason})

    # ---- state
    def _pose(self):
        for fr in (self.map_frame, self.odom_frame):
            try:
                t = self.tfb.lookup_transform(fr, self.base_frame, Time()).transform
                return {'x': round(t.translation.x, 3), 'y': round(t.translation.y, 3),
                        'yaw': round(_quat_yaw(t.rotation), 3), 'frame': fr}
            except Exception:  # noqa: BLE001
                continue
        return None

    def _on_mode(self, m):
        new = MODE_NAMES.get(m.data, f'mode {m.data}')
        if new != self.mode and self.mode != 'unknown':
            self._emit({'type': 'robot_event', 'kind': 'mode_changed', 'detail': f'{self.mode} -> {new}'})
        self.mode = new

    def _on_conn(self, m):
        if m.data != self.connected:
            self._emit({'type': 'robot_event', 'kind': 'link_up' if m.data else 'link_down',
                        'detail': 'robot SDK link ' + ('restored' if m.data else 'lost')})
        self.connected = bool(m.data)

    def _on_batt(self, m):
        self.battery = round(float(m.percentage), 3) if m.percentage == m.percentage else None
