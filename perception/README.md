# Perception: object and person detection

`detect.py` runs YOLO11 object detection on the D1 camera stream. It runs on a PC with an NVIDIA GPU, not on the robot.

## Camera stream

The robot publishes its camera through MediaMTX. The pipeline is started by `robot-launch`, which runs `/opt/app_launch/stream_4cams_rtsp.sh` on the robot.

| Protocol | URL |
|---|---|
| RTSP | `rtsp://<robot-ip>:8554/test` |
| WebRTC (browser) | `http://<robot-ip>:8889/test/` |
| HLS (browser) | `http://<robot-ip>:8888/test/` |

Only one camera (`/dev/video11`) is present. It streams 1920×1080 at 60 fps, H.264 at about 20 Mbit/s, with every frame a keyframe.

## Setup

```bash
cd perception
uv venv .venv --python 3.12        # or: python3 -m venv .venv
uv pip install --python .venv/bin/python torch torchvision --index-url https://download.pytorch.org/whl/cu128
uv pip install --python .venv/bin/python -r requirements.txt
```

The model weights (`yolo11s.pt`) download automatically on first run.

On WSL, the OpenCV window needs `sudo apt install libsm6 libice6`. Without them, the script skips the window and you can use the browser view.

## Run

```bash
.venv/bin/python detect.py --source rtsp://172.24.1.41:8554/test
```

Outputs:
- **Live window** with boxes. Press `q` to quit, or use `--no-show` to run without it.
- **Browser view** with boxes at `http://localhost:8090/`. The latest detections are at `/latest.json`. Change the port with `--port`, or set `--port 0` to turn it off.
- **JSON lines**, one per processed frame, on stdout. Use `--json detections.jsonl` to write to a file, or `--json ""` to turn it off:
  ```json
  {"ts": 1794671495.6, "frame": 856, "fps": 6.9, "width": 1920, "height": 1080,
   "detections": [{"class": "person", "conf": 0.81, "box": [x1, y1, x2, y2]}]}
  ```

Useful options:
- `--classes person`: keep only some classes (comma-separated COCO names).
- `--conf 0.6`: confidence threshold.
- `--model yolo11m.pt`: larger, more accurate model.

## Known limitations

- **Network bandwidth.** Over a slow link (~265 ms RTT in testing), only about 13 of the 60 fps arrive, and some frames are corrupted (H.264 decode errors). The GPU itself handles about 77 fps with yolo11s. Lowering the robot's stream bitrate, FPS and keyframe interval in `stream_4cams_rtsp.sh` fixes this.
- **Camera viewpoint.** The camera is low and looks upward, which COCO-trained models rarely see. Expect false positives (for example, a table underside labeled "airplane") and missed people when only legs are visible. Fine-tuning on robot footage is the real fix.
