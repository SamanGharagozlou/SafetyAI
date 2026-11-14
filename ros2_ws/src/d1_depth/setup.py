from setuptools import setup

package_name = 'd1_depth'

setup(
    name=package_name,
    version='0.1.0',
    packages=[package_name],
    data_files=[
        ('share/ament_index/resource_index/packages', ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        ('share/' + package_name + '/launch', ['launch/depth.launch.py', 'launch/depth_demo.launch.py']),
        ('share/' + package_name + '/rviz', ['rviz/depth.rviz']),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    entry_points={'console_scripts': ['depth_node = d1_depth.depth_node:main']},
)
