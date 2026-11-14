"""Behaviour policy (policies/behaviour.yaml): load, validate, save, and the rule checks shared by the skills,
the command gate's predictor and the continuous monitor.

A rule check returns Breach records: {rule, severity: soft|hard, detail, ...}. Predicted breaches block the
command before it is sent; the monitor stops the body on a hard breach.
"""

from __future__ import annotations

import copy
import datetime as _dt
import math
import os
import threading
from pathlib import Path

import yaml

from bridge.zones import Zones

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_FILE = ROOT / "policies" / "behaviour.yaml"
SPEED_HARD_MAX = 1.5  # m/s: no cap may exceed what the G1 locomotion controller can do
KNOWN_SKILLS = ("get_state", "navigate_to", "pick", "place", "say", "look_at", "hold_pose", "guide_to", "recover")


class PolicyError(ValueError):
    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = errors


def _num(errs, where, v, lo=0.0, hi=math.inf, strict_lo=True):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or math.isnan(v):
        errs.append(f"{where}: must be a number")
        return
    if (v <= lo if strict_lo else v < lo) or v > hi:
        errs.append(f"{where}: {v} outside {'(' if strict_lo else '['}{lo}, {hi}]")


def _hhmm(errs, where, v):
    try:
        _dt.datetime.strptime(str(v), "%H:%M")
    except ValueError:
        errs.append(f"{where}: {v!r} is not HH:MM")


def validate(raw: dict, zones: Zones) -> list[str]:
    """All problems with a policy document (empty list = valid)."""
    e: list[str] = []
    if not isinstance(raw, dict):
        return ["policy must be a mapping"]
    allowed_top = {
        "name",
        "version",
        "zones_file",
        "zones",
        "near_person_speed_cap",
        "allowed_objects",
        "place_targets",
        "skills",
        "max_retries",
        "forbidden",
        "monitor",
        "prediction",
        "quiet_hours",
    }
    for k in raw:
        if k not in allowed_top:
            e.append(f"unknown key {k!r}")
    if not isinstance(raw.get("name", ""), str):
        e.append("name: must be a string")
    zs = raw.get("zones")
    if not isinstance(zs, dict):
        e.append("zones: must map every zone to {speed_cap}")
    else:
        for z in zs:
            if z not in zones.names():
                e.append(f"zones.{z}: not a zone in the zones file ({zones.names()})")
        for z in zones.names():
            if z not in zs:
                e.append(f"zones.{z}: missing (every zone needs a speed_cap)")
            elif not isinstance(zs[z], dict) or "speed_cap" not in zs[z]:
                e.append(f"zones.{z}: needs speed_cap")
            else:
                _num(e, f"zones.{z}.speed_cap", zs[z]["speed_cap"], 0, SPEED_HARD_MAX)
    _num(e, "near_person_speed_cap", raw.get("near_person_speed_cap"), 0, SPEED_HARD_MAX)
    for key in ("allowed_objects", "place_targets"):
        v = raw.get(key)
        if not isinstance(v, list) or not all(isinstance(x, str) and x for x in v):
            e.append(f"{key}: must be a list of names")
    sk = raw.get("skills")
    if not isinstance(sk, dict):
        e.append("skills: must map skill names to bounds")
    else:
        for name, b in sk.items():
            if name not in KNOWN_SKILLS:
                e.append(f"skills.{name}: unknown skill")
                continue
            if not isinstance(b, dict) or "max_duration_s" not in b:
                e.append(f"skills.{name}: needs max_duration_s")
                continue
            for k, v in b.items():
                if k not in ("max_duration_s", "max_path_m"):
                    e.append(f"skills.{name}.{k}: unknown bound")
                else:
                    _num(e, f"skills.{name}.{k}", v, 0, 600)
    mr = raw.get("max_retries")
    if isinstance(mr, bool) or not isinstance(mr, int) or not 0 <= mr <= 10:
        e.append("max_retries: must be an integer 0..10")
    fb = raw.get("forbidden")
    if not isinstance(fb, dict):
        e.append("forbidden: must be a mapping")
    else:
        for k in ("enter_restricted_zones", "enter_camera_off_zones"):
            if not isinstance(fb.get(k), bool):
                e.append(f"forbidden.{k}: must be true/false")
        if fb.get("enter_restricted_zones") is False:
            e.append("forbidden.enter_restricted_zones: cannot be disabled (zones.yaml restricted is a hard rule)")
        _num(e, "forbidden.max_hold_height_m", fb.get("max_hold_height_m"), 0, 2.5)
        _num(e, "forbidden.max_speed_while_holding_mps", fb.get("max_speed_while_holding_mps"), 0, SPEED_HARD_MAX)
        _num(e, "forbidden.max_tilt_deg", fb.get("max_tilt_deg"), 0, 90)
        for k in fb:
            if k not in (
                "enter_restricted_zones",
                "enter_camera_off_zones",
                "max_hold_height_m",
                "max_speed_while_holding_mps",
                "max_tilt_deg",
            ):
                e.append(f"forbidden.{k}: unknown rule")
    mo = raw.get("monitor", {})
    if not isinstance(mo, dict):
        e.append("monitor: must be a mapping")
    else:
        _num(e, "monitor.period_s", mo.get("period_s", 0.2), 0.02, 5)
        _num(e, "monitor.soft_speed_margin", mo.get("soft_speed_margin", 0.1), 0, 5, strict_lo=False)
        _num(e, "monitor.hard_speed_margin", mo.get("hard_speed_margin", 0.5), 0, 5, strict_lo=False)
        if not e and mo.get("hard_speed_margin", 0.5) < mo.get("soft_speed_margin", 0.1):
            e.append("monitor.hard_speed_margin: must be >= soft_speed_margin")
    pr = raw.get("prediction", {})
    if not isinstance(pr, dict):
        e.append("prediction: must be a mapping")
    else:
        _num(e, "prediction.horizon_s", pr.get("horizon_s", 3.0), 0, 30)
        _num(e, "prediction.dt_s", pr.get("dt_s", 0.1), 0.005, 1)
    qh = raw.get("quiet_hours", {"enabled": False})
    if not isinstance(qh, dict) or not isinstance(qh.get("enabled"), bool):
        e.append("quiet_hours.enabled: must be true/false")
    else:
        _hhmm(e, "quiet_hours.start", qh.get("start", "22:00"))
        _hhmm(e, "quiet_hours.end", qh.get("end", "07:00"))
        fs = qh.get("forbid_skills", [])
        if not isinstance(fs, list) or any(s not in KNOWN_SKILLS for s in fs):
            e.append(f"quiet_hours.forbid_skills: must be skill names from {list(KNOWN_SKILLS)}")
        if "hold_pose" in (fs or []) or "get_state" in (fs or []):
            e.append("quiet_hours.forbid_skills: hold_pose and get_state must always be allowed")
        if "speed_cap" in qh:
            _num(e, "quiet_hours.speed_cap", qh["speed_cap"], 0, SPEED_HARD_MAX)
    return e


class BehaviourPolicy:
    """Thread-safe holder of the current policy. `raw` is the document; accessors read the current one."""

    def __init__(self, path: str | Path | None = None, zones: Zones | None = None):
        self.path = Path(path or os.environ.get("BEHAVIOUR_FILE") or DEFAULT_FILE)
        if not self.path.is_absolute():
            self.path = ROOT / self.path
        raw = yaml.safe_load(self.path.read_text())
        self.zones = zones or Zones(ROOT / raw.get("zones_file", "policies/zones.yaml"))
        errs = validate(raw, self.zones)
        if errs:
            raise PolicyError(errs)
        self._lock = threading.Lock()
        self.raw = raw
        self.clock = _dt.datetime.now  # injectable for quiet-hours tests

    # ------------------------------------------------------------------ edit
    def snapshot(self) -> dict:
        with self._lock:
            return copy.deepcopy(self.raw)

    def replace(self, new: dict, persist: bool = True) -> tuple[dict, list[str]]:
        """Validate and swap in a new document; returns (old, changed top-level keys). Raises PolicyError."""
        errs = validate(new, self.zones)
        if errs:
            raise PolicyError(errs)
        new = copy.deepcopy(new)
        with self._lock:
            old = self.raw
            new["version"] = int(old.get("version", 0)) + 1
            changed = sorted(k for k in set(old) | set(new) if k != "version" and old.get(k) != new.get(k))
            if persist:
                tmp = self.path.with_suffix(".yaml.tmp")
                header = "".join(ln + "\n" for ln in self.path.read_text().splitlines() if ln.startswith("#"))
                tmp.write_text(header + yaml.safe_dump(new, sort_keys=False))
                os.replace(tmp, self.path)
            self.raw = new
        return old, changed

    # ------------------------------------------------------------ accessors
    @property
    def name(self) -> str:
        return str(self.raw.get("name", "behaviour"))

    @property
    def allowed_objects(self) -> list[str]:
        return list(self.raw["allowed_objects"])

    @property
    def place_targets(self) -> list[str]:
        return list(self.raw["place_targets"])

    @property
    def max_retries(self) -> int:
        return int(self.raw["max_retries"])

    @property
    def horizon_s(self) -> float:
        return float(self.raw.get("prediction", {}).get("horizon_s", 3.0))

    @property
    def dt_s(self) -> float:
        return float(self.raw.get("prediction", {}).get("dt_s", 0.1))

    def monitor_cfg(self) -> dict:
        return {"period_s": 0.2, "soft_speed_margin": 0.1, "hard_speed_margin": 0.5, **self.raw.get("monitor", {})}

    def skill_limit(self, skill: str, key: str) -> float | None:
        v = self.raw["skills"].get(skill, {}).get(key)
        return float(v) if v is not None else None

    def quiet_now(self) -> bool:
        qh = self.raw.get("quiet_hours") or {}
        if not qh.get("enabled"):
            return False
        now = self.clock().time()
        a = _dt.datetime.strptime(qh["start"], "%H:%M").time()
        b = _dt.datetime.strptime(qh["end"], "%H:%M").time()
        return (a <= now < b) if a <= b else (now >= a or now < b)

    def speed_cap(self, zone: str, holding: bool = False, near_person: bool = False) -> float:
        cap = float(
            self.raw["zones"]
            .get(zone, {})
            .get("speed_cap", min(float(z["speed_cap"]) for z in self.raw["zones"].values()))
        )
        if holding:
            cap = min(cap, float(self.raw["forbidden"]["max_speed_while_holding_mps"]))
        if near_person:
            cap = min(cap, float(self.raw["near_person_speed_cap"]))
        if self.quiet_now() and "speed_cap" in self.raw["quiet_hours"]:
            cap = min(cap, float(self.raw["quiet_hours"]["speed_cap"]))
        return cap

    def forbidden_zone(self, zone: str) -> str | None:
        """Why entering this zone is forbidden, else None."""
        fb = self.raw["forbidden"]
        z = self.zones.zones.get(zone, {})
        if fb.get("enter_restricted_zones", True) and z.get("restricted"):
            return f"zone {zone} is restricted ({z.get('label', zone)})"
        if fb.get("enter_camera_off_zones") and z.get("camera_off"):
            return f"zone {zone} is a camera_off zone ({z.get('label', zone)})"
        return None

    def skill_forbidden(self, skill: str) -> str | None:
        if self.quiet_now() and skill in (self.raw["quiet_hours"].get("forbid_skills") or []):
            qh = self.raw["quiet_hours"]
            return f"{skill} is not allowed during quiet hours ({qh['start']}-{qh['end']})"
        return None

    def describe_zones(self) -> list[dict]:
        return [
            {
                "zone": n,
                "label": z.get("label", n),
                "restricted": self.zones.is_restricted(n),
                "camera_off": bool(z.get("camera_off")),
                "forbidden": self.forbidden_zone(n) is not None,
                "requires_approval": self.zones.requires_approval(n),
                "speed_cap": self.speed_cap(n),
            }
            for n, z in self.zones.zones.items()
        ]

    # ---------------------------------------------------------------- checks
    def check_sample(
        self,
        x: float,
        y: float,
        speed: float,
        holding: bool,
        hold_height: float | None,
        near_person: bool = False,
        speed_tol: float = 1e-6,
    ) -> list[dict]:
        """Breaches for one (predicted or observed) sample. Severity: forbidden zone / hold height = hard;
        speed is reported here as hard (predictions block on any breach; the monitor grades speed itself)."""
        out = []
        zone = self.zones.zone_of(x, y)
        why = self.forbidden_zone(zone)
        if why:
            out.append({"rule": "forbidden_zone", "severity": "hard", "zone": zone, "detail": why})
        cap = self.speed_cap(zone, holding, near_person)
        if speed > cap + speed_tol:
            rule = (
                "speed_while_holding"
                if holding and cap == float(self.raw["forbidden"]["max_speed_while_holding_mps"])
                else "speed_cap"
            )
            out.append(
                {
                    "rule": rule,
                    "severity": "hard",
                    "zone": zone,
                    "cap": round(cap, 3),
                    "value": round(speed, 3),
                    "detail": f"{speed:.2f} m/s over the {cap:.2f} m/s cap in {zone}",
                }
            )
        mh = float(self.raw["forbidden"]["max_hold_height_m"])
        if holding and hold_height is not None and hold_height > mh + 1e-6:
            out.append(
                {
                    "rule": "hold_height",
                    "severity": "hard",
                    "value": round(hold_height, 3),
                    "cap": mh,
                    "detail": f"carrying at {hold_height:.2f} m, above the {mh:.2f} m limit",
                }
            )
        return out
