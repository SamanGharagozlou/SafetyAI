from setuptools import setup

package_name = 'd1_lidar'

setup(
    name=package_name,
    version='0.1.0',
    packages=[package_name],
    data_files=[
        ('share/ament_index/resource_index/packages', ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
    ],
    install_requires=['setuptools'],
    zip_safe=False,
    entry_points={'console_scripts': [
        'lidar_localizer = d1_lidar.lidar_localizer:main',
        'metacam_relay = d1_lidar.metacam_relay:main',
    ]},
)
