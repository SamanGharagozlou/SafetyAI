"""Edge runtime for the D1 Ultra-W: the safety supervisor, perception, link watchdog, voice and the extra HTTP routes,
added to the skills API (skills/api.py) when the body config has a `safety:` section (config/d1w.yaml).

    G1_CONFIG=config/d1w.yaml python -m skills.edge        # = make edge; binds BIND_HOST:8765 (never 0.0.0.0)

Routes (X-Api-Key = EDGE_API_KEY on every request that does not come from this machine; see require_key):
  GET  /state         robot + safety FSM + observation summary + link + transport health + last verdicts
  GET  /observation   newest perception Observation (no pixels)
  GET  /events        server-sent events: every trace event (fsm.transition, person_near, judge_verdict, ...)
  POST /verdict       the brain relay posts Cosmos verdicts here
  POST /ask           {text, lang?, name?, session?} -> {reply, actions}: the concierge (brain, else the local venue
                      fallback). name / lang come from the team's QR onboarding; both stick for the session
  POST /greet         {name?, lang?}: say hello; shake_hand only if allow_greeting_gesture and every check passes
  POST /say           {text}: /say topic for the team's voice stack + local TTS
  POST /guide_to      {poi}: the guide skill (same local policy as the MCP tool)
  POST /confirm       a person confirmed a load / hand-off (pick / place on the quadruped)
  POST /approve /deny resume after a policy hold (console; ClawMetry's queue is the other path)
  POST /reset         recover into the go zone + re-arm (same as ClawMetry Reset)
  POST /refix         {landmark}: re-zero odometry drift at a named landmark
Remote callers send goals only. There is no velocity endpoint, and there never will be: velocity commands do not
cross the internet.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import math
import queue
import threading
import time
from collections import deque

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from leukerbad_g1 import settings

LOOPBACK = ("127.0.0.1", "::1", "localhost", "testclient")


def _is_local(host: str | None) -> bool:
    if not host:
        return False
    if host in LOOPBACK:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def require_key(request: Request, env: str = "EDGE_API_KEY") -> None:
    """Loopback callers (the MCP server in proxy mode, the local agent) pass; everyone else needs X-Api-Key."""
    if _is_local(request.client.host if request.client else None):
        return
    if not settings.key_ok(request.headers.get("x-api-key"), env):
        raise HTTPException(401, "X-Api-Key required")


class EventHub:
    """A Skills trace sink: keeps the last 300 events and fans them out to SSE subscribers."""

    def __init__(self, keep: int = 300):
        self.recent: deque = deque(maxlen=keep)
        self.subs: list[queue.Queue] = []
        self.lock = threading.Lock()

    def _push(self, ev: dict):
        with self.lock:
            self.recent.append(ev)
            for q in list(self.subs):
                try:
                    q.put_nowait(ev)
                except queue.Full:
                    pass

    def event(self, kind, data):
        self._push({"ts": round(time.time(), 3), "kind": kind, **_jsonable(data)})

    def turn_begin(self, skill, args, rationale, s0):
        self._push({"ts": round(time.time(), 3), "kind": "turn.begin", "skill": skill, "args": _jsonable(args)})

    def turn_end(self, skill, r, outcome, dev, s_end, tr=None):
        self._push(
            {
                "ts": round(time.time(), 3),
                "kind": "turn.end",
                "skill": skill,
                "outcome": outcome,
                "message": r.message[:200],
            }
        )

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=500)
        with self.lock:
            self.subs.append(q)
        return q

    def unsubscribe(self, q):
        with self.lock:
            if q in self.subs:
                self.subs.remove(q)


def _jsonable(d):
    try:
        return json.loads(json.dumps(d, default=str))
    except (TypeError, ValueError):
        return {"repr": str(d)[:300]}


class EdgeRuntime:
    def __init__(self, skills, start_threads: bool = True):
        from perception.pipeline import Perception
        from safety.approvals import Approvals
        from safety.link import LinkMonitor
        from safety.supervisor import SafetySupervisor
        from skills.voice import Voice

        self.sk = skills
        cfg = skills.cfg
        self.hub = EventHub()
        skills.sinks.append(self.hub)
        appr_url = settings.get("CLAWMETRY_APPROVALS_URL", "http://127.0.0.1:8900")
        self.approvals = Approvals(appr_url, session_id=f"leukerbad-{skills.bridge.body}") if appr_url else None
        self.supervisor = SafetySupervisor(skills, cfg, approvals=self.approvals)
        self.voice = Voice(cfg.get("voice") or {})
        if hasattr(skills.bridge, "speech_hooks"):
            skills.bridge.speech_hooks.append(self.voice.speak)
        self.perception = Perception(cfg, skills.bridge, skills.zones)
        self.perception.listeners.append(self.supervisor.on_observation)
        skills.perception = self.perception  # guide_to sets the intent the judge sees
        if hasattr(self.perception.src, "on_event"):  # a recorded video as the camera: journal its timeline
            self.perception.src.on_event = lambda kind, **d: skills.emit("camera.timeline", event=kind, **d)
        self.perception.judge_flag = self.supervisor.take_judge_flag
        self.perception.judge_requeue = self.supervisor.request_judge
        self.link = LinkMonitor(
            settings.get("BRAIN_URL"), settings.get("BRAIN_API_KEY"), on_change=self.supervisor.on_link
        )
        self.concierge = None  # lazily: concierge.client.ConciergeClient (brain first, local venue fallback)
        if start_threads:
            self.perception.start()
            self.link.start()

    def close(self):
        self.perception.close()
        self.link.close()

    def look(self, question: str | None = None) -> dict:
        obs = self.perception.latest()
        out = {
            "scene": obs.scene,
            "people_within_2m": obs.people_within_2m,
            "detections": [d.get("class") for d in obs.detections][:12],
            "camera_off": obs.camera_off,
            "description": None,
        }
        if obs.camera_off:
            out["description"] = "camera is off in this zone"
            return out
        frame, _, _ = self.perception.src.read()
        brain = settings.get("BRAIN_URL")
        if frame is None or not brain:
            return out
        try:
            import urllib.request

            from perception.sampler import multipart, to_jpeg

            body, ctype = multipart(
                {
                    "frame": ("image/jpeg", to_jpeg(frame)),
                    "meta": ("application/json", json.dumps({"question": question or ""}).encode()),
                }
            )
            req = urllib.request.Request(
                brain.rstrip("/") + "/describe",
                data=body,
                method="POST",
                headers={"content-type": ctype, "x-api-key": settings.get("BRAIN_API_KEY")},
            )
            with urllib.request.urlopen(req, timeout=12.0) as r:
                out["description"] = json.loads(r.read()).get("description")
        except Exception as e:  # noqa: BLE001
            out["description_error"] = f"{e.__class__.__name__}: {e}"[:160]
        return out

    def get_concierge(self):
        if self.concierge is None:
            from concierge.client import ConciergeClient

            self.concierge = ConciergeClient(self.sk, self)
        return self.concierge

    def state(self) -> dict:
        s = self.sk.bridge.state()
        b = self.sk.bridge
        obs = self.perception.latest()
        return {
            "ts": round(time.time(), 3),
            "robot": s.dict(),
            "paused": self.sk._pause.is_set(),
            "safety": _jsonable(self.supervisor.snapshot()),
            "observation": obs.summary(),
            "link": self.link.snapshot(),
            "transport": b.health() if hasattr(b, "health") else {"transport": b.body},
            "verdicts": list(self.supervisor.verdicts)[-5:],
            "escort_pending": None if not self.sk.escort_pending else {"poi": self.sk.escort_pending["poi"]},
            "events": [e for e in list(self.hub.recent)[-40:] if e.get("kind") not in ("predict",)][-15:],
            "sampler": None
            if self.perception.sampler is None
            else {
                "sent": self.perception.sampler.sent,
                "dropped": self.perception.sampler.dropped,
                "errors": self.perception.sampler.errors,
                "rtt_s": self.perception.sampler.last_rtt_s,
            },
            "zones": self.sk.policy.describe_zones(),
            "clawmetry": {
                "robot_id": getattr(_ctl(), "robot_id", None),
                "run_id": getattr(getattr(_ctl(), "journal", None), "run_id", None),
                "dashboard": settings.get("CLAWMETRY_DASHBOARD_URL", ""),
            },
        }


def _ctl():
    from skills import api

    return api._CTL


RUNTIME: EdgeRuntime | None = None


def attach(skills) -> EdgeRuntime | None:
    """Called from the skills API lifespan; only bodies whose config has a `safety:` section get the edge runtime."""
    global RUNTIME
    if not skills.cfg.get("safety"):
        return None
    RUNTIME = EdgeRuntime(skills)
    return RUNTIME


def detach():
    global RUNTIME
    if RUNTIME is not None:
        RUNTIME.close()
        RUNTIME = None


def rt() -> EdgeRuntime:
    if RUNTIME is None:
        raise HTTPException(404, "edge runtime not active (body config has no safety: section)")
    return RUNTIME


# ---------------------------------------------------------------------------------------------------- routes
router = APIRouter()


class Ask(BaseModel):
    text: str
    lang: str | None = None
    session: str | None = None
    name: str | None = None  # from the team's QR onboarding: greeted once, never sent anywhere but the brain


class GreetB(BaseModel):
    name: str | None = None
    lang: str = "en"


class SayB(BaseModel):
    text: str


class Poi(BaseModel):
    poi: str
    lang: str = "en"
    wait: bool = True  # False: start the guide in the background and return at once (the concierge does this)
    rationale: str | None = None


class Who(BaseModel):
    by: str = "console"


class Landmark(BaseModel):
    landmark: str


@router.get("/state")
def state(request: Request):
    require_key(request)
    return rt().state()


@router.get("/observation")
def observation(request: Request):
    require_key(request)
    return rt().perception.latest().dict()


@router.post("/verdict")
def verdict(request: Request, v: dict):
    require_key(request)
    rt().supervisor.on_verdict(v)
    return {"ok": True, "state": rt().supervisor.fsm.state}


@router.post("/ask")
def ask(request: Request, b: Ask):
    require_key(request)
    return rt().get_concierge().ask(b.text, b.lang, b.session, name=b.name)


@router.post("/greet")
def greet(request: Request, b: GreetB):
    require_key(request)
    return rt().sk.greet(b.name, b.lang).dict()


@router.post("/say")
def say(request: Request, b: SayB):
    require_key(request)
    return rt().sk.say(b.text).dict()


@router.post("/guide_to")
def guide_to(request: Request, b: Poi):
    require_key(request)
    sk = rt().sk
    sk.set_rationale(b.rationale)
    if not b.wait:
        p = sk.venue().poi(b.poi)
        if p is None or p.get("no_go"):  # refusals answer at once, nothing to wait for
            return sk.guide_to(b.poi, b.lang).dict()
        threading.Thread(target=sk.guide_to, args=(b.poi, b.lang), daemon=True, name="guide").start()
        return {"ok": True, "skill": "guide_to", "message": f"guiding to {p['id']}", "started": True, "poi": p["id"]}
    return sk.guide_to(b.poi, b.lang).dict()


class Alert(BaseModel):
    reason: str


@router.post("/alert")
def alert(request: Request, b: Alert):
    """alert_staff: a ClawMetry trace event (journal) the operators see; also spoken so the guest knows."""
    require_key(request)
    r = rt()
    r.sk.emit("alert_staff", reason=b.reason[:300], zone=r.sk.bridge.state().zone)
    return {"ok": True}


@router.get("/pois")
def pois(request: Request, lang: str = "en"):
    require_key(request)
    return rt().sk.venue().list_pois(lang)


@router.post("/look")
def look(request: Request, b: dict | None = None):
    """What the robot sees now: scene class, detections and, when the brain is reachable, a Cosmos description of one
    fresh frame (sign text included). The frame is sent once and dropped; nothing in a camera_off zone."""
    require_key(request)
    return rt().look((b or {}).get("question"))


@router.post("/confirm")
def confirm(request: Request):
    require_key(request)
    c = getattr(rt().sk.bridge, "confirm", None)
    if c is None:
        raise HTTPException(404, "this body has nothing to confirm")
    c()
    return {"ok": True}


@router.post("/approve")
def approve(request: Request, b: Who):
    require_key(request)
    if _decide_escort("allow", b.by):
        return {"ok": True, "decided": "staff_escort"}
    ok = rt().supervisor.approve(f"console:{b.by}"[:80])
    return {"ok": ok, "decided": "resume_after_hold", "safety": _jsonable(rt().supervisor.snapshot())}


@router.post("/deny")
def deny(request: Request, b: Who):
    require_key(request)
    if _decide_escort("deny", b.by):
        return {"ok": True, "decided": "staff_escort"}
    rt().supervisor.deny(f"console:{b.by}"[:80])
    return {"ok": True, "decided": "resume_after_hold", "safety": _jsonable(rt().supervisor.snapshot())}


def _decide_escort(decision: str, by: str) -> bool:
    """Approve / Deny decide a pending staff-escort request first (no ClawMetry Guard rule matched), else a resume."""
    pend = rt().sk.escort_pending
    if not pend or pend["event"].is_set() or pend["decision"] is not None:
        return False
    pend["decision"], pend["by"] = decision, f"console:{by}"[:80]
    pend["event"].set()
    return True


@router.post("/reset")
def reset(request: Request, b: Who):
    require_key(request)
    r = rt()
    r.sk.resume()
    return {**r.supervisor.reset(f"console:{b.by}"[:80]), "safety": _jsonable(r.supervisor.snapshot())}


@router.post("/refix")
def refix(request: Request, b: Landmark):
    require_key(request)
    f = getattr(rt().sk.bridge, "refix_at_landmark", None)
    if f is None:
        raise HTTPException(404, "this body has no landmark re-fix")
    try:
        return f(b.landmark)
    except KeyError as e:
        raise HTTPException(404, str(e)) from e


@router.get("/events")
async def events(request: Request):
    require_key(request)
    hub = rt().hub
    q = hub.subscribe()

    async def gen():
        try:
            for ev in list(hub.recent)[-20:]:
                yield f"data: {json.dumps(ev)}\n\n"
            while not await request.is_disconnected():
                try:
                    ev = await asyncio.to_thread(q.get, True, 1.0)
                except queue.Empty:
                    yield ": keepalive\n\n"
                    continue
                yield f"data: {json.dumps(ev)}\n\n"
        finally:
            hub.unsubscribe(q)

    return StreamingResponse(gen(), media_type="text/event-stream")


# ---------------------------------------------------------------------------------- mock-only demo hooks
def _mock_rt() -> EdgeRuntime:
    r = rt()
    if getattr(r.sk.bridge.t, "name", "") != "mock" or not (r.sk.cfg.get("mock") or {}).get("hooks"):
        raise HTTPException(404, "mock hooks exist only on the mock twin (config mock.hooks)")
    return r


@router.post("/mock/video")
def mock_video(request: Request, b: dict):
    """Freeze the walkthrough camera on a time (s), or {"t": null} to play. The video is not where the twin is."""
    require_key(request)
    src = _mock_rt().perception.src
    if not hasattr(src, "freeze"):
        raise HTTPException(409, "frame source is not a video")
    src.freeze(b.get("t"))
    return {"ok": True, "frozen": src.frozen}


@router.post("/mock/stage_breach")
def mock_stage_breach(request: Request, b: dict):
    """Staged breach for the demo: an outside motion source (like the team's keyboard teleop, which publishes cmd_vel
    straight to their bridge and never passes our gate) drives the twin forward at `speed` for up to `max_s`. Our
    layers must stop it: L1 on measured motion, L2 on the floor ahead, L3 on the frame. Ends as soon as the safety FSM
    leaves NORMAL/CAUTION (the outside source "lets go" when our stop arrives)."""
    require_key(request)
    r = _mock_rt()
    t = r.sk.bridge.t
    speed, max_s = float(b.get("speed", 0.25)), float(b.get("max_s", 8.0))
    slip_m = float(b.get("slip_m", 0.0))
    slip_after_s = float(b.get("slip_after_s", 0.0))
    if b.get("start"):  # staged: put the twin at a zone-frame pose first (e.g. the turnstiles, facing them)
        zx, zy, zyaw = (float(v) for v in b["start"])
        tx, ty, th = r.sk.bridge._tf
        c, s_ = math.cos(-th), math.sin(-th)
        dx, dy = zx - tx, zy - ty
        t.set_pose(c * dx - s_ * dy, s_ * dx + c * dy, zyaw - th)
        time.sleep(0.4)
    r.supervisor.request_judge("staged_breach")

    def drive():
        t0 = time.time()
        while time.time() - t0 < max_s and r.supervisor.fsm.allows_motion():
            t.send(speed, 0.0, 0.0)
            time.sleep(0.05)
        r.sk.bridge.stop()
        time.sleep(slip_after_s)
        if slip_m > 0:  # staged: wet tile carries the stopped robot on past the line (so recovery has work to do)
            raw = t.latest()
            t.set_pose(raw.x + slip_m * math.cos(raw.yaw), raw.y + slip_m * math.sin(raw.yaw), raw.yaw)
            r.sk.emit("mock.slip", slip_m=slip_m, note="staged: robot carried on after the stop")

    threading.Thread(target=drive, daemon=True, name="mock-teleop").start()
    return {"ok": True, "speed": speed, "max_s": max_s}


def main():
    import uvicorn

    host = settings.bind_host()
    port = int(settings.get("EDGE_PORT", "8765"))
    if not settings.get("EDGE_API_KEY") and not _is_local(host):
        raise SystemExit("EDGE_API_KEY is not set (.env): refusing to serve on a non-loopback address without a key")
    uvicorn.run("skills.api:app", host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
