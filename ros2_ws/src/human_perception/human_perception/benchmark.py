"""
Compare YOLO person-detection models on the same frames.

Usage (no ROS required)::

    human_perception_benchmark --source 0 --frames 200          # webcam 0
    human_perception_benchmark --source sample.mp4 --models yolo26n.pt yolo26s.pt
    human_perception_benchmark --source synthetic --frames 50    # CI-safe

Frames are captured ONCE into memory and replayed identically for every model.
Each model runs in its own subprocess so peak memory is attributable to it.
Results are printed and written to JSON.  There are no pass/fail thresholds:
latency depends on the hardware, so interpret numbers on the target machine.
"""

import argparse
from concurrent.futures import ProcessPoolExecutor
import datetime as _dt
import json
import multiprocessing
import os
import platform
import sys
import tempfile
import time
from typing import Any, Dict, List, Optional

import numpy as np

from .config import DetectorConfig
from .detection import DetectionPipeline
from .metrics import percentile


def capture_frames(source: str, count: int, width: int = 640, height: int = 480) -> np.ndarray:
    """Return ``count`` BGR frames from a webcam index, video/image path or 'synthetic'."""
    if source == 'synthetic':
        rng = np.random.default_rng(0)
        return rng.integers(0, 255, size=(count, height, width, 3), dtype=np.uint8)
    import cv2
    if os.path.isfile(source) and source.lower().endswith(('.jpg', '.jpeg', '.png', '.bmp')):
        img = cv2.imread(source)
        if img is None:
            raise SystemExit(f'cannot read image {source}')
        return np.stack([img] * count)
    cap = cv2.VideoCapture(int(source) if source.isdigit() else source)
    if not cap.isOpened():
        raise SystemExit(f'cannot open source {source!r} (webcam index, video file, image, '
                         'or "synthetic")')
    frames: List[np.ndarray] = []
    try:
        while len(frames) < count:
            ok, frame = cap.read()
            if not ok:
                if source.isdigit():
                    raise SystemExit('webcam stopped delivering frames')
                if not frames or not cap.set(cv2.CAP_PROP_POS_FRAMES, 0):
                    break  # loop short clips so every model sees `count` frames
                continue
            frames.append(frame)
    finally:
        cap.release()
    if not frames:
        raise SystemExit(f'no frames read from {source!r}')
    return np.stack(frames)


def _peak_rss_mb() -> Optional[float]:
    try:
        import resource
        kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return kb / 1024.0 if sys.platform != 'darwin' else kb / 1024.0 / 1024.0
    except Exception:  # noqa: B902
        return None


def run_model(model: str, frames_path: str, opts: Dict[str, Any]) -> Dict[str, Any]:
    """Benchmark one model (executed in a fresh subprocess)."""
    from .model_backend import UltralyticsDetector

    frames = np.load(frames_path, mmap_mode='r')
    cfg = DetectorConfig(
        model_name=model if os.sep not in model else '',
        model_path=model if os.sep in model else '',
        weights_dir=opts['weights_dir'], backend=opts['backend'], device=opts['device'],
        confidence_threshold=opts['conf'], image_size=opts['imgsz'], warmup_runs=0,
    ).validate()
    t_load = time.perf_counter()
    detector = UltralyticsDetector(cfg)
    load_s = time.perf_counter() - t_load
    pipeline = DetectionPipeline(detector, detector.person_ids, cfg.confidence_threshold)

    warm = min(opts['warmup'], len(frames))
    for i in range(warm):
        pipeline.process(np.ascontiguousarray(frames[i]))

    latencies, people = [], 0
    frames_with_people = 0
    t0 = time.perf_counter()
    for frame in frames[warm:]:  # warm-up frames are excluded from timing
        res = pipeline.process(np.ascontiguousarray(frame))
        latencies.append(res.inference_s)
        people += res.count
        frames_with_people += int(res.present)
    wall = time.perf_counter() - t0
    n = len(latencies)
    return {
        'model': model,
        'resolved_model': cfg.resolved_model_reference(),
        'input_resolution': f'{frames.shape[2]}x{frames.shape[1]}',
        'inference_image_size': cfg.image_size,
        'backend': cfg.backend,
        'device': cfg.device,
        'warmup_frames': warm,
        'processed_frames': n,
        'model_load_s': round(load_s, 3),
        'inference_mean_ms': round(1000 * sum(latencies) / n, 2),
        'inference_p95_ms': round(1000 * percentile(latencies, 95), 2),
        'end_to_end_fps': round(n / wall, 2) if wall > 0 else None,
        'peak_rss_mb': None if _peak_rss_mb() is None else round(_peak_rss_mb(), 1),
        'person_detections_total': people,
        'frames_with_person': frames_with_people,
    }


def recommend(results: List[Dict[str, Any]], target_fps: float) -> str:
    """Hardware-specific guidance; never claims a universally better model."""
    ok = [r for r in results if 'error' not in r]
    if not ok:
        return 'No model completed; see errors above.'
    lines = [f'Target: {target_fps:g} FPS sustained on THIS machine/config.']
    for r in ok:
        meets = r['end_to_end_fps'] is not None and r['end_to_end_fps'] >= target_fps
        lines.append(f"- {r['model']}: {r['end_to_end_fps']} FPS, p95 "
                     f"{r['inference_p95_ms']} ms -> "
                     f"{'meets' if meets else 'does NOT meet'} target")
    by_name = {os.path.basename(r['model']): r for r in ok}
    n, s = by_name.get('yolo26n.pt'), by_name.get('yolo26s.pt')
    if n and s:
        s_ok = s['end_to_end_fps'] and s['end_to_end_fps'] >= target_fps
        extra = s['inference_mean_ms'] - n['inference_mean_ms']
        if s_ok:
            lines.append(
                f'yolo26s sustains the target here; choose it if the extra accuracy is worth '
                f'~{extra:.1f} ms/frame mean latency (and the extra memory). Otherwise '
                'yolo26n keeps more headroom.')
        else:
            lines.append('yolo26s does not sustain the target here; prefer yolo26n when '
                         'real-time latency or CPU limits dominate (or lower image_size / '
                         'use an accelerated backend and re-measure).')
    lines.append('Detection counts on unlabeled footage are not an accuracy metric; validate '
                 'accuracy on representative labeled data before deciding.')
    return '\n'.join(lines)


def _print_table(results: List[Dict[str, Any]]) -> None:
    cols = [('model', 'model'), ('input_resolution', 'input'), ('inference_image_size', 'imgsz'),
            ('backend', 'backend'), ('device', 'device'), ('warmup_frames', 'warmup'),
            ('processed_frames', 'frames'), ('inference_mean_ms', 'mean ms'),
            ('inference_p95_ms', 'p95 ms'), ('end_to_end_fps', 'fps'),
            ('peak_rss_mb', 'peak MB'), ('person_detections_total', 'persons')]
    rows = [[h for _, h in cols]]
    for r in results:
        if 'error' in r:
            rows.append([r['model'], f"ERROR: {r['error']}"] + [''] * (len(cols) - 2))
        else:
            rows.append([str(r.get(k)) for k, _ in cols])
    widths = [max(len(row[i]) for row in rows) for i in range(len(cols))]
    for i, row in enumerate(rows):
        print('  '.join(c.ljust(w) for c, w in zip(row, widths)))
        if i == 0:
            print('  '.join('-' * w for w in widths))


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    ap.add_argument('--models', nargs='+', default=['yolo26n.pt', 'yolo26s.pt'])
    ap.add_argument('--source', default='0',
                    help='webcam index, video/image path, or "synthetic"')
    ap.add_argument('--frames', type=int, default=100)
    ap.add_argument('--warmup', type=int, default=10)
    ap.add_argument('--imgsz', type=int, default=640)
    ap.add_argument('--device', default='cpu')
    ap.add_argument('--backend', default='pytorch',
                    choices=['pytorch', 'onnx', 'openvino', 'tensorrt'])
    ap.add_argument('--conf', type=float, default=0.45)
    ap.add_argument('--target-fps', type=float, default=10.0)
    ap.add_argument('--weights-dir', default='~/.cache/human_perception/models')
    ap.add_argument('--output', default='benchmark_results/benchmark.json')
    args = ap.parse_args(argv)

    total = args.frames + args.warmup
    print(f'capturing {total} frames from {args.source!r} (replayed identically per model)...')
    frames = capture_frames(args.source, total)
    opts = {'weights_dir': args.weights_dir, 'backend': args.backend, 'device': args.device,
            'conf': args.conf, 'imgsz': args.imgsz, 'warmup': args.warmup}

    results: List[Dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix='hp_bench_') as tmp:
        path = os.path.join(tmp, 'frames.npy')
        np.save(path, frames)
        del frames  # frames are deleted with the temp dir; nothing is persisted
        ctx = multiprocessing.get_context('spawn')
        for model in args.models:
            print(f'benchmarking {model} on {args.device}/{args.backend} ...', flush=True)
            with ProcessPoolExecutor(max_workers=1, mp_context=ctx) as pool:
                try:
                    results.append(pool.submit(run_model, model, path, opts).result())
                except Exception as exc:  # noqa: B902 - report and continue
                    results.append({'model': model, 'error': f'{type(exc).__name__}: {exc}'})

    print()
    _print_table(results)
    advice = recommend(results, args.target_fps)
    print('\n' + advice)

    report = {
        'timestamp': _dt.datetime.now(_dt.timezone.utc).isoformat(),
        'host': {'platform': platform.platform(), 'python': platform.python_version(),
                 'processor': platform.processor() or platform.machine(),
                 'cpu_count': os.cpu_count()},
        'source': args.source if args.source in ('synthetic',) or args.source.isdigit()
        else os.path.basename(args.source),
        'frames': args.frames, 'target_fps': args.target_fps,
        'results': results, 'recommendation': advice,
    }
    out = os.path.expanduser(args.output)
    os.makedirs(os.path.dirname(out) or '.', exist_ok=True)
    with open(out, 'w', encoding='utf-8') as fh:
        json.dump(report, fh, indent=2)
    print(f'\nJSON written to {out}')
    return 0 if all('error' not in r for r in results) else 1


if __name__ == '__main__':
    sys.exit(main())
