from setuptools import setup

package_name = 'd1_slam'

setup(
    name=package_name,
    version='0.1.0',
    packages=[package_name],
    data_files=[
        ('share/ament_index/resource_index/packages', ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        ('share/' + package_name + '/launch', ['launch/mono_rtabmap.launch.py', 'launch/rgbd_rtabmap.launch.py']),
        ('share/' + package_name + '/rviz', ['rviz/mono_slam.rviz', 'rviz/mapping.rviz']),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
)
