import os

from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, SetEnvironmentVariable
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue


def generate_launch_description():
    args = [('rate', '5.0', 'max inference rate (Hz)'),
            ('max_range', '3.0', 'drop points farther than this (m); the model is unreliable beyond ~3 m on this low camera'),
            ('downsample', '4', 'point cloud pixel stride'),
            ('model', 'depth-anything/Depth-Anything-V2-Metric-Indoor-Small-hf', 'HF model id'),
            ('venv', os.environ.get('D1_DEPTH_VENV', '~/d1_depth_venv'), 'virtualenv that has torch + transformers')]
    la = [DeclareLaunchArgument(n, default_value=d, description=h) for n, d, h in args]
    c = LaunchConfiguration
    node = Node(
        package='d1_depth', executable='depth_node', name='depth_node', namespace='camera',
        output='screen',
        parameters=[{'rate': ParameterValue(c('rate'), value_type=float),
                     'max_range': ParameterValue(c('max_range'), value_type=float),
                     'downsample': ParameterValue(c('downsample'), value_type=int),
                     'model': ParameterValue(c('model'), value_type=str)}])
    return LaunchDescription(la + [SetEnvironmentVariable('PYTHONNOUSERSITE', '1'),
                                   SetEnvironmentVariable('D1_DEPTH_VENV', c('venv')), node])
