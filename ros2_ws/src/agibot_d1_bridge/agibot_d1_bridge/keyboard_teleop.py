"""Keyboard teleop for the AgiBot D1 bridge (publishes cmd_vel, robot_cmd, gait).

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
from std_msgs.msg import String

HELP = __doc__.split('\n', 1)[1]
GAITS = ('move', 'crawl', 'climb')
# key -> (x, y, wz) direction
MOVES = {
    'w': (1, 0, 0), 's': (-1, 0, 0), 'a': (0, 0, 1), 'd': (0, 0, -1),
    'q': (0, 1, 0), 'e': (0, -1, 0),
    '\x1b[A': (1, 0, 0), '\x1b[B': (-1, 0, 0), '\x1b[D': (0, 0, 1), '\x1b[C': (0, 0, -1),
}
CMDS = {'1': 'stand_up', '2': 'lie_down', '3': 'shake_hand', 'x': 'damp'}


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
    args, ros_args = ap.parse_known_args()

    if not sys.stdin.isatty():
        sys.exit('keyboard_teleop needs an interactive terminal (stdin is not a TTY)')

    rclpy.init(args=ros_args)
    node = rclpy.create_node('d1_keyboard_teleop')
    pub_vel = node.create_publisher(Twist, 'cmd_vel', 10)
    pub_cmd = node.create_publisher(String, 'robot_cmd', 10)
    pub_gait = node.create_publisher(String, 'gait', 10)

    lin, ang = args.lin, args.ang
    gait_i = 0
    cur = (0, 0, 0)          # active direction
    expire = 0.0
    zeros_left = 0
    msg_line = ''

    def status():
        sys.stdout.write(f'\r\x1b[K[{GAITS[gait_i]}] lin={lin:.2f} m/s ang={ang:.2f} rad/s  {msg_line}')
        sys.stdout.flush()

    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    print(HELP.replace('\n', '\r\n'))
    try:
        tty.setcbreak(fd)
        status()
        while rclpy.ok():
            r, _, _ = select.select([fd], [], [], 0.05)
            now = time.monotonic()
            if r:
                for k in read_keys(fd):
                    kl = k.lower() if len(k) == 1 else k
                    if kl in MOVES:
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
        except Exception:
            pass
        print('\r\nstopped')
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == '__main__':
    main()
