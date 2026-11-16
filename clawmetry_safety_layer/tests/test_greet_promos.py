"""greet(): voice only by default; the vendor shake_hand gesture only with allow_greeting_gesture true AND stationary,
safety NORMAL, nobody within 1.5 m, battery > 30 % (never a robot_cmd publisher). /ask name + lang from the QR
onboarding (greeted once, language sticks). Promotions: one per guided walk, never during a hold, time windows."""

from __future__ import annotations

import datetime as dt
import math
import os
import sys
import time
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
os.environ["CLAWMETRY_OTLP"] = "http://127.0.0.1:1"

import pytest
import yaml
from fake_ros import FakeRos
from test_agibot_d1w_bridge import TeamNode, cfg

from bridge.agibot_d1w_bridge import AgibotD1WBridge, CommandRefused
from concierge.local import GuestSessions
from perception.observation import Observation
from safety.supervisor import SafetySupervisor
from venue.promotions import Promotions


def test_default_config_is_voice_only():
    raw = yaml.safe_load((ROOT / "config/d1w.yaml").read_text())
    assert raw["allow_greeting_gesture"] is False


class Per:
    def __init__(self, obs):
        self.obs = obs
        self.intent = {}

    def latest(self):
        return self.obs


@pytest.fixture
def greeter(monkeypatch):
    monkeypatch.setenv("D1W_TRANSPORT", "mock")
    from bridge import load_config
    from skills.skills import Skills

    def make(allow=True, person=None, degraded=()):
        c = load_config("config/d1w.yaml")
        c["allow_greeting_gesture"] = allow
        sk = Skills(c)
        sup = SafetySupervisor(sk, c)
        sk.perception = Per(Observation(nearest_person_m=person, degraded=list(degraded)))
        time.sleep(0.15)
        made.append(sk)
        return sk, sup

    made: list = []
    yield make
    for sk in made:
        sk.bridge.close()


def test_greet_flag_off_is_voice_only(greeter):
    sk, _ = greeter(allow=False)
    r = sk.greet("Anna", "de")
    assert r.ok and r.extra["gesture"] is None and "allow_greeting_gesture is false" in r.extra["voice_only_because"]
    assert sk.bridge.t.gestures == [] and sk.bridge.t.said[-1].startswith("Grüezi Anna")


def test_greet_gesture_only_when_every_check_passes(greeter):
    sk, sup = greeter(allow=True)
    r = sk.greet("Anna")
    assert r.extra["gesture"] == "shake_hand" and sk.bridge.t.gestures == ["shake_hand"]
    assert sk.bridge.t.said[-1] == "Hello, Anna, welcome to Leukerbad Therme!"
    # a person at 1.2 m -> voice only
    sk.perception.obs = Observation(nearest_person_m=1.2)
    r = sk.greet()
    assert r.extra["gesture"] is None and any("person at 1.2" in w for w in r.extra["voice_only_because"])
    # people detection down -> voice only (can't prove nobody is near)
    sk.perception.obs = Observation(degraded=["l2_people_disabled"])
    assert sk.greet().extra["gesture"] is None
    sk.perception.obs = Observation()
    # safety not NORMAL -> voice only
    sup.fsm.raise_cause("person_near", "L2", "caution", "person at 1.8 m", ttl=5.0)
    assert any("CAUTION" in w for w in sk.greet().extra["voice_only_because"])
    sup.fsm.clear("person_near") if hasattr(sup.fsm, "clear") else None
    # battery 30 % -> voice only
    sk.bridge.t.raw.battery_pct = 30.0
    time.sleep(0.1)
    assert any("battery" in w for w in sk.greet().extra["voice_only_because"])
    assert sk.bridge.t.gestures == ["shake_hand"]  # still only the first one


def test_bridge_refuses_gesture_while_moving_or_without_flag():
    ros = FakeRos()
    team = TeamNode(ros)
    b = AgibotD1WBridge(cfg(transport="ros2"), ros=ros)
    b.connect("", 0)
    time.sleep(0.15)
    with pytest.raises(CommandRefused, match="allow_greeting_gesture"):
        b.shake_hand()
    b.close()
    b = AgibotD1WBridge(cfg(transport="ros2", allow_greeting_gesture=True), ros=ros)
    b.connect("", 0)
    time.sleep(0.15)
    b.move_velocity(0.3, 0, 0)
    with pytest.raises(CommandRefused, match="moving"):
        b.shake_hand()
    b.stop()
    time.sleep(0.1)
    b.shake_hand()  # the fake Trigger answers success=Msg (not True) -> False, but the call went to the service
    assert "/d1_cmd_vel_bridge/shake_hand" in ros.node.clients
    assert not any(t.rstrip("/").endswith(("robot_cmd", "attitude_cmd")) for t in ros.node.pubs)
    b.close()
    team.close()


def test_guest_sessions_greet_once_and_language_sticks():
    g = GuestSessions()
    assert g.turn("s", "Anna", "de") == ("Anna", "de", True)
    assert g.turn("s", None, None) == ("Anna", "de", False)
    assert g.turn("s", "Anna", "fr") == ("Anna", "fr", False)
    assert g.turn("t", None, "it") == (None, "it", False)


def test_promotions_windows_hold_and_once():
    p = Promotions()
    nov_11 = dt.datetime(2026, 11, 15, 11, 0)
    assert [x["id"] for x in p.active(nov_11)] == ["sauna_world", "massage_beauty", "pool_bar", "guest_card"]
    assert "sauna_world" not in [x["id"] for x in p.active(dt.datetime(2026, 7, 15, 11, 0))]  # season
    assert [x["id"] for x in p.active(dt.datetime(2026, 11, 15, 18, 30))] == ["sauna_world", "pool_bar", "guest_card"]
    assert p.active(dt.datetime(2026, 11, 15, 21, 0)) == []
    assert p.for_walk("HOLD", "en", "s", nov_11) is None and p.for_walk("RECOVER", "en", "s", nov_11) is None
    a = p.for_walk("NORMAL", "de", "s", nov_11)
    b = p.for_walk("CAUTION", "de", "s", nov_11)
    assert a["id"] == "sauna_world" and "Saunawelt" in a["text"] and b["id"] == "massage_beauty"


def _blk(**kw):
    return types.SimpleNamespace(**kw)


def test_agent_name_lang_and_one_promo_per_walk(monkeypatch):
    from concierge import agent as agent_mod
    from concierge.agent import Concierge

    calls = []

    class Edge:
        def __init__(self, state="NORMAL"):
            self.st = state

        def call(self, path, body=None):
            calls.append(path)
            if path == "state":
                return {"safety": {"state": self.st}}
            return {"ok": True, "started": True, "message": "guiding"}

    def script():
        return [
            _blk(
                stop_reason="tool_use",
                content=[
                    _blk(type="tool_use", id="a", name="guide_to", input={"poi": "kasse", "lang": "de"}),
                    _blk(type="tool_use", id="b", name="guide_to", input={"poi": "shop", "lang": "de"}),
                ],
            ),
            _blk(stop_reason="end_turn", content=[_blk(type="text", text="Grüezi Anna, folgen Sie mir.")]),
        ]

    class Fake:
        def __init__(self):
            self.req, self.s = [], []
            self.beta = types.SimpleNamespace(messages=types.SimpleNamespace(create=self.create))

        def create(self, **kw):
            self.req.append(kw)
            if not self.s:
                self.s = script()
            return self.s.pop(0)

    monkeypatch.setattr(agent_mod.Promotions, "_now", lambda self: dt.datetime(2026, 11, 15, 11, 0))
    fake = Fake()
    c = Concierge(edge=Edge(), client=fake)
    out = c.ask("Bring mich zur Kasse", lang="de", session="q1", name="Anna")

    def user_texts(req):
        return [m["content"] for m in req["messages"] if m["role"] == "user" and isinstance(m["content"], str)]

    first_user = user_texts(fake.req[0])[0]
    assert first_user.startswith("[guest language: de] [guest name: Anna, greet by name]")
    assert [a["id"] for a in out["actions"] if a["type"] == "promotion"] == ["sauna_world"]  # two guides, one promo
    out2 = c.ask("Und danach?", session="q1")  # no lang, no name: both remembered
    assert user_texts(fake.req[-1])[-1].startswith("[guest language: de] [guest name: Anna, already greeted]")
    assert out2["lang"] == "de"
    held = Concierge(edge=Edge("HOLD"), client=Fake())
    out3 = held.ask("take me to the kasse", session="q2")
    assert not [a for a in out3["actions"] if a["type"] == "promotion"]
    assert math.isfinite(out3["latency_s"])
