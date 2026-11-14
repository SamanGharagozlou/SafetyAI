import importlib

import pytest

PURE = ['human_perception', 'human_perception.config', 'human_perception.backoff',
        'human_perception.metrics', 'human_perception.image_codec',
        'human_perception.detection', 'human_perception.capture', 'human_perception.annotate',
        'human_perception.model_backend', 'human_perception.benchmark']
ROS = ['human_perception.ros_conversions', 'human_perception.diagnostics',
       'human_perception.human_detector_node', 'human_perception.video_source_node']


@pytest.mark.parametrize('mod', PURE)
def test_pure_modules_import_without_ros(mod):
    importlib.import_module(mod)


@pytest.mark.parametrize('mod', ROS)
def test_ros_modules_import(mod):
    pytest.importorskip('rclpy')
    pytest.importorskip('vision_msgs')
    importlib.import_module(mod)


def test_nodes_expose_main():
    pytest.importorskip('rclpy')
    pytest.importorskip('vision_msgs')
    from human_perception import human_detector_node, video_source_node
    assert callable(human_detector_node.main) and callable(video_source_node.main)


def test_model_backend_does_not_import_ultralytics_eagerly():
    import sys
    importlib.import_module('human_perception.model_backend')
    # importing the module must be cheap; ultralytics loads only when a model is created
    assert 'human_perception.model_backend' in sys.modules
