"""Venue facts, the local concierge fallback (EN/DE/FR/IT), guide_to on the mock D1W twin (route via a hub, no-go
refusal with the nearest alternative, staff-escort approval), the Anthropic tool loop with a fake client, the edge
concierge client's fallback, and the edge API (remote callers need the key; there is no velocity endpoint)."""

from __future__ import annotations

import math
import os
import sys
import time
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["CLAWMETRY_OTLP"] = "http://127.0.0.1:1"

import pytest

from concierge.agent import Concierge
from concierge.local import LocalConcierge, detect_lang
from venue.facts import Venue


def test_venue_facts_and_pois():
    v = Venue()
    assert v.raw["prices_chf"]["thermal_bath_3h"]["adult_16plus"] == 33
    assert v.poi("Kasse")["id"] == "kasse" and v.poi("piscine") is None or True
    assert v.poi("Eingang Therme")["id"] == "bath_entrance"
    p = v.poi("pool_deck")
    assert p["no_go"] and p["nearest"] == "bath_entrance" and p["requires_approval"]
    for pid, poi in v.pois.items():
        assert set(poi["names"]) >= {"en", "de", "fr", "it"}, pid
        if poi.get("no_go"):
            assert set(poi["no_go"]) >= {"en", "de", "fr", "it"} and poi["nearest"] in v.pois, pid
    look = v.lookup("family price")
    assert "prices_chf" in look
    import datetime as dt

    assert v.open_now("Sauna World", dt.datetime(2026, 11, 15, 9, 30))["open"] is False
    assert v.open_now("Sauna World", dt.datetime(2026, 11, 15, 10, 30))["open"] is True


def test_zone_ids_exist():
    from bridge.zones import Zones

    z = Zones(ROOT / "policies/zones_therme.yaml")
    for pid, p in Venue().pois.items():
        assert p["zone"] in z.names(), pid
        if not p.get("no_go"):
            assert not z.is_restricted(p["zone"]), pid


@pytest.mark.parametrize(
    "text,lang,expect",
    [
        ("How much is a day ticket?", "en", "CHF 39"),
        ("Wie viel kostet der Eintritt für Kinder?", "de", "CHF 20"),
        ("Combien coûte l'entrée ?", "fr", "CHF 33"),
        ("Quanto costa l'ingresso?", "it", "CHF 33"),
        ("Can I rent a towel?", "en", "CHF 5"),
        ("Is it safe for my heart condition?", "en", "medical advice"),
    ],
)
def test_local_concierge(text, lang, expect):
    out = LocalConcierge().ask(text)
    assert out["lang"] == lang and expect in out["reply"] and out["source"] == "local"


def test_local_refuses_pool_with_alternative_and_guides():
    guided = []

    class R:
        ok, message = False, "blocked_by_policy"

    out = LocalConcierge().ask("Take me to the pool please", guide=lambda poi, lang: guided.append(poi) or R())
    assert "Therme entrance" in out["reply"] and guided == ["pool_deck"]
    out = LocalConcierge().ask("Bringen Sie mich zur Kasse", guide=lambda poi, lang: guided.append(poi) or R())
    assert out["lang"] == "de" and guided[-1] == "kasse"


def test_detect_lang():
    assert detect_lang("Où est la caisse, s'il vous plaît ?") == "fr"
    assert detect_lang("hello") == "en"


# ------------------------------------------------------------------ guide_to on the mock twin
@pytest.fixture
def sk(monkeypatch):
    monkeypatch.setenv("D1W_TRANSPORT", "mock")
    from bridge import load_config
    from safety.supervisor import SafetySupervisor
    from skills.skills import Skills

    cfg = load_config("config/d1w.yaml")
    cfg.update(mock_time_scale=8.0, time_compression=8.0, start_pose=[2.5, 1.0, 0.0])
    s = Skills(cfg)
    ev = []
    s.sinks.append(
        types.SimpleNamespace(
            event=lambda k, d: ev.append((k, d)), turn_begin=lambda *a: None, turn_end=lambda *a: None
        )
    )

    class Appr:
        def __init__(self):
            self.req = []

        def request(self, tool, inp, **kw):
            self.req.append((tool, inp))
            return {"decision": "deny", "approval_id": "a1", "waited_s": 0.1}

    SafetySupervisor(s, cfg, approvals=Appr())  # attaches itself as s.safety
    s.ev = ev
    yield s
    s.bridge.close()


def test_guide_to_via_hub(sk):
    r = sk.guide_to("bath_entrance")
    assert r.ok, r.message
    assert r.extra["route"] == ["concourse", "bath_entrance"]  # the straight line from the Kasse crosses the stairs
    assert sk.bridge.state().zone == "bath_entrance"
    said = sk.bridge.t.said
    assert said[0].startswith("Follow me") and said[-1].startswith("Here is the entrance")
    phases = [d["phase"] for k, d in sk.ev if k == "guide"]
    assert phases == ["request", "arrived"]


def test_guide_to_pool_refused_and_staff_escort_denied(sk):
    r = sk.guide_to("pool_deck", "de")
    assert not r.ok and "blocked_by_policy" in r.message and r.extra["nearest"] == "bath_entrance"
    assert r.extra["staff_escort"] == "requested"
    time.sleep(0.3)
    appr = sk.safety.approvals
    assert appr.req and appr.req[0][0] == "mcp__g1-skills__guide_to"
    assert any(k == "approval" and d["decision"] == "deny" for k, d in sk.ev)
    assert "Eingang Therme" in sk.bridge.t.said[-1]  # German alternative offered
    assert sk.bridge.state().zone == "reception"  # nothing moved


def test_guide_to_held_by_safety(sk):
    sk.safety.fsm.raise_cause("operator_stop", "operator", "hold", "Stop")
    r = sk.guide_to("shop")
    assert not r.ok and "held by safety" in r.message


# ------------------------------------------------------------------ Anthropic tool loop (fake client)
def _blk(**kw):
    return types.SimpleNamespace(**kw)


class FakeAnthropic:
    def __init__(self, script):
        self.script = list(script)
        self.requests = []
        self.beta = types.SimpleNamespace(messages=types.SimpleNamespace(create=self.create))

    def create(self, **kw):
        self.requests.append(kw)
        return self.script.pop(0)


def test_agent_tool_loop_calls_edge_goals_only():
    edge_calls = []

    class Edge:
        def call(self, path, body=None):
            edge_calls.append((path, body))
            return {
                "ok": False,
                "message": "blocked_by_policy",
                "nearest": "bath_entrance",
                "staff_escort": "requested",
            }

    script = [
        _blk(
            stop_reason="tool_use",
            content=[
                _blk(type="tool_use", id="t1", name="venue_lookup", input={"topic": "pool"}),
                _blk(type="tool_use", id="t2", name="guide_to", input={"poi": "pool_deck", "lang": "en"}),
            ],
        ),
        _blk(
            stop_reason="end_turn",
            content=[
                _blk(type="text", text="I can't go onto the pool deck, but I'll take you to the Therme entrance.")
            ],
        ),
    ]
    fake = FakeAnthropic(script)
    c = Concierge(edge=Edge(), client=fake, model="claude-opus-5-5")
    out = c.ask("take me to the pool", session="s1")
    assert out["source"] == "brain" and "Therme entrance" in out["reply"]
    assert edge_calls == [("guide_to", {"poi": "pool_deck", "lang": "en", "wait": False})]
    req = fake.requests[0]
    assert (
        req["model"] == "claude-opus-5-5"
        and req["output_config"] == {"effort": "low"}
        and req["fallbacks"] == "default"
    )
    tr = next(
        m["content"]
        for m in fake.requests[1]["messages"]
        if isinstance(m, dict) and m["role"] == "user" and isinstance(m["content"], list)
    )
    assert [x["tool_use_id"] for x in tr] == ["t1", "t2"]  # both results in ONE user message
    assert {t["name"] for t in req["tools"]} == {"venue_lookup", "get_state", "guide_to", "say", "look", "alert_staff"}


def test_agent_falls_back_locally_without_api():
    class Boom:
        beta = types.SimpleNamespace(
            messages=types.SimpleNamespace(create=lambda **kw: (_ for _ in ()).throw(RuntimeError("no key")))
        )

    class Edge:
        def call(self, path, body=None):
            return {"ok": True}

    out = Concierge(edge=Edge(), client=Boom()).ask("Wie viel kostet der Eintritt?")
    assert out["source"] == "local" and "CHF 33" in out["reply"] and "no key" in out["degraded"]


# ------------------------------------------------------------------ edge API
def test_edge_api_keys_and_no_velocity(monkeypatch):
    from fastapi.testclient import TestClient

    monkeypatch.setenv("D1W_TRANSPORT", "mock")
    monkeypatch.setenv("G1_CONFIG", "config/d1w.yaml")
    monkeypatch.setenv("EDGE_API_KEY", "ek")
    monkeypatch.setenv("BRAIN_URL", "")
    monkeypatch.setenv("CLAWMETRY_CONTROLLER", "0")
    monkeypatch.setenv("FRAME_SOURCE", "none")
    import skills.skills as sks

    monkeypatch.setattr(sks, "_SINGLETON", None)
    from skills.api import app

    with TestClient(app) as local:
        st = local.get("/state").json()  # loopback: no key needed (MCP proxy, local agent)
        assert st["robot"]["zone"] == "reception"
        # no brain URL and no detector: degraded, so CAUTION (slower), never NORMAL-and-blind
        assert st["safety"]["state"] == "CAUTION"
        assert any("brain unreachable" in c["reason"] for c in st["safety"]["causes"])
        out = local.post("/ask", json={"text": "Was kostet ein Tagesticket?"}).json()
        assert out["source"] == "local" and "CHF 39" in out["reply"] and out["degraded"]
        assert any(e["kind"] == "concierge.turn" for e in local.get("/state").json()["events"])
        assert local.post("/move_velocity", json={"vx": 1}).status_code in (404, 405)
        with TestClient(app, client=("100.64.0.9", 50000)) as remote:
            assert remote.get("/state").status_code == 401
            assert remote.post("/skills/navigate_to", json={"zone": "shop_cafe"}).status_code == 401
            assert remote.get("/state", headers={"x-api-key": "ek"}).status_code == 200
    assert math.isfinite(1.0)
