"""Safety supervisor (edge): turns L1 / L2 / L3 / link / operator inputs into SafetyFSM causes, enforces the FSM on
every velocity command (through the command gate), stops the running skill on HOLD, runs recovery on Reset and asks
for approval to resume. Enforced in OUR skill server regardless of ClawMetry or the remote brain: if both are gone
the robot is slower and more constrained, never less.

Inputs -> causes (source, level, policy?):
  L1  gate's 2 s prediction leaves the go zone / enters a forbidden zone  -> hold, policy        (breach_predicted)
  L2  person < 1 m                                                     -> hold, ttl 1 s         (person_near)
      person 1-2 m, child within 2 m                                   -> caution, ttl 2 s      (person_near)
      no-go floor / marker ahead within 1.5 m while moving forward      -> hold, policy          (hazard)
      people detection unavailable                                     -> caution               (degraded)
  L3  verdict stop / leaves_go_zone (confidence >= 0.5)                -> hold, policy          (judge_verdict)
      hazards / people within 2 m                                      -> caution, ttl          (judge_verdict)
      judge unavailable (no verdict 10 s after a request, brain down)  -> caution (blocks guide_to if judge_required)
  link brain lost                                                      -> caution               (link_lost)
       control plane lost: hold_at_waypoint (default) -> hold at the next waypoint; continue_caution -> caution
  operator ClawMetry Stop                                              -> hold until Reset      (control.*)
"""

from __future__ import annotations

import math
import re
import threading
import time
from collections import deque
from typing import Any

from .approvals import Approvals
from .fsm import CAUTION, HOLD, NORMAL, RECOVER, SafetyFSM, SafetyHold
from .geofence import Geofence, GoZone

PEOPLE_WORDS = re.compile(
    r"\b(person|people|persons|guest|guests|child|children|kid|kids|adult|adults|man|woman|men|women|crowd|staff)\b",
    re.I,
)
MOTION_SKILLS = ("navigate_to", "guide_to", "pick", "place", "approach")
JUDGE_WAIT_S = 10.0


class SafetySupervisor:
    def __init__(self, skills, cfg: dict | None = None, approvals: Approvals | None = None):
        self.sk = skills
        self.cfg = sc = dict((cfg or skills.cfg).get("safety") or {})
        self.fsm = SafetyFSM(emit=skills.emit, caution_speed=float(sc.get("caution_speed", 0.15)))
        self.go = GoZone(skills.zones, skills.policy)
        self.geofence = Geofence(self.go)
        self.approvals = approvals
        self.judge_required = sc.get("judge_required", False) is True
        self.judge_every_m = float(sc.get("judge_every_m", 2.0))
        self.person_slow_m = float(sc.get("person_slow_m", 2.0))
        self.person_stop_m = float(sc.get("person_stop_m", 1.0))
        self.boundary_hold_m = float(sc.get("boundary_hold_m", 1.5))
        self.margin = float(sc.get("go_zone_margin_m", 0.3))
        rc = sc.get("recover") or {}
        self.rec = {
            "speed": float(rc.get("speed", 0.15)),
            "max_m": float(rc.get("max_m", 1.5)),
            "max_s": float(rc.get("max_s", 8.0)),
        }
        self.on_cp_loss = sc.get("on_control_plane_loss", "hold_at_waypoint")
        if self.on_cp_loss not in ("hold_at_waypoint", "continue_caution"):
            raise ValueError("safety.on_control_plane_loss must be hold_at_waypoint | continue_caution")
        self.go_zone_description = str(sc.get("go_zone_description", "the dry public floor"))
        self.verdicts: deque = deque(maxlen=30)
        self.last_obs = None
        self._edges: dict[str, bool] = {}
        self._judge_flag: str | None = None
        self._judge_asked_t: float | None = None
        self._judge_last_t = 0.0
        self._travel_since_judge = 0.0
        self._last_xy: tuple[float, float] | None = None
        self._last_scene = None
        self.hold_at_waypoint = False
        self.control_plane_ok: bool | None = None
        self.brain_ok: bool | None = None
        self._recovering_motion = False
        self._motion_prev: tuple[float, float, float] | None = None
        self._worker: threading.Thread | None = None
        self.fsm.listeners.append(self._on_transition)
        skills.safety = self

    # ------------------------------------------------------------ enforcement (called by gate / skills)
    def filter_velocity(self, vx: float, vy: float, vyaw: float) -> tuple[float, float, float]:
        st = self.fsm.state
        if st == RECOVER and self._recovering_motion:
            if vx > 0 or abs(vy) > 1e-9 or abs(vyaw) > 1e-9:
                raise SafetyHold("recovery may only back straight up")
            return max(vx, -self.rec["speed"]), 0.0, 0.0
        if st in (HOLD, RECOVER):
            raise SafetyHold(f"safety {st}: {self._why()}")
        cap = self.fsm.speed_cap()
        lin = math.hypot(vx, vy)
        if cap is not None and lin > cap > 0:
            k = cap / lin
            vx, vy = vx * k, vy * k
        return vx, vy, vyaw

    def check_skill(self, name: str) -> str | None:
        if name not in MOTION_SKILLS:
            return None
        st = self.fsm.state
        if st in (HOLD, RECOVER):
            return f"held by safety ({st}): {self._why()}"
        if name == "guide_to" and self.judge_required and self.judge_unavailable():
            return "the Cosmos judge is unavailable and safety.judge_required is true"
        return None

    def on_breach_predicted(self, breaches: list[dict], source: str = "L1"):
        b = breaches[0] if breaches else {}
        self.fsm.raise_cause("l1", source, "hold", f"predicted: {b.get('detail') or b.get('rule')}", policy=True)

    def _why(self) -> str:
        cs = [c for c in self.fsm.causes() if c["level"] == "hold"] or self.fsm.causes()
        snap = self.fsm.snapshot()
        if snap["latched"]:
            return f"{snap['latched']['source']}: {snap['latched']['reason']}" + (
                " (waiting for approval)" if snap["awaiting_approval"] else ""
            )
        return "; ".join(f"{c['source']}: {c['reason']}" for c in cs) or "recovering"

    def _on_transition(self, old, new, reason, source):
        if new == HOLD:
            self.sk.monitor_stop(f"safety HOLD ({source}): {reason}")
            try:
                self.sk.bridge.stop()
            except Exception as e:  # noqa: BLE001
                print(f"[safety] stop on HOLD failed: {e}")

    # ------------------------------------------------------------ L2 perception
    def _edge(self, key: str, on: bool) -> bool:
        """True on the rising edge of a condition."""
        was = self._edges.get(key, False)
        self._edges[key] = on
        return on and not was

    def check_measured_motion(self, s, now: float | None = None) -> bool:
        """L1 on what the robot actually does, not only on what we command: extrapolate the measured velocity over the
        prediction horizon. Catches motion that did not come through our gate (the team's keyboard teleop publishes
        cmd_vel straight to their bridge, wheel slip, a push). Leaving the go zone -> policy HOLD + our stop."""
        now = time.time() if now is None else now
        prev, self._motion_prev = self._motion_prev, (s.x, s.y, now)
        if prev is None or now - prev[2] <= 1e-3 or now - prev[2] > 1.0:
            return False
        dt = now - prev[2]
        if math.hypot(s.x - prev[0], s.y - prev[1]) > 1.0:
            return False  # a relocalisation / re-fix jump, not motion (the monitor uses the same 1 m rule)
        vx, vy = (s.x - prev[0]) / dt, (s.y - prev[1]) / dt
        if math.hypot(vx, vy) < 0.05 or not self.go.contains(s.x, s.y):
            return False
        h = float(self.sk.policy.horizon_s)
        for k in range(1, 11):
            t = h * k / 10
            px, py = s.x + vx * t, s.y + vy * t
            if not self.go.contains(px, py):
                z = self.sk.zones.zone_of(px, py)
                self.sk.emit(
                    "breach_predicted",
                    source="L1",
                    basis="measured_motion",
                    t=round(t, 2),
                    at=[round(px, 2), round(py, 2)],
                    zone=z,
                    speed_mps=round(math.hypot(vx, vy), 3),
                )
                self.fsm.raise_cause(
                    "l1", "L1", "hold", f"measured motion leaves the go zone in {t:.1f} s (into {z})", policy=True
                )
                return True
        return False

    def on_observation(self, obs):
        self.last_obs = obs
        s = self.sk.bridge.state()
        moving = s.mode == "walking"
        self.check_measured_motion(s)
        # travel since the last judge call -> every judge_every_m metres
        if self._last_xy is not None:
            self._travel_since_judge += math.hypot(s.x - self._last_xy[0], s.y - self._last_xy[1])
        self._last_xy = (s.x, s.y)
        if moving and self._travel_since_judge >= self.judge_every_m:
            self.request_judge("every_2m")
        if obs.camera_off:
            return
        d = obs.nearest_person_m
        if d is not None and d < self.person_stop_m:
            if self._edge("person_stop", True):
                self.sk.emit(
                    "person_near",
                    distance_m=round(d, 2),
                    level="stop",
                    source="L2",
                    people_within_2m=obs.people_within_2m,
                )
            self.fsm.raise_cause(
                "person_stop", "L2", "hold", f"person at {d:.1f} m (< {self.person_stop_m} m)", ttl=1.0
            )
        else:
            self._edge("person_stop", False)
        if (d is not None and d < self.person_slow_m) or obs.child_near:
            if self._edge("person_near", True):
                self.sk.emit(
                    "person_near",
                    distance_m=None if d is None else round(d, 2),
                    level="slow",
                    child=obs.child_near,
                    source="L2",
                    people_within_2m=obs.people_within_2m,
                )
            self.fsm.raise_cause(
                "person_near",
                "L2",
                "caution",
                f"person within {self.person_slow_m:.0f} m" + (" (child)" if obs.child_near else ""),
                ttl=2.0,
            )
        else:
            self._edge("person_near", False)
        if "l2_people_disabled" in obs.degraded:
            self.fsm.raise_cause(
                "l2_degraded", "L2", "caution", "people detection unavailable (detector down)", ttl=2.0
            )
        b = obs.boundary
        if b is not None and b.no_go_ahead and (b.ahead_m is None or b.ahead_m < self.boundary_hold_m):
            if self._edge("l2_boundary", True):
                self.sk.emit(
                    "hazard",
                    source="L2",
                    detector=b.detector,
                    ahead_m=None if b.ahead_m is None else round(b.ahead_m, 2),
                    floor_here=b.floor_here,
                    floor_ahead=b.floor_ahead,
                )
                self.request_judge("hazard")
            if moving:
                self.fsm.raise_cause(
                    "l2_boundary",
                    "L2",
                    "hold",
                    (
                        f"{b.floor_ahead} ahead at {b.ahead_m:.1f} m ({b.detector})"
                        if b.ahead_m is not None
                        else f"no-go floor ({b.floor_here or 'marker'}) under the camera ({b.detector})"
                    ),
                    policy=True,
                )
        else:
            self._edge("l2_boundary", False)
        if b is not None and b.fix:
            self.sk.emit("landmark_seen", **b.fix)
            if (
                self.cfg.get("auto_refix") is True
                and b.fix["range_m"] < 1.0
                and hasattr(self.sk.bridge, "refix_at_landmark")
            ):
                self.sk.bridge.refix_at_landmark(b.fix["landmark"])
        if obs.scene != "unknown" and obs.scene_conf >= 0.5 and obs.scene != self._last_scene:
            if self._last_scene is not None:
                self.request_judge(f"scene:{obs.scene}")
            self._last_scene = obs.scene
        # a requested verdict that never came -> judge unavailable
        if self._judge_asked_t is not None and time.time() - self._judge_asked_t > JUDGE_WAIT_S:
            self._judge_asked_t = None
            self.fsm.raise_cause("judge", "L3", "caution", "judge_unavailable: no verdict 10 s after the request")

    # ------------------------------------------------------------ L3 judge (frames go out via the sampler)
    def request_judge(self, why: str):
        self._judge_flag = why
        self._travel_since_judge = 0.0

    def take_judge_flag(self) -> str | None:
        f, self._judge_flag = self._judge_flag, None
        if f is not None and self._judge_asked_t is None:
            self._judge_asked_t = time.time()
        return f

    def judge_unavailable(self) -> bool:
        return any(c["key"] in ("judge", "brain_link") for c in self.fsm.causes()) or self.brain_ok is False

    def on_verdict(self, v: dict):
        self._judge_asked_t = None
        self._judge_last_t = time.time()
        v = dict(v)
        v.setdefault("ts", time.time())
        self.verdicts.append(v)
        self.sk.emit(
            "judge_verdict",
            **{
                k: v.get(k)
                for k in (
                    "leaves_go_zone",
                    "hazards",
                    "people_within_2m",
                    "stop",
                    "confidence",
                    "reason",
                    "latency_s",
                    "trigger",
                    "judge_unavailable",
                    "error",
                )
                if k in v
            },
        )
        if v.get("judge_unavailable"):
            self.fsm.raise_cause("judge", "L3", "caution", f"judge_unavailable: {v.get('error', '')}"[:120])
            return
        self.fsm.clear_cause("judge", "verdict received")
        conf = float(v.get("confidence", 0.0))
        # The 2B judge is sometimes inconsistent (stop=true with "path is clear"; docs/COSMOS.md): only a visible exit
        # from the go zone, or a stop backed by a hazard it saw, latches a HOLD. A bare stop is CAUTION.
        # Cosmos-Reason2-2B is not consistent about `stop` (stop=true with "path is clear", stop=true with "objects do
        # not obstruct"; docs/COSMOS.md): only a visible exit from the go zone latches a HOLD. Any stop is CAUTION;
        # people are L2's job (slow at 2 m, hold under 1 m, clears by itself).
        if conf >= 0.5 and v.get("leaves_go_zone"):
            self.fsm.raise_cause("l3", "L3", "hold", f"judge: {v.get('reason') or 'leaves the go zone'}", policy=True)
        elif conf >= 0.5 and v.get("stop"):
            self.fsm.raise_cause("l3_stop", "L3", "caution", f"judge said stop: {v.get('reason', '')}"[:120], ttl=5.0)
        if v.get("hazards"):
            self.sk.emit("hazard", source="L3", hazards=v["hazards"][:5])
            self.fsm.raise_cause("l3_hazard", "L3", "caution", "judge hazards: " + ", ".join(v["hazards"][:3]), ttl=5.0)
        if int(v.get("people_within_2m") or 0) > 0:
            self.fsm.raise_cause(
                "l3_people", "L3", "caution", f"judge: {v['people_within_2m']} people within 2 m", ttl=3.0
            )

    # ------------------------------------------------------------ link
    def on_link(self, name: str, ok: bool, detail: dict | None = None):
        prev = getattr(self, f"{name}_ok", None)
        setattr(self, f"{name}_ok", ok)
        if prev is not None or not ok:
            self.sk.emit(
                "link_restored" if ok else "link_lost",
                link=name,
                **({} if detail is None else {k: v for k, v in detail.items() if k.endswith(("error", "age_s"))}),
            )
        if name == "brain":
            if ok:
                self.fsm.clear_cause("brain_link", "brain link restored")
            else:
                self.fsm.raise_cause(
                    "brain_link", "link", "caution", "brain unreachable: judge_unavailable, concierge local fallback"
                )
        elif name == "control_plane":
            if ok:
                self.hold_at_waypoint = False
                self.fsm.clear_cause("control_plane", "control plane restored")
                self.fsm.clear_cause("control_plane_hold", "control plane restored")
            elif self.on_cp_loss == "continue_caution":
                self.fsm.raise_cause(
                    "control_plane", "link", "caution", "ClawMetry unreachable: continue at caution speed"
                )
            else:
                self.hold_at_waypoint = True
                self.fsm.raise_cause(
                    "control_plane", "link", "caution", "ClawMetry unreachable: holding at the next waypoint"
                )

    def at_waypoint(self, name: str):
        """guide_to calls this between legs; with the control plane lost (hold_at_waypoint) the robot holds here."""
        if self.hold_at_waypoint:
            self.fsm.raise_cause(
                "control_plane_hold", "link", "hold", f"ClawMetry unreachable: holding at waypoint {name}"
            )
            raise SafetyHold(f"holding at waypoint {name}: ClawMetry control plane unreachable")

    # ------------------------------------------------------------ operator: Stop / Reset / Approve
    def operator_stop(self, reason: str = "clawmetry_stop"):
        self.fsm.raise_cause("operator_stop", "operator", "hold", f"operator stop ({reason})")

    def reset(self, by: str = "operator", wait: bool = False) -> dict:
        """ClawMetry Reset / console: recover into the go zone if needed, then re-arm (NORMAL), or ask for approval
        first when the hold was policy-related."""
        if self._worker is not None and self._worker.is_alive():
            return {"ok": False, "detail": "recovery already running"}
        latched = self.fsm.reset(by)

        def work():
            s = self.sk.bridge.state()
            res: dict[str, Any] = {"ok": True, "detail": "inside the go zone", "moved_m": 0.0}
            if not self.go.inside_with_margin(s.x, s.y, self.margin):
                self._recovering_motion = True
                try:
                    res = self.sk.run_recover(self.go, **self.rec, margin=self.margin)
                finally:
                    self._recovering_motion = False
            if latched:
                self.fsm.recovered(res["ok"], res["detail"])
                if res["ok"] and self.approvals is not None:
                    self._ask_approval()
            elif not res["ok"]:
                self.fsm.raise_cause(
                    "recover_failed", "operator", "hold", f"recovery failed: {res['detail']}", policy=True
                )

        self._worker = threading.Thread(target=work, daemon=True, name="safety-reset")
        self._worker.start()
        if wait:
            self._worker.join()
        return {"ok": True, "latched": latched, "state": self.fsm.state}

    def _ask_approval(self):
        if self.approvals is None:
            return
        snap = self.fsm.snapshot()
        s = self.sk.bridge.state()
        r = self.approvals.request(
            "mcp__g1-skills__resume_after_hold",
            {
                "robot": self.sk.bridge.body,
                "hold": snap["latched"],
                "pose": [round(s.x, 2), round(s.y, 2)],
                "zone": s.zone,
            },
            cancelled=lambda: not self.fsm.awaiting_approval,
        )
        self.sk.emit(
            "approval",
            tool="resume_after_hold",
            decision=r["decision"],
            approval_id=r.get("approval_id"),
            waited_s=r.get("waited_s"),
        )
        if r["decision"] == "allow":
            self.fsm.approve("ClawMetry")
        elif r["decision"] == "deny":
            self.fsm.deny("ClawMetry")
        # no_rule / unavailable: stays waiting; the console's Approve (edge key) decides

    def approve(self, by: str) -> bool:
        ok = self.fsm.approve(by)
        self.sk.emit("approval", tool="resume_after_hold", decision="allow" if ok else "ignored", by=by[:80])
        return ok

    def deny(self, by: str):
        self.fsm.deny(by)
        self.sk.emit("approval", tool="resume_after_hold", decision="deny", by=by[:80])

    def snapshot(self) -> dict:
        snap = self.fsm.snapshot()
        snap.update(
            hold_at_waypoint=self.hold_at_waypoint,
            judge_required=self.judge_required,
            judge_unavailable=self.judge_unavailable(),
            last_verdict=self.verdicts[-1] if self.verdicts else None,
            brain_ok=self.brain_ok,
            control_plane_ok=self.control_plane_ok,
        )
        return snap


__all__ = ["SafetySupervisor", "SafetyHold", "NORMAL", "CAUTION", "HOLD", "RECOVER"]
