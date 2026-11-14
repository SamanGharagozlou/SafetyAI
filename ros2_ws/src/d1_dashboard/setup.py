from setuptools import setup

package_name = 'd1_dashboard'

setup(
    name=package_name,
    version='0.1.0',
    packages=[package_name],
    package_data={package_name: ['static/*']},
    include_package_data=True,
    data_files=[
        ('share/ament_index/resource_index/packages', ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        ('share/' + package_name + '/launch', ['launch/dashboard.launch.py']),
    ],
    install_requires=['setuptools'],
    zip_safe=False,
    entry_points={'console_scripts': ['dashboard = d1_dashboard.server:main']},
)
