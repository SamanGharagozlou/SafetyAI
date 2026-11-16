# 06 · Perception and the camera people-guard

[← 05 Audio](05-audio-bluetooth-mic.md) · [index](../README.md) · next: [07 Child safety](07-child-safety.md)

Source: `ros2_ws/src/human_perception`, `ros2_ws/src/d1_follow`,
`tools/robot-guard/`. Working docs: [perception.md](../perception.md),
[perception_architecture.md](../perception_architecture.md),
[human_follow.md](../human_follow.md),
[tools/robot-guard/README.md](../../tools/robot-guard/README.md).

---

## 0. The honesty notice that is in the code, not just here

> **Not a safety system.** This is a best-effort perception component. It is
> not safety-rated, it misses people (occlusion, distance, lighting, unusual
> poses), and it produces false positives. A missing or `false` detection must
> never be read as "no person is present". **Do not use it to gate robot motion
> or any safety function.**
> — `docs/perception.md`, top of file

The camera people-guard in `tools/robot-guard` **does** gate robot motion. That
is a documented, unresolved conflict between two packages in this repository,
and it is written down in both of them rather than quietly resolved in one
direction. See §7 and [15](15-gaps-and-roadmap.md).

---

## 1. One camera, no depth

The whole sensor table, from the vendor spec sheet §1.3 and confirmed against
the SDK headers:

| | |
|---|---|
| camera | **one** wide-angle monocular — DFOV 122°, HFOV 122°, VFOV 70° |
| IMU | standard |

No stereo. No depth. No LiDAR. No ultrasonic. No ToF. `highlevel.h` exposes
motion, joints, attitude and battery, and **no obstacle API**.

Feed: H.264 over RTSP, `rtsp://192.168.168.168:8554/test` wired or
`rtsp://192.168.234.1:8554/test` over the robot's own AP.

---

## 2. `human_perception`: detection as a ROS 2 citizen

```
webcam / D1 RTSP / file
        │
  [video_source] ──► /camera/image_raw
                            │
                     [human_detector]   Ultralytics YOLO26, COCO "person"
                            ├─► /perception/humans/detections   vision_msgs/Detection2DArray
                            ├─► /perception/humans/present      std_msgs/Bool
                            ├─► /perception/humans/count        std_msgs/Int32
                            ├─► /perception/humans/annotated_image
                            └─► /diagnostics
```

Status: **working on a PC in Docker**; Orin deployment planned.

### Freshness over completeness

Latency must not grow when inference is slower than the camera. Three layers
enforce that:

1. **Capture.** The grabber thread reads continuously so RTSP/FFmpeg never
   builds an internal backlog; only the newest frame is kept. A slow publish
   skips frames (`skipped_stale_frames`) and never slows capture.
2. **Transport.** Publisher and subscriber both `KEEP_LAST` depth 1.
3. **Detector.** The callback overwrites a one-message slot; a separate
   inference thread takes whatever is newest. Overwritten messages count as
   `dropped_frames`.

A backlog in a perception pipeline that gates motion is not a performance
problem, it is a correctness problem: it makes the robot act on where someone
*was*.

### The architecture rule that makes it testable

Almost nothing lives in the ROS node. `config.py`, `backoff.py`, `metrics.py`,
`capture.py`, `image_codec.py`, `detection.py`, `model_backend.py`,
`annotate.py` are all ROS-free, so the whole detector can be unit-tested and
benchmarked on a laptop — and the benchmark therefore measures exactly the code
path the node runs.

### Failure handling, all of it biased towards silence-as-finding

| Failure | Behaviour |
|---|---|
| Invalid parameter | `ConfigError`, node exits with a clear message |
| Model missing / CUDA unavailable | node stays up, ERROR diagnostics, **`present`/`count` are not published** |
| Malformed image | dropped, `malformed_frames++`, throttled warning |
| Inference exception | dropped, `inference_errors++` |
| Source will not open | backoff 0.5 → 10 s with jitter, `DISCONNECTED` diagnostics |
| Connected but no frames | WARN (stale) |
| Credentials in an RTSP URL | redacted in logs and diagnostics |

Note the second row: when the model is broken the node **does not publish
`present: false`**. Publishing "no person" because the detector is dead is the
exact failure this project keeps finding and keeps refusing to ship.

### A measured QoS surprise

The code default for the detector input is sensor-data (BEST_EFFORT), which is
correct for image topics. The **shipped YAML overrides it to RELIABLE**,
because with stock kernel buffers best-effort lost **100 %** of frames ≥ 0.5 MB
in testing. Both facts are documented; the override is deliberate, not an
oversight.

---

## 3. `d1_follow`: lock onto *one* guest

When a guest connects, the robot should follow **that** guest. Everyone else in
view is labelled `unknown` and never steers the robot.

```
/perception/humans/detections ──► target_follower ──► /follow/cmd_vel  (DRY RUN)
                                        ├─► /follow/state
                                        ├─► /follow/target
                                        └─► /follow/annotated_image
```

| Module | Role |
|---|---|
| `tracker.py` | greedy IoU tracker, constant-velocity prediction; confirmed after `min_hits`, dropped after `max_misses`; ids monotonic, never reused |
| `embedder.py` | OSNet x0_25 MSMT17 512-d appearance vector, L2-normalised (boxmot `ReID`), falling back to an HSV histogram with a warning |
| `target_lock.py` | `IDLE → LOCKING → TRACKING → LOST → …`, ROS-free |
| `follow_controller.py` | P controller, box → `(vx, wz)` |

> **Status: dry run.** `/follow/cmd_vel` is **not** wired to `/cmd_vel`.
> Nothing in the package publishes `/cmd_vel`. Developed against unit tests and
> the laptop webcam path; the live laptop test is still pending.

Two details worth noting:

- **Unpaired detections still run the tracker but cannot be embedded**, and are
  counted as `unpaired`. Until the first image is paired the frame size is
  unknown, so the lock update is skipped entirely and the velocity stays zero.
  Unknown geometry produces no motion.
- Re-identification here is **in-memory, session-scoped and never stored**,
  which is the only version of appearance matching that is defensible in a spa.
  See [13](13-privacy-and-compliance.md).

---

## 4. The camera people-guard: distance without a depth sensor

`tools/robot-guard` is the layer that actually stops the robot for a person. It
runs its own YOLO on the RTSP stream and computes range geometrically.

### Ground-plane ranging

People stand on the floor, and the floor is a known plane. Given camera height
and tilt, **the pixel row of someone's feet fixes their range.**

| range | resolution |
|---|---|
| 1 m | 0.6 cm / px |
| 2 m | 2.0 cm / px |
| 3 m | 4.5 cm / px |
| 4 m | 7.9 cm / px |
| 8 m | 31 cm / px |

Centimetres inside the safety envelope, useless at the far end — **which is the
right shape of error.** The question is always "is someone within two metres",
never "is that person 7.3 or 7.8 metres away".

Detection range is not the constraint: a 0.85 m toddler is ~73 px tall at 6 m,
well above any detector's floor.

### Calibration is mandatory, and the failure mode is silent

Everything rests on camera height and tilt. Wrong values are not noisy — they
are **confidently wrong by a constant factor**. 1.2 m reported as 2 m, all day,
without a flicker of doubt.

```bash
# robot standing on flat floor; tape at 1.0 m and 3.0 m straight ahead
python3 calibrate.py grab --rtsp rtsp://192.168.168.168:8554/test
python3 calibrate.py solve --near 1.0 449 --far 3.0 320
```

Then verify against a **third** mark you did not fit to. Until `--calibrated`
is passed, the guard caps the robot to slow.

---

## 5. The blind spot, which is the whole problem

A camera 0.40 m up with a 70° vertical FOV cannot see the ground nearer than:

| tilt down | blind inside |
|---|---|
| 0° | 0.57 m |
| 10° | 0.40 m |
| 15° | 0.34 m |
| 30° | 0.19 m |

> **A two-year-old who toddles up to touch the robot crosses that line and
> vanishes from the frame.** The naive implementation reads the empty frame as
> "all clear" and resumes walking — into the child it was looking at a moment
> ago.

**Disappearance at close range does not mean absence. It means *closer*.**

So the guard **latches**: a person lost at close range is presumed still
present, in the blind spot, for several seconds. A small person is latched
**longer (5 s) and harder (full freeze)** than an adult (3 s). Confidence
decays towards danger, not convenience.

The same rule covers a dead feed. **No fresh frame for 0.5 s → FREEZE.** This
stack has already shipped the opposite bug once: the public endpoint answered
`200` with nulls while the robot lay on the floor, and the voice agent reported
it as "stationary".

---

## 6. The state machine

| state | walking | gestures |
|---|---|---|
| `GO` | full speed | all |
| `SLOW` | capped 0.25 m/s | all |
| `HOLD` | none | four-feet-down only |
| `FREEZE` | none | none |

Triggers, taking the **worst** person ahead (±45°):

- beyond 4 m → `GO`
- 2–4 m → `SLOW`
- 0.9–2 m → `HOLD`
- inside 0.9 m, **or feet clipped by the frame bottom** → `FREEZE`
- **a child (< 1.25 m tall) within 2 m → `FREEZE`** — see [07](07-child-safety.md)
- closing fast enough to arrive within 2 s → escalates early
- no frame for 0.5 s → `FREEZE`

`HOLD` keeps soft gestures deliberately: *a robot that goes completely inert the
moment someone approaches is a worse greeter than one that nods at them.*

### Two layers, and why the second is the real one

`walk_distance` **blocks on the robot for up to 25 s**. By the time a guest
steps in front of the dog, the command was accepted long ago and nothing is
asking permission any more. Refusing the *next* command does nothing about the
one already running.

1. **Gate** (`GUARD_URL`) — refuses to *start* something already unsafe. Cheap,
   covers new commands only.
2. **Override** (this service) — sends `stop_robot` **every tick while unsafe**,
   interrupting what is in flight. **This is the one that matters.**

`stop_robot` and `robot_status` are never gated. Gating a stop is how you get a
robot that cannot be stopped.

---

## 7. The unreconciled conflict, stated in full

`tools/robot-guard` was built in parallel with `human_perception` /
`d1_follow`, and the two have not been merged. Three real conflicts need a
decision before anyone deploys this:

1. **Two detectors.** The guard runs its own YOLO on the RTSP stream while
   `human_perception` publishes detections from the same camera. Running both
   is wasteful and, on a Jetson with no torch installed, currently impossible.
   The right shape is for the guard to **consume those detections**.
2. **Where inference runs.** `perception.md` says inference never runs on the
   robot — reasonable, but it means a guard hosted off-board inherits the Wi-Fi
   hop, and **a stop that arrives a second late is close to useless**. Either
   inference moves onto the Jetson (opencv-dnn with an ONNX model avoids torch
   entirely; OpenCV 4.8 is already there), or the override loop lives wherever
   the inference does.
3. **`perception.md` explicitly says "do not use it to gate robot motion or any
   safety function" — and gating motion is exactly what the guard does.** The
   positions are closer than they look: the guard's own README says the same
   thing about not being a safety system, and argues a best-effort gate still
   reduces how often a handler has to be quick. But it is a documented project
   rule, and crossing it is the owner's decision, not a package's.

Also overlapping: `d1_follow` makes the robot **follow** a guest with proper
OSNet re-ID; `escort_core.py` in robot-guard does the opposite — the robot
**leads** and checks the guest is still behind. Different behaviours, duplicated
identity machinery, and `d1_follow`'s is better.

**Status on the Jetson: the guard unit is `inactive`.** The rules in
[chapter 07](07-child-safety.md) are implemented and tested, and the override
is not running on the robot today. That is the most important sentence in this
chapter.

---

## 8. Depth, and why it is not the answer here

`d1_depth` runs Depth Anything V2 monocular metric depth → `/camera/depth` and
`/camera/points`. Experimental.

It is **reliable only within ~3 m** ([costmap_plan.md](../costmap_plan.md)), so
the costmap built from it is short-range, and a learned monocular depth model
is a confident guesser on glass, water surfaces and featureless walls — three
things a thermal spa has in abundance.

Useful as a costmap layer at low speed. Not a substitute for a range sensor,
and explicitly not a collision monitor.

---

## 9. Running it

```bash
make build                                   # ROS 2 Humble + YOLO26 image, yolo26n baked in
make up                                      # laptop webcam -> detector
make up rviz=true                            # ... with boxes and a people count
make up source=d1-ethernet                   # the robot camera over the cable
make logs                                    # perf line every 10 s
```

```bash
# the guard, against the real camera
pip install ultralytics                      # opencv ships with JetPack
D1_ROBOT_TOKEN=... python3 tools/robot-guard/guard.py \
    --rtsp rtsp://192.168.168.168:8554/test \
    --robot http://127.0.0.1:8090 \
    --cam-height 0.40 --cam-tilt 12.0 --calibrated
```

Tests: `cd tools/robot-guard && python3 -m pytest -q` → **62 passed** (run
2026-11-16). See [14](14-testing-and-evidence.md).

---

[← 05 Audio](05-audio-bluetooth-mic.md) · [index](../README.md) · next: [07 Child safety](07-child-safety.md)
