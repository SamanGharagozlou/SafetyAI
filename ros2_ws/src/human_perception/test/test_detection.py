import numpy as np
import pytest

from fakes import FakeModel, FakeResult
from human_perception.detection import (COCO_PERSON_FALLBACK_ID, DetectionPipeline,
                                        extract_person_detections, InferenceError,
                                        PersonClassError, resolve_person_class_ids)

COCO_NAMES = {0: 'person', 1: 'bicycle', 2: 'car'}


def test_resolve_person_by_semantic_name():
    assert resolve_person_class_ids(COCO_NAMES) == ([0], False)
    assert resolve_person_class_ids({0: 'cat', 7: 'Person'}) == ([7], False)
    assert resolve_person_class_ids(['dog', 'person']) == ([1], False)


def test_resolve_falls_back_to_coco_zero_only_without_names():
    assert resolve_person_class_ids(None) == ([COCO_PERSON_FALLBACK_ID], True)
    assert resolve_person_class_ids({}) == ([0], True)


def test_resolve_rejects_models_without_person():
    with pytest.raises(PersonClassError):
        resolve_person_class_ids({0: 'cat', 1: 'dog'})


def test_filters_non_person_classes_and_low_confidence():
    result = FakeResult([
        (10, 10, 50, 100, 0.90, 0),   # person - keep
        (20, 20, 60, 90, 0.95, 2),    # car - drop
        (30, 30, 70, 80, 0.30, 0),    # person below threshold - drop
        (5, 5, 25, 45, 0.45, 0),      # exactly at threshold - keep
    ])
    dets = extract_person_detections(result, [0], 0.45)
    assert [round(d.score, 2) for d in dets] == [0.90, 0.45]
    assert all(d.class_index == 0 for d in dets)
    assert dets[0].center == (30.0, 55.0) and dets[0].size == (40.0, 90.0)


def test_empty_and_missing_results():
    assert extract_person_detections(FakeResult([]), [0], 0.45) == []
    assert extract_person_detections(None, [0], 0.45) == []


def test_boxes_are_clipped_and_degenerate_boxes_dropped():
    result = FakeResult([(-10, -5, 700, 500, 0.9, 0), (50, 50, 50, 80, 0.9, 0)])
    dets = extract_person_detections(result, [0], 0.45, image_shape=(480, 640))
    assert len(dets) == 1
    d = dets[0]
    assert (d.x1, d.y1, d.x2, d.y2) == (0.0, 0.0, 640.0, 480.0)


def test_nan_scores_dropped():
    assert extract_person_detections(FakeResult([(1, 1, 5, 5, np.nan, 0)]), [0], 0.1) == []


def test_pipeline_presence_and_count():
    frame = np.zeros((120, 160, 3), np.uint8)
    pipe = DetectionPipeline(FakeModel([(1, 1, 20, 40, 0.8, 0), (30, 1, 60, 40, 0.7, 0)]),
                             [0], 0.45)
    res = pipe.process(frame)
    assert res.present is True and res.count == 2
    assert (res.image_width, res.image_height) == (160, 120)
    assert res.inference_s >= 0.0

    empty = DetectionPipeline(FakeModel([]), [0], 0.45).process(frame)
    assert empty.present is False and empty.count == 0


def test_pipeline_wraps_model_exceptions():
    pipe = DetectionPipeline(FakeModel(raise_exc=RuntimeError('CUDA OOM')), [0], 0.45)
    with pytest.raises(InferenceError, match='CUDA OOM'):
        pipe.process(np.zeros((10, 10, 3), np.uint8))


@pytest.mark.parametrize('bad', [None, np.zeros((10, 10), np.uint8),
                                 np.zeros((10, 10, 3), np.float32),
                                 np.zeros((0, 10, 3), np.uint8)])
def test_pipeline_rejects_malformed_frames(bad):
    with pytest.raises(ValueError):
        DetectionPipeline(FakeModel(), [0], 0.45).process(bad)
