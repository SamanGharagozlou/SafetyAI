from setuptools import setup

package_name = 'd1_localization'

setup(
    name=package_name,
    version='0.1.0',
    packages=[package_name],
    data_files=[
        ('share/ament_index/resource_index/packages', ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        ('share/' + package_name + '/launch', ['launch/localize.launch.py']),
        ('share/' + package_name + '/config', ['config/ekf.yaml', 'config/view.rviz']),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    entry_points={
        'console_scripts': [
            'initial_pose_localizer = d1_localization.initial_pose_localizer:main',
        ],
    },
)
