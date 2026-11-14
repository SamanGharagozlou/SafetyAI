from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node


def generate_launch_description():
    args = [('host', '127.0.0.1'), ('port', '8765'), ('token', ''), ('places_file', ''), ('mock', 'false')]
    la = [DeclareLaunchArgument(n, default_value=d) for n, d in args]
    c = LaunchConfiguration
    node = Node(package='d1_gateway', executable='gateway', name='d1_gateway', output='screen',
                arguments=['--host', c('host'), '--port', c('port'), '--token', c('token'),
                           '--places', c('places_file'), '--mock', c('mock')])
    return LaunchDescription(la + [node])
