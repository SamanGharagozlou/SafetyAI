#!/usr/bin/env python3
"""Relay live MetaCam Lite topics (ROS 1, via rosbridge) into this laptop's ROS 2 graph for RViz.

  source /opt/ros/jazzy/setup.bash
  python3 tools/metacam_live_relay.py [ws://10.42.1.1:9089]

Republishes (read-only on the device):
  /mapping/odom       -> /metacam/odom   (nav_msgs/Odometry, frame mapping_init)
  /colorizer/cloud    -> /metacam/cloud  (sensor_msgs/PointCloud2, frame mapping_init)
Publishes a static transform map -> mapping_init (identity). The map origin is the scan start,
which is also the MetaCam's mapping_init origin (see map2d/README.md).

Stamps are set to the laptop clock: the device clock is wrong (2026-02), which RViz TF would reject.
"""
import base64
import json
import sys
import threading

import rclpy
from geometry_msgs.msg import TransformStamped
from nav_msgs.msg import Odometry
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy
from sensor_msgs.msg import PointCloud2, PointField
from tf2_ros import StaticTransformBroadcaster
import websocket

WS_URL = sys.argv[1] if len(sys.argv) > 1 else "ws://10.42.1.1:9089"
FRAME = "mapping_init"


def stamp_now(node):
    return node.get_clock().now().to_msg()


class Relay(Node):
    def __init__(self):
        super().__init__("metacam_live_relay")
        qos = QoSProfile(reliability=ReliabilityPolicy.BEST_EFFORT,
                         history=HistoryPolicy.KEEP_LAST, depth=5)
        self.pub_odom = self.create_publisher(Odometry, "/metacam/odom", qos)
        self.pub_cloud = self.create_publisher(PointCloud2, "/metacam/cloud", qos)
        self.tf_static = StaticTransformBroadcaster(self)
        t = TransformStamped()
        t.header.stamp = stamp_now(self)
        t.header.frame_id = "map"
        t.child_frame_id = FRAME
        t.transform.rotation.w = 1.0
        self.tf_static.sendTransform(t)
        self.get_logger().info(f"relaying {WS_URL} -> /metacam/odom, /metacam/cloud")

    def on_odom(self, m):
        o = Odometry()
        o.header.stamp = stamp_now(self)
        o.header.frame_id = FRAME
        o.child_frame_id = m["child_frame_id"] or "body"
        o.pose.pose.position.x = m["pose"]["pose"]["position"]["x"]
        o.pose.pose.position.y = m["pose"]["pose"]["position"]["y"]
        o.pose.pose.position.z = m["pose"]["pose"]["position"]["z"]
        q = m["pose"]["pose"]["orientation"]
        o.pose.pose.orientation.x, o.pose.pose.orientation.y = q["x"], q["y"]
        o.pose.pose.orientation.z, o.pose.pose.orientation.w = q["z"], q["w"]
        o.pose.covariance = m["pose"]["covariance"]
        self.pub_odom.publish(o)

    def on_cloud(self, m):
        c = PointCloud2()
        c.header.stamp = stamp_now(self)
        c.header.frame_id = FRAME
        c.height, c.width = m["height"], m["width"]
        c.fields = [PointField(name=f["name"], offset=f["offset"],
                               datatype=f["datatype"], count=f["count"]) for f in m["fields"]]
        c.is_bigendian = m["is_bigendian"]
        c.point_step, c.row_step = m["point_step"], m["row_step"]
        c.data = base64.b64decode(m["data"])
        c.is_dense = m["is_dense"]
        self.pub_cloud.publish(c)


def ws_loop(node):
    ws = websocket.create_connection(WS_URL, timeout=10)
    # throttle the cloud to 5 Hz so RViz stays smooth; odom is small so it runs at full rate
    ws.send(json.dumps({"op": "subscribe", "topic": "/mapping/odom",
                        "type": "nav_msgs/Odometry", "queue_length": 5}))
    ws.send(json.dumps({"op": "subscribe", "topic": "/colorizer/cloud",
                        "type": "sensor_msgs/PointCloud2", "throttle_rate": 200, "queue_length": 1}))
    while rclpy.ok():
        try:
            m = json.loads(ws.recv())
        except websocket.WebSocketTimeoutException:
            continue
        except Exception as e:  # connection dropped
            node.get_logger().error(f"websocket closed: {e}")
            break
        if m.get("op") != "publish":
            continue
        if m.get("topic") == "/mapping/odom":
            node.on_odom(m["msg"])
        elif m.get("topic") == "/colorizer/cloud":
            node.on_cloud(m["msg"])
    ws.close()


def main():
    rclpy.init()
    node = Relay()
    threading.Thread(target=ws_loop, args=(node,), daemon=True).start()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
