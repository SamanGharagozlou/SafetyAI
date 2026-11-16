# Human Follow (lock-on + follow) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** New ROS 2 package `d1_follow` that locks onto the connected guest, labels everyone else `unknown`, and publishes a dry-run follow velocity, testable with the laptop webcam in RViz.

**Architecture:** `d1_follow/target_follower` consumes the existing `/perception/humans/detections` + `/camera/image_raw`, runs an IoU tracker, an OSNet re-ID gallery lock state machine and a P follow controller (all ROS-free, unit tested), and publishes `/follow/*`. Lock is triggered by Trigger services (CLI, gateway intents, or `auto_lock`).

**Tech Stack:** ROS 2 Humble (rclpy, vision_msgs, std_srvs), numpy<2, OpenCV, boxmot 19.0.0 (OSNet x0_25 msmt17), pytest, Docker image `human_perception:humble`.

Spec: `docs/superpowers/specs/2026-11-15-human-follow-design.md`.

## Global Constraints

- Never publish `/cmd_vel`. Output only `/follow/cmd_vel`. `vx ∈ [0, 0.5]` m/s, `|wz| ≤ 0.8` rad/s.
- numpy must stay < 2 in the image (`pip3 install -c` constraint with `numpy<2`). `boxmot==19.0.0` pinned.
- No frames, crops or embeddings written to disk or logs.
- ROS-free modules (`tracker`, `embedder`, `target_lock`, `follow_controller`, `annotate`) import no `rclpy`.
- Python style: match `human_perception` (flake8 line length 99, single quotes, short *why* comments).
- Gateway never sends raw velocities; `estop` not exposed via new intents.
- Unit tests run in the image: `docker run --rm -v $PWD/ros2_ws/src/d1_follow:/pkg -w /pkg --entrypoint bash human_perception:humble -c 'python3 -m pytest -q test'` (abbrev. **RUN_TESTS** below).

## File Structure

```
ros2_ws/src/d1_follow/
  package.xml, setup.py, setup.cfg, resource/d1_follow
  d1_follow/__init__.py
  d1_follow/tracker.py            Track, IouTracker
  d1_follow/embedder.py           HistogramEmbedder, OsnetEmbedder, make_embedder()
  d1_follow/target_lock.py        State, LockParams, LockOutput, TargetLock
  d1_follow/follow_controller.py  FollowParams, FollowController
  d1_follow/annotate.py           draw()
  d1_follow/target_follower_node.py  ROS node (main)
  d1_follow/lock_cli.py           lock_main(), release_main()
  config/follow.yaml
  launch/follow.launch.py
  rviz/follow.rviz
  test/conftest.py, test_tracker.py, test_embedder.py, test_target_lock.py,
       test_follow_controller.py, test_annotate.py
ros2_ws/src/d1_gateway/d1_gateway/{protocol.py,ros_robot.py}, test/test_gateway.py   (modify)
docker/Dockerfile, docker/entrypoint.sh, docker-compose.yml, Makefile                 (modify)
docs/human_follow.md (new), docs/robot_gateway.md, AGENTS.md                          (modify)
```

---

### Task 1: Package scaffold + IoU tracker

**Files:** Create `package.xml`, `setup.py`, `setup.cfg`, `resource/d1_follow`, `d1_follow/__init__.py`, `d1_follow/tracker.py`, `test/conftest.py`, `test/test_tracker.py`.

**Interfaces — Produces:**
- `Track` dataclass: `id:int, box:np.ndarray(4,) xyxy float, score:float, hits:int, misses:int`; property `confirmed` (`hits >= min_hits`, `min_hits` stored on track); property `visible` (`misses == 0`).
- `iou(a, b) -> float` for xyxy boxes.
- `IouTracker(iou_min=0.3, min_hits=3, max_misses=15)`; `update(boxes:(N,4), scores:(N,)) -> list[Track]` returns **all live tracks** (visible ones have `misses == 0`); `tracks` property same list.
- Association: predict each track box as `box + vel`; greedy by descending IoU with `iou >= iou_min`; matched → `vel = 0.5*vel + 0.5*(new-old)`, `box=new`, `hits+=1`, `misses=0`; unmatched tracks `misses+=1`, removed when `misses > max_misses`; unmatched detections → new track with next id (ids start at 1, never reused).

- [ ] **Step 1: scaffold** — `package.xml` (format 3, ament_python, exec_depend rclpy std_msgs std_srvs sensor_msgs vision_msgs geometry_msgs launch launch_ros human_perception python3-numpy python3-opencv; test_depend python3-pytest; license BSD-3-Clause), `setup.py` modelled on human_perception (data_files launch/config/rviz globs; entry points `target_follower = d1_follow.target_follower_node:main`, `lock = d1_follow.lock_cli:lock_main`, `release = d1_follow.lock_cli:release_main`), `setup.cfg` (script_dir/install_scripts `$base/lib/d1_follow`), `test/conftest.py` inserting package dir on `sys.path`.

- [ ] **Step 2: failing tests** `test/test_tracker.py`:

```python
import numpy as np
from d1_follow.tracker import IouTracker, iou


def B(*xyxy):
    return np.array([xyxy], dtype=np.float32)


def S(n=1):
    return np.ones(n, dtype=np.float32)


def test_iou():
    assert iou(np.array([0, 0, 10, 10]), np.array([0, 0, 10, 10])) == 1.0
    assert iou(np.array([0, 0, 10, 10]), np.array([20, 20, 30, 30])) == 0.0
    assert abs(iou(np.array([0, 0, 10, 10]), np.array([5, 0, 15, 10])) - 1 / 3) < 1e-6


def test_id_persists_and_confirms():
    t = IouTracker(min_hits=3)
    ids = []
    for k in range(4):
        tr = t.update(B(100 + 5 * k, 100, 200 + 5 * k, 400), S())
        assert len(tr) == 1
        ids.append(tr[0].id)
    assert len(set(ids)) == 1
    assert t.tracks[0].confirmed


def test_two_people_keep_ids_when_moving():
    t = IouTracker()
    boxes = np.array([[0, 0, 100, 300], [400, 0, 500, 300]], dtype=np.float32)
    first = {int(tr.box[0]) // 200: tr.id for tr in t.update(boxes, S(2))}
    for k in range(1, 6):
        moved = boxes + np.array([[10 * k, 0, 10 * k, 0], [-10 * k, 0, -10 * k, 0]], np.float32)
        tr = t.update(moved, S(2))
        by_side = {0 if tr_.box[0] < 250 else 2: tr_.id for tr_ in tr if tr_.visible}
        assert by_side == first


def test_track_dropped_after_max_misses_and_ids_not_reused():
    t = IouTracker(max_misses=2)
    a = t.update(B(0, 0, 100, 300), S())[0].id
    empty = np.zeros((0, 4), np.float32)
    for _ in range(2):
        assert len(t.update(empty, S(0))) == 1
    assert t.update(empty, S(0)) == []
    b = t.update(B(0, 0, 100, 300), S())[0].id
    assert b != a


def test_missed_track_is_not_visible():
    t = IouTracker()
    t.update(B(0, 0, 100, 300), S())
    tr = t.update(np.zeros((0, 4), np.float32), S(0))
    assert len(tr) == 1 and not tr[0].visible
```

- [ ] **Step 3:** RUN_TESTS → FAIL (`ModuleNotFoundError: d1_follow.tracker`).
- [ ] **Step 4:** implement `tracker.py` per Interfaces.
- [ ] **Step 5:** RUN_TESTS → PASS. Commit `feat(d1_follow): package scaffold and IoU tracker`.

---

### Task 2: Embedders

**Files:** Create `d1_follow/embedder.py`, `test/test_embedder.py`.

**Interfaces — Produces:**
- `HistogramEmbedder().embed(image_bgr, boxes_xyxy:(N,4)) -> np.ndarray (N, 2*H*S) float32`, each row L2-normalised; HSV 2D histogram (16 hue × 8 sat bins) of the upper half and lower half of the clipped crop, concatenated. Empty/degenerate crop → zero row. `name = 'histogram'`.
- `OsnetEmbedder(device='cpu')`: lazily imports `boxmot.reid.core.reid.ReID`, `ReID(device=device)`; `embed()` calls `self._m(image, boxes.astype(np.float32))`, re-normalises rows. `name = 'osnet'`. N==0 → `(0, 512)`.
- `make_embedder(kind:str, device:str, log=print) -> embedder`: `'osnet'` tries OsnetEmbedder, on any import/load exception logs a warning and returns HistogramEmbedder; `'histogram'` returns HistogramEmbedder.

- [ ] **Step 1: failing tests**

```python
import numpy as np
import pytest
from d1_follow.embedder import HistogramEmbedder, make_embedder


def person(color_top, color_bottom, h=200, w=80):
    img = np.zeros((h, w, 3), np.uint8)
    img[: h // 2] = color_top
    img[h // 2:] = color_bottom
    return img


def scene():
    img = np.zeros((300, 400, 3), np.uint8)
    img[50:250, 10:90] = person((0, 0, 200), (200, 0, 0))      # red top, blue bottom (BGR)
    img[50:250, 150:230] = person((0, 0, 200), (200, 0, 0))    # same clothes
    img[50:250, 300:380] = person((0, 200, 0), (0, 200, 200))  # green top, yellow bottom
    boxes = np.array([[10, 50, 90, 250], [150, 50, 230, 250], [300, 50, 380, 250]], np.float32)
    return img, boxes


def test_histogram_shapes_and_norm():
    img, boxes = scene()
    f = HistogramEmbedder().embed(img, boxes)
    assert f.shape[0] == 3 and f.dtype == np.float32
    assert np.allclose(np.linalg.norm(f, axis=1), 1.0, atol=1e-5)


def test_histogram_discriminates():
    img, boxes = scene()
    f = HistogramEmbedder().embed(img, boxes)
    assert f[0] @ f[1] > 0.95
    assert f[0] @ f[2] < 0.5


def test_degenerate_and_empty():
    img, _ = scene()
    e = HistogramEmbedder()
    assert e.embed(img, np.zeros((0, 4), np.float32)).shape[0] == 0
    f = e.embed(img, np.array([[500, 500, 600, 600]], np.float32))   # fully outside
    assert np.allclose(f, 0)


def test_make_embedder_falls_back():
    msgs = []
    e = make_embedder('osnet', 'cpu', log=msgs.append)
    assert e.name == 'osnet' or (e.name == 'histogram' and msgs)
    assert make_embedder('histogram', 'cpu').name == 'histogram'
```

- [ ] **Step 2:** RUN_TESTS → FAIL. **Step 3:** implement. **Step 4:** RUN_TESTS → PASS.
- [ ] **Step 5:** Commit `feat(d1_follow): histogram and OSNet embedders`.

---

### Task 3: TargetLock state machine

**Files:** Create `d1_follow/target_lock.py`, `test/test_target_lock.py`.

**Interfaces — Consumes:** `Track`, `iou` (Task 1). **Produces:**
- `State(str, Enum)`: `IDLE, LOCKING, TRACKING, LOST` (values lowercase strings? — use uppercase names, values `'IDLE'` etc.).
- `LockParams` dataclass: `lock_window_s=0.5, lock_margin=1.3, lock_timeout_s=5.0, seed_size=3, gallery_size=10, verify_period_s=1.0, sim_accept=0.70, sim_reject=0.50, reject_checks=2, reacquire_period_s=0.3, reacquire_hits=3, reacquire_margin=0.10, lost_timeout_s=30.0, overlap_iou=0.1`.
- `LockOutput` dataclass: `state:State, target:Track|None (visible target only), candidate_id:int|None, labels:dict[int,str] ('target'|'candidate'|'unknown'), similarity:float|None, event:str|None, reason:str|None`.
- `TargetLock(params=LockParams(), auto_lock=False)`: `request_lock(now)`, `release()`, `update(now, tracks, frame_w, frame_h, embed) -> LockOutput`, properties `state`, `target_id`.
- `embed: Callable[[list[Track]], np.ndarray | None]` → rows L2-normalised, aligned with input list; `None` means "image unavailable, skip check".
- `reason` persists until the next transition; `event` is set only on the update where the transition happens.
- Events: `'locked'`, `'lock_failed'` (reason `'no_person'` or `'ambiguous'`), `'lost'` (reason `'track_lost'` or `'appearance_mismatch'`), `'reacquired'`, `'lost_timeout'`, `'released'`.

Behaviour (from spec):
- Proximity score for confirmed visible tracks: `area_frac * max(0, 1 - 2*|cx/W - 0.5|)`.
- LOCKING: best must beat runner-up by `lock_margin` (×). Candidate change → reset `cand_since` and seeds. Seed the candidate every `lock_window_s/seed_size`. Done when `now - cand_since >= lock_window_s` and seeds == `seed_size` → TRACKING, event `locked`. `now - lock_started > lock_timeout_s` → IDLE, `lock_failed` (`no_person` if no confirmed track was ever seen during LOCKING, else `ambiguous`).
- `auto_lock`: while IDLE with `auto_pending` and a confirmed visible track → `request_lock(now)`. `auto_pending` cleared on `locked`; `release()` also clears it.
- TRACKING: target track missing from `tracks` → LOST (`track_lost`). Target visible and `now - last_verify >= verify_period_s` → embed; `sim = max(gallery @ e)`. `sim >= sim_accept` and no other visible track with `iou > overlap_iou` → append to gallery (seeds kept; non-seed FIFO to `gallery_size`). `sim < sim_reject` → `reject_count += 1`, reaching `reject_checks` → LOST (`appearance_mismatch`); otherwise `reject_count = 0`.
- LOST: every `reacquire_period_s` embed all confirmed visible tracks (one call). Per-id `hits[id]` += 1 if `sim >= sim_accept` else reset to 0. Best by sim must lead second best by `reacquire_margin` (second = 0 when alone). Best with `hits >= reacquire_hits` → TRACKING with new `target_id`, event `reacquired`. `now - lost_since > lost_timeout_s` → IDLE, `lost_timeout`, gallery cleared.

- [ ] **Step 1: failing tests** (fake embedder: each track id maps to a fixed identity vector)

```python
import numpy as np
from d1_follow.target_lock import LockParams, State, TargetLock
from d1_follow.tracker import Track

W, H = 640, 480
ID_A = np.eye(8, dtype=np.float32)[0]          # the guest
ID_B = np.eye(8, dtype=np.float32)[1]          # a bystander
LOOKALIKE = (0.55 * ID_A + 0.835 * ID_B)        # cos(A, lookalike) ~0.55 < sim_accept
LOOKALIKE /= np.linalg.norm(LOOKALIKE)


def T(tid, box, misses=0, hits=5):
    return Track(id=tid, box=np.array(box, np.float32), score=0.9, hits=hits, misses=misses)


class FakeEmbed:
    def __init__(self, identity):
        self.identity, self.calls = identity, 0

    def __call__(self, tracks):
        self.calls += 1
        return np.stack([self.identity[t.id] for t in tracks]) if tracks else np.zeros((0, 8))


CENTER = [220, 40, 420, 470]     # big, centred: the guest at the laptop
SIDE = [520, 200, 600, 400]      # small, at the edge: a teammate


def run(lock, frames, embed, t0=0.0, dt=0.1):
    out, t = None, t0
    for tracks in frames:
        out = lock.update(t, tracks, W, H, embed)
        t += dt
    return out, t


def locked_lock(identity=None):
    identity = identity or {1: ID_A, 2: ID_B}
    lock, emb = TargetLock(LockParams()), FakeEmbed(identity)
    lock.request_lock(0.0)
    out, t = run(lock, [[T(1, CENTER), T(2, SIDE)]] * 10, emb)
    assert out.state == State.TRACKING and lock.target_id == 1
    return lock, emb, t


def test_locks_onto_dominant_person_and_labels_others_unknown():
    lock, emb, t = locked_lock()
    out = lock.update(t, [T(1, CENTER), T(2, SIDE)], W, H, emb)
    assert out.labels == {1: 'target', 2: 'unknown'}
    assert out.target.id == 1


def test_lock_fails_with_no_person():
    lock = TargetLock(LockParams(lock_timeout_s=1.0))
    lock.request_lock(0.0)
    out, _ = run(lock, [[]] * 15, FakeEmbed({}))
    assert out.state == State.IDLE and lock.target_id is None


def test_lock_ambiguous_when_two_equal_people():
    lock = TargetLock(LockParams(lock_timeout_s=1.0))
    lock.request_lock(0.0)
    a, b = [100, 40, 300, 470], [340, 40, 540, 470]
    out, _ = run(lock, [[T(1, a), T(2, b)]] * 15, FakeEmbed({1: ID_A, 2: ID_B}))
    assert out.state == State.IDLE


def test_auto_lock():
    lock = TargetLock(LockParams(), auto_lock=True)
    out, _ = run(lock, [[T(1, CENTER)]] * 10, FakeEmbed({1: ID_A}))
    assert out.state == State.TRACKING


def test_occlusion_lost_then_reacquired_as_same_person_not_bystander():
    lock, _, t = locked_lock()
    # guest fully occluded: tracker drops track 1; bystander 2 stays in view
    out = lock.update(t, [T(2, SIDE)], W, H, FakeEmbed({2: ID_B}))
    assert out.state == State.LOST and out.target is None
    # bystander moves to centre while guest is hidden: must never become target
    emb = FakeEmbed({2: ID_B, 3: ID_A})
    out, t = run(lock, [[T(2, CENTER)]] * 20, emb, t0=t + 0.1)
    assert out.state == State.LOST and out.labels == {2: 'unknown'}
    # guest reappears with a new track id 3
    out, t = run(lock, [[T(2, SIDE), T(3, CENTER)]] * 15, emb, t0=t)
    assert out.state == State.TRACKING and lock.target_id == 3
    assert out.labels == {2: 'unknown', 3: 'target'}


def test_lookalike_never_accepted():
    lock, _, t = locked_lock()
    lock.update(t, [], W, H, FakeEmbed({}))           # lost
    emb = FakeEmbed({4: LOOKALIKE})
    out, _ = run(lock, [[T(4, CENTER)]] * 50, emb, t0=t + 0.1)
    assert out.state == State.LOST


def test_id_switch_detected_by_appearance():
    lock, _, t = locked_lock()
    # tracker wrongly hands id 1 to the bystander
    emb = FakeEmbed({1: ID_B})
    out, _ = run(lock, [[T(1, CENTER)]] * 30, emb, t0=t)
    assert out.state == State.LOST and out.reason == 'appearance_mismatch'


def test_lost_timeout_and_release():
    lock, _, t = locked_lock()
    lock.update(t, [], W, H, FakeEmbed({}))
    out = lock.update(t + 31.0, [], W, H, FakeEmbed({}))
    assert out.state == State.IDLE and out.event == 'lost_timeout'
    lock2, _, _ = locked_lock()
    lock2.release()
    assert lock2.state == State.IDLE and lock2.target_id is None


def test_embed_budget_while_tracking():
    lock, _, t = locked_lock()
    emb = FakeEmbed({1: ID_A, 2: ID_B})
    run(lock, [[T(1, CENTER), T(2, SIDE)]] * 50, emb, t0=t)     # 5 s at 10 Hz
    assert emb.calls <= 6


def test_embed_none_is_skipped():
    lock, _, t = locked_lock()
    out = lock.update(t + 2.0, [T(1, CENTER)], W, H, lambda tracks: None)
    assert out.state == State.TRACKING
```

- [ ] **Step 2:** RUN_TESTS → FAIL. **Step 3:** implement per behaviour list. **Step 4:** RUN_TESTS → PASS.
- [ ] **Step 5:** Commit `feat(d1_follow): target lock state machine with re-ID gallery`.

---

### Task 4: Follow controller

**Files:** Create `d1_follow/follow_controller.py`, `test/test_follow_controller.py`.

**Interfaces — Produces:** `FollowParams(target_height_frac=0.6, k_yaw=1.0, k_x=1.2, yaw_deadband=0.08, x_deadband=0.05, max_vx=0.5, max_wz=0.8)`; `FollowController(params).compute(box_xyxy | None, frame_w, frame_h) -> (vx, wz)`. `ex = (cx - W/2)/(W/2)`; `wz = clamp(-k_yaw*ex, ±max_wz)` if `|ex| >= yaw_deadband` else 0; `e = target_height_frac - h/H`; `vx = clamp(k_x*e, 0, max_vx)` if `|e| >= x_deadband` else 0. `None` → `(0.0, 0.0)`.

- [ ] **Step 1: failing tests**

```python
from d1_follow.follow_controller import FollowController, FollowParams

W, H = 640, 480
C = FollowController(FollowParams())


def box(cx, h):
    return [cx - 40, H / 2 - h / 2, cx + 40, H / 2 + h / 2]


def test_none_is_zero():
    assert C.compute(None, W, H) == (0.0, 0.0)


def test_centered_at_target_distance_is_zero():
    assert C.compute(box(W / 2, 0.6 * H), W, H) == (0.0, 0.0)


def test_turns_toward_person():
    _, wz_right = C.compute(box(W * 0.9, 0.6 * H), W, H)
    _, wz_left = C.compute(box(W * 0.1, 0.6 * H), W, H)
    assert wz_right < 0 < wz_left


def test_far_person_forward_close_person_never_reverse():
    vx_far, _ = C.compute(box(W / 2, 0.2 * H), W, H)
    vx_close, _ = C.compute(box(W / 2, 0.95 * H), W, H)
    assert vx_far > 0 and vx_close == 0.0


def test_clamped():
    p = FollowParams(k_x=100, k_yaw=100)
    vx, wz = FollowController(p).compute(box(W, 0.05 * H), W, H)
    assert vx == p.max_vx and wz == -p.max_wz
```

- [ ] **Step 2–4:** RUN_TESTS FAIL → implement → PASS. **Step 5:** Commit `feat(d1_follow): dry-run follow controller`.

---

### Task 5: Annotation

**Files:** Create `d1_follow/annotate.py`, `test/test_annotate.py`.

**Interfaces — Consumes:** `Track`, `LockOutput`, `State`. **Produces:** `draw(frame_bgr, tracks, out: LockOutput, vx, wz) -> np.ndarray` (copy; never modifies input). Visible tracks only. Colours BGR: target `(0,200,0)` thickness 4 label `TARGET #id sim 0.82`; candidate `(0,220,255)` thickness 3 `LOCKING #id`; unknown `(160,160,160)` thickness 1 `unknown #id`. Top banner (dark rect, height 32): `state  target #id  vx 0.20 wz -0.30 (dry-run)` plus an arrow from banner centre proportional to `wz` (left/right) and `vx`.

- [ ] **Step 1: failing tests**

```python
import numpy as np
from d1_follow.annotate import draw
from d1_follow.target_lock import LockOutput, State
from d1_follow.tracker import Track


def test_draw_colours_and_copy():
    img = np.zeros((480, 640, 3), np.uint8)
    tr = [Track(1, np.array([100, 100, 300, 400], np.float32), 0.9, 5, 0),
          Track(2, np.array([400, 100, 500, 300], np.float32), 0.9, 5, 0)]
    out = LockOutput(State.TRACKING, tr[0], None, {1: 'target', 2: 'unknown'}, 0.8, None, None)
    res = draw(img, tr, out, 0.2, -0.3)
    assert img.sum() == 0 and res.shape == img.shape
    assert tuple(res[250, 100]) == (0, 200, 0)          # target left edge green
    assert tuple(res[200, 400]) == (160, 160, 160)      # unknown left edge grey
```

- [ ] **Step 2–4:** FAIL → implement → PASS. **Step 5:** Commit `feat(d1_follow): RViz annotation`.

---

### Task 6: ROS node, CLI, launch, config, RViz

**Files:** Create `d1_follow/target_follower_node.py`, `d1_follow/lock_cli.py`, `config/follow.yaml`, `launch/follow.launch.py`, `rviz/follow.rviz`.

**Interfaces — Consumes:** all Tasks 1–5; `human_perception.image_codec.image_msg_to_bgr`, `fill_image_msg`. **Produces:**
- Node name `d1_follow`; services `/d1_follow/lock`, `/d1_follow/release` (`std_srvs/Trigger`).
- Topics: `/follow/cmd_vel` (Twist), `/follow/state` (String JSON `{"state","target_id","similarity","n_people","event","reason","embedder"}`), `/follow/target` (vision_msgs/Detection2D, id=`str(track_id)`), `/follow/annotated_image` (Image, only when subscribed).
- Parameters (all in `config/follow.yaml` under `d1_follow: ros__parameters:`): `image_topic=/camera/image_raw`, `detections_topic=/perception/humans/detections`, `embedder=osnet`, `device=cpu`, `auto_lock=false`, `stale_s=0.5`, `image_buffer=10`, plus every `LockParams`/`FollowParams`/tracker field with the spec defaults.

Node behaviour:
- Image sub: BEST_EFFORT, KEEP_LAST 5 → ring buffer `{stamp_ns: msg}` (max `image_buffer`).
- Detections sub (RELIABLE, depth 10) → boxes from `bbox.center.position.x/y, size_x/size_y` → tracker.update → under `self._mx` lock: `out = lock.update(now, tracks, W, H, embed)` where `embed` decodes the paired image lazily (`None` if missing). Image size from paired image, else last known size (default 640×480).
- `vx, wz = controller.compute(out.target.box if out.target else None, W, H)`; publish cmd_vel; store `last_det_time`.
- 10 Hz timer: if `now - last_det_time > stale_s` publish zero Twist. 2 Hz timer: publish state JSON (also on event).
- `lock` service: ReentrantCallbackGroup; calls `lock.request_lock(now)`, waits (≤ `lock_timeout_s + 1`) on a `threading.Event` set when update emits `locked` / `lock_failed`; responds `success`, `message = 'locked target #id'` or the reason. `release` → `lock.release()`, success.
- `MultiThreadedExecutor(num_threads=3)`; strip user site-packages like human_perception nodes (`PYTHONNOUSERSITE` already set by launch).
- Log every event at INFO (`locked target #3`, `lost (track_lost)`, …). Never log images.

`lock_cli.py`: `lock_main()` / `release_main()` create node, wait ≤ 3 s for service, call, print `message`, exit code 0/1.

`follow.launch.py` args: `with_perception` (true), `source_type, webcam_device, rtsp_url, file_path, source_params, detector_params, model_name, device` (forwarded to `human_perception.launch.py` with `rviz:=false`), `auto_lock` (false), `embedder` (osnet), `params` (follow.yaml), `rviz` (false; starts rviz2 with `rviz/follow.rviz`). Sets `PYTHONNOUSERSITE=1`.

`follow.rviz`: copy of `human_perception.rviz` with Image display named `Follow` on `/follow/annotated_image`, window 960×640.

- [ ] **Step 1:** write files. **Step 2:** `colcon build --packages-select d1_follow` inside the image (volume-mount) → succeeds; `ros2 launch d1_follow follow.launch.py --show-args` lists args.
- [ ] **Step 3:** smoke test in container with `source_type:=file` on a synthetic generated video is not required; verify node starts and `/follow/state` publishes `IDLE` with `ros2 topic echo --once /follow/state`.
- [ ] **Step 4:** Commit `feat(d1_follow): target_follower node, lock CLI, launch and RViz config`.

---

### Task 7: Gateway intents

**Files:** Modify `d1_gateway/protocol.py` (INTENTS), `d1_gateway/ros_robot.py` (follow services), `test/test_gateway.py`.

- [ ] **Step 1: failing test** append to `test_gateway.py`:

```python
def test_follow_intents():
    async def t(url, core):
        async with websockets.connect(url) as ws:
            hello = json.loads(await ws.recv())
            assert {'follow_lock', 'follow_release'} <= set(hello['intents'])
            for name in ('follow_lock', 'follow_release'):
                await ws.send(json.dumps({'type': 'intent', 'id': name, 'name': name}))
                m, _ = await _until(ws, lambda m: m['type'] == 'intent_done' and m['id'] == name)
    run(_with_gateway(t))
```

- [ ] **Step 2:** `pytest -p no:anyio ros2_ws/src/d1_gateway/test -q` → FAIL (unknown intent).
- [ ] **Step 3:** add to `INTENTS`: `'follow_lock': 'lock onto the guest in front of the camera (d1_follow)'`, `'follow_release': 'forget the locked guest (d1_follow)'`. In `RosRobot.__init__`: `p('follow_node', 'd1_follow')`; `self.follow = {'follow_lock': create_client(Trigger, f'/{fn}/lock'), 'follow_release': create_client(Trigger, f'/{fn}/release')}`. In `intent()`: `cli = self.follow.get(name) or self.trig[SERVICE_FOR[name]]`; unavailable follow service → `intent_failed` reason `'follow node not running'`.
- [ ] **Step 4:** tests PASS. **Step 5:** Commit `feat(d1_gateway): follow_lock / follow_release intents`.

---

### Task 8: Docker / Makefile integration

**Files:** Modify `docker/Dockerfile`, `docker/entrypoint.sh`, `docker-compose.yml`, `Makefile`.

- [ ] **Step 1:** Dockerfile after ML stack: `RUN echo 'numpy<2' > /tmp/np.txt && pip3 install --default-timeout=120 -c /tmp/np.txt "boxmot==19.0.0"`; add `COPY ros2_ws/src/d1_follow/ src/d1_follow/`; after model preload: `RUN python3 -c "from boxmot.reid.core.reid import ReID; ReID(device='cpu')"` (bakes OSNet weights).
- [ ] **Step 2:** compose env: `FOLLOW: ${FOLLOW:-false}`, `AUTO_LOCK: ${AUTO_LOCK:-false}`, `EMBEDDER: ${EMBEDDER:-osnet}`. Makefile vars `follow ?= false`, `auto_lock ?= false`, `embedder ?= osnet`, passed in `COMPOSE_ENV`; help lines; `test` target also builds/tests `d1_follow`.
- [ ] **Step 3:** entrypoint: if `FOLLOW == true` → same `args` array, plus `auto_lock`, `embedder`, `rviz`, then `exec ros2 launch d1_follow follow.launch.py "${args[@]}"` (the `rviz` arg goes to follow launch, perception rviz off).
- [ ] **Step 4:** `make build` → success; `make test` → human_perception + d1_follow tests pass.
- [ ] **Step 5:** Commit `build: d1_follow in the perception image (boxmot OSNet), make up follow=true`.

---

### Task 9: Docs

- [ ] Create `docs/human_follow.md`: purpose, topics/services table, params, state machine, how to run on WSL (`make up source=wsl-webcam follow=true auto_lock=true rviz=true display=localhost:0.0`, `docker exec human_perception bash -lc 'ros2 run d1_follow lock'`), tuning (`sim_accept`/`sim_reject` per embedder), status *dry-run, unverified on robot*.
- [ ] Update `AGENTS.md` repo map + docs index, `docs/robot_gateway.md` intents list.
- [ ] Commit `docs: human follow`.

---

### Task 10: Live WSL verification (with the user)

- [ ] `make up source=wsl-webcam follow=true auto_lock=true rviz=true display=localhost:0.0`; `make logs` shows `locked target #N`.
- [ ] Scenario checklist from the spec (lock, bystanders grey, occlusion → LOST + zero cmd, reappear → TARGET, release/lock).
- [ ] `ros2 topic echo /follow/cmd_vel` shows sane signs (person right → wz < 0).
- [ ] Tune thresholds in `config/follow.yaml` if needed; commit.
