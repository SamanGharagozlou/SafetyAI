"""Editing the operator's guardrail file, safely, from the dashboard.

WHY THIS MODULE EXISTS
    Until now the only way to change what the robot may do was to SSH into the
    Orin and edit YAML. That is fine for an engineer and useless for the person
    who is actually responsible for guest safety - who is the person the file
    says it is written for. A guardrail nobody at the venue can read or change
    is not a guardrail, it is a hardcoded constant with a comment.

THE TWO RULES THIS MODULE ADDS

    1. EVERY CHANGE IS CLASSIFIED, NOT JUST RECORDED. A change either tightens
       what the robot may do, loosens it, or does neither. Tightening needs a
       name. Loosening needs a name AND a deliberate confirmation, and lands in
       the ledger flagged so it is findable in an audit without reading a diff.

    2. THE DOCUMENT SURVIVES THE EDIT. The venue file is mostly prose: the rules
       it states, why a zone is closed, what the frame origin is anchored to.
       Round-tripping it through a YAML dumper would delete all of that and
       leave a machine-readable file no human would ever sign. So edits are
       surgical text replacements of single scalar values, and every byte we did
       not deliberately change is byte-identical afterwards.

TWO LOOSENINGS THAT DO NOT LOOK LIKE SAFETY CHANGES, and are the two an
operator will reach for first:

    `verified: plan` -> `on_site`   lifts the unverified clamp, so a zone jumps
                                    from 0.15 m/s to its own cap. Ticking a box
                                    labelled "measured" can triple a speed.
    `signed_off_by: ""` -> a name   lifts the same clamp across the whole venue
                                    AND enables guiding at all.

Both are classified `loosen` for exactly that reason. Calling them paperwork is
how a 16 kg machine ends up moving twice as fast next to someone in a bathrobe.

WHAT IS DELIBERATELY NOT EDITABLE HERE
    Zone polygons, and adding or removing zones. Not because it is hard, but
    because a boundary drawn with a mouse over a floor plan and a boundary
    measured at the venue are different claims, and a UI that accepts the first
    while the file calls it `verified` would launder one into the other. Geometry
    changes stay a file edit made by someone standing in the building.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from pathlib import Path

TIGHTEN, LOOSEN, NEUTRAL = "tighten", "loosen", "neutral"

# Scalar fields the dashboard may set, and which direction an increase means.
# `up` is what a LARGER (or truthier) value does to the robot's freedom.
#   "loosen"  - a bigger number lets the robot do more
#   "tighten" - a bigger number restrains it more
ZONE_FIELDS = {
    "speed_cap":         {"type": "float", "up": LOOSEN,
                          "effect": "how fast the robot may move in this zone"},
    "restricted":        {"type": "bool",  "up": TIGHTEN,
                          "effect": "whether the robot is forbidden to enter at all"},
    "camera_off":        {"type": "bool",  "up": TIGHTEN,
                          "effect": "whether any frame may be captured here"},
    "requires_approval": {"type": "bool",  "up": TIGHTEN,
                          "effect": "whether a human must approve before acting here"},
    "verified":          {"type": "enum",  "up": LOOSEN, "values": ["plan", "on_site"],
                          "effect": "whether this zone's geometry was measured at the "
                                    "venue; `plan` holds it at the unverified speed"},
}

VENUE_FIELDS = {
    "signed_off_by":   {"type": "str", "up": LOOSEN,
                        "effect": "who accepts these limits; empty runs the most "
                                  "cautious profile and forbids guiding entirely"},
    "signed_off_date": {"type": "str", "up": NEUTRAL, "effect": "when it was signed"},
}

LIMIT_FIELDS = {
    "max_speed":            {"type": "float", "up": LOOSEN,
                             "effect": "the ceiling every zone cap is clamped against"},
    "unverified_zone_speed": {"type": "float", "up": LOOSEN,
                              "effect": "the speed an unmeasured or unsigned venue runs at"},
}

PEOPLE_FIELDS = {
    "slow_within_m":     {"type": "float", "up": TIGHTEN,
                          "effect": "how far away a person starts slowing the robot"},
    "hold_within_m":     {"type": "float", "up": TIGHTEN,
                          "effect": "how close a person brings the robot to a stop"},
    "near_person_speed": {"type": "float", "up": LOOSEN,
                          "effect": "the speed allowed with someone nearby"},
    "latch_s":           {"type": "float", "up": TIGHTEN,
                          "effect": "how long a sighting keeps restraining the robot"},
    "crowd_count":       {"type": "float", "up": LOOSEN,
                          "effect": "how many people count as a crowd"},
}

CHILD_FIELDS = {
    "height_under_m":       {"type": "float", "up": LOOSEN,
                             "effect": "the height below which a person is treated as a child"},
    "freeze_within_m":      {"type": "float", "up": TIGHTEN,
                             "effect": "how far away a child stops the robot"},
    "latch_s":              {"type": "float", "up": TIGHTEN,
                             "effect": "how long a child sighting keeps the robot held"},
    "forbid_limb_gestures": {"type": "bool", "up": TIGHTEN,
                             "effect": "whether limb gestures are refused near a child"},
}


@dataclass
class Change:
    path: list                      # e.g. ["zones", "pool_deck", "speed_cap"]
    before: object
    after: object
    direction: str
    effect: str = ""
    note: str = ""

    @property
    def key(self) -> str:
        return ".".join(str(p) for p in self.path)

    def as_dict(self) -> dict:
        return {"path": self.path, "key": self.key, "before": self.before,
                "after": self.after, "direction": self.direction,
                "effect": self.effect, "note": self.note}


@dataclass
class Plan:
    changes: list = field(default_factory=list)
    errors: list = field(default_factory=list)

    @property
    def loosens(self) -> bool:
        return any(c.direction == LOOSEN for c in self.changes)

    @property
    def ok(self) -> bool:
        return not self.errors and bool(self.changes)

    def as_dict(self) -> dict:
        return {"changes": [c.as_dict() for c in self.changes],
                "errors": self.errors, "loosens": self.loosens,
                "tightens": any(c.direction == TIGHTEN for c in self.changes),
                "ok": self.ok}


# -- classification -------------------------------------------------------

def _spec(path: list) -> dict | None:
    if len(path) == 3 and path[0] == "zones":
        return ZONE_FIELDS.get(path[2])
    if len(path) == 2 and path[0] == "venue":
        return VENUE_FIELDS.get(path[1])
    if len(path) == 2 and path[0] == "limits":
        return LIMIT_FIELDS.get(path[1])
    if len(path) == 2 and path[0] == "people":
        return PEOPLE_FIELDS.get(path[1])
    if len(path) == 3 and path[0] == "people" and path[1] == "child":
        return CHILD_FIELDS.get(path[2])
    return None


def _direction(spec: dict, before, after) -> str:
    """Which way this single edit moves the robot's freedom."""
    up = spec.get("up", NEUTRAL)
    if up == NEUTRAL:
        return NEUTRAL
    other = TIGHTEN if up == LOOSEN else LOOSEN
    t = spec.get("type")
    if t == "bool":
        b, a = bool(before), bool(after)
        if b == a:
            return NEUTRAL
        return up if a else other
    if t == "str":
        b, a = str(before or "").strip(), str(after or "").strip()
        if b == a:
            return NEUTRAL
        # Filling a required name loosens; clearing it tightens.
        return up if a and not b else (other if b and not a else NEUTRAL)
    if t == "enum":
        vals = spec.get("values") or []
        try:
            b, a = vals.index(str(before)), vals.index(str(after))
        except ValueError:
            return LOOSEN                      # unknown value: assume the worse
        return NEUTRAL if a == b else (up if a > b else other)
    try:
        b, a = float(before), float(after)
    except (TypeError, ValueError):
        return LOOSEN
    if a == b:
        return NEUTRAL
    return up if a > b else other


def _coerce(spec: dict, raw):
    t = spec.get("type")
    if t == "bool":
        if isinstance(raw, bool):
            return raw
        return str(raw).strip().lower() in ("1", "true", "yes", "on")
    if t == "float":
        return float(raw)
    if t == "enum":
        v = str(raw).strip()
        if v not in (spec.get("values") or []):
            raise ValueError(f"must be one of {spec['values']}")
        return v
    return str(raw)


def build_plan(current: dict, edits: list) -> Plan:
    """`edits` is [{"path": [...], "value": x}, ...] against the raw YAML dict."""
    p = Plan()
    seen = set()
    for e in edits:
        path = list(e.get("path") or [])
        spec = _spec(path)
        if not spec:
            p.errors.append(f"{'.'.join(map(str, path))} is not an editable field")
            continue
        key = ".".join(map(str, path))
        if key in seen:
            p.errors.append(f"{key} given twice")
            continue
        seen.add(key)
        if path[0] == "zones" and path[1] not in (current.get("zones") or {}):
            p.errors.append(f"no zone named {path[1]!r}")
            continue
        try:
            after = _coerce(spec, e.get("value"))
        except (ValueError, TypeError) as ex:
            p.errors.append(f"{key}: {ex}")
            continue
        before = _dig(current, path, spec)
        note = ""
        if spec.get("type") == "float":
            if after <= 0 and path[-1] not in ("signed_off_date",):
                p.errors.append(f"{key}: {after} is not a usable positive number")
                continue
            ceiling = float(((current.get("limits") or {}).get("max_speed")) or 0.4)
            if path[0] == "zones" and path[2] == "speed_cap" and after > ceiling:
                note = (f"asks for {after} m/s, above the {ceiling} m/s ceiling - "
                        f"will be clamped to {ceiling} when read")
        d = _direction(spec, before, after)
        if d == NEUTRAL and before == after:
            continue                            # nothing to do, not an error
        p.changes.append(Change(path=path, before=before, after=after,
                                direction=d, effect=spec.get("effect", ""), note=note))
    return p


def _dig(raw: dict, path: list, spec: dict):
    cur = raw
    for k in path:
        if not isinstance(cur, dict):
            return None
        cur = (cur or {}).get(k)
    if cur is None and spec.get("type") == "bool":
        return False                            # absent means off, per the venue rules
    return cur


# -- surgical YAML writing ------------------------------------------------

_KEY = re.compile(r"^(\s*)([A-Za-z0-9_.-]+)\s*:(.*)$")


def set_scalar(text: str, path: list, value) -> str:
    """Replace one scalar in YAML text, preserving every other byte.

    Walks the file by indentation to find `path`, then rewrites only the value,
    keeping any trailing `# comment` on that line. If the key is absent from an
    existing mapping it is inserted at the right indent. A missing PARENT is an
    error rather than a silent creation - inventing a `zones:` block from a typo
    is how a guardrail file ends up describing a venue nobody recognises.
    """
    lines = text.split("\n")
    lo, hi, indent = 0, len(lines), 0
    for depth, key in enumerate(path):
        found = None
        i = lo
        while i < hi:
            m = _KEY.match(lines[i])
            if m and len(m.group(1)) == indent and m.group(2) == key:
                found = i
                break
            # A shallower key at this level ends the parent's block.
            if m and len(m.group(1)) < indent:
                break
            i += 1
        if found is None:
            if depth != len(path) - 1:
                raise KeyError(f"{'.'.join(map(str, path[:depth + 1]))} is not in the file")
            return _insert(lines, lo, hi, indent, key, value)
        if depth == len(path) - 1:
            m = _KEY.match(lines[found])
            head = f"{m.group(1)}{key}: {_fmt(value)}"
            lines[found] = head + _comment(m.group(3), len(head),
                                           len(m.group(1)) + len(key) + 1)
            return "\n".join(lines)
        # Descend: the child block runs until a line at this indent or shallower.
        lo, indent = found + 1, indent + 2
        j = lo
        while j < hi:
            m = _KEY.match(lines[j])
            if m and len(m.group(1)) < indent and lines[j].strip():
                break
            j += 1
        hi = j
    raise KeyError("empty path")


def _insert(lines: list, lo: int, hi: int, indent: int, key: str, value) -> str:
    at = lo
    for i in range(lo, min(hi, len(lines))):
        if lines[i].strip():
            at = i + 1
    lines.insert(at, f"{' ' * indent}{key}: {_fmt(value)}")
    return "\n".join(lines)


def _comment(tail: str, written: int, orig_head: int) -> str:
    """Keep a trailing comment, drop the old value, and keep its column.

    There must ALWAYS be whitespace before the `#`. Without it YAML reads the
    comment as part of the scalar, so `signed_off_by: Name# who signed` silently
    becomes the string "Name# who signed" - a corruption that parses fine and
    means something else. `written` is the length of the line so far, so a
    comment that used to be aligned stays aligned whenever it still fits.
    """
    h = tail.find("#")
    if h < 0:
        return ""
    if tail[:h].count('"') % 2 or tail[:h].count("'") % 2:
        return ""                              # the `#` is inside a quoted value
    orig_col = orig_head + h                   # where the `#` sat on the old line
    return " " * max(1, orig_col - written) + tail[h:].rstrip()


def _fmt(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float):
        s = f"{v:.4f}".rstrip("0").rstrip(".")
        return s or "0"
    if isinstance(v, (int,)):
        return str(v)
    s = str(v)
    # Quote unless YAML will read the bare text back as exactly this string.
    # Asking YAML is the only test that cannot be wrong: a regex has to predict
    # which bare scalars get retyped, and the one that caught this out was
    # `signed_off_date: 2026-11-16`, which parses as a datetime.date - a value
    # that is not a string, is not JSON serializable, and broke the response
    # AFTER the file had already been written.
    try:
        import yaml
        if yaml.safe_load(s) == s and s.strip() == s and s:
            return s
    except Exception:                          # noqa: BLE001 - unparseable means quote it
        pass
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def write_plan(path: str, plan: Plan) -> tuple[str, str]:
    """Apply a plan to the file atomically. Returns (backup_path, new_text).

    The previous version is kept beside it. A guardrail file that can be changed
    without the old one surviving cannot answer "what did it say at the time",
    which is the only question an incident ever asks of it.
    """
    p = Path(path)
    text = p.read_text()
    for c in plan.changes:
        text = set_scalar(text, c.path, c.after)
    stamp = time.strftime("%Y%m%dT%H%M%S", time.gmtime())
    backup = p.with_suffix(p.suffix + f".{stamp}.bak")
    backup.write_text(p.read_text())
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(text)
    tmp.replace(p)
    return str(backup), text


def editable_schema() -> dict:
    """What the dashboard is allowed to render as a control."""
    return {"zone": ZONE_FIELDS, "venue": VENUE_FIELDS, "limits": LIMIT_FIELDS,
            "people": PEOPLE_FIELDS, "child": CHILD_FIELDS,
            "not_editable": {
                "polygon": "zone geometry is measured at the venue, not drawn in a "
                           "browser; a mouse-drawn boundary must never be able to "
                           "call itself `verified: on_site`",
                "zones": "adding or removing a zone changes what counts as "
                         "undescribed - and undescribed means forbidden",
            }}
