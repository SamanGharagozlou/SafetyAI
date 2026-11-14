"""Perception loop (edge, CPU): frame source + detections -> proximity, boundary, scene -> one Observation for the
safety FSM, the monitor and the trace; the sampler forwards frames to the brain's Cosmos judge.

    p = Perception(cfg, bridge, zones).start()
    p.listeners.append(fn)        # fn(Observation), called at ~5 Hz
    p.latest()                    # newest Observation

camera_off zones (zones file) are honoured: while the robot stands in one, no frame is read, nothing is detected,
classified or sent; the Observation says camera_off=True.
"""

from __future__ import annotations

import os
import threading
import time

from leukerbad_g1 import settings

from .boundary import FloorTransitionDetector, make_boundary
from .camera import Camera
from .detections import LocalYolo, TeamDetectorClient
from .observation import Observation
from .proximity import persons_from, summarise
from .sampler import FrameSampler
from .scene import SceneClassifier
from .sources import make_source


class Perception:
    def __init__(self, cfg: dict, bridge=None, zones=None, source=None, detector=None, sampler=None, scene=None):
        pc = cfg.get("perception") or {}
        self.cfg, self.pc = cfg, pc
        self.bridge, self.zones = bridge, zones
        self.cam = Camera.from_cfg(pc.get("camera"))
        self.person_h = float(pc.get("person_height_m", 1.70))
        self.src = source or make_source(cfg)
        kind = os.environ.get("FRAME_SOURCE") or pc.get("source", "none")
        url = os.environ.get("TEAM_DETECTOR_URL") or pc.get("team_detector_url", "http://127.0.0.1:8090")
        self.team = (
            detector
            if detector is not None
            else (TeamDetectorClient(url) if kind == "team" or pc.get("use_team_detector") else None)
        )
        self.local = (
            LocalYolo(os.environ.get("YOLO_MODEL") or pc.get("yolo_model", "yolo11n.pt"))
            if pc.get("local_yolo")
            else None
        )
        self.boundary = make_boundary(cfg)
        self.scene = scene or SceneClassifier()
        self.scene_period = float(pc.get("scene_period_s", 0.5))
        self._scene_t = 0.0
        self._scene_last = ("unknown", 0.0)
        sp = pc.get("sampler") or {}
        brain = settings.get("BRAIN_URL")
        self.sampler = sampler or (
            FrameSampler(
                brain,
                settings.get("BRAIN_API_KEY"),
                float(sp.get("fps", 2.0)),
                int(sp.get("max_w", 640)),
                int(sp.get("max_h", 360)),
                int(sp.get("jpeg_quality", 70)),
            )
            if brain and pc.get("send_frames", True)
            else None
        )
        self.intent: dict = {}  # set by guide_to: {"action": "forward 2 m at 0.3 m/s toward bath_entrance", "poi": ...}
        self.listeners: list = []
        self.judge_requeue = None  # callable(trigger): put a judge request back when its frame could not be sent
        self.judge_flag = None  # callable -> trigger name when the next frame should be judged (safety supervisor)
        self._obs = Observation()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self.period = 1.0 / float(pc.get("hz", 5.0))

    def start(self):
        self.src.start()
        if self.team is not None:
            self.team.start()
        threading.Thread(target=self._loop, daemon=True, name="perception").start()
        return self

    def close(self):
        self._stop.set()
        self.src.close()
        if self.team is not None:
            self.team.close()

    def latest(self) -> Observation:
        with self._lock:
            return self._obs

    def _loop(self):
        while not self._stop.is_set():
            t0 = time.time()
            try:
                obs = self.step()
                for fn in list(self.listeners):
                    try:
                        fn(obs)
                    except Exception as e:  # noqa: BLE001
                        print(f"[perception] listener failed: {e}")
            except Exception as e:  # noqa: BLE001  (perception must keep running; degraded flags say what broke)
                print(f"[perception] step failed: {e.__class__.__name__}: {e}")
            self._stop.wait(max(0.0, self.period - (time.time() - t0)))

    def _pose(self):
        if self.bridge is None:
            return None
        try:
            return self.bridge.state()
        except Exception:  # noqa: BLE001
            return None

    def step(self) -> Observation:
        s = self._pose()
        zone = s.zone if s is not None else "unknown"
        camera_off = bool(self.zones and zone in self.zones.zones and self.zones.zones[zone].get("camera_off"))
        if camera_off:
            obs = Observation(camera_off=True, source=self.src.name, degraded=["camera_off_zone"])
            self._publish(obs)
            return obs
        frame, ts, seq = self.src.read()
        obs = Observation(ts=ts or time.time(), frame_seq=seq, source=self.src.name)
        if frame is None:
            obs.degraded.append("no_frame")
        else:
            obs.height, obs.width = frame.shape[:2]
        # detections: the team's detector, else the local fallback, else none (explicit degraded flag)
        dets: list[dict] = []
        w, h = obs.width, obs.height
        if self.team is not None:
            d, meta, stale = self.team.get()
            obs.detector_age_s = self.team.age_s()
            if not stale:
                dets, obs.detector = d, "team"
                w, h = int(meta.get("width") or w), int(meta.get("height") or h)
            else:
                obs.degraded.append("detector_stale")
        if obs.detector == "none" and self.local is not None and frame is not None and self.local.available():
            dets, obs.detector = self.local.detect(frame), "local_yolo"
        if obs.detector == "none":
            obs.degraded.append("l2_people_disabled")
        obs.detections = dets
        obs.persons = persons_from(dets, w, h, self.cam, self.person_h)
        obs.nearest_person_m, obs.people_within_2m, obs.child_near = summarise(obs.persons)
        if frame is not None:
            obs.boundary = self.boundary.detect(frame)
            if isinstance(self.boundary, FloorTransitionDetector) and self.boundary.model is None:
                obs.degraded.append("boundary_uncalibrated")
            now = time.time()
            if now - self._scene_t >= self.scene_period:
                self._scene_t = now
                self._scene_last = self.scene.classify(frame)
                if self.scene.error:
                    obs.degraded.append("scene_unavailable")
            obs.scene, obs.scene_conf = self._scene_last
            if self.sampler is not None and s is not None:
                trig = self.judge_flag() if callable(self.judge_flag) else None
                sent = self.sampler.offer(
                    frame,
                    {
                        "ts": obs.ts,
                        "seq": seq,
                        "pose": [round(s.x, 3), round(s.y, 3), round(s.yaw, 3)],
                        "zone": zone,
                        "judge": trig,  # None = just a frame; a trigger name = please judge this one
                        **self.intent,
                    },
                    force=trig is not None,
                )
                if trig is not None and not sent and callable(self.judge_requeue):
                    self.judge_requeue(trig)  # a frame was still in flight: ask again on the next tick
        if self.bridge is not None:
            self.bridge.person_distance_m = obs.nearest_person_m
        self._publish(obs)
        return obs

    def _publish(self, obs: Observation):
        with self._lock:
            self._obs = obs
