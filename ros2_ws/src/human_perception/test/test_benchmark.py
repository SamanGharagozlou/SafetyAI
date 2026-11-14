import json

from human_perception import benchmark


def test_synthetic_capture_shape():
    frames = benchmark.capture_frames('synthetic', 3, width=64, height=48)
    assert frames.shape == (3, 48, 64, 3)


def _r(model, fps, mean, p95):
    return {'model': model, 'end_to_end_fps': fps, 'inference_mean_ms': mean,
            'inference_p95_ms': p95}


def test_recommendation_prefers_n_when_s_misses_target():
    text = benchmark.recommend([_r('yolo26n.pt', 25, 30, 35), _r('yolo26s.pt', 8, 90, 110)], 15)
    assert 'prefer yolo26n' in text
    assert 'universally' not in text


def test_recommendation_allows_s_when_target_met():
    text = benchmark.recommend([_r('yolo26n.pt', 60, 10, 12), _r('yolo26s.pt', 30, 25, 30)], 15)
    assert 'yolo26s sustains the target' in text
    assert 'not an accuracy metric' in text


def test_end_to_end_with_mocked_worker(tmp_path, monkeypatch):
    calls = []

    class InlinePool:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def submit(self, fn, model, path, opts):
            calls.append(model)

            class F:
                def result(self_inner):
                    return {**_r(model, 20, 40, 50), 'input_resolution': '640x480',
                            'inference_image_size': 640, 'backend': 'pytorch',
                            'device': 'cpu', 'warmup_frames': 1, 'processed_frames': 2,
                            'peak_rss_mb': 1.0, 'person_detections_total': 0}
            return F()

    monkeypatch.setattr(benchmark, 'ProcessPoolExecutor', InlinePool)
    out = tmp_path / 'r.json'
    rc = benchmark.main(['--source', 'synthetic', '--frames', '2', '--warmup', '1',
                         '--output', str(out)])
    assert rc == 0 and calls == ['yolo26n.pt', 'yolo26s.pt']
    data = json.loads(out.read_text())
    assert len(data['results']) == 2 and 'recommendation' in data


def test_run_model_excludes_warmup_frames(tmp_path, monkeypatch):
    import numpy as np

    from fakes import FakeModel
    from human_perception import model_backend

    class FakeDetector(FakeModel):
        def __init__(self, cfg):
            super().__init__([(1, 1, 10, 20, 0.9, 0)])

    monkeypatch.setattr(model_backend, 'UltralyticsDetector', FakeDetector)
    path = tmp_path / 'f.npy'
    np.save(path, np.zeros((5, 32, 32, 3), np.uint8))
    opts = {'weights_dir': str(tmp_path), 'backend': 'pytorch', 'device': 'cpu',
            'conf': 0.45, 'imgsz': 64, 'warmup': 2}
    r = benchmark.run_model('yolo26n.pt', str(path), opts)
    assert r['warmup_frames'] == 2
    assert r['processed_frames'] == 3
    assert r['person_detections_total'] == 3
    assert r['inference_p95_ms'] >= 0 and r['end_to_end_fps'] > 0
