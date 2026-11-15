"""Replay helper: re-publish a recorded /tf_rec on /tf WITHOUT the recorded map->odom (the recorded RTAB-Map run published its own;
the replayed RTAB-Map must publish its own). Run in the same private ROS domain as the bag play."""
import rclpy
from rclpy.node import Node
from tf2_msgs.msg import TFMessage


class Relay(Node):
    def __init__(self):
        super().__init__('lc_tf_relay')
        self.pub = self.create_publisher(TFMessage, '/tf', 100)
        self.create_subscription(TFMessage, '/tf_rec', self.cb, 100)

    def cb(self, m):
        keep = [t for t in m.transforms if t.header.frame_id != 'map']
        if keep:
            self.pub.publish(TFMessage(transforms=keep))


def main():
    rclpy.init()
    rclpy.spin(Relay())


if __name__ == '__main__':
    main()
