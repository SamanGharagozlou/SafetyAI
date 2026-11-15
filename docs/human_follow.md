# d1_follow — lock onto one guest and follow (dry run)

When a guest connects, the robot should lock onto **that guest** in its camera view and follow them. Everyone else in
view is labelled `unknown` and never steers the robot. `d1_follow` consumes the person detections from
[human_perception](perception.md), tracks people frame to frame, identifies the locked guest by appearance (person
re-ID), and publishes a follow velocity.

> **Status: dry run, unverified on the robot.** `/follow/cmd_vel` is **not** wired to `/cmd_vel`; nothing in this
> package publishes `/cmd_vel`. It has only been developed against unit tests and the laptop webcam path (see
> [Run on the laptop](#run-on-the-laptop-wsl-webcam)); the live laptop test is still pending. Design:
> [superpowers/specs/2026-11-15-human-follow-design.md](superpowers/specs/2026-11-15-human-follow-design.md).
> Not a safety system (same caveats as [perception.md](perception.md)).

## How it works

```
/camera/image_raw ──► human_detector ──► /perception/humans/detections
        │                                          │
        └─────────────────────────► d1_follow/target_follower ◄── /d1_follow/lock, /d1_follow/release
                                            │                          ▲
                                            ├─► /follow/cmd_vel        └── gateway intents follow_lock / follow_release
                                            ├─► /follow/state
                                            ├─► /follow/target
                                            └─► /follow/annotated_image
```

Per detection array the node: drops the array if it is older than `stale_s` (age from its header stamp) → pairs it
with its frame by exact header stamp (ring buffer of `image_buffer` recent images; unpaired arrays still run the
tracker but cannot be embedded, and are counted as `unpaired`) → `IouTracker` → `TargetLock` → `FollowController`.
Until the first image is paired the frame size is unknown, so the lock update is skipped entirely and the velocity
stays zero.

| Module | Role |
|---|---|
| `tracker.py` | Greedy IoU tracker with constant-velocity prediction. A track is *confirmed* after `min_hits` hits, dropped after `max_misses` missed frames. Ids are monotonic and never reused. |
| `embedder.py` | Appearance vector per person crop, L2-normalised. `osnet` (boxmot `ReID`, OSNet x0_25 MSMT17, 512-d) or `histogram` (HSV hue/saturation of upper and lower body). Falls back to `histogram` with a warning if boxmot or weights are unavailable. |
| `target_lock.py` | State machine below. ROS-free. |
| `follow_controller.py` | P controller, box -> `(vx, wz)`. |
| `annotate.py` | Draws the RViz image. |

### State machine

```mermaid
stateDiagram-v2
    [*] --> IDLE
    IDLE --> LOCKING: lock service / auto_lock + confirmed person
    LOCKING --> TRACKING: same candidate for lock_window_s, clear winner, seed_size embeddings
    LOCKING --> IDLE: lock_timeout_s (no_person | ambiguous)
    TRACKING --> LOST: track dropped | appearance mismatch
    LOST --> TRACKING: re-acquired
    LOST --> IDLE: lost_timeout_s
    LOCKING --> IDLE: release
    TRACKING --> IDLE: release
    LOST --> IDLE: release
```

| State | Behaviour |
|---|---|
| **IDLE** | No target, every person `unknown`, velocity 0. `auto_lock` starts LOCKING when the first confirmed person appears (only until the first lock; `release` disables it). |
| **LOCKING** | Each confirmed, visible person is scored `area_frac × (1 − 2·|cx − 0.5|)` (big and centred). The top scorer is the candidate only if it beats the runner-up by `lock_margin` (1.3×); if the candidate changes the window restarts. The candidate is embedded every `lock_window_s / seed_size` s to seed the gallery. After `lock_window_s` with `seed_size` embeddings -> TRACKING. If nothing resolves within `lock_timeout_s` -> IDLE, reason `no_person` (nobody seen) or `ambiguous` (people seen but never a clear winner). |
| **TRACKING** | Target = the track with `target_id`. **Overlap**: while the target box overlaps another track (IoU > `overlap_iou`) nothing is embedded (the crop holds two people) and the target is marked *suspect*. **Suspect check**: on the first update after they separate the target is embedded immediately (ignoring `verify_period_s`) and compared with the **seed** embeddings; `seed_sim ≥ sim_accept` clears the suspicion, otherwise a fail is counted and the check repeats every `reacquire_period_s` — `reject_checks` fails → LOST (`appearance_mismatch`). **Periodic check**: otherwise every `verify_period_s` the target crop is embedded and compared with the whole gallery; `sim ≥ sim_accept` resets the counter and, if `seed_sim ≥ sim_accept` too, the embedding joins the gallery; `sim < sim_reject` for `reject_checks` checks in a row → LOST (`appearance_mismatch`); in between: counter reset, no learning. Track dropped by the tracker → LOST (`track_lost`). A briefly missed (occluded) target keeps its state until the tracker drops it. |
| **LOST** | Velocity 0. Every `reacquire_period_s`, the confirmed visible tracks that are not known to be someone else are embedded in one call (at most `reacquire_max_candidates`, the biggest boxes first) and compared with the **seeds**. A track is re-acquired when `seed_sim ≥ sim_accept` for `reacquire_hits` consecutive checks **and** it beats the second-best candidate by `reacquire_margin`. → TRACKING with the new track id. After `lost_timeout_s` → IDLE (`lost_timeout`). |

`release` from any state -> IDLE and clears the gallery (and resolves a pending `lock` call with `released`). A new
`lock` while LOCKING or TRACKING restarts from scratch.

### Identity rules: seeds vs gallery

The gallery holds the `seed_size` embeddings taken during LOCKING (never evicted) plus later accepted ones (FIFO).
Similarity is always the **maximum** over a set of rows, so which set is used decides what can go wrong:

| Decision | Compared against | Why |
|---|---|---|
| Keep or reject the target in the periodic TRACKING check | whole gallery | Tolerates pose, scale and lighting change; a wrong accept here only delays LOST |
| Add an embedding to the gallery | **seeds only** | A sample that no longer resembles the person who was locked must not extend the gallery, otherwise the gallery walks away from the guest sample by sample |
| Clear a suspicion after an overlap | **seeds only** | After an ID swap the previous frames are the occluder; only the seeds still describe the guest |
| Re-acquire in LOST | **seeds only** | This grants the lock to a new track id |

In short: **the gallery can only widen what counts as "still the guest", never what counts as "this is the guest"**.
Every embedding in the gallery is within `sim_accept` of a seed, and nothing is learned from a crop that overlaps
another person. The earlier claim that "the gallery cannot drift" was wrong: the seeds do not bound a *maximum* over
the whole gallery, which is why identity-granting decisions now use the seeds alone.

LOST also keeps a set of **known-not-target** track ids: every track seen beside the target (visible at the same time,
not overlapping it) is provably a different person, so it is excluded from re-acquisition and costs no embedding. The
set is dropped on `release`, `lock` and `lost_timeout`. After an `appearance_mismatch` it is reset to the mismatching
id alone, because after a swap the tracker's ids say nothing about who is who.

### Why a bystander cannot become the target

- **Lock needs a clear winner** (`lock_margin`), stable for the whole window; two guests side by side give `ambiguous`, not a coin flip.
- **Identity is only ever granted by the seed embeddings** (see [Identity rules](#identity-rules-seeds-vs-gallery)), which are taken while the guest was the unambiguous winner and are never evicted.
- **No learning from overlapped crops**: a box overlapping another person is not added to the gallery, and no identity decision is taken from it.
- **Crossings are re-checked immediately**: after the target box overlapped someone else, the next clear view is verified against the seeds right away, so an ID swap onto the occluder is caught within `reject_checks` checks instead of waiting for `verify_period_s`.
- **Appearance is checked continuously**: an ID swap (e.g. two people crossing) shows as low similarity and drops to LOST.
- **Re-acquisition is strict**: several consecutive matches against the seeds plus a margin over the second-best person, and people already seen next to the guest are not candidates at all.
- **Velocity is exactly 0 unless TRACKING with a fresh detection**; `unknown` people never produce a command.
- Track ids are never reused, so a new person cannot inherit the target id.

Residual risk: while the target overlaps someone else the tracker alone decides identity, so after a swap the robot can
briefly steer toward the wrong person (until they separate, plus up to `reacquire_period_s × reject_checks`) before
LOST. All thresholds above are untested against real OSNet similarities; see [Tuning](#tuning). Unverified with real
crowds; one more reason this is dry-run only.

### Follow controller

- `wz = −k_yaw · ex`, `ex = (cx − W/2)/(W/2)`; 0 inside `yaw_deadband`. Positive `wz` turns left, so a guest on the right gives `wz < 0`.
- `vx = k_x · (target_height_frac − h/H)`; 0 inside `x_deadband`, **clamped to `[0, max_vx]`** (never reverses toward a guest).
- The node clamps `max_vx ≤ 0.5 m/s` and `max_wz ≤ 0.8 rad/s` whatever the YAML says (warning logged).
- Distance is a monocular proxy (bbox height), no depth.

## Topics and services

| Name | Type | Direction | QoS | Notes |
|---|---|---|---|---|
| `/camera/image_raw` | `sensor_msgs/Image` | in | RELIABLE, KEEP_LAST 5 | `image_topic`; parameter `image_reliability: best_effort` switches it. Reliable by default: a best-effort subscriber was measured to receive **0** of ~1 MB frames here ([perception.md](perception.md)) |
| `/perception/humans/detections` | `vision_msgs/Detection2DArray` | in | RELIABLE, KEEP_LAST 1 | `detections_topic`; header must carry the source image stamp. Newest only (no backlog behind a slow embed). Arrays older than `stale_s` are dropped |
| `/follow/cmd_vel` | `geometry_msgs/Twist` | out | RELIABLE, KEEP_LAST 10 | **Dry run.** At the detection rate, plus zero from a 10 Hz timer when the last processed detection stamp is older than `stale_s` |
| `/follow/state` | `std_msgs/String` (JSON) | out | RELIABLE, KEEP_LAST 10 | 2 Hz and on events: `{state, target_id, similarity, n_people, unpaired, event, reason, embedder}` |
| `/follow/target` | `vision_msgs/Detection2D` | out | RELIABLE, KEEP_LAST 10 | Target box only, `id` = track id; only while TRACKING |
| `/follow/annotated_image` | `sensor_msgs/Image` | out | RELIABLE, KEEP_LAST 1 | Only produced while someone subscribes |
| `/d1_follow/lock` | `std_srvs/Trigger` | service | default | Returns when LOCKING resolves: success `locked target #N`, or failure with reason `no_person` / `ambiguous` / `timeout: no detections` |
| `/d1_follow/release` | `std_srvs/Trigger` | service | default | Always succeeds, `released`; a `lock` call still waiting is answered `released` |

`event` in the state JSON is one of `locked`, `lock_failed`, `lost`, `reacquired`, `lost_timeout`, `released`, or null.
`unpaired` counts detection arrays that found no image for their stamp (a warning naming the image topic and its QoS
is logged at most once per 10 s). Every appearance check also logs one INFO line with the similarity value (numbers
only, at most 1/s): `verify sim=0.812 state=TRACKING`, `suspect ...`, `reacquire ...`.

## Parameters

Node `d1_follow`, defaults from [config/follow.yaml](../ros2_ws/src/d1_follow/config/follow.yaml).

| Parameter | Default | Meaning |
|---|---|---|
| `image_topic` | `/camera/image_raw` | Frames to embed and annotate |
| `image_reliability` | `reliable` | `reliable` or `best_effort` QoS for the image subscription. Use `best_effort` with `d1_camera`'s `rtsp_camera` (it publishes BEST_EFFORT; a reliable subscriber gets nothing and the node warns about unpaired detections) |
| `detections_topic` | `/perception/humans/detections` | Person detections |
| `embedder` | `osnet` | `osnet` or `histogram` (automatic fallback) |
| `device` | `cpu` | Device for OSNet |
| `auto_lock` | `false` | Lock onto the first confirmed person without a service call; retries after a `lock_failed` until the first successful lock |
| `stale_s` | `0.8` | Detection arrays older than this (by header stamp) are dropped and the velocity is zeroed. Covers detector latency (~0.2 s) plus one OSNet call (~0.3 s on CPU) |
| `image_buffer` | `15` | Frames kept to pair with slow detections by stamp |
| `iou_min` | `0.3` | Minimum IoU to associate a detection with a track |
| `min_hits` | `3` | Hits before a track counts as confirmed |
| `max_misses` | `15` | Missed frames before a track is dropped |
| `lock_window_s` | `0.5` | Candidate must stay the clear winner this long |
| `lock_margin` | `1.3` | Winner's score must be at least this multiple of the runner-up |
| `lock_timeout_s` | `5.0` | Give up locking (`no_person` / `ambiguous`) |
| `seed_size` | `3` | Embeddings taken during LOCKING; never evicted, and the only ones that can grant identity |
| `gallery_size` | `10` | Max gallery embeddings |
| `verify_period_s` | `1.0` | Time between appearance checks of the target |
| `sim_accept` | `0.70` | Cosine similarity to accept / re-acquire |
| `sim_reject` | `0.50` | Similarity below this counts as a mismatch in the periodic check |
| `reject_checks` | `2` | Consecutive mismatches (periodic or suspect) before LOST |
| `reacquire_period_s` | `0.5` | Minimum time between re-acquire embeddings in LOST, and between repeated suspect checks |
| `reacquire_max_candidates` | `3` | People embedded per re-acquire check (biggest boxes first) |
| `reacquire_hits` | `3` | Consecutive matching checks to re-acquire |
| `reacquire_margin` | `0.10` | Required similarity lead over the second-best candidate |
| `lost_timeout_s` | `30.0` | LOST -> IDLE |
| `overlap_iou` | `0.1` | Above this IoU with another person the crop is not used (no learning, no check) and the target becomes *suspect* |
| `target_height_frac` | `0.6` | Desired bbox height / image height (follow distance) |
| `k_yaw` | `1.0` | Yaw gain |
| `k_x` | `1.2` | Forward gain |
| `yaw_deadband` | `0.08` | Normalised horizontal error ignored |
| `x_deadband` | `0.05` | Height-fraction error ignored |
| `max_vx` | `0.5` | m/s, clamped to 0.5 by the node |
| `max_wz` | `0.8` | rad/s, clamped to 0.8 by the node |

Launch arguments of [follow.launch.py](../ros2_ws/src/d1_follow/launch/follow.launch.py): `with_perception` (default
`true`: also starts video source and detector), the perception source arguments (`source_type`, `webcam_device`,
`rtsp_url`, `file_path`, `source_params`, `detector_params`, `model_name`, `device`), `auto_lock`, `embedder`, `params`,
`rviz`.

## Run on the laptop (WSL webcam)

```bash
make build                    # once; installs boxmot and bakes the OSNet weights
make up source=wsl-webcam follow=true auto_lock=true rviz=true display=localhost:0.0
make logs
```

`follow=true` makes the container run `follow.launch.py` (which starts perception and owns RViz) instead of
`human_perception.launch.py`. `auto_lock=true` locks onto the first person who appears; without it, lock by hand:

```bash
docker exec -it human_perception bash -lc 'ros2 run d1_follow lock'
docker exec -it human_perception bash -lc 'ros2 run d1_follow release'

docker exec -it human_perception bash -lc 'ros2 topic echo /follow/state'
docker exec -it human_perception bash -lc 'ros2 topic echo /follow/cmd_vel'
```

`make up` variables: `follow`, `auto_lock`, `embedder` (see the Makefile header). `make test` also runs the `d1_follow` unit tests.

### What you should see in RViz

The image panel shows `/follow/annotated_image`:

| Element | Meaning |
|---|---|
| Thick **green** box `TARGET #id sim 0.82` | The locked guest and the latest appearance similarity |
| Thin **grey** box `unknown #id` | Any other person; never steers |
| **Yellow** box `LOCKING #id` | The candidate being locked |
| Top banner | State, target id, dry-run `vx / wz`; a green arrow shows the commanded motion |

Expected: sit in front of the camera -> yellow, then green TARGET. Others walking past stay grey. If someone fully
covers you the state goes to LOST with `vx = wz = 0`; when you reappear you become TARGET again and the other person
does not. `release` -> IDLE, `lock` locks again. (Not yet run end to end.)

## Gateway

`d1_gateway` exposes the two services as intents `follow_lock` and `follow_release` (see [robot_gateway.md](robot_gateway.md)).
`follow_lock` answers `intent_started` immediately and `intent_done` / `intent_failed` (reason from the service, e.g.
`no_person`) when the lock resolves, which can take a few seconds. If the node is down: `intent_failed` with
"follow node not running". The gateway still never sends velocities.

## Tuning

- **`sim_accept` / `sim_reject` must be set from the first live session.** The defaults (0.70 / 0.50) are guesses for
  OSNet x0_25, taken from the literature and never measured here; different people in similar clothing are commonly
  reported around 0.5–0.7, which overlaps the "same person" range, and webcam upper-body crops are far from the
  full-body pedestrians the model was trained on. Procedure: run the session, watch the throttled
  `verify/suspect/reacquire sim=...` INFO lines and `similarity` in `/follow/state` while (a) the guest changes pose
  and comes back after an occlusion and (b) teammates stand in front of the camera; set `sim_accept` below the
  observed same-person range and `sim_reject` above the observed different-person range. **Log the numbers only**
  — never a crop, a frame or an embedding (see [Privacy](#privacy)).
- The `histogram` fallback is a much weaker identity cue (clothing colour only; similar clothing scores high), so its
  thresholds are not the OSNet ones: check `embedder` in `/follow/state` and expect both false accepts and false
  rejects until they are retuned separately.
- `auto_lock` keeps retrying after a `lock_failed`: with two people side by side it logs `lock_failed (ambiguous)`
  about every `lock_timeout_s` until somebody is the clear winner. That is intended; use the `lock` service instead if
  the noise is unwanted.
- `target_height_frac` sets the follow distance: larger = closer. Webcam framing differs from the robot camera, so retune on the robot.
- Gains and deadbands only matter once the output is wired to the robot; keep `max_vx` / `max_wz` at or below the caps.
- `image_buffer` costs memory (full frames; at 1080p about 6 MB each). It must cover the detector latency in frames, otherwise detections are unpaired and cannot be embedded.

## Privacy

Frames, crops and embeddings live in memory only: nothing is written to disk or logged. The annotated image is a live
topic produced only while subscribed, never recorded. Do not record it or `/camera/image_raw` rosbags of real guests
(see [feature_privacy_anonymization.md](feature_privacy_anonymization.md)).

## Limitations and next steps

- Dry run: `/follow/cmd_vel` -> `/cmd_vel` needs the `/cmd_vel` arbitration from [system_architecture.md](system_architecture.md#safety-architecture) first; there is no arbiter yet.
- Not yet integrated with the spa state machine (greet / chat / escort / refuse), and not driven by the frontend beyond the two intents.
- `source=d1-camera` + `follow=true` is not supported by `make up` yet (the entrypoint starts without `d1_follow`).
- OSNet takes about 300 ms per call on CPU (measured in WSL Docker, 3 crops), hence the 1 Hz verify and, in LOST, one
  call of at most `reacquire_max_candidates` crops every `reacquire_period_s` (worst case ~60% of one core for up to
  `lost_timeout_s`); unverified on the Orin.
- Monocular distance proxy only; no obstacle awareness. Following a person through a spa needs Nav2/costmap input before it moves the robot.
- Not tested with a real robot, wet floors, crowds or similar clothing.
