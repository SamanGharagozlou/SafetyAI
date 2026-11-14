"""RGB-D RTAB-Map on the D1 using the *estimated* depth from d1_depth (/camera/depth) and the bridge odometry.

Outputs: /map (2D occupancy grid built from depth, heights above the floor frame), /mapPath, /mapGraph, /mapData
(3D coloured cloud), and the database at `database_path`.

Needs running: bridge (TF odom->base_link and odom->base_footprint), camera + depth node
(`ros2 launch d1_depth depth_demo.launch.py rviz:=false`).

Notes: the depth is only trustworthy within ~3 m (docs/costmap_plan.md), so all depth use is capped at 3 m.
`frame_id` is base_footprint (z = 0 on the floor) so that Grid/*Height thresholds are heights above the floor.
"""
import os

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, SetEnvironmentVariable
from launch.conditions import IfCondition, UnlessCondition
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue
from launch_ros.substitutions import FindPackageShare


def generate_launch_description():
    args = [('database_path', os.path.expanduser('~/d1_maps/d1_rgbd_map.db'), 'RTAB-Map database'),
            ('delete_db', 'true', 'start a new map'),
            ('rviz', 'true', 'open RViz'),
            ('max_depth', '3.0', 'ignore depth beyond this (m)'),
            ('linear_update', '0.1', 'add a node after this much translation (m)'),
            ('angular_update', '0.1', 'add a node after this much rotation (rad)')]
    la = [DeclareLaunchArgument(n, default_value=d, description=h) for n, d, h in args]
    c = LaunchConfiguration
    s = lambda name: ParameterValue(c(name), value_type=str)  # noqa: E731  (RTAB-Map params are strings)

    params = {
        'frame_id': 'base_footprint',
        'subscribe_depth': True,
        'subscribe_rgb': True,
        'subscribe_odom_info': False,
        'approx_sync': True,
        'qos_image': 2,
        'qos_camera_info': 2,
        'sync_queue_size': 20,
        'topic_queue_size': 20,
        'wait_for_transform': 0.5,
        'database_path': s('database_path'),
        # --- registration / loop closure (visual, with the estimated depth for 3D->2D PnP)
        'Reg/Strategy': '0',
        'Reg/Force3DoF': 'true',
        'Vis/EstimationType': '1',
        'Vis/MinInliers': '12',
        'Vis/MaxDepth': s('max_depth'),
        'Kp/MaxDepth': s('max_depth'),
        'Kp/MaxFeatures': '600',
        'Vis/MaxFeatures': '600',
        'Rtabmap/DetectionRate': '2',
        'RGBD/NeighborLinkRefining': 'false',
        'RGBD/LinearUpdate': s('linear_update'),
        'RGBD/AngularUpdate': s('angular_update'),
        'Optimizer/Slam2D': 'true',
        'Mem/IncrementalMemory': 'true',
        'Mem/InitWMWithAllNodes': 'false',
        # --- 2D occupancy grid from depth: heights are above the floor (frame_id = base_footprint)
        'Grid/Sensor': '1',                # 1 = depth (replaces Grid/FromDepth)
        'Grid/RangeMin': '0.4',
        'Grid/RangeMax': s('max_depth'),
        'Grid/CellSize': '0.05',
        'Grid/MaxGroundHeight': '0.08',
        'Grid/MaxObstacleHeight': '0.5',
        'Grid/NormalsSegmentation': 'false',
        'Grid/RayTracing': 'true',
        'Grid/DepthDecimation': '4',
    }
    remaps = [('rgb/image', '/camera/image_raw'), ('depth/image', '/camera/depth'),
              ('rgb/camera_info', '/camera/camera_info'), ('odom', '/odom')]

    new_map = Node(package='rtabmap_slam', executable='rtabmap', name='rtabmap', output='screen',
                   parameters=[params], remappings=remaps, arguments=['-d'], condition=IfCondition(c('delete_db')))
    keep_map = Node(package='rtabmap_slam', executable='rtabmap', name='rtabmap', output='screen',
                    parameters=[params], remappings=remaps, condition=UnlessCondition(c('delete_db')))
    rviz = Node(package='rviz2', executable='rviz2', name='rviz2_mapping', output='log',
                arguments=['-d', PathJoinSubstitution([FindPackageShare('d1_slam'), 'rviz', 'mapping.rviz'])],
                condition=IfCondition(c('rviz')))
    return LaunchDescription(la + [SetEnvironmentVariable('PYTHONNOUSERSITE', '1'), new_map, keep_map, rviz])
