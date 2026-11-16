"""Zone geometry and policy lookups, shared by every bridge and the skills."""

from __future__ import annotations

import argparse
import math
from pathlib import Path

import yaml


def _point_in_poly(x: float, y: float, poly: list[list[float]]) -> bool:
    inside = False
    n = len(poly)
    j = n - 1
    for i in range(n):
        xi, yi = poly[i]
        xj, yj = poly[j]
        if (yi > y) != (yj > y):
            xint = (xj - xi) * (y - yi) / ((yj - yi) or 1e-9) + xi
            if x < xint:
                inside = not inside
        j = i
    return inside


class Zones:
    def __init__(self, path: str | Path):
        with open(path) as f:
            self.raw = yaml.safe_load(f)
        self.zones: dict[str, dict] = self.raw["zones"]
        self.retry_limit = int(self.raw.get("retry_limit", 2))
        self.allowed_objects = list(self.raw.get("allowed_objects", []))
        self.place_targets = list(self.raw.get("place_targets", []))
        self.near_person_cap = float(self.raw.get("near_person_speed_cap", 0.2))

    def names(self) -> list[str]:
        return list(self.zones)

    def zone_of(self, x: float, y: float) -> str:
        for name, z in self.zones.items():
            if _point_in_poly(x, y, z["polygon"]):
                return name
        return "outside"

    def centre(self, name: str) -> tuple[float, float]:
        z = self.zones[name]
        if "centre" in z:
            return tuple(z["centre"])
        xs = [p[0] for p in z["polygon"]]
        ys = [p[1] for p in z["polygon"]]
        return (sum(xs) / len(xs), sum(ys) / len(ys))

    def speed_cap(self, name: str) -> float:
        return float(self.zones.get(name, {}).get("speed_cap", 0.3))

    def is_restricted(self, name: str) -> bool:
        return bool(self.zones.get(name, {}).get("restricted", False))

    def requires_approval(self, name: str) -> bool:
        return bool(self.zones.get(name, {}).get("requires_approval", False))

    def path_crosses_restricted(self, x0, y0, x1, y1, step=0.1) -> str | None:
        """Return the first restricted zone a straight path would enter, else None."""
        d = math.hypot(x1 - x0, y1 - y0)
        n = max(1, int(d / step))
        for i in range(n + 1):
            a = i / n
            z = self.zone_of(x0 + (x1 - x0) * a, y0 + (y1 - y0) * a)
            if self.is_restricted(z):
                return z
        return None

    def describe(self) -> list[dict]:
        return [
            {
                "zone": n,
                "label": z.get("label", n),
                "restricted": self.is_restricted(n),
                "requires_approval": self.requires_approval(n),
                "speed_cap": self.speed_cap(n),
            }
            for n, z in self.zones.items()
        ]


def fit_rigid_2d(src: list, dst: list) -> tuple[float, float, float, float]:
    """Least-squares 2D rigid transform (rotation + translation, no scale) mapping src points onto dst points
    (2D Kabsch). Returns (tx, ty, theta, rms_m): dst ~= R(theta) @ src + t. Needs >= 2 distinct points."""
    if len(src) != len(dst) or len(src) < 2:
        raise ValueError("need at least two point pairs")
    n = len(src)
    sx = sum(p[0] for p in src) / n
    sy = sum(p[1] for p in src) / n
    dx = sum(p[0] for p in dst) / n
    dy = sum(p[1] for p in dst) / n
    sxx = sxy = 0.0
    for (ax, ay), (bx, by) in zip(src, dst, strict=True):
        ax, ay, bx, by = ax - sx, ay - sy, bx - dx, by - dy
        sxx += ax * bx + ay * by  # cos component
        sxy += ax * by - ay * bx  # sin component
    if abs(sxx) < 1e-12 and abs(sxy) < 1e-12:
        raise ValueError("points are degenerate (all the same place)")
    th = math.atan2(sxy, sxx)
    c, s_ = math.cos(th), math.sin(th)
    tx, ty = dx - (c * sx - s_ * sy), dy - (s_ * sx + c * sy)
    rms = math.sqrt(
        sum(
            (c * a[0] - s_ * a[1] + tx - b[0]) ** 2 + (s_ * a[0] + c * a[1] + ty - b[1]) ** 2
            for a, b in zip(src, dst, strict=True)
        )
        / n
    )
    return tx, ty, th, rms


def _calibrate_landmarks(config: str, landmarks: list[str], read_odom=None, save: bool = True) -> dict:
    """Landmark-based odom -> zone calibration (no floor tape at the venue): drive the robot (operator, controller)
    to each named existing feature in the zones file's `landmarks`, read its odometry there, and fit the 2D rigid
    transform. read_odom(name) -> (x, y) in the odom frame; default: live from the bridge's transport, else typed."""
    import os
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from bridge import load_config

    cfg = load_config(config)
    from bridge.paths import REPO_ROOT

    zfile = REPO_ROOT / cfg.get("zones_file", "policies/zones.yaml")
    with open(zfile) as f:
        raw = yaml.safe_load(f)
    lms = raw.get("landmarks") or {}
    missing = [n for n in landmarks if n not in lms]
    if missing:
        raise SystemExit(f"unknown landmarks {missing}; the zones file has {sorted(lms)}")
    bridge = None
    if read_odom is None:
        if (
            os.environ.get("D1W_TRANSPORT", cfg.get("transport")) in ("ros2", "sdk", "mock")
            and cfg.get("bridge") == "agibot_d1w"
        ):
            from bridge import make_bridge

            bridge = make_bridge(cfg)

            def read_odom(name):
                input(f"Put the robot at {name} ({lms[name].get('note', '')}) and press Enter: ")
                r = bridge.t.latest()
                print(f"  odom at {name}: ({r.x:.3f}, {r.y:.3f})")
                return r.x, r.y
        else:

            def read_odom(name):
                s = input(f"odom x y at {name} ({lms[name].get('note', '')}): ")
                x, y = map(float, s.split())
                return x, y

    src, dst = [], []
    for n in landmarks:
        src.append(tuple(read_odom(n)))
        dst.append(tuple(float(v) for v in lms[n]["xy"]))
    if bridge is not None:
        bridge.close()
    tx, ty, th, rms = fit_rigid_2d(src, dst)
    out = {"odom_to_zone": [round(tx, 4), round(ty, 4), round(th, 5)], "rms_m": round(rms, 3), "landmarks": landmarks}
    print(f"odom_to_zone = {out['odom_to_zone']}  (rms {rms:.3f} m over {len(landmarks)} landmarks)")
    if rms > 0.25:
        print("WARNING: rms > 0.25 m: re-measure the landmark positions or the fixes before trusting zones")
    if save:
        cpath = Path(cfg["_path"])
        text = cpath.read_text()
        import re

        new, k = re.subn(
            r"(odom_to_zone:\s*)\[[^\]]*\]",
            lambda m: m.group(1) + "[" + ", ".join(str(v) for v in out["odom_to_zone"]) + "]",
            text,
            count=1,
        )
        if k:
            cpath.write_text(new)
            print("saved to", cpath)
        else:
            print(f"add under d1w: in {cpath}:  odom_to_zone: {out['odom_to_zone']}")
    return out


def _calibrate(path: str):
    """Interactive re-measure of each zone's rectangle on the venue floor."""
    with open(path) as f:
        raw = yaml.safe_load(f)
    print(
        "Enter the two opposite corners of each zone (x0 y0 x1 y1) in metres, "
        "measured from the same origin corner you mark with tape. Blank = keep."
    )
    for name, z in raw["zones"].items():
        s = input(f"{name} ({z.get('label', name)}) currently {z['polygon']}: ").strip()
        if not s:
            continue
        x0, y0, x1, y1 = map(float, s.split())
        z["polygon"] = [[x0, y0], [x1, y0], [x1, y1], [x0, y1]]
        z["centre"] = [(x0 + x1) / 2, (y0 + y1) / 2]
    with open(path, "w") as f:
        yaml.safe_dump(raw, f, sort_keys=False)
    print("saved", path)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--calibrate", action="store_true")
    ap.add_argument("--file", default=str(Path(__file__).resolve().parent.parent / "policies/zones.yaml"))
    ap.add_argument(
        "--config",
        help="robot config with a landmarks zones file (config/d1w.yaml): landmark-based odom -> zone calibration",
    )
    ap.add_argument(
        "--landmarks", default="kasse_corner,pillar,shop_door", help="three named features, comma-separated"
    )
    ap.add_argument("--rect", action="store_true", help="the old taped-rectangle re-measure of --file")
    a = ap.parse_args()
    if a.calibrate and a.config and not a.rect:
        _calibrate_landmarks(a.config, [n.strip() for n in a.landmarks.split(",") if n.strip()])
    elif a.calibrate:
        _calibrate(a.file)
    else:
        for row in Zones(a.file).describe():
            print(row)
