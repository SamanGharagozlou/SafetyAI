"""Loads the operator's guardrail file and answers questions about places.

The venue file (venue/leukerbad_therme.yaml) is the spa's document, not the
engineers'. This module is the only thing that reads it, so the rules it states
in prose are enforced in exactly one place:

  CONFIG CAN ONLY TIGHTEN. Every cap is clamped against the hard ceiling in
  `limits`. A file asking for 2 m/s next to guests gets the ceiling, and the
  discrepancy is reported rather than silently honoured.

  ANYTHING UNSET IS FORBIDDEN. A point inside no described zone is `unknown`
  and treated as restricted. A venue that forgot to describe the corridor does
  not thereby authorise the corridor.

  UNSIGNED MEANS CAUTIOUS. With `signed_off_by` empty the whole venue runs at
  the unverified speed and every zone needs approval. Nobody has agreed to
  these numbers yet, so they are proposals, not permissions.

  UNVERIFIED NUMBERS ARE SLOW. A zone marked `verified: plan` came off a floor
  plan and has not been measured on site, so it runs at `unverified_zone_speed`
  no matter what its own `speed_cap` says.

PyYAML is present on both the Orin (ROS depends on it) and the venue laptop.
If it is ever missing, loading fails loudly rather than falling back to
defaults - running a spa robot on built-in guesses is the failure this file
exists to prevent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

UNKNOWN = "unknown"


@dataclass
class Zone:
    name: str
    label: str = ""
    polygon: list = field(default_factory=list)
    speed_cap: float = 0.15
    restricted: bool = False
    camera_off: bool = False
    requires_approval: bool = False
    reason: str = ""
    verified: str = "plan"

    @property
    def is_verified(self) -> bool:
        return str(self.verified).lower() in ("on_site", "onsite", "measured", "true")


@dataclass
class Venue:
    name: str = "unknown venue"
    area: str = ""
    signed_off_by: str = ""
    signed_off_date: str = ""
    zones: dict = field(default_factory=dict)
    people: dict = field(default_factory=dict)
    limits: dict = field(default_factory=dict)
    escort: dict = field(default_factory=dict)
    destinations: list = field(default_factory=list)
    on_uncertainty: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)
    path: str = ""

    # -- the three rules -------------------------------------------------

    @property
    def signed(self) -> bool:
        return bool(str(self.signed_off_by).strip())

    @property
    def max_speed(self) -> float:
        return float(self.limits.get("max_speed", 0.4))

    @property
    def unverified_speed(self) -> float:
        return float(self.limits.get("unverified_zone_speed", 0.15))

    def zone_at(self, x, y) -> Zone:
        """Which zone a point is in. Nowhere described -> restricted `unknown`."""
        if x is None or y is None:
            return self._unknown("no pose: the robot does not know where it is")
        for z in self.zones.values():
            if z.polygon and _inside(x, y, z.polygon):
                return z
        return self._unknown("this point is in no described zone")

    def _unknown(self, reason) -> Zone:
        return Zone(name=UNKNOWN, label="undescribed area", speed_cap=self.unverified_speed,
                    restricted=True, camera_off=True, requires_approval=True,
                    reason=reason, verified="plan")

    def speed_cap_at(self, x, y) -> float:
        z = self.zone_at(x, y)
        cap = min(float(z.speed_cap), self.max_speed)
        if not z.is_verified:
            cap = min(cap, self.unverified_speed)
        if not self.signed:
            cap = min(cap, self.unverified_speed)
        return cap

    def camera_off_zones(self) -> set:
        off = {n for n, z in self.zones.items() if z.camera_off}
        off.add(UNKNOWN)
        return off

    def may_guide_to(self, dest: str) -> tuple[bool, str]:
        if dest not in self.destinations:
            return False, f"{dest!r} is not a permitted destination in {self.name}"
        z = self.zones.get(dest)
        if z and z.restricted:
            return False, f"{z.label} is restricted: {z.reason or 'no entry'}"
        if not self.signed:
            return False, "the venue guardrail file is not signed off yet"
        return True, ""

    # -- people ----------------------------------------------------------

    def person_policy(self) -> dict:
        """Flat dict for the correlator, with the strict defaults applied."""
        p = self.people or {}
        c = p.get("child") or {}
        return {
            "slow_within_m": float(p.get("slow_within_m", 2.0)),
            "hold_within_m": float(p.get("hold_within_m", 1.0)),
            "near_person_speed": min(float(p.get("near_person_speed", 0.15)), self.max_speed),
            "latch_s": float(p.get("latch_s", 3.0)),
            "stale_frame_s": float(p.get("stale_frame_s", 0.5)),
            "crowd_count": int(p.get("crowd_count", 4)),
            "child_height_under_m": float(c.get("height_under_m", 1.25)),
            "child_freeze_within_m": float(c.get("freeze_within_m", 2.0)),
            "child_latch_s": float(c.get("latch_s", 5.0)),
            "child_forbid_limb_gestures": bool(c.get("forbid_limb_gestures", True)),
        }


def _inside(x: float, y: float, poly: list) -> bool:
    """Ray casting. Points exactly on an edge count as inside - a boundary is
    not a safe place to be ambiguous about."""
    inside = False
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        if (y1 > y) != (y2 > y):
            xint = (x2 - x1) * (y - y1) / ((y2 - y1) or 1e-12) + x1
            if x <= xint:
                inside = not inside
    return inside


def load(path: str) -> Venue:
    try:
        import yaml
    except ImportError as e:                       # fail loud, never on defaults
        raise RuntimeError(
            "PyYAML is required to read the venue guardrail file. Refusing to run "
            "a venue robot on built-in defaults."
        ) from e
    p = Path(path)
    raw = yaml.safe_load(p.read_text()) or {}
    v = raw.get("venue") or {}
    limits = raw.get("limits") or {}
    out = Venue(name=v.get("name", "unknown venue"), area=v.get("area", ""),
                signed_off_by=str(v.get("signed_off_by", "") or ""),
                # str(): an unquoted date in YAML loads as a datetime.date, which
                # is not JSON serializable and takes the whole API response down.
                signed_off_date=str(v.get("signed_off_date", "") or ""),
                people=raw.get("people") or {}, limits=limits,
                escort=raw.get("escort") or {},
                destinations=list(raw.get("guide_destinations") or []),
                on_uncertainty=raw.get("on_uncertainty") or {}, path=str(p))

    ceiling = float(limits.get("max_speed", 0.4))
    for name, z in (raw.get("zones") or {}).items():
        z = z or {}
        asked = float(z.get("speed_cap", ceiling))
        if asked > ceiling:
            out.warnings.append(
                f"zone {name!r} asks for {asked} m/s, above the {ceiling} m/s ceiling - clamped")
        out.zones[name] = Zone(
            name=name, label=z.get("label", name), polygon=list(z.get("polygon") or []),
            speed_cap=min(asked, ceiling), restricted=bool(z.get("restricted")),
            camera_off=bool(z.get("camera_off")),
            requires_approval=bool(z.get("requires_approval")),
            reason=z.get("reason", ""), verified=str(z.get("verified", "plan")))

    if not out.signed:
        out.warnings.append(
            "NOT SIGNED OFF: no operator name in `signed_off_by`. Running the most "
            "cautious profile - every zone capped at the unverified speed and no guiding.")
    unver = [n for n, z in out.zones.items() if not z.is_verified]
    if unver:
        out.warnings.append(
            f"{len(unver)} of {len(out.zones)} zones are `verified: plan`, not measured on "
            f"site ({', '.join(sorted(unver)[:4])}{'...' if len(unver) > 4 else ''}) - "
            f"capped at {out.unverified_speed} m/s")
    for name in out.destinations:
        if name not in out.zones:
            out.warnings.append(f"guide destination {name!r} is not a described zone")
    return out
