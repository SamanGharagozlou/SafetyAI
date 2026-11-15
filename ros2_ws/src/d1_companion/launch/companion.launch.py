from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    args = [('places', ''), ('gateway', 'ws://127.0.0.1:8765'), ('host', '127.0.0.1'), ('port', '8780'),
            ('join_token', ''), ('admin_token', ''), ('robot_speech', 'false')]
    la = [DeclareLaunchArgument(n, default_value=d) for n, d in args]
    c = LaunchConfiguration
    node = Node(package='d1_companion', executable='companion', name='d1_companion', output='screen',
                arguments=['--places', c('places'), '--gateway', c('gateway'), '--host', c('host'), '--port', c('port'),
                           '--join-token', c('join_token'), '--admin-token', c('admin_token'), '--robot-speech', c('robot_speech')])
    return LaunchDescription(la + [node])
