from setuptools import setup

package_name = 'd1_nav'

setup(
    name=package_name,
    version='0.1.0',
    packages=[package_name],
    data_files=[
        ('share/ament_index/resource_index/packages', ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        ('share/' + package_name + '/launch', ['launch/local_costmap.launch.py']),
        ('share/' + package_name + '/params', ['params/local_costmap.yaml']),
        ('share/' + package_name + '/rviz', ['rviz/costmap.rviz']),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
)
