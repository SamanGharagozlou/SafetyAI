"""Monocular (RGB-only) RTAB-Map on the AgiBot D1.

Inputs : /camera/image_raw + /camera/camera_info (d1_camera), /odom + TF odom->base_link (agibot_d1_bridge)
Output : RTAB-Map pose graph of image nodes with loop closures, database at `database_path`.

NOTE: RTAB-Map needs depth, stereo or LiDAR to build a *metric 3D/occupancy* map. With RGB only it can
still build a visual pose graph (place recognition, loop closures, relocalisation). To get a metric map,
feed an estimated depth image (e.g. a monocular depth network) as `depth_topic` and set rgb_only:=false.

The bridge must be running separately (it owns the robot link):
  ros2 run agibot_d1_bridge cmd_vel_bridge --ros-args -p local_ip:=<pc> -p robot_ip:=<robot>
"""
import os

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription, SetEnvironmentVariable
from launch.conditions import IfCondition, UnlessCondition
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue
from launch_ros.substitutions import FindPackageShare

ARGS = [
    ('use_camera', 'true', 'start the d1_camera driver'),
    ('url', 'rtsp://172.24.1.41:8554/test', 'camera source (RTSP URL or video file)'),
    ('fps', '10.0', 'camera publish rate'),
    ('scale', '0.5', 'image scale vs 1920x1080'),
    ('camera_info_file', PathJoinSubstitution([FindPackageShare('d1_camera'), 'config', 'd1_camera_estimated.yaml']),
     'ROS camera calibration YAML (default: estimate from the robot factory calibration)'),
    ('cam_x', '0.28', 'camera position in base_link (m): front face of the body mesh (x=+0.282); y,z are estimates'),
    ('cam_y', '0.0', ''),
    ('cam_z', '0.02', ''),
    ('cam_roll', '0.0', 'camera orientation in base_link (rad); +pitch = nose down'),
    ('cam_pitch', '-0.28', 'camera looks UP ~16 deg (horizon 210 px below image centre, f~735 px); +pitch = nose down'),
    ('cam_yaw', '0.0', ''),
    ('odom_topic', '/odom', ''),
    ('database_path', os.path.expanduser('~/.ros/d1_mono_rtabmap.db'), 'RTAB-Map database'),
    ('delete_db', 'true', 'start from an empty map'),
    ('localization', 'false', 'localise in an existing database instead of mapping'),
    ('linear_update', '0.1', 'add a node after this much translation (m)'),
    ('angular_update', '0.1', 'add a node after this much rotation (rad)'),
    ('rtabmap_viz', 'false', 'open the RTAB-Map GUI'),
    ('rviz', 'false', 'open RViz with the mapping layout'),
]


def generate_launch_description():
    la = [DeclareLaunchArgument(n, default_value=d, description=h) for n, d, h in ARGS]
    c = LaunchConfiguration

    camera = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(PathJoinSubstitution(
            [FindPackageShare('d1_camera'), 'launch', 'camera.launch.py'])),
        launch_arguments={'url': c('url'), 'fps': c('fps'), 'scale': c('scale'),
                          'camera_info_file': c('camera_info_file')}.items(),
        condition=IfCondition(c('use_camera')))

    # base_link -> camera_link (x forward) -> camera_optical_frame (z forward, ROS optical convention)
    tf_mount = Node(
        package='tf2_ros', executable='static_transform_publisher', name='tf_base_to_camera',
        arguments=['--x', c('cam_x'), '--y', c('cam_y'), '--z', c('cam_z'),
                   '--roll', c('cam_roll'), '--pitch', c('cam_pitch'), '--yaw', c('cam_yaw'),
                   '--frame-id', 'base_link', '--child-frame-id', 'camera_link'])
    tf_optical = Node(
        package='tf2_ros', executable='static_transform_publisher', name='tf_camera_optical',
        arguments=['--roll', '-1.5707963', '--pitch', '0', '--yaw', '-1.5707963',
                   '--frame-id', 'camera_link', '--child-frame-id', 'camera_optical_frame'])

    rtabmap_params = {
        'frame_id': 'base_link',
        'subscribe_depth': False,
        'subscribe_rgb': True,
        'subscribe_odom_info': False,
        'approx_sync': True,
        'qos_image': 2,          # 2 = best effort, matches the d1_camera publisher
        'qos_camera_info': 2,
        'sync_queue_size': 20,
        'topic_queue_size': 20,
        'wait_for_transform': 0.5,
        'database_path': ParameterValue(c('database_path'), value_type=str),
        # RTAB-Map parameters (strings)
        'Reg/Strategy': '0',                # visual registration
        'Reg/Force3DoF': 'true',            # ground robot: x, y, yaw
        'Vis/EstimationType': '2',          # 2D->2D epipolar (no depth available) - experimental
        'Vis/MinInliers': '15',
        'Kp/MaxFeatures': '600',
        'Vis/MaxFeatures': '600',
        'Rtabmap/DetectionRate': '2',
        'RGBD/NeighborLinkRefining': 'false',
        # RTAB-Map declares these as strings; stop launch from turning '0.1' into a float
        'RGBD/LinearUpdate': ParameterValue(c('linear_update'), value_type=str),
        'RGBD/AngularUpdate': ParameterValue(c('angular_update'), value_type=str),
        'Mem/IncrementalMemory': 'true',
        'Mem/InitWMWithAllNodes': 'false',
    }
    remaps = [('rgb/image', '/camera/image_raw'),
              ('rgb/camera_info', '/camera/camera_info'),
              ('odom', c('odom_topic')),
              # the bridge's /imu (frame imu_link has no TF) is not used here; without this RTAB-Map waits
              # wait_for_transform (0.5 s) for imu_link on every frame
              ('imu', '/rtabmap_imu_unused')]

    # `-d` deletes the database on start; run the other variant to keep and extend an existing map.
    rtabmap_new = Node(
        package='rtabmap_slam', executable='rtabmap', name='rtabmap', output='screen',
        parameters=[rtabmap_params], remappings=remaps,
        arguments=['-d'], condition=IfCondition(c('delete_db')))
    rtabmap_keep = Node(
        package='rtabmap_slam', executable='rtabmap', name='rtabmap', output='screen',
        parameters=[rtabmap_params], remappings=remaps,
        condition=UnlessCondition(c('delete_db')))

    viz = Node(
        package='rtabmap_viz', executable='rtabmap_viz', name='rtabmap_viz', output='screen',
        parameters=[{'frame_id': 'base_link', 'subscribe_depth': False, 'subscribe_rgb': True,
                     'subscribe_odom_info': False, 'approx_sync': True}],
        remappings=remaps, condition=IfCondition(c('rtabmap_viz')))

    rviz = Node(
        package='rviz2', executable='rviz2', name='rviz2', output='log',
        arguments=['-d', PathJoinSubstitution([FindPackageShare('d1_slam'), 'rviz', 'mono_slam.rviz'])],
        condition=IfCondition(c('rviz')))

    return LaunchDescription(la + [SetEnvironmentVariable('PYTHONNOUSERSITE', '1'),
                                   camera, tf_mount, tf_optical, rtabmap_new, rtabmap_keep, viz, rviz])
