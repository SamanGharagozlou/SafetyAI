# Human follow (lock-on + follow) — design

Date: 2026-11-15 · Branch: `feature/human-follow` · Status: approved design, not yet implemented

## Goal

When a guest connects (QR scan → web app), the D1 locks onto **that guest** in its camera view and can follow them.
Everyone else in view is labelled `unknown` and never steers the robot. This phase delivers lock-on tracking and a
follow controller in **dry-run** (velocity published on `/follow/cmd_vel`, not wired to the robot). First test target:
the laptop webcam under WSL, visualised in RViz.

Out of scope (later specs): wiring `/follow/cmd_vel` to `/cmd_vel` (needs the cmd_vel arbitration from
`docs/system_architecture.md`), the spa state machine (greet / chat / escort / refuse), frontend changes.

## Decisions

| Topic | Decision |
|---|---|
| Who is "the user" at connect time | Proximity: the person with the best size × centredness score, stable for ~0.5 s |
| Identity | Frame-to-frame IoU tracker + OSNet person re-ID embedding (boxmot `ReID`, `osnet_x0_25_msmt17`, 512-d, L2-normalised) |
| Architecture | New package `d1_follow` consuming the existing `/perception/humans/detections`; `human_perception` unchanged |
| Trigger | Trigger services `/d1_follow/lock`, `/d1_follow/release`; gateway intents `follow_lock` / `follow_release`; `auto_lock:=true` for the webcam test |
| Distance | Monocular proxy: bbox height (no depth needed) |
| Visualisation | RViz image panel on `/follow/annotated_image` |

## Architecture

```
/camera/image_raw ──► human_detector (unchanged) ──► /perception/humans/detections
        │                                                     │
        └──────────────────────────────► d1_follow/target_follower ◄── /d1_follow/lock, /d1_follow/release (std_srvs/Trigger)
                                                │                         ▲
                                                │                         └── d1_gateway intents follow_lock / follow_release
                                                ├─► /follow/cmd_vel         geometry_msgs/Twist   (dry-run, not wired)
                                                ├─► /follow/state           std_msgs/String (JSON)
                                                ├─► /follow/target          vision_msgs/Detection2D (target only)
                                                └─► /follow/annotated_image sensor_msgs/Image (RViz)
```

Detections carry the source image header (`copy_header` in `human_perception`), so the follower pairs a detection
array with its frame by exact stamp from a small ring buffer of recent images (default 10).

## Components (`ros2_ws/src/d1_follow/d1_follow/`)

ROS-free modules are unit-tested without ROS (same pattern as `human_perception`).

### `tracker.py` — `IouTracker`
- Input per frame: `N×4` xyxy boxes + scores. Output: list of `Track(id, box, score, age, hits, misses)`.
- Greedy IoU association (highest IoU first, `iou_min=0.3`), with a constant-velocity box prediction for unmatched
  tracks. New track per unmatched detection; a track is `confirmed` after 3 hits; dropped after `max_misses=15` frames.
- Track IDs are monotonic and never reused.

### `embedder.py` — `Embedder` protocol: `embed(image_bgr, boxes_xyxy) -> (N, D) float32, L2-normalised`
- `OsnetEmbedder`: wraps `boxmot.reid.core.reid.ReID` (pinned `boxmot==19.0.0`), weights baked into the Docker image.
  Measured: ~300 ms per call (3 crops) on CPU in WSL Docker → called sparingly (see budget below).
- `HistogramEmbedder`: HSV histogram of the upper/lower body halves, L2-normalised. Used in unit tests and as the
  automatic fallback if boxmot or weights are unavailable (logged as a warning; weaker identity).

### `target_lock.py` — `TargetLock` state machine
States: `IDLE → LOCKING → TRACKING ⇄ LOST → IDLE`.

- **IDLE**: no target; every person is `unknown`. `lock()` → LOCKING. With `auto_lock`, LOCKING starts automatically
  when a confirmed track is present.
- **LOCKING** (`lock_window_s=0.5`): score each confirmed track by `area_frac × (1 − |cx − 0.5|·2)`; the candidate must
  be the top scorer for the whole window and beat the runner-up by `lock_margin=1.3×`. Embed it 3 times during the window
  to seed the **gallery** (max `gallery_size=10`). Success → TRACKING with `target_id`. No person by
  `lock_timeout_s=5` → IDLE, result `no_person`.
- **TRACKING**: the target is the track with `target_id`. Every `verify_period_s=1.0` the target crop is embedded and
  compared to the gallery (max cosine similarity):
  - sim ≥ `sim_accept=0.70` and the box does not overlap another person (IoU < 0.1) → add to gallery (FIFO, first 3
    seed embeddings are never evicted, so the gallery cannot drift onto a bystander).
  - sim < `sim_reject=0.50` for 2 checks in a row → treat as an ID switch → LOST.
  - target track disappears (dropped by tracker) → LOST.
- **LOST**: zero velocity. Each confirmed track that is not known-unknown is embedded (at most one new embed per track
  per `verify_period_s`). A candidate is re-acquired when its similarity ≥ `sim_accept` for `reacquire_hits=3`
  consecutive checks **and** exceeds the second-best candidate by `reacquire_margin=0.10` → TRACKING with the new
  track id. `lost_timeout_s=30` → IDLE with reason `lost_timeout`.
- `release()` from any state → IDLE (gallery cleared).
- Output per frame: `state`, `target` (track or None), `labels: {track_id: 'target'|'unknown'}`, `similarity`.

Embedding budget: LOCKING ≈ 3 calls per lock, TRACKING ≈ 1 call/s, LOST ≈ 1 call/s (batching all candidates in one
call). Frame-to-frame identity is the tracker's job, never the embedder's.

### `follow_controller.py` — `FollowController`
- Inputs: target box, image size, state. Output: `(vx, wz)`.
- `wz = −k_yaw · ex` with `ex = (cx − W/2)/(W/2)`, deadband `|ex| < 0.08`.
- `vx = k_x · (target_height_frac − h/H)`, deadband 0.05, **clamped to `[0, max_vx]`** (never reverses toward a guest).
- Defaults: `target_height_frac=0.6`, `k_yaw=1.0`, `k_x=1.2`, `max_vx=0.5 m/s`, `max_wz=0.8 rad/s`.
- Output is exactly zero unless state is TRACKING and the latest detection is fresh (≤ `stale_s=0.5`).

### `target_follower_node.py` — ROS wiring
- Subscribes `/camera/image_raw` (sensor-data QoS) and `/perception/humans/detections`; pairs by stamp; unmatched
  arrays are skipped and counted.
- Runs tracker → lock → controller per detection array; publishes the outputs listed above.
- `/follow/cmd_vel` published at the detection rate and also by a 10 Hz timer (zero when stale) so a future consumer
  sees an explicit stop.
- Services: `~/lock`, `~/release` (`std_srvs/Trigger`; node name `d1_follow` → `/d1_follow/lock`). `lock` returns
  after LOCKING resolves (success with target id, or failure `no_person`).
- `/follow/state` JSON: `{"state","target_id","similarity","n_people","reason"}` at 2 Hz and on change.
- Annotated image (only when someone subscribes): target = thick green box `TARGET #id sim 0.82`; others = thin grey
  `unknown #id`; LOCKING candidate = yellow; banner with state + dry-run `vx / wz` arrow. Never written to disk.

### `lock_cli.py`
`ros2 run d1_follow lock` / `ros2 run d1_follow release` → call the Trigger services and print the result.

## Gateway integration (`d1_gateway`)
- `protocol.INTENTS` gains `follow_lock` and `follow_release`.
- `RosRobot`: these intents call `/d1_follow/lock` / `/d1_follow/release` (Trigger) and map the response to
  `intent_done` / `intent_failed` (reason from the service message, e.g. `no_person`). If the service is not up →
  `intent_failed: follow node not running`.
- `MockRobot` already handles generic intents; a test asserts the new intents are accepted.
- Gateway still never sends raw velocities.

## Launch, Docker, RViz
- `d1_follow/launch/follow.launch.py` args: `auto_lock` (false), `embedder` (`osnet|histogram`), `rviz` (false),
  `params` (YAML, default `config/follow.yaml`).
- `d1_follow/rviz/follow.rviz`: Image display on `/follow/annotated_image`.
- Docker (`docker/Dockerfile`): `pip3 install -c numpy<2 boxmot==19.0.0` (verified: keeps numpy 1.26.4, torch,
  ultralytics 8.4, rclpy imports), bake `osnet_x0_25_msmt17.pt`, copy + build `d1_follow`.
- `Makefile`/compose/entrypoint: `follow=true` → runs the perception launch (its rviz off) plus `follow.launch.py`
  with `rviz` and `auto_lock` passed through. Test command:
  `make build && make up source=wsl-webcam follow=true auto_lock=true rviz=true display=localhost:0.0`.

## Error handling
| Situation | Behaviour |
|---|---|
| No image for a detection stamp | skip frame, count `unpaired`, warn once per 10 s |
| Detections stop (> `stale_s`) | velocity zero; state stays (LOST only via tracker drop) |
| `lock` with nobody in view | `no_person` after `lock_timeout_s`; state IDLE |
| boxmot / weights missing | fall back to `HistogramEmbedder`, warn, report `embedder` in state JSON |
| Embedding call raises | log, skip the check (no state change) |

## Testing
- Unit (pytest, no ROS): tracker (ID persistence, crossing boxes, drop after misses); lock (proximity choice + margin,
  `no_person` timeout, occlusion → LOST → re-acquire of the right person, **look-alike bystander never accepted**,
  release); controller (deadbands, clamp, never negative vx, zero when not TRACKING / stale). Lock tests use a fake
  embedder with controlled vectors.
- Gateway: `follow_lock` / `follow_release` accepted by the protocol and complete with the mock robot.
- Live (WSL webcam, RViz): (1) user sits in front → green TARGET; (2) teammates enter / pass behind → grey unknown;
  (3) teammate fully occludes user → LOST, `vx=wz=0`; (4) user reappears → TARGET again, teammate never becomes TARGET;
  (5) `ros2 run d1_follow release` → IDLE, then `lock` re-locks.

## Safety & privacy
- Dry-run only; nothing publishes `/cmd_vel`. Caps far below `SAFE_LIMIT`; vx never negative.
- Frames and crops stay in memory; embeddings are not persisted; annotated image is a live topic only.

## Revisions

**2026-11-15, after the whole-branch review** (implemented; see [docs/human_follow.md](../../human_follow.md)):

- **Identity decisions split seeds from gallery.** The design let the gallery max decide everything; a single accepted
  bystander crop then scores ~1.0 against itself forever. Decisions that *grant* identity (gallery learning, clearing
  a suspicion, LOST re-acquisition) now use the **seed** embeddings only; only keep/reject in the periodic TRACKING
  check still uses the gallery max, so pose change is still tolerated.
- **Suspect check after an overlap.** While the target box overlaps another track nothing is embedded (the crop holds
  two people) and the target is marked suspect; on the first clear view it is verified immediately against the seeds,
  ignoring `verify_period_s`. This catches the ID switch onto an occluder, which `sim < sim_reject` alone would miss.
- **LOST excludes known-not-target ids** (tracks seen beside the target) — this was the spec's "not known-unknown",
  which the first implementation had dropped — and embeds at most `reacquire_max_candidates` (3) per check at
  `reacquire_period_s` 0.5 s (was 0.3 s) to bound the CPU cost.
- **Freshness by header stamp**, not receipt time; arrays older than `stale_s` are dropped.
- **Image QoS is RELIABLE by default** (`image_reliability` parameter), because this repo measured a best-effort
  subscriber receiving 0 of ~1 MB frames; `unpaired` arrays are counted, warned about and reported in the state JSON.

**2026-11-15, after the first live webcam test** (supersedes the seeds-only rule above):

- **Learning from track continuity.** Seeds-only failed live: the guest's own similarity to close-up seeds dropped to
  0.54–0.75 when moving, causing false LOSTs, slow re-acquisition and finally `lost_timeout`. Now every decision uses
  the gallery max, and the gallery learns distinct views (`sim_learn` 0.60 ≤ sim < `learn_max_sim` 0.95) only while
  the target track is unbroken and unoccluded. A missed frame now also makes the target suspect (someone could inherit
  the track at the same spot), like an overlap.
- **OSNet capped at `embed_threads` (2) torch threads**: with all 20 cores next to YOLO and RViz one embed took ~6 s
  (measured in the live container), which caused the 3.5 s detection stalls; with 2 threads ~20 ms.
- **`auto_lock` re-arms after `lost_timeout`** (dev/test option only).
- `verify_period_s` 1.0 → 0.5 and `gallery_size` 10 → 30 now that an embed is cheap.
