from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, SetEnvironmentVariable
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue


def generate_launch_description():
    return LaunchDescription([
        DeclareLaunchArgument('url', default_value='rtsp://172.24.1.41:8554/test'),
        DeclareLaunchArgument('fps', default_value='15.0', description='publish rate (Hz)'),
        DeclareLaunchArgument('scale', default_value='0.5', description='output size / 1920x1080'),
        DeclareLaunchArgument('stamp_offset_s', default_value='1.35',
                              description='video latency (s) subtracted from image stamps; measured ~1.35 s'),
        DeclareLaunchArgument('camera_info_file', default_value='',
                              description='ROS camera calibration YAML (else placeholder pinhole)'),
        # ~/.local NumPy 2 breaks ROS's NumPy-1-built OpenCV; the node also strips it itself.
        SetEnvironmentVariable('PYTHONNOUSERSITE', '1'),
        Node(
            package='d1_camera',
            executable='rtsp_camera',
            name='rtsp_camera',
            namespace='camera',
            output='screen',
            parameters=[{
                'url': LaunchConfiguration('url'),
                'fps': LaunchConfiguration('fps'),
                'scale': LaunchConfiguration('scale'),
                'camera_info_file': LaunchConfiguration('camera_info_file'),
                'stamp_offset_s': ParameterValue(LaunchConfiguration('stamp_offset_s'), value_type=float),
            }],
        ),
    ])
