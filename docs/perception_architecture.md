# Architecture — human_perception (milestone 1)

## Scope

Milestone 1 covers 2D person detection on a single camera stream, with inference on a laptop or
an off-robot companion computer. It is out of scope:
- tracking, re-identification, faces and demographics;
- navigation, SLAM and motion commands;
- cloud inference and recording;
- any safety rating.

## Components

```
┌──────────────── video_source (node) ────────────────┐      ┌──────────── human_detector (node) ─────────────┐
│ FrameGrabber thread                                 │      │ subscription (KEEP_LAST depth 1)               │
│  open_capture(cfg) ─► cv2.VideoCapture              │      │   └─► single-slot buffer (overwrite = "drop")  │
│   webcam (V4L2) | RTSP (FFmpeg/GStreamer) | file    │ Image│ inference thread                               │
│  read() as fast as source delivers (drains decoder) ├─────►│   image_codec ─► DetectionPipeline ─► model    │
│  reconnect: bounded exponential backoff + jitter    │      │   ─► Detection2DArray / Bool / Int32 / annot.  │
│  on_frame ─► stamp(now) ─► publish (rate-capped)    │      │ timers: /diagnostics (1 Hz), perf log          │
│ timers: /diagnostics (1 Hz), perf log               │      └────────────────────────────────────────────────┘
└─────────────────────────────────────────────────────┘
```

| Module | ROS-free? | Responsibility |
|---|---|---|
| `config.py` | yes | `SourceConfig` / `DetectorConfig` dataclasses, validation, URL redaction |
| `backoff.py` | yes | bounded exponential backoff |
| `metrics.py` | yes | rolling mean / p95, rate meter, log throttle |
| `capture.py` | yes | `FrameGrabber` (thread, latest-frame slot, reconnect), `open_capture` |
| `image_codec.py` | yes | `sensor_msgs/Image` fields ⇄ BGR numpy, with validation (replaces cv_bridge) |
| `detection.py` | yes | semantic `person` class resolution, result filtering, `DetectionPipeline` |
| `model_backend.py` | yes | Ultralytics loading for pytorch / onnx / openvino / tensorrt, warm-up |
| `annotate.py` | yes | debug overlay (boxes and scores only) |
| `ros_conversions.py`, `diagnostics.py` | no | message construction, typed parameter declaration |
| `*_node.py` | no | thin ROS wrappers |
| `benchmark.py` | yes | offline model comparison (reuses `DetectionPipeline` and `UltralyticsDetector`) |

The nodes contain almost no logic. Everything testable lives in the ROS-free modules. The
benchmark therefore measures exactly the code path the node runs.

## Freshness over completeness

Latency must not grow when inference is slower than the camera. Three layers enforce this:
1. **Capture.** The grabber thread reads continuously, so RTSP/FFmpeg never builds an internal
   backlog. Only the newest frame is kept. A separate publisher thread sends the newest frame,
   optionally downscaled (`output_width`) and rate-capped (`max_publish_fps`). A slow publish
   skips frames (`skipped_stale_frames`) and never slows capture.
2. **Transport.** The publisher and the detector subscription use `KEEP_LAST` with depth 1.
3. **Detector.** The callback only overwrites a one-message slot. A separate inference thread
   takes whatever is newest, and `max_inference_fps` limits the rate. Overwritten messages
   count as `dropped_frames`.

## QoS

| Endpoint | Reliability | History | Why |
|---|---|---|---|
| `/camera/image_raw` publisher | RELIABLE (param) | depth 1 | matches both best-effort and reliable subscribers (rqt, rosbag) |
| detector input | `input_reliability`: code default BEST_EFFORT (sensor-data), shipped YAML RELIABLE | depth 1 | with stock kernel buffers, best-effort lost 100% of ≥0.5 MB frames in testing (see perception.md) |
| results | RELIABLE | depth 10 | small messages; consumers should not miss state changes |
| annotated image | RELIABLE | depth 1 | rendered only when someone subscribes |

## Time and frames

`video_source` stamps each frame with the ROS clock when it is grabbed and uses the
configured `frame_id`. Every detector output copies the **input** header. Frame age is
`now − header.stamp` at publish time, which covers transport, queueing and inference.

## Failure handling

| Failure | Behaviour |
|---|---|
| Invalid parameter | `ConfigError`, the node exits with a clear `invalid configuration` message |
| Model missing, download failure, CUDA unavailable | node stays up; ERROR diagnostics; throttled error log; **no** present/count published |
| Model without a `person` class | load error (COCO index 0 is used only when the model exposes no class names) |
| Malformed or unsupported image | frame dropped, `malformed_frames` incremented, throttled warning |
| Inference exception | frame dropped, `inference_errors` incremented, throttled error |
| Source cannot open or the stream drops | backoff 0.5 → 10 s with jitter; ERROR diagnostics `DISCONNECTED …` |
| Connected but no frames | WARN diagnostics (stale) |
| Credentials in an RTSP URL | redacted in logs and diagnostics; native FFmpeg/OpenCV logging silenced for that URL |

## Extension points

- **Other cameras.** Any driver that publishes `sensor_msgs/Image` works: run
  `detector_only.launch.py` with `image_topic:=...`.
- **Other backends or models.** Use the `backend` and `model_path` parameters. Anything with
  `predict(frame) → Results-like` and `person_ids` can be injected into the node
  (`model_factory`) or the pipeline.
- **Later milestones.** Tracking, depth fusion or 3D could subscribe to `Detection2DArray`
  without changing the detector.
