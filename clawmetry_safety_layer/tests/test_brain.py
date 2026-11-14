"""Brain relay + console (FastAPI TestClient): keys, frame -> judge -> verdict back to the edge, nothing stored,
the console's login cookie and edge allow-list."""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pytest

pytest.importorskip("cv2")
from fastapi.testclient import TestClient

from brain.relay import Relay, create_app
from perception.sampler import multipart


class FakeJudge:
    url = "http://127.0.0.1:1/v1/chat/completions"
    api_key = ""

    def __init__(self):
        self.calls = 0
        self.failures = 0
        self.last_latency_s = None

    def judge(self, jpeg, go_zone, action):
        self.calls += 1
        return {
            "leaves_go_zone": False,
            "hazards": [],
            "people_within_2m": 1,
            "stop": False,
            "confidence": 0.9,
            "reason": "guest nearby",
            "latency_s": 0.01,
            "go_zone": go_zone,
        }

    def describe(self, jpeg, q="x"):
        return "A sign says Kasse."


JPEG = b"\xff\xd8" + b"\x00" * 100 + b"\xff\xd9"


@pytest.fixture
def brain(monkeypatch, tmp_path):
    monkeypatch.setenv("BRAIN_API_KEY", "bk")
    monkeypatch.setenv("EDGE_API_KEY", "ek")
    monkeypatch.chdir(tmp_path)
    posted = []
    relay = Relay(judge=FakeJudge(), post_edge=posted.append, go_zone="dry floor")
    app = create_app(relay)
    return TestClient(app), relay, posted


def frame(c, meta, key="bk"):
    body, ctype = multipart(
        {"frame": ("image/jpeg", JPEG), "meta": ("application/json", __import__("json").dumps(meta).encode())}
    )
    return c.post("/frame", content=body, headers={"content-type": ctype, "x-api-key": key})


def test_frame_needs_key(brain):
    c, _, _ = brain
    assert frame(c, {}, key="wrong").status_code == 401
    assert c.get("/verdicts").status_code == 401


def test_unflagged_frame_is_not_judged_flagged_is_and_goes_back(brain, tmp_path):
    c, relay, posted = brain
    r = frame(c, {"seq": 1, "zone": "concourse"})
    assert r.status_code == 200 and r.json()["judged"] is False and relay.judge.calls == 0
    r = frame(c, {"seq": 2, "zone": "concourse", "judge": "guide_start", "poi": "bath_entrance"})
    v = r.json()["verdict"]
    assert v["trigger"] == "guide_start" and v["action"] == "forward 2 m at 0.3 m/s toward bath_entrance"
    time.sleep(0.1)
    assert posted and posted[0]["people_within_2m"] == 1
    assert c.get("/verdicts", headers={"x-api-key": "bk"}).json()[-1]["reason"] == "guest nearby"
    assert list(tmp_path.iterdir()) == []  # no frame written anywhere


def test_non_jpeg_refused(brain):
    c, _, _ = brain
    body, ctype = multipart({"frame": ("image/jpeg", b"PNG..."), "meta": ("application/json", b"{}")})
    assert c.post("/frame", content=body, headers={"content-type": ctype, "x-api-key": "bk"}).status_code == 400


def test_describe(brain):
    c, _, _ = brain
    body, ctype = multipart({"frame": ("image/jpeg", JPEG), "meta": ("application/json", b'{"question": "signs?"}')})
    r = c.post("/describe", content=body, headers={"content-type": ctype, "x-api-key": "bk"})
    assert r.json()["description"] == "A sign says Kasse."


def test_console_login_and_allow_list(brain, monkeypatch):
    c, _, _ = brain
    assert "BRAIN_API_KEY" in c.get("/").text  # login form
    assert c.post("/login", data={"key": "nope"}, follow_redirects=False).status_code == 401
    r = c.post("/login", data={"key": "bk"}, follow_redirects=False)
    assert (
        r.status_code == 303
        and "httponly" in r.headers["set-cookie"].lower()
        and "samesite=strict" in r.headers["set-cookie"].lower()
    )
    c.cookies.set("brain_key", "bk")
    assert "Mission Control" in c.get("/").text
    assert "pool_deck" in c.get("/api/zones").json()["zones"]
    calls = []
    import brain.console as con

    monkeypatch.setattr(
        con, "edge_call", lambda path, body=None, timeout=10.0: calls.append((path, body)) or (200, {"ok": True})
    )
    assert c.post("/api/edge/move_velocity", json={"vx": 1}).status_code == 404  # never: velocities don't cross
    assert c.post("/api/edge/guide_to", json={"poi": "kasse"}).json() == {"ok": True}
    assert c.post("/api/edge/approve", json={}).status_code == 200
    assert calls == [("guide_to", {"poi": "kasse"}), ("approve", {"by": "mission-control"})]
