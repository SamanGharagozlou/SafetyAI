"""RTAB-Map (RGB-D, recorded depth) for replaying a bag with loop-closure settings overridden.
  ros2 launch lc_replay.launch.py db:=/root/d1_maps/eval/v1.db overrides:='{"Vis/MinInliers":"6"}'"""
import json

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, OpaqueFunction
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node

BASE = {
    'Reg/Strategy': '0', 'Reg/Force3DoF': 'true', 'Vis/EstimationType': '1', 'Vis/MinInliers': '12', 'Vis/MaxDepth': '4.0',
    'Kp/MaxDepth': '4.0', 'Kp/MaxFeatures': '600', 'Vis/MaxFeatures': '600', 'Rtabmap/DetectionRate': '2',
    'RGBD/NeighborLinkRefining': 'false', 'RGBD/LinearUpdate': '0.1', 'RGBD/AngularUpdate': '0.1', 'Optimizer/Slam2D': 'true',
    'Mem/IncrementalMemory': 'true', 'Mem/InitWMWithAllNodes': 'false',
    'Grid/Sensor': '1', 'Grid/RangeMin': '0.4', 'Grid/RangeMax': '4.0', 'Grid/CellSize': '0.05', 'Grid/MaxGroundHeight': '0.08',
    'Grid/MaxObstacleHeight': '0.5', 'Grid/NormalsSegmentation': 'false', 'Grid/RayTracing': 'true', 'Grid/DepthDecimation': '4',
}


def _setup(context):
    ov = json.loads(LaunchConfiguration('overrides').perform(context) or '{}')
    p = {**BASE, **ov}
    params = {'frame_id': 'base_footprint', 'subscribe_depth': True, 'subscribe_rgb': True, 'subscribe_odom_info': False,
              'approx_sync': True, 'qos_image': 2, 'qos_camera_info': 2, 'sync_queue_size': 40, 'topic_queue_size': 40,
              'wait_for_transform': 0.5, 'database_path': LaunchConfiguration('db').perform(context), **p}
    return [Node(package='rtabmap_slam', executable='rtabmap', name='rtabmap', output='screen', parameters=[params],
                 remappings=[('rgb/image', '/camera/image_raw'), ('depth/image', '/camera/depth'),
                             ('rgb/camera_info', '/camera/camera_info'), ('odom', '/odom'), ('imu', '/unused_imu')],
                 arguments=['-d'])]


def generate_launch_description():
    return LaunchDescription([DeclareLaunchArgument('db', default_value='/tmp/lc.db'),
                              DeclareLaunchArgument('overrides', default_value='{}'), OpaqueFunction(function=_setup)])
