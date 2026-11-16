"""Robot bridges: the only layer that knows which body is attached."""

from __future__ import annotations

import os
from pathlib import Path

import yaml

from .base import RobotBridge, RobotState
from .paths import REPO_ROOT as ROOT
from .paths import expand


def load_config(path: str | None = None) -> dict:
    path = path or os.environ.get("G1_CONFIG", "config/fake.yaml")
    p = Path(path)
    if not p.is_absolute():
        p = ROOT / p
    with open(p) as f:
        cfg = yaml.safe_load(f)
    if cfg.get("extends"):  # e.g. config/vendors/*.yaml extend config/isaac.yaml
        base = load_config(cfg.pop("extends"))
        base.update(cfg)
        cfg = base
    cfg = expand(cfg)  # ${ISAAC_PYTHON}, ${VENDOR_USD_DIR}, ... (bridge/paths.py)
    cfg["_path"] = str(p)
    return cfg


def make_bridge(cfg: dict | None = None) -> RobotBridge:
    cfg = cfg or load_config()
    kind = cfg.get("bridge", "fake")
    if kind == "fake":
        from .fake_bridge import FakeBridge

        b = FakeBridge(cfg)
    elif kind == "mujoco":
        from .mujoco_bridge import MujocoBridge

        b = MujocoBridge(cfg)
    elif kind == "g1":
        from .g1_bridge import G1Bridge

        b = G1Bridge(cfg)
    elif kind == "isaac":
        from .isaac_bridge import IsaacBridge

        b = IsaacBridge(cfg)
    elif kind == "go2":
        from .go2_bridge import Go2Bridge

        b = Go2Bridge(cfg)
    elif kind == "panther":
        from .panther_bridge import PantherBridge

        b = PantherBridge(cfg)
    elif kind == "agibot_x2":
        from .agibot_x2_bridge import AgibotX2Bridge

        b = AgibotX2Bridge(cfg)
    elif kind == "agibot_a2":
        from .agibot_a2_bridge import AgibotA2Bridge

        b = AgibotA2Bridge(cfg)
    elif kind == "agibot_d1w":
        from .agibot_d1w_bridge import AgibotD1WBridge

        b = AgibotD1WBridge(cfg)
    else:
        raise ValueError(f"unknown bridge kind {kind!r}")
    b.connect(cfg.get("iface", "lo"), int(cfg.get("domain", 0)))
    return b


__all__ = ["RobotBridge", "RobotState", "load_config", "make_bridge", "ROOT"]
