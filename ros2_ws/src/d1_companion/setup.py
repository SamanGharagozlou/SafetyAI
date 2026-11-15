from setuptools import setup

package_name = 'd1_companion'

setup(
    name=package_name,
    version='0.1.0',
    packages=[package_name],
    data_files=[
        ('share/ament_index/resource_index/packages', ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
        ('share/' + package_name + '/launch', ['launch/companion.launch.py']),
    ],
    install_requires=['setuptools', 'websockets', 'pyyaml'],
    zip_safe=False,
    entry_points={'console_scripts': ['companion = d1_companion.server:main']},
)
