"""Mission control (web page on the brain): robot state, zone map with the pose, safety FSM, last verdicts, people
count, link RTT, ClawMetry session link, and buttons pause / resume / say / guide_to / approve / deny / reset /
confirm that call the edge API with the edge key. The browser never sees the edge key: it logs in with the brain
key once (HttpOnly, SameSite=Strict cookie) and the brain proxies an allow-list of edge endpoints.

    http://<brain>:8780/            (tailnet only; BIND_HOST is the tailnet address)
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path

import yaml
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

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
