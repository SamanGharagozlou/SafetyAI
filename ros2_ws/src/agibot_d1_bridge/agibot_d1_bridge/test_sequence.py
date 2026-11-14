"""cmd_vel test: forward 2s, stop 2s, reverse 2s, stop 2s, then same for yaw.

Publishes at 20 Hz so the bridge watchdog stays fed. Ctrl-C sends a stop.
The robot must already be standing:
  ros2 service call /d1_cmd_vel_bridge/stand_up std_srvs/srv/Trigger
"""
import argparse
import time

import rclpy
from geometry_msgs.msg import Twist


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--lin', type=float, default=0.3, help='linear speed m/s')
    ap.add_argument('--ang', type=float, default=0.5, help='angular speed rad/s')
    ap.add_argument('--dur', type=float, default=2.0, help='seconds per phase')
    ap.add_argument('--topic', default='cmd_vel')
    args = ap.parse_args()

    rclpy.init()
    node = rclpy.create_node('d1_cmd_vel_test')
    pub = node.create_publisher(Twist, args.topic, 10)

    def send(name, x=0.0, wz=0.0):
        print(f'{name:<14} x={x:+.2f} m/s  wz={wz:+.2f} rad/s', flush=True)
        msg = Twist()
        msg.linear.x, msg.angular.z = float(x), float(wz)
        end = time.monotonic() + args.dur
        while time.monotonic() < end:
            pub.publish(msg)
            time.sleep(0.05)

    try:
        time.sleep(0.5)  # let the subscriber match
        send('forward', x=args.lin)
        send('stop')
        send('reverse', x=-args.lin)
        send('stop')
        send('turn left', wz=args.ang)
        send('stop')
        send('turn right', wz=-args.ang)
        send('stop')
        print('done')
    except KeyboardInterrupt:
        pass
    finally:
        pub.publish(Twist())
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
