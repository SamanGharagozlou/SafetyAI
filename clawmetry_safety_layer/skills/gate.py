"""Command gate: every actuator command the skills send goes through here.

Before a command is sent: predict it over the horizon (bridge/predictor.py), emit a `predict` trace event, check
the prediction against the behaviour policy; a predicted breach emits `breach_predicted` and raises PredictedBreach
so the skill holds pose before anything moves. After it is sent: record it as a `command` event (velocity vector +
duration, merged into segments; or the arm targets + ramp). Pure bookkeeping around the bridge's own calls: the
bridge (and the vendor balance controller under it) is never bypassed.
"""

from __future__ import annotations

import math
import time

from bridge.predictor import ArmCommand, KinematicPredictor, VelocityCommand

# a new velocity "segment" (one predict + one command event) starts when the command changes by more than this
SEG_DV, SEG_DYAW = 0.05, 0.15


class PredictedBreach(Exception):
    def __init__(self, breaches: list[dict], prediction):
        self.breaches, self.prediction = breaches, prediction
        super().__init__("; ".join(b["detail"] for b in breaches))


class CommandGate:
    def __init__(self, skills):
        self.sk = skills
        self.bridge = skills.bridge
        self.predictor = KinematicPredictor(getattr(self.bridge, "speed_scale", 1.0))
        self._seg = None  # current velocity segment {v, t0, n, sum}
        self.commands_sent = 0

    # ---------------------------------------------------------------- helpers
    def _ctx(self):
        s = self.bridge.state()
        near = bool(getattr(self.bridge, "near_person", lambda: False)())
        holding = bool(s.holding or self.sk._carrying)
        return s, holding, self.sk.hold_height(s.arm_q) if holding else None, near

    def check(self, pred, s, holding: bool, hold_height, near: bool) -> list[dict]:
        """Policy breaches anywhere in the prediction (deduplicated by rule+zone), plus the safety supervisor's L1
        go-zone geofence when one is attached (safety/geofence.py)."""
        pol, tc = self.sk.policy, self.sk.time_compression
        start_zone = pol.zones.zone_of(s.x, s.y)
        found: dict[tuple, dict] = {}
        moving_until = pred.duration_s if pred.duration_s is not None else math.inf
        for (t, x, y, _), v in zip(pred.samples(), pred.v, strict=False):
            speed = v / tc if t < moving_until else 0.0
            for b in pol.check_sample(x, y, speed, holding, hold_height, near):
                if b["rule"] == "forbidden_zone" and b["zone"] == start_zone:
                    continue  # already inside (e.g. after a drift): walking out is allowed
                found.setdefault((b["rule"], b.get("zone")), {**b, "t": t})
        saf = getattr(self.sk, "safety", None)
        if saf is not None:
            for b in saf.geofence.check(pred):
                found.setdefault((b["rule"], b.get("zone")), b)
        return list(found.values())

    def _emit(self, kind, **data):
        self.sk.emit(kind, **data)

    def _gate(self, pred, s, holding, hh, near, command: dict):
        breaches = self.check(pred, s, holding, hh, near)
        self._emit(
            "predict",
            command=command,
            prediction=pred.event(),
            breach=bool(breaches),
            time_compression=self.sk.time_compression,
        )
        self.sk._turn_predicted(pred, command)
        if breaches:
            self._emit(
                "breach_predicted",
                command=command,
                breaches=breaches,
                prediction_model=pred.model,
                action="blocked; hold_pose before the command was sent",
                source="L1",
            )
            saf = getattr(self.sk, "safety", None)
            pos = [b for b in breaches if b.get("rule") in ("leaves_go_zone", "forbidden_zone")]
            if saf is not None and command.get("type") == "velocity" and pos:
                # a moving robot about to leave the go zone latches a policy HOLD; a refused plan (nothing moved yet,
                # e.g. "take me to the pool") is only a refusal
                saf.on_breach_predicted(pos, "L1")
            raise PredictedBreach(breaches, pred)

    def _close_segment(self):
        seg, self._seg = self._seg, None
        if seg and seg["n"]:  # n == 0: the body refused the first command (e.g. observe-only); nothing was sent
            dur = time.time() - seg["t0"]
            n = seg["n"]
            self._emit(
                "command",
                type="velocity",
                vector=[round(v, 3) for v in seg["v"]],
                vector_mean=[round(a / n, 3) for a in seg["sum"]],
                duration_s=round(dur, 3),
                n_commands=n,
                speed_scale=getattr(self.bridge, "speed_scale", 1.0),
            )

    # --------------------------------------------------------------- commands
    def move_velocity(
        self, vx: float, vy: float, vyaw: float, until_m: float | None = None, until_yaw: float | None = None
    ) -> None:
        saf = getattr(self.sk, "safety", None)
        if saf is not None:  # FSM: CAUTION caps the speed, HOLD / RECOVER raise SafetyHold (nothing is sent)
            vx, vy, vyaw = saf.filter_velocity(float(vx), float(vy), float(vyaw))
        v = (float(vx), float(vy), float(vyaw))
        seg = self._seg
        new = (
            seg is None
            or abs(v[0] - seg["v"][0]) > SEG_DV
            or abs(v[1] - seg["v"][1]) > SEG_DV
            or abs(v[2] - seg["v"][2]) > SEG_DYAW
        )
        s, holding, hh, near = self._ctx()
        cmd = VelocityCommand(*v, until_m=until_m, until_yaw=until_yaw)
        pred = self.predictor.predict(
            s, cmd, self.sk.policy.horizon_s, self.sk.policy.dt_s, self.sk.policy.zones.zone_of
        )
        if new:
            self._close_segment()
            self._gate(
                pred,
                s,
                holding,
                hh,
                near,
                {"type": "velocity", "vector": [round(a, 3) for a in v], "until_m": until_m, "until_yaw": until_yaw},
            )
            self._seg = {"v": v, "t0": time.time(), "n": 0, "sum": [0.0, 0.0, 0.0]}
        else:
            breaches = self.check(pred, s, holding, hh, near)  # every command is checked; events per segment
            if breaches:
                self._close_segment()
                self._gate(pred, s, holding, hh, near, {"type": "velocity", "vector": [round(a, 3) for a in v]})
        self.bridge.move_velocity(*v)
        self.commands_sent += 1
        self._seg["n"] += 1
        self._seg["sum"] = [a + b for a, b in zip(self._seg["sum"], v, strict=False)]

    def stop(self) -> None:
        self._close_segment()
        self.bridge.stop()

    def stop_and_settle(self):
        self._close_segment()
        return self.bridge.stop_and_settle()

    def set_arm_targets(self, q, ramp_s: float = 2.0) -> None:
        self._close_segment()
        s, holding, hh, near = self._ctx()
        target_h = self.sk.hold_height(q)
        cmd = ArmCommand(list(q), ramp_s, target_h if holding else None)
        pred = self.predictor.predict(
            s, cmd, self.sk.policy.horizon_s, self.sk.policy.dt_s, self.sk.policy.zones.zone_of
        )
        command = {
            "type": "arm",
            "targets": [round(a, 3) for a in q],
            "ramp_s": ramp_s,
            "pose": self.sk.pose_name(q),
            "grasp_height_m": target_h,
        }
        self._gate(pred, s, holding, target_h if holding else None, near, command)
        t0 = time.time()
        self.bridge.set_arm_targets(q, ramp_s)
        self.commands_sent += 1
        dur = time.time() - t0
        self.sk._turn_arm_done(ramp_s, dur)
        self._emit("command", **command, duration_s=round(dur, 3))
