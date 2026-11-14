from setuptools import setup

package_name = 'agibot_d1_bridge'

setup(
    name=package_name,
    version='0.1.0',
    packages=[package_name],
    data_files=[
        ('share/ament_index/resource_index/packages', ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        ('share/' + package_name + '/launch', ['launch/bridge.launch.py']),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    entry_points={
        'console_scripts': [
            'cmd_vel_bridge = agibot_d1_bridge.cmd_vel_bridge:main',
            'test_sequence = agibot_d1_bridge.test_sequence:main',
            'keyboard_teleop = agibot_d1_bridge.keyboard_teleop:main',
        ],
    },
)
