from glob import glob

from setuptools import find_packages, setup

package_name = 'human_perception'

setup(
    name=package_name,
    version='0.1.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages', ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        ('share/' + package_name + '/launch', glob('launch/*.launch.py')),
        ('share/' + package_name + '/config', glob('config/*.yaml')),
        ('share/' + package_name + '/rviz', glob('rviz/*.rviz')),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='vivek',
    maintainer_email='jayaprakashnis619@gmail.com',
    description='Source-agnostic 2D human detection for ROS 2 with Ultralytics YOLO26.',
    license='AGPL-3.0-only',
    # colcon uses these to pick the pytest runner (otherwise it silently runs unittest).
    tests_require=['pytest'],
    extras_require={'test': ['pytest']},
    entry_points={
        'console_scripts': [
            'video_source = human_perception.video_source_node:main',
            'human_detector = human_perception.human_detector_node:main',
            'human_perception_benchmark = human_perception.benchmark:main',
        ],
    },
)
