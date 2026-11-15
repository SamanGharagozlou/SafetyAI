"""Relay the MetaCam Lite's live topics (ROS 1, via rosbridge websocket) into ROS 2. Read-only on the device.

Based on tools/metacam_live_relay.py, with differences: it does NOT publish a static map->mapping_init transform (the lidar
localizer owns the lidar<->map alignment), reconnects when the link drops, and the cloud is optional.

  python3 metacam_relay.py [--url ws://10.42.1.1:9089] [--cloud true]      (needs python3-websocket)
  ros2 run d1_lidar metacam_relay --url ws://10.42.1.1:9089

  /mapping/odom       -> /metacam/odom   nav_msgs/Odometry,  frame mapping_init, child body, ~10 Hz
  /colorizer/cloud    -> /metacam/cloud  sensor_msgs/PointCloud2, throttled to 5 Hz

Stamps are the receive time on this computer: the device clock is wrong (2026-02).
"""
import argparse
import base64
import json
import threading
import time

import rclpy
from nav_msgs.msg import Odometry
from rclpy.node import Node
from rclpy.qos import HistoryPolicy, QoSProfile, ReliabilityPolicy
from sensor_msgs.msg import PointCloud2, PointField

FRAME = 'mapping_init'


class Relay(Node):
    def __init__(self, url, cloud):
        super().__init__('metacam_relay')
        self.url, self.cloud = url, cloud
        qos = QoSProfile(reliability=ReliabilityPolicy.BEST_EFFORT, history=HistoryPolicy.KEEP_LAST, depth=5)
        self.pub_odom = self.create_publisher(Odometry, '/metacam/odom', qos)
        self.pub_cloud = self.create_publisher(PointCloud2, '/metacam/cloud', qos) if cloud else None
        self.n_odom = 0
        self.connected = False
        threading.Thread(target=self.loop, daemon=True).start()
        self.create_timer(5.0, lambda: self.get_logger().info(
            f'{"connected" if self.connected else "NOT connected"} to {self.url}; odom samples so far: {self.n_odom}'))

    def on_odom(self, m):
        o = Odometry()
        o.header.stamp, o.header.frame_id = self.get_clock().now().to_msg(), FRAME
        o.child_frame_id = m.get('child_frame_id') or 'body'
        pp, q = m['pose']['pose']['position'], m['pose']['pose']['orientation']
        o.pose.pose.position.x, o.pose.pose.position.y, o.pose.pose.position.z = pp['x'], pp['y'], pp['z']
        o.pose.pose.orientation.x, o.pose.pose.orientation.y, o.pose.pose.orientation.z, o.pose.pose.orientation.w = q['x'], q['y'], q['z'], q['w']
        o.pose.covariance = m['pose']['covariance']
        self.pub_odom.publish(o)
        self.n_odom += 1

    def on_cloud(self, m):
        c = PointCloud2()
        c.header.stamp, c.header.frame_id = self.get_clock().now().to_msg(), FRAME
        c.height, c.width = m['height'], m['width']
        c.fields = [PointField(name=f['name'], offset=f['offset'], datatype=f['datatype'], count=f['count']) for f in m['fields']]
        c.is_bigendian, c.point_step, c.row_step, c.is_dense = m['is_bigendian'], m['point_step'], m['row_step'], m['is_dense']
        c.data = base64.b64decode(m['data'])
        self.pub_cloud.publish(c)

    def loop(self):
        import websocket
        while rclpy.ok():
            try:
                ws = websocket.create_connection(self.url, timeout=5)
                ws.send(json.dumps({'op': 'subscribe', 'topic': '/mapping/odom', 'type': 'nav_msgs/Odometry', 'queue_length': 5}))
                if self.cloud:
                    ws.send(json.dumps({'op': 'subscribe', 'topic': '/colorizer/cloud', 'type': 'sensor_msgs/PointCloud2',
                                        'throttle_rate': 200, 'queue_length': 1}))
                self.connected = True
                self.get_logger().info(f'connected to {self.url}')
                while rclpy.ok():
                    try:
                        m = json.loads(ws.recv())
                    except websocket.WebSocketTimeoutException:
                        continue
                    if m.get('op') != 'publish':
                        continue
                    if m.get('topic') == '/mapping/odom':
                        self.on_odom(m['msg'])
                    elif m.get('topic') == '/colorizer/cloud' and self.pub_cloud is not None:
                        self.on_cloud(m['msg'])
            except Exception as e:  # noqa: BLE001
                self.get_logger().warn(f'lidar link: {e}; retrying in 2 s', throttle_duration_sec=10.0)
            self.connected = False
            time.sleep(2.0)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--url', default='ws://10.42.1.1:9089')
    ap.add_argument('--cloud', default='true')
    a, ros_args = ap.parse_known_args()
    rclpy.init(args=ros_args)
    n = Relay(a.url, str(a.cloud).lower() in ('1', 'true', 'yes'))
    try:
        rclpy.spin(n)
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
