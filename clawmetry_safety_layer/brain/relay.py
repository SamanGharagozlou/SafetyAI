"""Brain relay (this Spark): frames in, verdicts out. One FastAPI app with the mission-control console
(brain/console.py) and the concierge (concierge/agent.py) mounted on it.

    python -m brain          # = make brain; BIND_HOST:8780 (tailnet IP; never 0.0.0.0)

POST /frame   X-Api-Key BRAIN_API_KEY; multipart frame (JPEG <= 640x360) + meta JSON {ts, seq, pose, zone, judge,
              action, poi}. When meta.judge names a trigger, the frame goes to the Cosmos judge and the verdict is
              returned AND posted to the edge's /verdict (EDGE_URL, X-Api-Key EDGE_API_KEY). Frames without a trigger
              only update the "last frame seen" counters. The JPEG is dropped as soon as the call returns: no frame
              is written anywhere (docs/PRIVACY.md); the ring keeps the last 30 verdicts, never the pixels.
GET  /verdicts   the ring
GET  /health     {ok, cosmos, frames_seen, verdicts}
POST /ask        the concierge (Anthropic API tool loop), for the edge's /ask
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import threading
import time
import urllib.request
from collections import OrderedDict, deque
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from leukerbad_g1 import settings

ROOT = Path(__file__).resolve().parent.parent

# ==================================================================================== the Cosmos judge (L3)
# The ONE judge implementation: prompt, request, robust parsing and cache live here, in the brain relay. The edge
# never calls Cosmos; it sends frames to POST /frame and gets verdicts back on /verdict.
#
# L3: Cosmos Reason 2 as a scene judge (OpenAI-compatible vLLM at COSMOS_URL, model nvidia/Cosmos-Reason2-2B).
#
# Input: one frame (<= 640x360 JPEG), the go-zone description from config, the intended action ("forward 2 m at
# 0.3 m/s toward bath_entrance"). Output: JSON only
#     {"leaves_go_zone": bool, "hazards": [str], "people_within_2m": int, "stop": bool, "confidence": 0..1,
#      "reason": str}
# max_tokens 150, temperature 0, timeout 8 s, no <think> block requested. The reply is parsed robustly (first JSON
# object, <think> stripped, fields coerced, defaults on gaps); results are cached by frame hash. Any failure returns
# judge_unavailable; the FSM treats that as CAUTION, and blocks guide_to only when safety.judge_required is true.
# Called at guide_to start, every 2 m, on a new scene class or a hazard (the supervisor decides when).

SYSTEM = (
    "You are the safety judge for a small wheeled-leg robot guiding guests in a thermal spa. You see one frame "
    "from its forward camera, which is about 30 cm above the floor. Answer with ONE JSON object and nothing "
    "else: no reasoning, no markdown, no <think>."
)
SCHEMA = (
    '{"leaves_go_zone": true|false, "hazards": ["things you can SEE in the image"], "people_within_2m": integer, '
    '"stop": true|false, "confidence": 0.0-1.0, "reason": "at most 12 words"}'
)
FIELDS = ("leaves_go_zone", "hazards", "people_within_2m", "stop", "confidence", "reason")


def prompt(go_zone: str, action: str) -> str:
    return (
        f"Go zone (where the robot may drive): {go_zone}\n"
        f"Intended action: {action}\n"
        "Look only at the image. leaves_go_zone: does the floor straight ahead (next 2 m) visibly change into steps, "
        "a staircase, water, or a different corridor behind a door or gate? hazards: only objects you actually see "
        "in the path; [] if none; never copy words from this prompt. "
        "people_within_2m: people you see close to the camera. stop: true if driving ahead now would be unsafe; it "
        "must agree with your reason.\n"
        f"Reply with exactly this JSON shape: {SCHEMA}"
    )


def _bool(v) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.strip().lower() in ("true", "yes", "1")
    if isinstance(v, (int, float)):
        return v != 0
    return False


def parse(text: str) -> dict | None:
    """First JSON object in the reply, coerced to the schema. None if there is no usable object."""
    if not text:
        return None
    text = re.sub(r"<think>.*?(</think>|$)", "", text, flags=re.S | re.I)
    text = re.sub(r"```(?:json)?", "", text)
    start = text.find("{")
    while start != -1:
        depth, end = 0, None
        in_str, esc = False, False
        for i in range(start, len(text)):
            ch = text[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
        if end is None:
            break
        try:
            obj = json.loads(text[start:end])
        except json.JSONDecodeError:
            obj = None
        if isinstance(obj, dict) and any(k in obj for k in FIELDS):
            hz = obj.get("hazards") or []
            if isinstance(hz, str):
                hz = [hz]
            try:
                people = int(float(obj.get("people_within_2m") or 0))
            except (TypeError, ValueError):
                people = 0
            try:
                conf = float(obj.get("confidence", 0.5))
            except (TypeError, ValueError):
                conf = 0.5
            return {
                "leaves_go_zone": _bool(obj.get("leaves_go_zone", False)),
                "hazards": [str(h)[:80] for h in hz if str(h).strip()][:8],
                "people_within_2m": max(0, people),
                "stop": _bool(obj.get("stop", False)),
                "confidence": max(0.0, min(1.0, conf)),
                "reason": str(obj.get("reason", ""))[:240],
            }
        start = text.find("{", start + 1)
    return None


class CosmosJudge:
    def __init__(
        self,
        url: str,
        model: str = "nvidia/Cosmos-Reason2-2B",
        timeout_s: float = 8.0,
        cache_size: int = 64,
        post=None,
        api_key: str | None = None,
    ):
        from leukerbad_g1 import settings

        self.url = url.rstrip("/") + "/chat/completions"
        self.api_key = settings.get("COSMOS_API_KEY") if api_key is None else api_key
        self.model = model
        self.timeout_s = timeout_s
        self._post = post or self._http
        self._cache: OrderedDict[str, dict] = OrderedDict()
        self._cache_size = cache_size
        self._lock = threading.Lock()
        self.calls = 0
        self.failures = 0
        self.last_latency_s: float | None = None

    def _http(self, payload: dict) -> dict:
        req = urllib.request.Request(
            self.url,
            data=json.dumps(payload).encode(),
            method="POST",
            headers={
                "content-type": "application/json",
                **({"authorization": f"Bearer {self.api_key}"} if self.api_key else {}),
            },
        )
        with urllib.request.urlopen(req, timeout=self.timeout_s) as r:
            return json.loads(r.read())

    def judge(self, jpeg: bytes, go_zone: str, action: str) -> dict:
        """Verdict dict (+ latency_s, cached, model) or {"judge_unavailable": True, "error": ...}."""
        h = hashlib.sha256(jpeg + action.encode() + go_zone.encode()).hexdigest()[:24]
        with self._lock:
            if h in self._cache:
                v = dict(self._cache[h])
                v["cached"] = True
                return v
        payload = {
            "model": self.model,
            "temperature": 0,
            "max_tokens": 150,
            "messages": [
                {"role": "system", "content": SYSTEM},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()},
                        },
                        {"type": "text", "text": prompt(go_zone, action)},
                    ],
                },
            ],
        }
        t0 = time.time()
        self.calls += 1
        try:
            resp = self._post(payload)
            text = resp["choices"][0]["message"]["content"] or ""
        except Exception as e:  # noqa: BLE001  (timeout / server down / bad reply: unavailable, never a crash)
            self.failures += 1
            return {
                "judge_unavailable": True,
                "error": f"{e.__class__.__name__}: {e}"[:200],
                "latency_s": round(time.time() - t0, 3),
            }
        lat = time.time() - t0
        self.last_latency_s = lat
        parsed = parse(text)
        if parsed is None:
            self.failures += 1
            return {
                "judge_unavailable": True,
                "error": "unparseable reply",
                "raw": text[:200],
                "latency_s": round(lat, 3),
            }
        v = parsed
        v.update(latency_s=round(lat, 3), cached=False, model=self.model, frame_hash=h)
        with self._lock:
            self._cache[h] = v
            while len(self._cache) > self._cache_size:
                self._cache.popitem(last=False)
        return dict(v)

    def describe(
        self,
        jpeg: bytes,
        question: str = "Describe what the robot sees in one or two sentences, including the text of any signs.",
    ) -> str | None:
        """Free-text answer for the concierge's look tool (sign text etc.). None when unavailable."""
        payload = {
            "model": self.model,
            "temperature": 0,
            "max_tokens": 120,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()},
                        },
                        {"type": "text", "text": question + " Answer directly, no <think>."},
                    ],
                }
            ],
        }
        try:
            text = self._post(payload)["choices"][0]["message"]["content"] or ""
        except Exception:  # noqa: BLE001
            return None
        return re.sub(r"<think>.*?(</think>|$)", "", text, flags=re.S | re.I).strip()[:400] or None


class Relay:
    def __init__(self, judge: CosmosJudge | None = None, post_edge=None, go_zone: str | None = None):
        self.judge = judge or CosmosJudge(
            settings.get("COSMOS_URL", "http://127.0.0.1:8000/v1"),
            settings.get("COSMOS_MODEL", "nvidia/Cosmos-Reason2-2B"),
        )
        self.verdicts: deque = deque(maxlen=30)
        self.frames_seen = 0
        self.last_frame: dict = {}
        self._post_edge = post_edge or self._http_edge
        self.edge_errors = 0
        self.lock = threading.Lock()
        if go_zone is None:
            try:
                from bridge import load_config

                go_zone = load_config("config/d1w.yaml")["safety"]["go_zone_description"]
            except Exception:  # noqa: BLE001
                go_zone = "the dry, flat public floor"
        self.go_zone = go_zone

    def _http_edge(self, verdict: dict):
        url = settings.get("EDGE_URL", "http://127.0.0.1:8765").rstrip("/") + "/verdict"
        req = urllib.request.Request(
            url,
            data=json.dumps(verdict).encode(),
            method="POST",
            headers={"content-type": "application/json", "x-api-key": settings.get("EDGE_API_KEY")},
        )
        with urllib.request.urlopen(req, timeout=5.0) as r:
            r.read()

    def frame(self, jpeg: bytes, meta: dict) -> dict:
        with self.lock:
            self.frames_seen += 1
            self.last_frame = {
                "ts": meta.get("ts"),
                "seq": meta.get("seq"),
                "zone": meta.get("zone"),
                "pose": meta.get("pose"),
                "bytes": len(jpeg),
                "received": round(time.time(), 3),
            }
        trig = meta.get("judge")
        if not trig:
            return {"ok": True, "judged": False}
        action = meta.get("action") or (
            f"forward 2 m at 0.3 m/s toward {meta['poi']}" if meta.get("poi") else "forward 2 m at 0.3 m/s"
        )
        v = self.judge.judge(jpeg, self.go_zone, action)
        v.update(
            trigger=str(trig)[:40], frame_ts=meta.get("ts"), pose=meta.get("pose"), zone=meta.get("zone"), action=action
        )
        with self.lock:
            self.verdicts.append(v)
        threading.Thread(target=self._forward, args=(v,), daemon=True).start()
        return {"ok": True, "judged": True, "verdict": v}

    def _forward(self, v: dict):
        try:
            self._post_edge(v)
        except Exception as e:  # noqa: BLE001  (edge unreachable: it marks the judge unavailable on its own timer)
            self.edge_errors += 1
            print(f"[relay] verdict -> edge failed: {e}")

    def cosmos_ok(self) -> bool:
        try:
            req = urllib.request.Request(
                self.judge.url.rsplit("/chat/completions", 1)[0] + "/models",
                headers={"authorization": f"Bearer {self.judge.api_key}"} if self.judge.api_key else {},
            )
            with urllib.request.urlopen(req, timeout=2.0) as r:
                return r.status == 200
        except Exception:  # noqa: BLE001
            return False


def _multipart(body: bytes, ctype: str) -> dict[str, bytes]:
    """Minimal multipart/form-data parser (our own sampler is the only client): name -> bytes."""
    if "boundary=" not in ctype:
        raise HTTPException(400, "multipart boundary missing")
    b = ("--" + ctype.split("boundary=", 1)[1].split(";")[0].strip().strip('"')).encode()
    out: dict[str, bytes] = {}
    for part in body.split(b)[1:]:
        if part.startswith(b"--"):
            break
        head, _, data = part.partition(b"\r\n\r\n")
        name = None
        for line in head.decode(errors="replace").split("\r\n"):
            if line.lower().startswith("content-disposition") and 'name="' in line:
                name = line.split('name="', 1)[1].split('"', 1)[0]
        if name:
            out[name] = data[:-2] if data.endswith(b"\r\n") else data
    return out


def require_brain_key(request: Request) -> None:
    if settings.key_ok(request.headers.get("x-api-key"), "BRAIN_API_KEY"):
        return
    if settings.key_ok(request.cookies.get("brain_key"), "BRAIN_API_KEY"):
        return
    raise HTTPException(401, "X-Api-Key required")


def create_app(relay: Relay | None = None, concierge=None) -> FastAPI:
    app = FastAPI(title="leukerbad brain")
    app.state.relay = relay or Relay()
    app.state.concierge = concierge

    @app.post("/frame")
    async def frame(request: Request):
        require_brain_key(request)
        body = await request.body()
        if len(body) > 2_000_000:
            raise HTTPException(413, "frame too large (<= 640x360 JPEG expected)")
        parts = _multipart(body, request.headers.get("content-type", ""))
        jpeg = parts.get("frame")
        if not jpeg or not jpeg.startswith(b"\xff\xd8"):
            raise HTTPException(400, "frame must be a JPEG")
        try:
            meta = json.loads(parts.get("meta") or b"{}")
        except ValueError as e:
            raise HTTPException(400, "meta must be JSON") from e
        import asyncio

        out = await asyncio.to_thread(app.state.relay.frame, jpeg, meta)
        del jpeg, body, parts
        return out

    @app.post("/describe")
    async def describe(request: Request):
        require_brain_key(request)
        body = await request.body()
        parts = _multipart(body, request.headers.get("content-type", ""))
        jpeg = parts.get("frame")
        if not jpeg or not jpeg.startswith(b"\xff\xd8") or len(jpeg) > 2_000_000:
            raise HTTPException(400, "frame must be a JPEG")
        try:
            q = (json.loads(parts.get("meta") or b"{}").get("question") or "").strip()
        except ValueError:
            q = ""
        import asyncio

        r = app.state.relay
        text = (
            await asyncio.to_thread(r.judge.describe, jpeg, q) if q else await asyncio.to_thread(r.judge.describe, jpeg)
        )
        del jpeg, body, parts
        return {"description": text}

    @app.get("/pois")
    def pois_proxy(request: Request):
        from brain.console import _authed, edge_call

        if not _authed(request):
            raise HTTPException(401)
        code, out = edge_call("pois")
        return JSONResponse(out if code == 200 else [], status_code=200)

    @app.get("/verdicts")
    def verdicts(request: Request):
        require_brain_key(request)
        return list(app.state.relay.verdicts)

    @app.get("/health")
    def health(request: Request):
        require_brain_key(request)
        r = app.state.relay
        return {
            "ok": True,
            "cosmos": r.cosmos_ok(),
            "frames_seen": r.frames_seen,
            "verdicts": len(r.verdicts),
            "last_frame": r.last_frame,
            "judge": {"calls": r.judge.calls, "failures": r.judge.failures, "last_latency_s": r.judge.last_latency_s},
        }

    @app.post("/ask")
    async def ask(request: Request):
        require_brain_key(request)
        d = await request.json()
        if app.state.concierge is None:
            from concierge.agent import Concierge

            app.state.concierge = Concierge()
        import asyncio

        return JSONResponse(
            await asyncio.to_thread(
                app.state.concierge.ask,
                str(d.get("text", ""))[:500],
                d.get("lang"),
                d.get("session"),
                d.get("context") or {},
                d.get("name"),
            )
        )

    from brain.console import mount

    mount(app)
    return app
