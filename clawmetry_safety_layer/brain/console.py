"""Mission control (web page on the brain): robot state, zone map with the pose, safety FSM, last verdicts, people
count, link RTT, ClawMetry session link, and buttons pause / resume / say / guide_to / approve / deny / reset /
confirm that call the edge API with the edge key. The browser never sees the edge key: it logs in with the brain
key once (HttpOnly, SameSite=Strict cookie) and the brain proxies an allow-list of edge endpoints.

    http://<brain>:8780/            (tailnet only; BIND_HOST is the tailnet address)
    http://<brain>:8780/?watch=1    full-screen, no buttons: the demo screen and the recording

Pipeline strip (/api/pipeline, polled every 2 s): vLLM /health, brain relay, edge /state, team ROS bridge (connected,
ctrl_mode), team detector (/latest.json staleness), ClawMetry journal flush age, Tailscale RTT to the edge. Event log:
/api/events proxies the edge's SSE (/events) with the edge key. No camera images anywhere (frames are not stored).
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import yaml
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response, StreamingResponse

from leukerbad_g1 import settings

ROOT = Path(__file__).resolve().parent.parent
ACTIONS = ("pause", "resume", "say", "guide_to", "approve", "deny", "reset", "confirm", "ask", "refix")
GET_PATHS = ("state", "observation")


def edge_call(path: str, body: dict | None = None, timeout: float = 10.0) -> tuple[int, dict]:
    url = settings.get("EDGE_URL", "http://127.0.0.1:8765").rstrip("/") + "/" + path
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(
        url,
        data=data,
        method="GET" if body is None else "POST",
        headers={"content-type": "application/json", "x-api-key": settings.get("EDGE_API_KEY")},
    )
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            out = json.loads(r.read() or b"{}")
            if isinstance(out, dict):
                out.setdefault("_rtt_s", round(time.time() - t0, 3))
            return r.status, out
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except ValueError:
            return e.code, {"detail": str(e)}
    except Exception as e:  # noqa: BLE001
        return 502, {"detail": f"edge unreachable: {e.__class__.__name__}: {e}"[:200]}


def _authed(request: Request) -> bool:
    return settings.key_ok(request.cookies.get("brain_key"), "BRAIN_API_KEY") or settings.key_ok(
        request.headers.get("x-api-key"), "BRAIN_API_KEY"
    )


def _zones() -> dict:
    try:
        from bridge import load_config

        cfg = load_config(settings.get("BRAIN_ROBOT_CONFIG", "config/d1w.yaml"))  # the robot this console watches
        zf = ROOT / cfg.get("zones_file", "policies/zones_therme.yaml")
    except Exception:  # noqa: BLE001
        zf = ROOT / "policies/zones_therme.yaml"
    raw = yaml.safe_load(zf.read_text())
    return {"zones": raw.get("zones", {}), "landmarks": raw.get("landmarks", {})}


# ------------------------------------------------------------------------------------------- pipeline health
EVENT_KINDS = (
    "fsm.",
    "judge_verdict",
    "hazard",
    "breach",
    "person_near",
    "approval",
    "link_",
    "recover",
    "concierge.turn",
    "guide",
    "greet",
    "gait.set",
    "alert_staff",
    "pause",
    "resume",
)
_ts: dict = {"rtt_ms": None, "via": None, "error": "not probed yet", "at": 0.0}


def _edge_host() -> str:
    return re.sub(r"^\w+://", "", settings.get("EDGE_URL", "http://127.0.0.1:8765")).split("/")[0].rsplit(":", 1)[0]


def _tailscale_loop() -> None:
    """tailscale ping the edge every 5 s (a subprocess per poll would be too heavy at 2 s)."""
    while True:
        host = _edge_host()
        if host in ("127.0.0.1", "localhost", "::1"):
            _ts.update(rtt_ms=0.0, via="loopback", error=None, at=time.time())
        elif not shutil.which("tailscale"):
            _ts.update(rtt_ms=None, via=None, error="tailscale CLI not found", at=time.time())
        else:
            try:
                out = subprocess.run(
                    ["tailscale", "ping", "-c", "1", "--timeout", "3s", host],
                    capture_output=True,
                    text=True,
                    timeout=6,
                ).stdout
                m = re.search(r"via (\S+) in ([\d.]+)ms", out)
                if m:
                    _ts.update(rtt_ms=float(m.group(2)), via=m.group(1), error=None, at=time.time())
                else:
                    _ts.update(
                        rtt_ms=None, via=None, error=(out.strip().splitlines() or ["no pong"])[-1][:120], at=time.time()
                    )
            except Exception as e:  # noqa: BLE001
                _ts.update(rtt_ms=None, via=None, error=f"{e.__class__.__name__}", at=time.time())
        time.sleep(5.0)


def _vllm_health() -> dict:
    base = re.sub(r"/v1/?$", "", settings.get("COSMOS_URL", "http://127.0.0.1:8000/v1").rstrip("/"))
    t0 = time.time()
    try:
        with urllib.request.urlopen(base + "/health", timeout=2.0) as r:
            return {"ok": r.status == 200, "ms": round((time.time() - t0) * 1000)}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": f"{e.__class__.__name__}: {e}"[:120]}


_clips: dict = {"mtime": None, "n": 0}


def _dataset_clips() -> int:
    m = ROOT / settings.get("COSMOS_DATASET_DIR", "datasets/cosmos_d1w") / "manifest.json"
    try:
        mt = m.stat().st_mtime
        if mt != _clips["mtime"]:
            _clips.update(mtime=mt, n=len(json.loads(m.read_text()).get("clips") or []))
    except (OSError, ValueError, AttributeError):
        _clips.update(mtime=None, n=0)
    return _clips["n"]


def _tile(level: str, label: str, detail: str) -> dict:
    return {"level": level, "label": label, "detail": detail}


def pipeline(relay) -> dict:
    """green / amber / red tiles; each derived from one measurement, never guessed."""
    v = _vllm_health()
    code, st = edge_call("state", timeout=2.0)
    edge_ok = code == 200 and isinstance(st, dict) and "robot" in st
    tiles: dict = {}
    tiles["vllm"] = _tile(
        "ok" if v["ok"] else "bad", "vLLM (Cosmos)", f"/health {v['ms']} ms" if v["ok"] else v.get("error", "down")
    )
    j = relay.judge
    fail_rate = j.failures / j.calls if j.calls else 0.0
    tiles["brain"] = _tile(
        "warn" if fail_rate > 0.2 else "ok",
        "Brain relay",
        f"judge {j.calls} calls, {j.failures} failed, last {j.last_latency_s or '–'} s",
    )
    if not edge_ok:
        tiles["edge"] = _tile("bad", "Edge /state", str(st.get("detail", code))[:120])
        for k, lab in (("bridge", "Team ROS bridge"), ("detector", "Team detector"), ("clawmetry", "ClawMetry sync")):
            tiles[k] = _tile("bad", lab, "unknown (edge unreachable)")
    else:
        rtt = st.get("_rtt_s") or 0.0
        tiles["edge"] = _tile(
            "ok" if rtt < 0.5 else "warn",
            "Edge /state",
            f"{round(rtt * 1000)} ms, {st.get('safety', {}).get('state', '?')}",
        )
        t = st.get("transport") or {}
        mode, age = t.get("ctrl_mode"), t.get("state_age_s")
        if not t.get("connected"):
            lvl, d = "bad", "not connected"
        elif not t.get("fresh", True):
            lvl, d = "bad", f"state stale {age} s"
        elif t.get("handover"):
            lvl, d = "warn", "handed over (operator)"
        else:
            lvl, d = "ok", f"{t.get('transport')} connected, ctrl_mode {mode}, age {age} s"
        tiles["bridge"] = _tile(lvl, "Team ROS bridge", d)
        pl = st.get("pipeline") or {}
        det = pl.get("detector") or {}
        dage, stale = det.get("age_s"), det.get("stale_s") or 1.0
        if det.get("kind") != "team":
            tiles["detector"] = _tile("warn", "Team detector", f"not used (FRAME_SOURCE={det.get('kind')})")
        elif dage is None:
            tiles["detector"] = _tile("bad", "Team detector", "no /latest.json yet")
        else:
            tiles["detector"] = _tile(
                "ok" if dage <= stale else ("warn" if dage <= 3 * stale else "bad"),
                "Team detector",
                f"/latest.json {dage} s old",
            )
        jr = pl.get("journal") or {}
        fa = jr.get("flush_age_s")
        if not jr.get("on"):
            tiles["clawmetry"] = _tile("bad", "ClawMetry sync", "journal off (CLAWMETRY_CONTROLLER)")
        else:
            tiles["clawmetry"] = _tile(
                "ok" if fa is not None and fa < 30 else ("warn" if fa is not None and fa < 120 else "bad"),
                "ClawMetry sync",
                f"journal flushed {fa} s ago",
            )
    ts_age = time.time() - _ts["at"]
    if _ts["rtt_ms"] is not None and ts_age < 20:
        r = _ts["rtt_ms"]
        tiles["tailscale"] = _tile(
            "ok" if r < 150 else ("warn" if r < 400 else "bad"), "Tailscale to edge", f"{r:.0f} ms via {_ts['via']}"
        )
    else:
        tiles["tailscale"] = _tile("bad", "Tailscale to edge", _ts["error"] or "stale probe")
    pl = st.get("pipeline", {}) if edge_ok else {}
    return {
        "ts": round(time.time(), 3),
        "tiles": tiles,
        "counters": {
            "turns_today": pl.get("turns_today"),
            "judge_calls": j.calls,
            "dataset_clips": _dataset_clips(),
        },
    }


LOGIN = """<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>Mission control</title><style>body{font:16px system-ui;background:#0e1116;color:#e6e6e6;display:grid;
place-items:center;height:100vh;margin:0}form{display:grid;gap:8px}input,button{font:inherit;padding:8px}</style>
<form method=post action=/login><b>Leukerbad D1 Ultra-W, mission control</b>
<input type=password name=key placeholder="BRAIN_API_KEY" autofocus><button>Open</button></form>"""


def mount(app: FastAPI) -> None:
    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        if not _authed(request):
            return HTMLResponse(LOGIN)
        return HTMLResponse((Path(__file__).parent / "console.html").read_text())

    @app.post("/login")
    def login(key: str = Form(...)):
        if not settings.key_ok(key, "BRAIN_API_KEY"):
            return HTMLResponse(LOGIN.replace("<b>", "<b style=color:#f66>Wrong key. "), status_code=401)
        r = RedirectResponse("/", status_code=303)
        r.set_cookie("brain_key", key, httponly=True, samesite="strict", max_age=12 * 3600)
        return r

    threading.Thread(target=_tailscale_loop, name="tailscale-rtt", daemon=True).start()

    @app.get("/api/pipeline")
    async def api_pipeline(request: Request):
        if not _authed(request):
            raise HTTPException(401)
        import asyncio

        return await asyncio.to_thread(pipeline, app.state.relay)

    @app.get("/api/events")
    async def api_events(request: Request):
        """The edge's SSE, filtered to operator events (FSM, verdicts, holds, approvals, link, turns)."""
        if not _authed(request):
            raise HTTPException(401)
        import asyncio

        url = settings.get("EDGE_URL", "http://127.0.0.1:8765").rstrip("/") + "/events"
        req = urllib.request.Request(url, headers={"x-api-key": settings.get("EDGE_API_KEY")})

        async def gen():
            try:
                resp = await asyncio.to_thread(lambda: urllib.request.urlopen(req, timeout=5.0))
            except Exception as e:  # noqa: BLE001
                yield f"event: down\ndata: {json.dumps({'detail': str(e)[:160]})}\n\n"
                return
            try:
                while not await request.is_disconnected():
                    try:
                        line = await asyncio.to_thread(resp.readline)
                    except Exception:  # noqa: BLE001  (edge keepalives every 1 s; a timeout means it is gone)
                        break
                    if not line:
                        break
                    t = line.decode("utf-8", "replace")
                    if t.startswith("data: "):
                        try:
                            ev = json.loads(t[6:])
                        except ValueError:
                            continue
                        kind = str(ev.get("kind", ""))
                        if kind.startswith(EVENT_KINDS) or (kind == "command" and ev.get("type") == "hold_pose"):
                            yield f"data: {json.dumps(ev)}\n\n"
                    elif t.startswith(":"):
                        yield ": keepalive\n\n"
            finally:
                resp.close()
            yield f"event: down\ndata: {json.dumps({'detail': 'edge stream ended'})}\n\n"

        return StreamingResponse(gen(), media_type="text/event-stream", headers={"cache-control": "no-cache"})

    @app.get("/api/zones")
    def zones(request: Request):
        if not _authed(request):
            raise HTTPException(401)
        return _zones()

    @app.get("/api/edge/{path}")
    def edge_get(path: str, request: Request):
        if not _authed(request):
            raise HTTPException(401)
        if path not in GET_PATHS:
            raise HTTPException(404)
        code, out = edge_call(path)
        out_relay = app.state.relay
        if isinstance(out, dict):
            out["brain"] = {
                "frames_seen": out_relay.frames_seen,
                "verdicts": list(out_relay.verdicts)[-5:],
                "judge_last_latency_s": out_relay.judge.last_latency_s,
            }
        return Response(json.dumps(out), status_code=code, media_type="application/json")

    @app.post("/api/edge/{action}")
    async def edge_post(action: str, request: Request):
        if not _authed(request):
            raise HTTPException(401)
        if action not in ACTIONS:
            raise HTTPException(404, f"not an allowed action: {action}")
        try:
            body = await request.json()
        except ValueError:
            body = {}
        if not isinstance(body, dict):
            body = {}
        if action in ("approve", "deny", "reset"):
            body.setdefault("by", "mission-control")
        import asyncio

        code, out = await asyncio.to_thread(edge_call, action, body, 120.0 if action == "guide_to" else 30.0)
        return Response(json.dumps(out), status_code=code, media_type="application/json")
