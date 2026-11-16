"""Link watchdog (edge side): heartbeat to the brain (GET {BRAIN_URL}/health with X-Api-Key) and to the ClawMetry
control plane (the local sync daemon's heartbeat file, ~/.clawmetry/sync.heartbeat, refreshed while it reaches the
cloud). Flags: brain_ok, control_plane_ok, rtt_s. On a change it calls on_change(name, ok, detail).

Link loss never makes the robot faster or less constrained: brain lost -> judge_unavailable -> CAUTION; control plane
lost -> policy on_control_plane_loss (hold_at_waypoint default | continue_caution); the journal keeps buffering
locally either way (ClawMetry's sync daemon uploads it when the link returns).
"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.request
from pathlib import Path


class LinkMonitor:
    def __init__(
        self,
        brain_url: str | None,
        api_key: str = "",
        heartbeat_file: str | Path | None = None,
        period_s: float = 2.0,
        brain_timeout_s: float = 2.0,
        control_plane_max_age_s: float = 60.0,
        on_change=None,
        probe=None,
    ):
        self.brain_url = (brain_url or "").rstrip("/")
        self.key = api_key
        hb = (
            heartbeat_file
            or os.environ.get("CLAWMETRY_HEARTBEAT")
            or str(Path.home() / ".clawmetry" / "sync.heartbeat")
        )
        self.heartbeat = Path(hb)
        self.period_s = period_s
        self.brain_timeout_s = brain_timeout_s
        self.cp_max_age = control_plane_max_age_s
        self.on_change = on_change or (lambda name, ok, detail: None)
        self._probe = probe or self._http
        self.brain_ok: bool | None = None
        self.control_plane_ok: bool | None = None
        self.rtt_s: float | None = None
        self.detail: dict = {}
        self._fails = 0
        self._stop = threading.Event()

    def _http(self) -> dict:
        req = urllib.request.Request(self.brain_url + "/health", headers={"x-api-key": self.key})
        with urllib.request.urlopen(req, timeout=self.brain_timeout_s) as r:
            return json.loads(r.read() or b"{}")

    def check_brain(self) -> bool:
        if not self.brain_url:
            return False
        t0 = time.time()
        try:
            self._probe()
            self.rtt_s = round(time.time() - t0, 3)
            self._fails = 0
            return True
        except Exception as e:  # noqa: BLE001
            self._fails += 1
            self.detail["brain_error"] = f"{e.__class__.__name__}: {e}"[:160]
            return self._fails < 2 and bool(self.brain_ok)  # one missed beat is not a lost link

    def check_control_plane(self) -> bool:
        try:
            age = time.time() - self.heartbeat.stat().st_mtime
        except OSError:
            self.detail["control_plane_error"] = f"no {self.heartbeat}"
            return False
        self.detail["control_plane_age_s"] = round(age, 1)
        return age <= self.cp_max_age

    def tick(self):
        for name, ok in (("brain", self.check_brain()), ("control_plane", self.check_control_plane())):
            prev = getattr(self, f"{name}_ok")
            setattr(self, f"{name}_ok", ok)
            if prev is None or prev != ok:
                self.on_change(name, ok, dict(self.detail))

    def start(self):
        def loop():
            while not self._stop.is_set():
                try:
                    self.tick()
                except Exception as e:  # noqa: BLE001
                    print(f"[link] tick failed: {e}")
                self._stop.wait(self.period_s)

        threading.Thread(target=loop, daemon=True, name="link-watchdog").start()
        return self

    def close(self):
        self._stop.set()

    def snapshot(self) -> dict:
        return {
            "brain_ok": self.brain_ok,
            "control_plane_ok": self.control_plane_ok,
            "rtt_s": self.rtt_s,
            **self.detail,
        }
