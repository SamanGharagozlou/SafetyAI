"""Generate params/nav2_params.yaml for the D1 from the stock nav2_bringup defaults.

Run:  python3 tools/make_nav2_params.py
Keeps the stock BT plugin lists etc. and overrides what matters for this robot. Frames: map -> odom (RTAB-Map) ->
base_footprint (floor frame from the bridge). Obstacles: the monocular depth cloud /camera/points (<= 3 m, 10-50 cm high).
"""
import copy
import os

import yaml

stock = os.path.join(os.popen('ros2 pkg prefix nav2_bringup').read().strip(), 'share/nav2_bringup/params/nav2_params.yaml')
P = yaml.safe_load(open(stock))

# keep only the nodes we run (RTAB-Map provides localization and the map)
keep = ['bt_navigator', 'bt_navigator_navigate_through_poses_rclcpp_node', 'bt_navigator_navigate_to_pose_rclcpp_node',
        'controller_server', 'local_costmap', 'global_costmap', 'planner_server', 'smoother_server',
        'behavior_server', 'waypoint_follower', 'velocity_smoother']
P = {k: v for k, v in P.items() if k in keep}


def rp(node):
    return P[node]['ros__parameters']


def local(d):  # nested {name: {ros__parameters}} for costmaps
    return d


for n in P:
    if 'ros__parameters' in P[n]:
        P[n]['ros__parameters']['use_sim_time'] = False
P['local_costmap']['local_costmap']['ros__parameters']['use_sim_time'] = False
P['global_costmap']['global_costmap']['ros__parameters']['use_sim_time'] = False

BASE = 'base_footprint'
FOOTPRINT = '[[0.315, 0.18], [0.315, -0.18], [-0.315, -0.18], [-0.315, 0.18]]'
# --- speed limits: conservative, above the SDK minimums (vx 0.05, vy 0.1, wz 0.1 are the smallest non-zero commands)
VX, VXMIN, VY, WZ = 0.30, -0.15, 0.20, 0.50

# --- BT navigator
for n in ('bt_navigator',):
    rp(n).update({'global_frame': 'map', 'robot_base_frame': BASE, 'odom_topic': '/odom'})

# --- controller: MPPI, holonomic
c = rp('controller_server')
c.clear()
c.update({
    'use_sim_time': False,
    'controller_frequency': 10.0,
    'min_x_velocity_threshold': 0.05,
    'min_y_velocity_threshold': 0.10,
    'min_theta_velocity_threshold': 0.10,
    'failure_tolerance': 0.5,
    'progress_checker_plugin': 'progress_checker',
    'goal_checker_plugins': ['general_goal_checker'],
    'controller_plugins': ['FollowPath'],
    'progress_checker': {'plugin': 'nav2_controller::SimpleProgressChecker',
                         'required_movement_radius': 0.25, 'movement_time_allowance': 20.0},
    'general_goal_checker': {'stateful': True, 'plugin': 'nav2_controller::SimpleGoalChecker',
                             'xy_goal_tolerance': 0.25, 'yaw_goal_tolerance': 0.5},
    'FollowPath': {
        'plugin': 'nav2_mppi_controller::MPPIController',
        'time_steps': 40, 'model_dt': 0.1, 'batch_size': 1000, 'vx_std': 0.12, 'vy_std': 0.08, 'wz_std': 0.2,
        'vx_max': VX, 'vx_min': VXMIN, 'vy_max': VY, 'wz_max': WZ,
        'iteration_count': 1, 'prune_distance': 1.5, 'transform_tolerance': 0.5, 'temperature': 0.3, 'gamma': 0.015,
        'motion_model': 'Omni', 'visualize': False, 'reset_period': 1.0,
        'regenerate_noises': True,
        'TrajectoryVisualizer': {'trajectory_step': 5, 'time_step': 3},
        'AckermannConstraints': {'min_turning_r': 0.2},
        'critics': ['ConstraintCritic', 'CostCritic', 'GoalCritic', 'GoalAngleCritic', 'PathAlignCritic',
                    'PathFollowCritic', 'PathAngleCritic', 'PreferForwardCritic'],
        'ConstraintCritic': {'enabled': True, 'cost_power': 1, 'cost_weight': 4.0},
        'GoalCritic': {'enabled': True, 'cost_power': 1, 'cost_weight': 5.0, 'threshold_to_consider': 1.0},
        'GoalAngleCritic': {'enabled': True, 'cost_power': 1, 'cost_weight': 3.0, 'threshold_to_consider': 0.5},
        'PreferForwardCritic': {'enabled': True, 'cost_power': 1, 'cost_weight': 5.0, 'threshold_to_consider': 0.5},
        'CostCritic': {'enabled': True, 'cost_power': 1, 'cost_weight': 3.81, 'critical_cost': 300.0,
                       'consider_footprint': True, 'collision_cost': 1000000.0, 'near_goal_distance': 0.8,
                       'trajectory_point_step': 2},
        'PathAlignCritic': {'enabled': True, 'cost_power': 1, 'cost_weight': 14.0, 'max_path_occupancy_ratio': 0.05,
                            'trajectory_point_step': 4, 'threshold_to_consider': 0.5, 'offset_from_furthest': 20,
                            'use_path_orientations': False},
        'PathFollowCritic': {'enabled': True, 'cost_power': 1, 'cost_weight': 5.0, 'offset_from_furthest': 5,
                             'threshold_to_consider': 1.0},
        'PathAngleCritic': {'enabled': True, 'cost_power': 1, 'cost_weight': 2.0, 'offset_from_furthest': 4,
                            'threshold_to_consider': 0.5, 'max_angle_to_furthest': 1.0, 'mode': 0},
    },
})

# --- costmaps
VOXEL = {
    'plugin': 'nav2_costmap_2d::VoxelLayer', 'enabled': True, 'publish_voxel_map': False,
    'origin_z': 0.0, 'z_resolution': 0.05, 'z_voxels': 16, 'max_obstacle_height': 0.6, 'mark_threshold': 0,
    'observation_sources': 'depth_cloud',
    'depth_cloud': {'topic': '/camera/points', 'data_type': 'PointCloud2', 'min_obstacle_height': 0.10,
                    'max_obstacle_height': 0.5, 'obstacle_min_range': 0.2, 'obstacle_max_range': 3.0,
                    'raytrace_min_range': 0.0, 'raytrace_max_range': 3.0, 'clearing': True, 'marking': True},
}
INFL = {'plugin': 'nav2_costmap_2d::InflationLayer', 'cost_scaling_factor': 3.0, 'inflation_radius': 0.45}
lc = P['local_costmap']['local_costmap']['ros__parameters']
lc.clear()
lc.update({'use_sim_time': False, 'update_frequency': 5.0, 'publish_frequency': 2.0, 'global_frame': 'odom',
           'robot_base_frame': BASE, 'transform_tolerance': 0.5, 'rolling_window': True, 'width': 4, 'height': 4,
           'resolution': 0.05, 'footprint': FOOTPRINT, 'always_send_full_costmap': True,
           'plugins': ['voxel_layer', 'inflation_layer'], 'voxel_layer': copy.deepcopy(VOXEL),
           'inflation_layer': copy.deepcopy(INFL)})
gc = P['global_costmap']['global_costmap']['ros__parameters']
gc.clear()
gc.update({'use_sim_time': False, 'update_frequency': 1.0, 'publish_frequency': 1.0, 'global_frame': 'map',
           'robot_base_frame': BASE, 'transform_tolerance': 0.5, 'resolution': 0.05, 'footprint': FOOTPRINT,
           'track_unknown_space': True, 'always_send_full_costmap': True,
           'plugins': ['static_layer', 'voxel_layer', 'inflation_layer'],
           'static_layer': {'plugin': 'nav2_costmap_2d::StaticLayer', 'map_subscribe_transient_local': True,
                            'subscribe_to_updates': True},
           'voxel_layer': copy.deepcopy(VOXEL), 'inflation_layer': copy.deepcopy(INFL)})

# --- planner / smoother / behaviours
pl = rp('planner_server')
pl.update({'use_sim_time': False, 'expected_planner_frequency': 1.0, 'planner_plugins': ['GridBased'],
           'GridBased': {'plugin': 'nav2_navfn_planner/NavfnPlanner', 'tolerance': 0.5, 'use_astar': True,
                         'allow_unknown': True}})
bh = rp('behavior_server')
bh.update({'use_sim_time': False, 'local_frame': 'odom', 'global_frame': 'map', 'robot_base_frame': BASE,
           'transform_tolerance': 0.5, 'max_rotational_vel': WZ, 'min_rotational_vel': 0.1, 'rotational_acc_lim': 1.0,
           'simulate_ahead_time': 2.0})
rp('smoother_server')['use_sim_time'] = False

# --- velocity smoother: output /cmd_vel for the bridge
vs = rp('velocity_smoother')
vs.update({'use_sim_time': False, 'smoothing_frequency': 20.0, 'scale_velocities': False,
           'feedback': 'OPEN_LOOP', 'max_velocity': [VX, VY, WZ], 'min_velocity': [VXMIN, -VY, -WZ],
           'max_accel': [0.4, 0.4, 0.8], 'max_decel': [-0.6, -0.6, -1.2], 'odom_topic': '/odom',
           'odom_duration': 0.1, 'deadband_velocity': [0.0, 0.0, 0.0], 'velocity_timeout': 1.0})
rp('waypoint_follower')['use_sim_time'] = False

out = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'params', 'nav2_params.yaml')
with open(out, 'w') as f:
    f.write('# GENERATED by tools/make_nav2_params.py from the stock nav2_bringup params. Edit the script, not this file.\n')
    yaml.safe_dump(P, f, default_flow_style=False, sort_keys=False)
print('wrote', out)
