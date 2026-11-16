"""CLI: ``ros2 run d1_follow lock`` / ``release`` call the target_follower services."""

import sys

import rclpy
from std_srvs.srv import Trigger

SERVICE_WAIT_S = 3.0
CALL_TIMEOUT_S = 10.0  # lock waits up to lock_timeout_s + 1 on the node side


def _call(service: str) -> int:
    rclpy.init()
    node = rclpy.create_node('d1_follow_cli')
    try:
        client = node.create_client(Trigger, service)
        if not client.wait_for_service(timeout_sec=SERVICE_WAIT_S):
            print(f'{service} not available (is target_follower running?)', file=sys.stderr)
            return 1
        future = client.call_async(Trigger.Request())
        rclpy.spin_until_future_complete(node, future, timeout_sec=CALL_TIMEOUT_S)
        if not future.done():
            print(f'{service}: no response', file=sys.stderr)
            return 1
        res = future.result()
        print(res.message)
        return 0 if res.success else 1
    finally:
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


def lock_main() -> int:
    return _call('/d1_follow/lock')


def release_main() -> int:
    return _call('/d1_follow/release')


if __name__ == '__main__':
    sys.exit(lock_main())
