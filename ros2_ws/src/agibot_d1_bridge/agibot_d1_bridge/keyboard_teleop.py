"""Keyboard teleop for the AgiBot D1 bridge (publishes cmd_vel, attitude_cmd, robot_cmd, gait).

A terminal reports key *presses* (and auto-repeat while held) but never key
*releases*, so motion lasts `hold` seconds after the last key event: hold a key
to keep moving, let go and the robot stops. The bridge watchdog is a second net.

  w / s  or  Up / Down      forward / back
  a / d  or  Left / Right   turn left / right
  q / e                     strafe left / right
  space                     stop
  + / -                     speed up / down
  1  stand up     2  lie down     3  shake hand
  c  cycle gait (move -> crawl -> climb)
  x  DAMP (emergency, robot drops)
  Gestures (robot must be standing; any key cancels):
  4 nod   5 shake   6 tilt   7 bow   8 squat   9 wiggle   0 look around
  r sway circle     g greet (bow + shake hand)
  Ctrl-C  stop and quit (robot keeps its pose; bridge watchdog then damps it)
"""
import argparse
import os
import select
import sys
import termios
import time
import tty

import rclpy
from geometry_msgs.msg import Twist
from std_msgs.msg import Int32, String

from agibot_d1_bridge.d1w_gestures import (GESTURES, MODE_STAND, SEQUENCES, validate,
                                           validate_sequences)

HELP = __doc__.split('\n', 1)[1]
GAITS = ('move', 'crawl', 'climb')
# key -> (x, y, wz) direction
MOVES = {
    'w': (1, 0, 0), 's': (-1, 0, 0), 'a': (0, 0, 1), 'd': (0, 0, -1),
    'q': (0, 1, 0), 'e': (0, -1, 0),
    '\x1b[A': (1, 0, 0), '\x1b[B': (-1, 0, 0), '\x1b[D': (0, 0, 1), '\x1b[C': (0, 0, -1),
}
CMDS = {'1': 'stand_up', '2': 'lie_down', '3': 'shake_hand', 'x': 'damp'}
GESTURE_KEYS = {'4': 'nod', '5': 'shake', '6': 'tilt', '7': 'bow', '8': 'squat',
                '9': 'wiggle', '0': 'look_around', 'r': 'sway_circle', 'g': 'greet'}
# SDK method used in d1w_gestures.SEQUENCES -> robot_cmd understood by cmd_vel_bridge
SDK_TO_CMD = {'shakeHand': 'shake_hand'}
RESET_S = 1.0     # wait after the stand_up re-centre, as in d1w_gestures


def gesture_plan(name, scale, reset=True):
    """Flatten a d1w_gestures gesture/sequence into timed steps.

    Each step is (duration_s, seg, robot_cmd): `seg` (a d1w_gestures.Seg) is streamed
    on attitude_cmd for `duration_s`; `robot_cmd` is published once when the step starts.
    The SDK link belongs to cmd_vel_bridge, so gestures go through it as topics.
    """
    if name in SEQUENCES:
        steps = []
        for kind, *arg in SEQUENCES[name]:
            if kind == 'gesture':
                steps += gesture_plan(arg[0], scale, reset)
            else:
                method, wait_s = arg
                steps.append((wait_s, None, SDK_TO_CMD[method]))
                if reset:
                    steps.append((RESET_S, None, 'stand_up'))
        return steps
    segs = [s.scaled(scale) for s in GESTURES[name]]
    validate(segs)
    steps = [(s.duration, s, None) for s in segs]
    if reset:
        steps.append((RESET_S, None, 'stand_up'))
    return steps


class GesturePlayer:
    """Non-blocking playback of a gesture plan; call tick() every loop iteration."""

    def __init__(self, pub_att, pub_cmd):
        self.pub_att, self.pub_cmd = pub_att, pub_cmd
        self.name, self.steps, self.i, self.t0 = None, [], 0, 0.0

    @property
    def active(self):
        return self.name is not None

    def start(self, name, steps, now):
        self.name, self.steps, self.i = name, steps, -1
        self._next(now)

    def _next(self, now):
        prev = self.steps[self.i][1] if self.i >= 0 else None
        self.i += 1
        self.t0 = now
        if prev is not None and (self.i >= len(self.steps) or self.steps[self.i][1] is None):
            self.pub_att.publish(Twist())          # attitude segment over: zero rates
        if self.i >= len(self.steps):
            self.name = None
            return
        cmd = self.steps[self.i][2]
        if cmd:
            self.pub_cmd.publish(String(data=cmd))

    def tick(self, now):
        while self.active and now - self.t0 >= self.steps[self.i][0]:
            self._next(self.t0 + self.steps[self.i][0])
        if not self.active:
            return
        seg = self.steps[self.i][1]
        if seg is not None:
            r, p, y, h = seg.rates_at(now - self.t0)
            m = Twist()
            m.angular.x, m.angular.y, m.angular.z, m.linear.z = r, p, y, h
            self.pub_att.publish(m)

    def cancel(self):
        if self.active:
            self.pub_att.publish(Twist())
            self.name = None


def read_keys(fd):
    """Return a list of keys from whatever bytes are waiting (arrows = 3 bytes)."""
    data = os.read(fd, 64).decode(errors='ignore')
    keys, i = [], 0
    while i < len(data):
        if data[i] == '\x1b' and data[i + 1:i + 2] == '[' and i + 2 < len(data):
            keys.append(data[i:i + 3])
            i += 3
        else:
            keys.append(data[i])
            i += 1
    return keys


def main():
    ap = argparse.ArgumentParser(description='Keyboard teleop for the D1 bridge')
    ap.add_argument('--lin', type=float, default=0.2, help='initial linear speed m/s')
    ap.add_argument('--ang', type=float, default=0.4, help='initial angular speed rad/s')
    ap.add_argument('--hold', type=float, default=0.5,
                    help='seconds a keypress keeps the robot moving')
    ap.add_argument('--max-lin', type=float, default=1.0)
    ap.add_argument('--max-ang', type=float, default=1.5)
    ap.add_argument('--gesture-scale', type=float, default=1.0,
                    help='gesture size multiplier in (0,1], default 1.0')
    ap.add_argument('--no-reset', action='store_true',
                    help='skip the stand_up re-centre after each gesture')
    args, ros_args = ap.parse_known_args()
    if not 0.0 < args.gesture_scale <= 1.0:
        sys.exit('--gesture-scale must be in (0, 1]')
    validate_sequences()
    missing = {a[0] for st in SEQUENCES.values() for k, *a in st if k == 'sdk'} - set(SDK_TO_CMD)
    if missing:
        sys.exit(f'no robot_cmd mapping for SDK actions {sorted(missing)}')
    plans = {n: gesture_plan(n, args.gesture_scale, not args.no_reset)
             for n in GESTURE_KEYS.values()}    # validates every gesture up front

    if not sys.stdin.isatty():
        sys.exit('keyboard_teleop needs an interactive terminal (stdin is not a TTY)')

    rclpy.init(args=ros_args)
    node = rclpy.create_node('d1_keyboard_teleop')
    pub_vel = node.create_publisher(Twist, 'cmd_vel', 10)
    pub_cmd = node.create_publisher(String, 'robot_cmd', 10)
    pub_gait = node.create_publisher(String, 'gait', 10)
    pub_att = node.create_publisher(Twist, 'attitude_cmd', 10)
    player = GesturePlayer(pub_att, pub_cmd)
    mode = {'v': None}                  # last ctrl_mode from the bridge
    node.create_subscription(Int32, 'ctrl_mode', lambda m: mode.__setitem__('v', m.data), 10)

    lin, ang = args.lin, args.ang
    gait_i = 0
    cur = (0, 0, 0)          # active direction
    expire = 0.0
    zeros_left = 0
    msg_line = ''

    def status():
        g = f'gesture: {player.name}  ' if player.active else ''
        sys.stdout.write(f'\r\x1b[K[{GAITS[gait_i]}] lin={lin:.2f} m/s ang={ang:.2f} rad/s  {g}{msg_line}')
        sys.stdout.flush()

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    print(HELP.replace('\n', '\r\n'))
    try:
        tty.setcbreak(fd)
        status()
        while rclpy.ok():
            rclpy.spin_once(node, timeout_sec=0)
            # 50 Hz while a gesture streams attitude rates, else 20 Hz
            r, _, _ = select.select([fd], [], [], 0.02 if player.active else 0.05)
            now = time.monotonic()
            if r:
                for k in read_keys(fd):
                    kl = k.lower() if len(k) == 1 else k
                    if player.active:          # any key cancels a running gesture
                        player.cancel()
                        msg_line = 'gesture cancelled'
                    if kl in GESTURE_KEYS:
                        name = GESTURE_KEYS[kl]
                        if mode['v'] != MODE_STAND:
                            msg_line = (f'{name}: robot not standing (ctrl_mode={mode["v"]}); '
                                        f'press 1 first')
                        else:
                            cur, zeros_left = (0, 0, 0), 3
                            player.start(name, plans[name], now)
                            msg_line = ''
                    elif kl in MOVES:
                        if MOVES[kl] != cur:
                            msg_line = ''
                        cur, expire = MOVES[kl], now + args.hold
                    elif kl == ' ':
                        cur, zeros_left, msg_line = (0, 0, 0), 3, 'stop'
                    elif kl in CMDS:
                        cur, zeros_left = (0, 0, 0), 3
                        pub_cmd.publish(String(data=CMDS[kl]))
                        msg_line = CMDS[kl].upper()
                    elif kl == 'c':
                        gait_i = (gait_i + 1) % len(GAITS)
                        cur, zeros_left = (0, 0, 0), 3
                        pub_gait.publish(String(data=GAITS[gait_i]))
                    elif kl in ('+', '='):
                        lin, ang = min(lin + 0.05, args.max_lin), min(ang + 0.1, args.max_ang)
                    elif kl in ('-', '_'):
                        lin, ang = max(lin - 0.05, 0.05), max(ang - 0.1, 0.1)
                    elif kl == '\x03':
                        raise KeyboardInterrupt
                    status()
            was_active = player.active
            player.tick(now)
            if was_active and not player.active:
                status()
            msg = Twist()
            if cur != (0, 0, 0) and now < expire:
                msg.linear.x, msg.linear.y, msg.angular.z = cur[0] * lin, cur[1] * lin, cur[2] * ang
                pub_vel.publish(msg)
            else:
                if cur != (0, 0, 0):      # key released / timed out
                    cur, zeros_left = (0, 0, 0), 3
                if zeros_left:
                    pub_vel.publish(msg)
                    zeros_left -= 1
    except KeyboardInterrupt:
        pass
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
        try:
            pub_vel.publish(Twist())
            pub_att.publish(Twist())
        except Exception:
            pass
        print('\r\nstopped')
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
