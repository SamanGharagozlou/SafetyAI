"""FastAPI mirror of the skills + operator pause/resume endpoints + the behaviour policy.

  uvicorn skills.api:app --port 8765
  curl -X POST localhost:8765/skills/navigate_to -d '{"zone":"B","rationale":"guest asked for towels at B"}' \
       -H 'content-type: application/json'
  curl -X POST localhost:8765/pause   # what ClawMetry's Pause should call (via a webhook/alert)
  curl -X POST localhost:8765/resume
  curl localhost:8765/policy                                   # behaviour policy (policies/behaviour.yaml)
  curl -X PUT localhost:8765/policy -H 'content-type: application/json' -H 'X-Operator: alice' -d @policy.json

Every skill body takes an optional `rationale` (the agent's one-line reason; recorded in the turn.decide event).

At startup the API also
  * registers itself with ClawMetry's Physical AI page (/robotics) as a live controller with Stop support
    (Stop -> bridge.stop_and_settle, logged) and writes the robot-session trace journal there
    (skills/clawmetry_robotics.py, docs/ROBOT-SESSIONS.md). CLAWMETRY_CONTROLLER=0 turns that off;
  * starts the continuous behaviour monitor (skills/monitor.py, every 200 ms). BEHAVIOUR_MONITOR=0 turns it off.
The OTLP spans/metrics (Digital AI path) keep their names; spans gain skill.outcome / skill.deviation_* attributes.
"""

from __future__ import annotations

import datetime as _dt
import os
from contextlib import asynccontextmanager

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import skills.skills as _sk
from skills import clawmetry_robotics, edge
from skills.behaviour import PolicyError
from skills.monitor import Monitor
from skills.skills import get_skills

_CTL = None
_MON = None


@asynccontextmanager
async def _lifespan(app):
    # Build the body at startup, on the main (event-loop) thread, instead of lazily in a worker
    # thread on the first request: the MuJoCo viewer must be created and closed on the same thread.
    global _CTL, _MON
    sk = get_skills()
    _CTL = clawmetry_robotics.start(sk)
    if os.environ.get("BEHAVIOUR_MONITOR", "1") != "0":
        _MON = Monitor(sk).start()
    edge.attach(sk)  # D1W venue edge: safety supervisor, perception, link watchdog (only with a `safety:` config)
    yield
    edge.detach()
    if _MON is not None:
        _MON.close()
        _MON = None
    if _CTL is not None:
        _CTL.close()
        _CTL = None
    # Close the body while the server is still alive (stops motion; for the MuJoCo body, stops the
    # render thread and closes the viewer). Leaving it to interpreter teardown crashed under uvicorn.
    if _sk._SINGLETON is not None:
        _sk._SINGLETON.bridge.close()


app = FastAPI(title="leukerbad-g1 skills", lifespan=_lifespan)
app.include_router(edge.router)


@app.middleware("http")
async def _remote_needs_key(request: Request, call_next):
    """Requests from another machine (tailnet / LAN) must carry X-Api-Key = EDGE_API_KEY; loopback callers (the MCP
    server in proxy mode, the local agent, ClawMetry's local daemon) do not. No key configured -> remote refused."""
    host = request.client.host if request.client else None
    if not edge._is_local(host) and not edge.settings.key_ok(request.headers.get("x-api-key"), "EDGE_API_KEY"):
        return JSONResponse({"detail": "X-Api-Key required"}, status_code=401)
    return await call_next(request)


class _Why(BaseModel):
    rationale: str | None = None


class Nav(_Why):
    zone: str
    max_s: float = 20


class Pick(_Why):
    object: str = "tray"


class Place(_Why):
    target: str = "table_B"


class Text(_Why):
    text: str


class Target(_Why):
    target: str


class Reason(_Why):
    reason: str = "operator"


def _sk_with(b: _Why | None):
    sk = get_skills()
    sk.set_rationale(b.rationale if b is not None else None)
    return sk


@app.get("/health")
def health():
    return {
        "ok": True,
        "body": get_skills().bridge.body,
        "monitor": _MON is not None,
        "controller": None if _CTL is None else _CTL.controller_id,
    }


@app.post("/skills/get_state")
def get_state(b: _Why | None = None):
    return _sk_with(b).get_state().dict()


@app.post("/skills/navigate_to")
def navigate_to(b: Nav):
    return _sk_with(b).navigate_to(b.zone, b.max_s).dict()


@app.post("/skills/pick")
def pick(b: Pick):
    return _sk_with(b).pick(b.object).dict()


@app.post("/skills/place")
def place(b: Place):
    return _sk_with(b).place(b.target).dict()


@app.post("/skills/say")
def say(b: Text):
    return _sk_with(b).say(b.text).dict()


@app.post("/skills/look_at")
def look_at(b: Target):
    return _sk_with(b).look_at(b.target).dict()


@app.post("/skills/hold_pose")
def hold_pose(b: Reason):
    return _sk_with(b).hold_pose(b.reason).dict()


class Guide(_Why):
    poi: str
    lang: str = "en"


@app.post("/skills/guide_to")
def guide_to(b: Guide):
    return _sk_with(b).guide_to(b.poi, b.lang).dict()


class Greet(_Why):
    name: str | None = None
    lang: str = "en"


@app.post("/skills/greet")
def greet(b: Greet):
    return _sk_with(b).greet(b.name, b.lang).dict()


class TaskRequest(BaseModel):
    text: str
    requested_by: str = "operator"


@app.post("/task")
def task(b: TaskRequest):
    """A guest request in plain words (the same entry ClawMetry's Physical AI console uses). The controller journals
    who asked and what, runs the agent against the seven skills and journals what it intended and what happened."""
    if _CTL is None:
        raise HTTPException(503, "live controller not registered (CLAWMETRY_CONTROLLER=0)")
    try:
        return _CTL.request_task(b.text, b.requested_by)
    except (ValueError, RuntimeError) as e:
        raise HTTPException(409, str(e)) from e


@app.post("/pause")
def pause(b: Reason | None = None):
    return get_skills().pause((b or Reason()).reason).dict()


@app.post("/resume")
def resume():
    return get_skills().resume().dict()


# ---------------------------------------------------------------- behaviour policy
@app.get("/policy")
def get_policy():
    pol = get_skills().policy
    return {
        "path": str(pol.path),
        "policy": pol.snapshot(),
        "zones": pol.describe_zones(),
        "quiet_now": pol.quiet_now(),
    }


@app.put("/policy")
def put_policy(request: Request, doc: dict = Body(...)):
    """Replace the behaviour policy (the whole document, as returned under "policy" by GET). Validated; on success
    persisted to the policy file and logged as a policy.changed trace event with who/when. Who: the X-Operator
    header, else `changed_by` in the body, else the client address."""
    sk = get_skills()
    doc = dict(doc)
    who = (
        request.headers.get("x-operator")
        or doc.pop("changed_by", None)
        or (f"{request.client.host}" if request.client else "unknown")
    )
    doc.pop("changed_by", None)
    doc.setdefault("version", sk.policy.raw.get("version", 0))
    try:
        old, changed = sk.policy.replace(doc)
    except PolicyError as e:
        raise HTTPException(status_code=422, detail={"errors": e.errors}) from e
    new = sk.policy.snapshot()
    sk.emit(
        "policy.changed",
        policy_name=new.get("name"),
        version=new["version"],
        previous_version=old.get("version"),
        changed_by=str(who)[:120],
        changed_at=_dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        changed_keys=changed,
        changes={k: {"from": old.get(k), "to": new.get(k)} for k in changed},
        policy={"name": new.get("name"), "version": new["version"]},
    )
    print(f"[policy] {who} changed {changed} -> version {new['version']}")
    return {"ok": True, "version": new["version"], "changed_keys": changed, "policy": new}
