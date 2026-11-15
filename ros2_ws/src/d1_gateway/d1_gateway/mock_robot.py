"""A simulated robot for frontend development and tests: no ROS, no hardware. Same events as the real one.

Goals move a point robot at `speed` m/s; goals to y < -50 never arrive within the test window (cancel test), unreachable
targets (|x| or |y| > 100) are rejected like Nav2 would."""
import math
import threading
import time


class MockRobot:
    def __init__(self, speed=0.5, intent_s=0.8, localized=True):
        self.speed, self.intent_s, self.localized = speed, intent_s, localized
        self.pose = {'x': 0.0, 'y': 0.0, 'yaw': 0.0, 'frame': 'map'}
        self.mode = 'standing'
        self.battery = 0.87
        self._emit = lambda m: None
        self._goal = None
        self._lock = threading.Lock()

    def start(self, emit):
        self._emit = emit
        threading.Thread(target=self._run, daemon=True).start()

    def state(self):
        loc = {'state': 'localized', 'ready': True, 'source': 'mock', 'message': 'mock robot'} if self.localized else \
            {'state': 'need_pose', 'ready': False, 'source': 'none', 'message': 'place the robot on the map'}
        return {'pose': dict(self.pose), 'battery': self.battery, 'mode': self.mode, 'connected': True, 'nav_ready': True,
                'localization': loc}

    def navigate(self, gid, pose):
        with self._lock:
            old, self._goal = self._goal, None
        if old:
            self._emit({'type': 'goal_canceled', 'id': old['id'], 'reason': 'preempted by a new goal'})
        if abs(pose['x']) > 100 or abs(pose['y']) > 100:
            self._emit({'type': 'goal_rejected', 'id': gid, 'reason': 'target outside the map'})
            return
        if self.mode != 'standing':
            self._emit({'type': 'goal_rejected', 'id': gid, 'reason': f'robot is {self.mode}; send intent stand_up first'})
            return
        if not self.localized:
            self._emit({'type': 'goal_rejected', 'id': gid, 'reason': 'robot is not localized on the map (place the robot on the map)'})
            return
        with self._lock:
            self._goal = {'id': gid, 'pose': pose, 't0': time.time(),
                          'd0': math.hypot(pose['x'] - self.pose['x'], pose['y'] - self.pose['y'])}
        self._emit({'type': 'goal_accepted', 'id': gid})

    def cancel_goal(self, gid, reason='canceled'):
        with self._lock:
            g = self._goal
            if g and g['id'] == gid:
                self._goal = None
            else:
                g = None
        if g:
            self._emit({'type': 'goal_canceled', 'id': gid, 'reason': reason})

    def stop(self):
        with self._lock:
            g = self._goal
        if g:
            self.cancel_goal(g['id'], 'stop')

    def estop(self):
        self.stop()
        self.mode = 'damping'

    def intent(self, iid, name, args):
        self._emit({'type': 'intent_started', 'id': iid, 'name': name})

        def done():
            time.sleep(self.intent_s)
            if name == 'stand_up':
                self.mode = 'standing'
            elif name == 'lie_down':
                self.mode = 'lying'
            self._emit({'type': 'intent_done', 'id': iid, 'name': name})
        threading.Thread(target=done, daemon=True).start()

    def _run(self):
        last = 0.0
        while True:
            time.sleep(0.1)
            with self._lock:
                g = self._goal
            if not g:
                continue
            p, t = g['pose'], g['pose']
            dx, dy = t['x'] - self.pose['x'], t['y'] - self.pose['y']
            d = math.hypot(dx, dy)
            step = self.speed * 0.1
            if d <= max(step, 0.05):
                self.pose.update(x=t['x'], y=t['y'], yaw=t['yaw'])
                with self._lock:
                    self._goal = None
                self._emit({'type': 'goal_reached', 'id': g['id']})
                continue
            self.pose['x'] += dx / d * step
            self.pose['y'] += dy / d * step
            self.pose['yaw'] = math.atan2(dy, dx)
            if time.time() - last > 0.5:
                last = time.time()
                self._emit({'type': 'goal_progress', 'id': g['id'], 'distance_remaining': round(d, 2),
                            'elapsed_s': round(time.time() - g['t0'], 1)})
