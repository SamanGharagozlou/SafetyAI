from glob import glob

from setuptools import find_packages, setup

package_name = 'd1_follow'

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
    description='Lock onto one guest, publish a follow velocity, optionally relay it to /cmd_vel.',
    license='BSD-3-Clause',
    # colcon uses these to pick the pytest runner (otherwise it silently runs unittest).
    tests_require=['pytest'],
    extras_require={'test': ['pytest']},
    entry_points={
        'console_scripts': [
            'target_follower = d1_follow.target_follower_node:main',
            'follow_relay = d1_follow.follow_relay_node:main',
            'drive = d1_follow.follow_relay_node:drive_main',
            'lock = d1_follow.lock_cli:lock_main',
            'release = d1_follow.lock_cli:release_main',
        ],
    },
)
