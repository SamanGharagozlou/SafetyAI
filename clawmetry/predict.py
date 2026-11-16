"""Predict every action before it runs, and score it after. Two models, on purpose.

The deck asks for prediction before and breach detection after. Those are two
different jobs with incompatible latency budgets, so they get two models:

  KINEMATIC (always, ~0 ms, deterministic)
      Where this command puts the robot, from the command itself and the
      device profile. Cheap enough to run inside the gate, so it can refuse
      an action BEFORE it starts. This is what makes "even a misfire cannot
      move the robot into a restricted zone" true.

  COSMOS (best effort, seconds, a world model)
      Looks at the scene and the proposed action and answers the questions
      geometry cannot: is that a child, is the floor wet, is that doorway
      actually passable. Runs OFF the critical path, annotates and escalates.

WHY COSMOS MUST NOT GATE SYNCHRONOUSLY. A judge on a remote Spark, over
Tailscale, from a Swiss venue to the UK, cannot be in the path of a stop. The
robot covers ~12 cm per 250 ms at guide pace. A gate that waits seconds for a
verdict is a gate that has already let the thing happen. So the kinematic
model holds the gate and Cosmos escalates behind it - and when Cosmos is
unreachable the system degrades to "kinematically gated, unjudged", which is
recorded as such rather than quietly passing as safe.

The deviation score deliberately reuses the edge's formula (telemetry/otel.py:
max(position_error/pos_sigma, |duration_error|/dur_sigma), defaults 0.10 m and
15 % of predicted duration floored at 0.5 s) so a sigma means the same thing
in both dashboards. Two governance systems that disagree about what counts as
an anomaly are worse than one.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field, asdict

# Device profiles - the "one brain, many bodies" seam. The bridge is the only
# other code that knows which body is attached.
PROFILES = {
    # AgiBot D1 Ultra-W. speed is a FRACTION of the cap, not m/s - a unit
    # confusion that has already cost this project a debugging session.
    "agibot_d1w": {
        "max_vx": 0.4, "max_vy": 0.15, "max_wz": 0.6,
        "speed_is_fraction": True,
        "turn_rate_rad_s": 1.0,
        # MEASURED ON THE ROBOT, 2026-11-15, 5 walks, battery 49% (baseline.json).
        #
        # Two things the obvious model gets wrong, both of which produced false
        # anomalies before they were measured:
        #
        # 1. Duration is NOT distance/speed. It is overhead + distance/v. A
        #    legged gait spends its first strides ramping up and then waits out
        #    SETTLE_S while it coasts to a stop. Fitting the three forward legs
        #    gives v=0.1373 m/s and overhead=4.11 s, which predicts 11.19 s and
        #    14.70 s against measured 11.2 s and 14.7 s.
        # 2. It UNDERSHOOTS by a near-constant 4.1 cm +/- 0.7 cm at every
        #    distance - the agent's coast compensation over-cuts slightly. A
        #    predictor that ignores this is wrong by a fixed amount every time,
        #    which is the kind of error that looks like a real drift.
        #
        # The earlier simulator-derived guess was 0.041 m/s: wrong by 3.4x.
        "walk_speed_mps": 0.1373,
        "walk_overhead_s": 4.11,
        "walk_undershoot_m": 0.041,
        # n=5, so the sigmas are deliberately looser than the observed spread
        # (0.7 cm, 0.48 s). Tight sigmas off five samples manufacture breaches.
        "calibrated": True,
        "pos_sigma_m": 0.04, "dur_sigma_frac": 0.10, "dur_sigma_min_s": 1.5,
    },
    "unitree_g1": {
        "max_vx": 0.6, "max_vy": 0.2, "max_wz": 0.8,
        "speed_is_fraction": False,
        "turn_rate_rad_s": 1.0,
        "walk_speed_mps": 0.3,
        "calibrated": False,
        "pos_sigma_m": 0.10, "dur_sigma_frac": 0.15, "dur_sigma_min_s": 0.5,
    },
    "fake": {
        "max_vx": 0.5, "max_vy": 0.2, "max_wz": 1.0,
        "speed_is_fraction": True,
        "turn_rate_rad_s": 1.0,
        "walk_speed_mps": 0.2,
        "calibrated": True,
        "pos_sigma_m": 0.10, "dur_sigma_frac": 0.15, "dur_sigma_min_s": 0.5,
    },
}

DIR_VEC = {
    "forward": (1.0, 0.0), "backward": (-1.0, 0.0),
    "strafe_left": (0.0, 1.0), "strafe_right": (0.0, -1.0),
}
TURNS = {"turn_left": 1.0, "turn_right": -1.0}


@dataclass
class Prediction:
    tool: str
    basis: str                      # kinematic | cosmos | none
    duration_s: float = 0.0
    end_pose: tuple = (0.0, 0.0, 0.0)   # x, y, yaw in the frame of the input state
    distance_m: float = 0.0
    predicted_breach: bool = False
    calibrated: bool = True
    reason: str = ""
    zones_crossed: list = field(default_factory=list)
    confidence: float = 1.0

    def as_dict(self) -> dict:
        d = asdict(self)
        d["end_pose"] = list(self.end_pose)
        return d


def kinematic_predict(tool: str, args: dict, state: dict, body: str = "fake",
                      zones=None) -> Prediction:
    """Where this command puts the robot. Deterministic, instant, gate-safe."""
    p = PROFILES.get(body, PROFILES["fake"])
    x = float(state.get("x") or 0.0)
    y = float(state.get("y") or 0.0)
    yaw = float(state.get("yaw") or 0.0)
    args = args or {}

    dur, dist, dyaw = 0.0, 0.0, 0.0

    if tool == "walk_distance":
        cm = float(args.get("distance_cm") or 0.0)
        dist = abs(cm) / 100.0
        if (args.get("direction") or "forward") == "backward":
            dist = -dist
        dur = p.get("walk_overhead_s", 0.0) + abs(dist) / max(p.get("walk_speed_mps", 0.2), 1e-6)
        # systematic undershoot: the robot stops this far short, every time
        u = p.get("walk_undershoot_m", 0.0)
        dist = (dist - u) if dist > 0 else (dist + u)

    elif tool == "move_robot":
        d = args.get("direction") or "forward"
        dur = float(args.get("duration") or 1.0)
        frac = float(args.get("speed") or 0.3)
        # the fraction/m-s trap: on a fraction body, speed scales the cap
        v = p["max_vx"] * frac if p["speed_is_fraction"] else min(frac, p["max_vx"])
        if d in TURNS:
            dyaw = TURNS[d] * p["turn_rate_rad_s"] * dur
        elif d in DIR_VEC:
            dist = v * dur * DIR_VEC[d][0]
            lat = v * dur * DIR_VEC[d][1]
            x += -math.sin(yaw) * lat
            y += math.cos(yaw) * lat

    elif tool in ("guide_to", "navigate_to", "go_to_place"):
        # A named destination: we only know the straight-line distance if the
        # caller gave one. Without it the honest prediction is "unknown", and
        # an unknown prediction must not read as a safe one.
        dist = float(args.get("distance_cm") or 0.0) / 100.0
        dur = p.get("walk_overhead_s", 0.0) + abs(dist) / max(p.get("walk_speed_mps", 0.2), 1e-6)
        # systematic undershoot: the robot stops this far short, every time
        u = p.get("walk_undershoot_m", 0.0)
        dist = (dist - u) if dist > 0 else (dist + u) if dist else 0.0
        if not dist:
            return Prediction(tool=tool, basis="kinematic", confidence=0.0,
                              reason="no distance given: end state unknown, not predicted safe",
                              end_pose=(x, y, yaw))

    else:
        # gestures, posture, speech: no translation expected
        return Prediction(tool=tool, basis="kinematic", duration_s=0.0,
                          end_pose=(x, y, yaw), distance_m=0.0)

    nx = x + math.cos(yaw) * dist
    ny = y + math.sin(yaw) * dist
    nyaw = _wrap(yaw + dyaw)

    pred = Prediction(tool=tool, basis="kinematic", duration_s=round(dur, 2),
                      end_pose=(round(nx, 3), round(ny, 3), round(nyaw, 3)),
                      distance_m=round(abs(dist), 3),
                      calibrated=bool(p.get("calibrated", False)))

    crossed = _zones_crossed((x, y), (nx, ny), zones or [])
    pred.zones_crossed = [z["zone"] for z in crossed]
    bad = [z for z in crossed if z.get("forbidden") or z.get("no_go") or z.get("restricted")]
    if bad:
        pred.predicted_breach = True
        why = bad[0].get("reason") or "no entry"
        pred.reason = f"path enters {bad[0].get('label') or bad[0]['zone']}: {why}"
    return pred


def _wrap(a: float) -> float:
    return (a + math.pi) % (2 * math.pi) - math.pi


def _in_zone(px, py, z) -> bool:
    poly = z.get("polygon")
    if poly:
        inside, n = False, len(poly)
        for i in range(n):
            x1, y1 = poly[i]
            x2, y2 = poly[(i + 1) % n]
            if (y1 > py) != (y2 > py):
                xint = (x2 - x1) * (py - y1) / ((y2 - y1) or 1e-12) + x1
                if px <= xint:
                    inside = not inside
        return inside
    return (z.get("x0", -1e9) <= px <= z.get("x1", 1e9)
            and z.get("y0", -1e9) <= py <= z.get("y1", 1e9))


def _zones_crossed(start, end, zones) -> list:
    """Sample the straight path; any zone containing a sample is crossed.

    Sampling rather than analytic intersection on purpose: the venue file's
    zones are hand-edited polygons, paths are a few metres, and 5 cm sampling
    is well inside the pose error we already carry. Accepts both the venue
    file's polygons and plain boxes.
    """
    out, seen = [], set()
    dx, dy = end[0] - start[0], end[1] - start[1]
    steps = max(2, int(math.hypot(dx, dy) / 0.05) + 1)
    for i in range(steps + 1):
        t = i / steps
        px, py = start[0] + dx * t, start[1] + dy * t
        for z in zones:
            name = z.get("zone")
            if name in seen:
                continue
            if _in_zone(px, py, z):
                seen.add(name)
                out.append(z)
    return out


def verify(prediction: dict, outcome: dict, body: str = "fake") -> dict:
    """Score what happened against what was predicted. Returns the deviation z.

    Formula matches clawmetry_safety_layer/telemetry/otel.py so the number
    means the same thing on both dashboards.
    """
    p = PROFILES.get(body, PROFILES["fake"])
    pred_pose = (prediction or {}).get("end_pose")
    act_pose = (outcome or {}).get("end_pose")

    # "The robot is at the origin" and "nobody told me where the robot is" are
    # different facts, and conflating them invents anomalies. The simulator
    # publishes no odometry at all, which the first version scored as a 10
    # sigma position error on a walk that completed perfectly.
    scored_position = bool(pred_pose) and bool(act_pose)
    pos_err = (math.hypot(float(act_pose[0]) - float(pred_pose[0]),
                          float(act_pose[1]) - float(pred_pose[1]))
               if scored_position else 0.0)

    pred_dur = float((prediction or {}).get("duration_s") or 0.0)
    act_dur = float((outcome or {}).get("duration_s") or 0.0)
    dur_err = abs(act_dur - pred_dur)

    pos_sigma = p["pos_sigma_m"]
    dur_sigma = max(pred_dur * p["dur_sigma_frac"], p["dur_sigma_min_s"])

    z_pos = pos_err / pos_sigma if pos_sigma else 0.0
    z_dur = dur_err / dur_sigma if dur_sigma else 0.0

    # An uncalibrated profile's predicted duration is a guess. Scoring a real
    # robot against a guess manufactures anomalies - the first run of this
    # collector reported 46.7 sigma on a walk that went exactly as the
    # simulator intended, purely because nobody had measured the walk speed.
    # Geometry needs no baseline, so position is always scored; duration is
    # scored only once the body has been calibrated.
    scored_duration = bool((prediction or {}).get("calibrated", False))
    zs = ([z_pos] if scored_position else []) + ([z_dur] if scored_duration else [])
    # Nothing scoreable -> no score. `None` is not zero: the dashboard shows
    # "unverified", which is a finding of its own, not a pass.
    dev = round(max(zs), 2) if zs else None
    return {
        "deviation_z": dev,
        "position_scored": scored_position,
        "position_error_m": round(pos_err, 3) if scored_position else None,
        "duration_error_s": round(dur_err, 2),
        "z_position": round(z_pos, 2),
        "z_duration": round(z_dur, 2),
        "duration_scored": scored_duration,
        "basis": (prediction or {}).get("basis", "none"),
    }


class BrainJudge:
    """Cosmos-Reason2 through eaby's brain relay (POST /frame) rather than vLLM direct.

    Going through the relay instead of straight to vLLM is deliberate: the relay
    carries his prompt and verdict schema and records the verdict in HIS system
    too, so both dashboards see the same judgement instead of two world models
    disagreeing about the same robot. Contract read from
    clawmetry_safety_layer/brain/relay.py, not guessed:

        POST {brain}/frame   X-Api-Key: <BRAIN_API_KEY>
        multipart: frame=<JPEG, <=640x360, <=2MB>, meta=<json>
        meta.judge truthy triggers the judge; otherwise the frame is just filed.
    """

    def __init__(self, brain_url: str = "", key: str = "", timeout_s: float = 10.0):
        self.url = (brain_url or os.environ.get("BRAIN_URL", "")).rstrip("/")
        self.key = key or os.environ.get("BRAIN_API_KEY", "")
        self.timeout_s = timeout_s
        self.seq = 0

    @property
    def configured(self) -> bool:
        return bool(self.url and self.key)

    def judge(self, action: str, args: dict, scene: dict, jpeg: bytes = b"") -> dict:
        if not self.configured:
            return {"available": False,
                    "reason": "BRAIN_URL/BRAIN_API_KEY not set - no world model attached"}
        if not jpeg.startswith(b"\xff\xd8"):
            return {"available": False, "reason": "no JPEG frame to judge"}
        self.seq += 1
        meta = {"ts": time.time(), "seq": self.seq, "judge": True,
                "action": f"{action} {json.dumps(args or {})}",
                "zone": scene.get("zone"), "pose": scene.get("pose")}
        body, ctype = _multipart({"frame": (jpeg, "image/jpeg")},
                                 {"meta": json.dumps(meta)})
        t0 = time.time()
        try:
            req = urllib.request.Request(f"{self.url}/frame", data=body, method="POST",
                                         headers={"content-type": ctype, "x-api-key": self.key})
            with urllib.request.urlopen(req, timeout=self.timeout_s) as r:
                body = json.loads(r.read() or b"{}")
            lat = round(time.time() - t0, 2)
            if not body.get("judged", True):
                return {"available": False, "latency_s": lat,
                        "reason": "relay filed the frame but did not judge it"}

            # THE VERDICT IS NESTED: {"ok":true,"judged":true,"verdict":{...}}
            # (brain/relay.py frame()). Reading `stop` from the top level found
            # nothing and reported unsafe=False - so a real Cosmos verdict of
            # stop=true at 0.98 confidence ("backpack and blue object are in the
            # path") was delivered to the gate as APPROVED. A governance layer
            # that inverts a stop is worse than no governance layer.
            v = body.get("verdict") or {}
            keys = {"stop", "unsafe", "leaves_go_zone", "hazards", "reason", "confidence"}
            if not isinstance(v, dict) or not (keys & set(v)):
                # Unparseable is NOT safe. Fail closed, loudly.
                return {"available": False, "latency_s": lat,
                        "reason": f"judge answered in a shape this client does not "
                                  f"understand: {sorted(v)[:6] if isinstance(v, dict) else type(v).__name__}",
                        "raw": body}
            flags_unsafe = bool(v.get("stop") or v.get("unsafe") or v.get("leaves_go_zone"))
            # THE JUDGE CAN CONTRADICT ITSELF. Observed on the real robot: flags
            # said stop=false, leaves_go_zone=false, while the same verdict's
            # prose read "forward is unsafe because the person is in the path".
            # The gate trusted the flags and allowed the walk. A judge whose
            # structured output disagrees with its own reasoning is not a clear
            # answer, so this resolves the disagreement the only safe way - the
            # cautious reading wins - and says so, rather than silently picking.
            prose = str(v.get("reason", "")).lower()
            prose_unsafe = any(w in prose for w in
                               ("unsafe", "must stop", "should stop", "do not", "danger",
                                "blocked", "in the path", "collision", "not safe"))
            contradiction = prose_unsafe and not flags_unsafe
            return {"available": True, "latency_s": lat,
                    "unsafe": flags_unsafe or contradiction,
                    "flags_unsafe": flags_unsafe,
                    "contradiction": contradiction,
                    "reason": str(v.get("reason", ""))[:200],
                    "hazards": v.get("hazards") or [],
                    "people_within_2m": v.get("people_within_2m"),
                    "confidence": float(v.get("confidence") or 0.0),
                    "model": v.get("model"), "raw": v}
        except Exception as e:
            # Never an opinion on failure: a judge that fails open is
            # indistinguishable from a judge that approved.
            return {"available": False, "latency_s": round(time.time() - t0, 2),
                    "reason": f"brain unreachable: {e}"}


def _multipart(files: dict, fields: dict) -> tuple[bytes, str]:
    b = "----clawmetry" + hashlib.sha256(str(time.time()).encode()).hexdigest()[:16]
    out = []
    for k, v in fields.items():
        out += [f"--{b}\r\n".encode(),
                f'Content-Disposition: form-data; name="{k}"\r\n\r\n'.encode(),
                v.encode() if isinstance(v, str) else v, b"\r\n"]
    for k, (data, ctype) in files.items():
        out += [f"--{b}\r\n".encode(),
                f'Content-Disposition: form-data; name="{k}"; filename="{k}.jpg"\r\n'.encode(),
                f"Content-Type: {ctype}\r\n\r\n".encode(), data, b"\r\n"]
    out.append(f"--{b}--\r\n".encode())
    return b"".join(out), f"multipart/form-data; boundary={b}"


class CosmosJudge:
    """Cosmos-Reason2 over an OpenAI-compatible endpoint (vLLM).

    Best effort by contract. Unreachable, slow or malformed answers all return
    `available: False` rather than an opinion, because a judge that fails open
    is indistinguishable from a judge that approved.
    """

    PROMPT = (
        "You are a safety reviewer for a service robot in a public spa. "
        "Given the scene and the proposed action, answer STRICT JSON only: "
        '{"unsafe": bool, "reason": "<=20 words", "confidence": 0..1}. '
        "Unsafe means: a person could be struck, the robot would enter water, "
        "stairs, or a restricted area, or the floor ahead is not clearly passable."
    )

    def __init__(self, url: str = "", model: str = "", api_key: str = "", timeout_s: float = 8.0):
        self.url = (url or os.environ.get("COSMOS_URL", "")).rstrip("/")
        self.model = model or os.environ.get("COSMOS_MODEL", "nvidia/Cosmos-Reason2-2B")
        self.key = api_key or os.environ.get("COSMOS_API_KEY", "")
        self.timeout_s = timeout_s

    @property
    def configured(self) -> bool:
        return bool(self.url)

    def judge(self, action: str, args: dict, scene: dict, image_b64: str = "") -> dict:
        if not self.configured:
            return {"available": False, "reason": "COSMOS_URL not set"}
        content = [{"type": "text", "text":
                    f"{self.PROMPT}\n\nProposed action: {action} {json.dumps(args or {})}"
                    f"\nScene: {json.dumps(scene or {})}"}]
        if image_b64:
            content.append({"type": "image_url",
                            "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}})
        body = {"model": self.model, "max_tokens": 120, "temperature": 0,
                "messages": [{"role": "user", "content": content}]}
        headers = {"content-type": "application/json"}
        if self.key:
            headers["authorization"] = f"Bearer {self.key}"
        t0 = time.time()
        try:
            req = urllib.request.Request(f"{self.url}/chat/completions",
                                         data=json.dumps(body).encode(),
                                         headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=self.timeout_s) as r:
                raw = json.loads(r.read())
            text = raw["choices"][0]["message"]["content"]
            verdict = json.loads(_first_json(text))
            return {"available": True, "latency_s": round(time.time() - t0, 2),
                    "unsafe": bool(verdict.get("unsafe")),
                    "reason": str(verdict.get("reason", ""))[:200],
                    "confidence": float(verdict.get("confidence", 0.0))}
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            return {"available": False, "latency_s": round(time.time() - t0, 2),
                    "reason": f"judge unreachable: {e}"}
        except (KeyError, ValueError, TypeError) as e:
            return {"available": False, "latency_s": round(time.time() - t0, 2),
                    "reason": f"judge returned something unparseable: {e}"}


def _first_json(text: str) -> str:
    i, j = text.find("{"), text.rfind("}")
    return text[i:j + 1] if i >= 0 and j > i else "{}"
