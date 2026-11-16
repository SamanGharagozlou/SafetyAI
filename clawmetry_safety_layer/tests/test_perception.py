"""Edge perception on synthetic frames (CPU, no camera, no network): floor-transition boundary, colour markers,
AprilTag boards, camera geometry, person proximity, the team detector client, the frame sampler and the pipeline."""

from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

from perception.boundary import (  # noqa: E402
    AprilTagBoardDetector,
    ColourMarkerDetector,
    FloorModel,
    FloorTransitionDetector,
)
from perception.camera import Camera  # noqa: E402
from perception.detections import TeamDetectorClient  # noqa: E402
from perception.pipeline import Perception  # noqa: E402
from perception.proximity import persons_from, summarise  # noqa: E402
from perception.sampler import FrameSampler, to_jpeg  # noqa: E402
from perception.scene import SceneClassifier  # noqa: E402
from perception.sources import FrameSource  # noqa: E402

RNG = np.random.default_rng(7)


def floor(bgr, h, w, noise=12):
    img = np.empty((h, w, 3), np.int16)
    img[:] = bgr
    img += RNG.integers(-noise, noise + 1, size=img.shape, dtype=np.int16)
    return np.clip(img, 0, 255).astype(np.uint8)


WOOD, WET, TILE = (40, 90, 150), (170, 120, 60), (200, 200, 205)  # BGR


def model():
    return FloorModel.fit(
        {"wood": [floor(WOOD, 40, 80)], "wet_deck": [floor(WET, 40, 80)], "concourse_tile": [floor(TILE, 40, 80)]}
    )


def test_camera_rows():
    cam = Camera(height_m=0.3, pitch_deg=-10, vfov_deg=60)
    d_bottom = cam.row_to_distance(719, 720)
    assert 0.6 < d_bottom < 1.0  # 20 deg below the horizon at 0.3 m
    assert cam.row_to_distance(100, 720) is None  # above the horizon (camera tilted up)
    assert cam.row_to_distance(600, 720) > d_bottom  # higher rows are farther


def test_floor_transition_to_wet_deck(tmp_path):
    m = model()
    p = tmp_path / "floor.json"
    m.save(p)
    m2 = FloorModel.load(p)
    assert set(m2.classes) == {"wood", "wet_deck", "concourse_tile"}
    h, w = 360, 640
    img = floor((90, 90, 90), h, w)  # walls
    img[int(h * 0.6) : int(h * 0.8)] = floor(WET, int(h * 0.8) - int(h * 0.6), w)  # wet deck ahead
    img[int(h * 0.8) :] = floor(WOOD, h - int(h * 0.8), w)  # wood under the robot
    det = FloorTransitionDetector(m2, Camera(0.3, -10, 60))
    b = det.detect(img)
    assert b.floor_here == "wood" and b.floor_ahead == "wet_deck" and b.no_go_ahead
    assert b.ahead_m is not None and 0.8 < b.ahead_m < 3.0
    same = floor(WOOD, h, w)
    b2 = det.detect(same)
    assert b2.floor_here == "wood" and b2.ahead_m is None and not b2.no_go_ahead


def test_floor_transition_uncalibrated_is_silent():
    b = FloorTransitionDetector(None, Camera()).detect(floor(WOOD, 360, 640))
    assert b.ahead_m is None and b.confidence == 0.0


def test_floor_model_refuses_unknown_class():
    with pytest.raises(ValueError):
        FloorModel.fit({"tape": [floor(WOOD, 10, 10)]})


def test_colour_marker_cone():
    img = floor(TILE, 360, 640)
    cv2.rectangle(img, (300, 250), (330, 300), (0, 120, 255), -1)  # an orange cone
    b = ColourMarkerDetector(Camera(0.3, -10, 60)).detect(img)
    assert b.no_go_ahead and b.ahead_m is not None and b.ahead_m > 0.5
    assert ColourMarkerDetector(Camera()).detect(floor(TILE, 360, 640)).ahead_m is None


def test_apriltag_board_fix():
    d = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_APRILTAG_36h11)
    tag = cv2.aruco.generateImageMarker(d, 3, 120)
    img = np.full((480, 640, 3), 255, np.uint8)
    img[180:300, 260:380] = cv2.cvtColor(tag, cv2.COLOR_GRAY2BGR)
    b = AprilTagBoardDetector(Camera(vfov_deg=60), {3: "pillar"}, tag_size_m=0.16).detect(img)
    assert b.fix is not None and b.fix["landmark"] == "pillar" and b.fix["tag_id"] == 3
    assert 0.5 < b.fix["range_m"] < 2.0


def box_at(cam, d, height, h, u=900, w_px=80):
    """A person standing d m away, `height` m tall, as the camera would box them (feet row from the geometry)."""
    import math

    f = cam.focal_px(h)
    foot = h / 2 + f * math.tan(math.atan(cam.height_m / d) - math.radians(cam.pitch_deg))
    return {"class": "person", "conf": 0.9, "box": [u, foot - f * height / d, u + w_px, foot]}


def test_proximity_distances_and_child():
    cam = Camera(height_m=0.3, pitch_deg=-10, vfov_deg=60)
    h, w = 1080, 1920
    adult_3m = box_at(cam, 3.0, 1.7, h)
    child = box_at(cam, 1.8, 1.1, h, u=400)
    legs = {"class": "person", "conf": 0.7, "box": [100, 0, 600, 1079]}  # cut at both edges: very near
    shins = {"class": "person", "conf": 0.7, "box": [700, 900, 760, 1079]}  # feet below the frame: short box, NEAR
    assert persons_from([shins], w, h, cam)[0].distance_m <= cam.lowest_floor_row_distance(h) + 0.01
    chair = {"class": "chair", "conf": 0.9, "box": [0, 0, 10, 10]}
    ps = persons_from([adult_3m, child, legs, chair], w, h, cam)
    assert len(ps) == 3
    assert abs(ps[0].distance_m - 3.0) < 0.05 and not ps[0].child
    assert abs(ps[1].distance_m - 1.8) < 0.05 and ps[1].child
    assert ps[2].distance_m == 0.8
    nearest, within, child_near = summarise(ps)
    assert nearest == 0.8 and within == 2 and child_near


def test_team_detector_staleness():
    frames = iter(range(1000))
    state = {"frame": 1, "down": False}

    def fetch():
        if state["down"]:
            raise ConnectionError("detector down")
        return {
            "ts": time.time(),
            "frame": state["frame"],
            "width": 1920,
            "height": 1080,
            "detections": [{"class": "person", "conf": 0.9, "box": [0, 0, 1, 1]}],
        }

    c = TeamDetectorClient("http://team:8090", stale_s=0.3, fetch=fetch)
    c.poll_once()
    dets, meta, stale = c.get()
    assert not stale and meta["width"] == 1920 and dets
    time.sleep(0.35)
    c.poll_once()  # same frame number: the detector is frozen
    assert c.get()[2]
    state["frame"] = next(frames) + 2
    c.poll_once()
    assert not c.get()[2]
    state["down"] = True
    time.sleep(0.35)
    c.poll_once()
    assert c.get()[2] and "detector down" in c.error


def test_sampler_size_rate_camera_off_and_no_disk(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    got = []
    gate = threading.Event()

    def post(body, ctype):
        got.append((body, ctype))
        gate.wait(1.0)
        return {}

    s = FrameSampler("http://brain:8780", "k", fps=5, post=post)
    big = floor(TILE, 1080, 1920)
    assert not s.offer(big, {"zone": "pool_deck"}, camera_off=True)
    assert s.offer(big, {"zone": "concourse", "intent": "x"})
    assert not s.offer(big, {}, force=True)  # one in flight: dropped, not queued
    assert s.dropped == 1
    gate.set()
    time.sleep(0.2)
    body, ctype = got[0]
    assert ctype.startswith("multipart/form-data")
    jpg = body[body.index(b"\xff\xd8") : body.rindex(b"\xff\xd9") + 2]
    img = cv2.imdecode(np.frombuffer(jpg, np.uint8), cv2.IMREAD_COLOR)
    assert img.shape[1] <= 640 and img.shape[0] <= 360
    assert b'"intent": "x"' in body
    assert list(tmp_path.iterdir()) == []  # nothing written


def test_to_jpeg_keeps_aspect():
    j = to_jpeg(floor(TILE, 720, 960))
    img = cv2.imdecode(np.frombuffer(j, np.uint8), cv2.IMREAD_COLOR)
    assert img.shape[:2] == (360, 480)


def test_scene_smoothing():
    sc = SceneClassifier(smooth_s=1.0)
    k, _ = sc.update({"corridor": 0.9, "stairs": 0.1}, now=0.0)
    assert k == "corridor"
    k, _ = sc.update({"corridor": 0.1, "stairs": 0.9}, now=0.2)  # one flicker frame does not flip it
    assert k == "corridor"
    for t in (0.6, 1.0, 1.4, 1.8):
        k, _ = sc.update({"corridor": 0.1, "stairs": 0.9}, now=t)
    assert k == "stairs"


class OneFrame(FrameSource):
    name = "test"

    def __init__(self, img):
        super().__init__()
        self._put(img)

    def start(self):
        return self


class Det:
    def __init__(self, dets, stale=False):
        self.dets, self.stale = dets, stale

    def start(self):
        return self

    def close(self):
        pass

    def age_s(self):
        return 0.1

    def get(self):
        return self.dets, {"width": 640, "height": 360}, self.stale


class NoScene:
    error = "scene_unavailable (test)"

    def classify(self, f):
        return "unknown", 0.0


def test_pipeline_observation_and_camera_off(tmp_path):
    from bridge.zones import Zones

    zones = Zones(ROOT / "policies/zones_therme.yaml")

    class B:
        person_distance_m = None
        zone = "concourse"

        def state(self):
            from bridge.base import RobotState

            return RobotState(x=9.0, y=2.0, zone=self.zone)

    b = B()
    cfg = {
        "perception": {
            "source": "none",
            "camera": {"vfov_deg": 60},
            "send_frames": False,
            "boundary": {"model": str(tmp_path / "none.json")},
        }
    }
    person = box_at(Camera(vfov_deg=60), 1.5, 1.7, 360, u=300, w_px=40)
    p = Perception(cfg, b, zones, source=OneFrame(floor(TILE, 360, 640)), detector=Det([person]), scene=NoScene())
    o = p.step()
    assert o.detector == "team" and o.people_within_2m == 1 and abs(o.nearest_person_m - 1.5) < 0.05
    assert b.person_distance_m == o.nearest_person_m
    assert "boundary_uncalibrated" in o.degraded and "scene_unavailable" in o.degraded
    json.dumps(o.dict())  # serialisable, no pixels
    b.zone = "pool_deck"
    o = p.step()
    assert o.camera_off and o.detections == [] and o.boundary is None
    b.zone = "concourse"
    p.team = Det([], stale=True)
    o = p.step()
    assert "detector_stale" in o.degraded and "l2_people_disabled" in o.degraded
