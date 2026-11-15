"""Robot gateway: ROS 2 <-> WebSocket. Frontends (ClawMetry, dashboards, apps) talk JSON over one WebSocket and never
see ROS.   ros2 run d1_gateway gateway --host 127.0.0.1 --port 8765 [--token T] [--places places.yaml] [--mock true]"""
import argparse
import asyncio
import logging
import threading

from d1_gateway.core import GatewayCore


def load_places(path):
    if not path:
        return {}
    import yaml
    with open(path) as f:
        return yaml.safe_load(f) or {}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--host', default='127.0.0.1')
    ap.add_argument('--port', type=int, default=8765)
    ap.add_argument('--token', default='')
    ap.add_argument('--places', default='')
    ap.add_argument('--mock', default='false')
    a, ros_args = ap.parse_known_args(argv)
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(name)s %(message)s')
    mock = str(a.mock).lower() in ('1', 'true', 'yes')
    places = load_places(a.places)
    if mock:
        from d1_gateway.mock_robot import MockRobot
        robot, spin = MockRobot(), None
    else:
        import rclpy
        from rclpy.executors import MultiThreadedExecutor
        from d1_gateway.ros_robot import RosRobot
        rclpy.init(args=ros_args)
        robot = RosRobot()
        ex = MultiThreadedExecutor(num_threads=2)
        ex.add_node(robot)
        spin = threading.Thread(target=ex.spin, daemon=True)
        spin.start()
    core = GatewayCore(robot, places, token=a.token, host=a.host, port=a.port)

    async def run():
        await core.serve()
        await asyncio.Event().wait()
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass
    finally:
        if not mock:
            import rclpy
            if rclpy.ok():
                rclpy.shutdown()


if __name__ == '__main__':
    main()
