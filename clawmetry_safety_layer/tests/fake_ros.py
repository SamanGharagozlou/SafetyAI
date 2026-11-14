"""A fake rclpy for the real-robot bridge tests: nodes, publishers (looped back to subscribers on the same topic,
like DDS), subscriptions, service clients and action clients. No ROS 2 install needed."""

from __future__ import annotations

import threading
import time
import types


class Msg:
    """Attribute bag that auto-creates nested bags (msg.linear.x = ...). Also used as every message type."""

    def __getattr__(self, k):
        if k.startswith("__"):
            raise AttributeError(k)
        v = Msg()
        setattr(self, k, v)
        return v


class Future:
    def __init__(self, value=None, done=True):
        self._v, self._done = value, done

    def done(self):
        return self._done

    def result(self):
        return self._v


class Pub:
    def __init__(self, node, topic):
        self.node, self.topic, self.sent, self.times = node, topic, [], []

    def publish(self, m):
        self.sent.append(m)
        self.times.append(time.time())
        for cb in list(self.node.subs.get(self.topic, [])):  # loopback, like DDS
            cb(m)


class ServiceClient:
    def __init__(self, node, srv_type, name):
        self.node, self.name, self.calls = node, name, []

    def wait_for_service(self, timeout_sec=None):
        return True

    def call_async(self, req):
        self.calls.append(req)
        resp = Msg()
        resp.response.header.code = self.node.ros.service_code(self.name, req)
        return Future(resp)


class GoalHandle:
    def __init__(self, client):
        self.accepted = True
        self.cancelled = False
        self.client = client

    def cancel_goal_async(self):
        self.cancelled = True
        self.client.cancels += 1
        return Future(None)

    def get_result_async(self):
        return Future(types.SimpleNamespace(result=Msg()))


class ActionClient:
    def __init__(self, node, action_type, name):
        self.name, self.goals, self.handles, self.cancels = name, [], [], 0
        node.actions[name] = self

    def wait_for_server(self, timeout_sec=None):
        return True

    def send_goal_async(self, goal):
        self.goals.append(goal)
        h = GoalHandle(self)
        self.handles.append(h)
        return Future(h)


class Node:
    def __init__(self, ros, name):
        self.ros, self.name = ros, name
        self.pubs: dict[str, Pub] = {}
        self.subs: dict[str, list] = {}
        self.clients: dict[str, ServiceClient] = {}
        self.actions: dict[str, ActionClient] = {}

    def create_publisher(self, t, topic, qos):
        self.pubs[topic] = Pub(self, topic)
        return self.pubs[topic]

    def create_subscription(self, t, topic, cb, qos):
        self.subs.setdefault(topic, []).append(cb)

    def create_client(self, t, name):
        self.clients[name] = ServiceClient(self, t, name)
        return self.clients[name]

    def destroy_node(self):
        pass

    def feed(self, topic, msg):
        for cb in list(self.subs.get(topic, [])):
            cb(msg)


class FakeRos:
    """Namespace shaped like the bridges' _import_ros() result. Every message type is Msg; services/actions have
    .Request / .Goal = Msg. service_code(name, req) -> response code (override per test)."""

    def __init__(self, **types_):
        self._ok = False
        self.node: Node | None = None
        self.nodes: list[Node] = []
        self.rclpy = types.SimpleNamespace(
            ok=lambda: self._ok, init=self._init, create_node=self._node, spin=lambda n: None, shutdown=self._shutdown
        )
        self.ActionClient = ActionClient
        self.qos = "best_effort"
        self.codes: dict = {}
        self.lock = threading.Lock()

    def __getattr__(self, name):  # any message / service / action type
        if name[:1].isupper():
            return _Type()
        raise AttributeError(name)

    def service_code(self, name, req) -> int:
        f = self.codes.get(name)
        return int(f(req)) if callable(f) else int(f or 0)

    def _init(self):
        self._ok = True

    def _shutdown(self):
        self._ok = False

    def _node(self, name):
        self.node = Node(self, name)
        self.nodes.append(self.node)
        return self.node


class _Type:
    """A message type: calling it makes a Msg; .Request() / .Goal() make Msgs too."""

    Request = Msg
    Goal = Msg

    def __call__(self):
        return Msg()


def odom(x, y, yaw, vx=0.0):
    import math

    m = Msg()
    m.pose.pose.position.x, m.pose.pose.position.y = x, y
    q = m.pose.pose.orientation
    q.x, q.y, q.z, q.w = 0.0, 0.0, math.sin(yaw / 2), math.cos(yaw / 2)
    m.twist.twist.linear.x, m.twist.twist.linear.y, m.twist.twist.angular.z = vx, 0.0, 0.0
    return m


def imu(roll=0.0, pitch=0.0):
    import math

    cr, sr, cp, sp = math.cos(roll / 2), math.sin(roll / 2), math.cos(pitch / 2), math.sin(pitch / 2)
    m = Msg()
    m.orientation.w, m.orientation.x, m.orientation.y, m.orientation.z = cr * cp, sr * cp, cr * sp, -sr * sp
    return m
