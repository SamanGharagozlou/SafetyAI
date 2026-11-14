from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    return LaunchDescription([
        DeclareLaunchArgument('local_ip', default_value='192.168.234.100',
                              description='IP of this computer on the robot network'),
        DeclareLaunchArgument('robot_ip', default_value='192.168.234.1'),
        DeclareLaunchArgument('auto_stand', default_value='false'),
        Node(
            package='agibot_d1_bridge',
            executable='cmd_vel_bridge',
            name='d1_cmd_vel_bridge',
            output='screen',
            respawn=True,          # restart if the SDK link dies (see link_timeout)
            respawn_delay=2.0,
            parameters=[{
                'local_ip': LaunchConfiguration('local_ip'),
                'robot_ip': LaunchConfiguration('robot_ip'),
                'auto_stand': LaunchConfiguration('auto_stand'),
            }],
        ),
    ])
