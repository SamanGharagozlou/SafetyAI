"""twist_mux in front of the SafetyAI team's agibot_d1_bridge: one /cmd_vel, sources ranked by config/twist_mux.yaml
(our safety edge highest, keyboard teleop lowest). ROS 2 Humble; needs `apt install ros-humble-twist-mux`.

    ros2 launch launch/twist_mux.launch.py                 # then start the edge with D1W_CMD_VEL=/cmd_vel_edge
    ros2 launch launch/twist_mux.launch.py config:=/path/to/other.yaml

Remap the other sources onto their mux inputs, e.g.
    ros2 run agibot_d1_bridge keyboard_teleop --ros-args -r cmd_vel:=/cmd_vel_teleop
"""

from pathlib import Path

from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node

from launch import LaunchDescription

DEFAULT = str(Path(__file__).resolve().parent.parent / "config" / "twist_mux.yaml")


def generate_launch_description():
    return LaunchDescription(
        [
            DeclareLaunchArgument("config", default_value=DEFAULT, description="twist_mux topics / locks YAML"),
            DeclareLaunchArgument("cmd_vel_out", default_value="/cmd_vel", description="what the bridge listens to"),
            Node(
                package="twist_mux",
                executable="twist_mux",
                name="twist_mux",
                output="screen",
                parameters=[LaunchConfiguration("config")],
                remappings=[("cmd_vel_out", LaunchConfiguration("cmd_vel_out"))],
            ),
        ]
    )
