"""Live demo: D1 camera stream + camera TFs + metric depth node + RViz (image, depth, point cloud in the floor frame).

Needs the bridge running (TF odom->base_link with the real body height):
  ros2 launch agibot_d1_bridge bridge.launch.py local_ip:=<pc> robot_ip:=<robot>
"""
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription
from launch.conditions import IfCondition
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution
from launch_ros.actions import Node
from launch_ros.substitutions import FindPackageShare


def generate_launch_description():
    args = [('url', 'rtsp://172.24.1.41:8554/test'), ('fps', '10.0'), ('rate', '5.0'), ('max_range', '3.0'),
            ('cam_x', '0.28'), ('cam_y', '0.0'), ('cam_z', '0.02'), ('cam_roll', '0.0'),
            ('cam_pitch', '-0.28'), ('cam_yaw', '0.0'), ('rviz', 'true')]
    la = [DeclareLaunchArgument(n, default_value=d) for n, d in args]
    c = LaunchConfiguration
    pkg = lambda name: FindPackageShare(name)  # noqa: E731

    camera = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(PathJoinSubstitution([pkg('d1_camera'), 'launch', 'camera.launch.py'])),
        launch_arguments={'url': c('url'), 'fps': c('fps'),
                          'camera_info_file': PathJoinSubstitution(
                              [pkg('d1_camera'), 'config', 'd1_camera_estimated.yaml'])}.items())
    depth = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(PathJoinSubstitution([pkg('d1_depth'), 'launch', 'depth.launch.py'])),
        launch_arguments={'rate': c('rate'), 'max_range': c('max_range')}.items())
    tf_mount = Node(
        package='tf2_ros', executable='static_transform_publisher', name='tf_base_to_camera',
        arguments=['--x', c('cam_x'), '--y', c('cam_y'), '--z', c('cam_z'), '--roll', c('cam_roll'),
                   '--pitch', c('cam_pitch'), '--yaw', c('cam_yaw'),
                   '--frame-id', 'base_link', '--child-frame-id', 'camera_link'])
    tf_optical = Node(
        package='tf2_ros', executable='static_transform_publisher', name='tf_camera_optical',
        arguments=['--roll', '-1.5707963', '--pitch', '0', '--yaw', '-1.5707963',
                   '--frame-id', 'camera_link', '--child-frame-id', 'camera_optical_frame'])
    rviz = Node(package='rviz2', executable='rviz2', name='rviz2', output='log',
                arguments=['-d', PathJoinSubstitution([pkg('d1_depth'), 'rviz', 'depth.rviz'])],
                condition=IfCondition(c('rviz')))
    return LaunchDescription(la + [camera, tf_mount, tf_optical, depth, rviz])
