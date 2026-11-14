"""Every location outside the repo, in one place (stdlib only: also imported by the Isaac-side scripts, which run in
Isaac's own Python with only `bridge/` on sys.path, as `import paths`).

Each is an environment variable with a default next to the clone (the layout `scripts/setup_*.sh` creates):

  REPO_ROOT            this checkout                                   (default: derived from this file)
  ISAAC_PYTHON         Isaac Sim's python                              (default: ../isaac-venv/bin/python)
  SIMREADY_DIR         NVIDIA SimReady assets for the Isaac venue      (default: ../simready_assets)
  VENDOR_USD_DIR       vendor robots converted to USD (gallery)        (default: ../vendor_usd)
  VENDOR_MODELS_DIR    vendor robot sources: URDF/MJCF/USD clones      (default: ../vendor_models)
  UNITREE_MUJOCO_DIR   unitree_mujoco clone (G1 MJCF + meshes)         (default: ../unitree_mujoco)

Config values may use ${VAR} for any of these (load_config expands them). Scene files cannot expand variables
(USD asset paths, MJCF <include>), so they reference repo-relative `third_party/<name>`: a gitignored folder of
symlinks that ensure_links() points at the directories above. Every scene loader calls it first.
"""

from __future__ import annotations

import os
import platform
from pathlib import Path

REPO_ROOT = Path(os.environ.get("REPO_ROOT") or Path(__file__).resolve().parents[1]).expanduser()
THIRD_PARTY = REPO_ROOT / "third_party"


def _p(var: str, default: Path) -> Path:
    v = os.environ.get(var)
    p = Path(v).expanduser() if v else default
    return p if p.is_absolute() else (REPO_ROOT / p)


# NOT .resolve()d: a venv's bin/python is a symlink to the system interpreter; resolving it leaves the venv.
ISAAC_PYTHON = _p("ISAAC_PYTHON", REPO_ROOT.parent / "isaac-venv" / "bin" / "python")
SIMREADY_DIR = _p("SIMREADY_DIR", REPO_ROOT.parent / "simready_assets")
VENDOR_USD_DIR = _p("VENDOR_USD_DIR", REPO_ROOT.parent / "vendor_usd")
VENDOR_MODELS_DIR = _p("VENDOR_MODELS_DIR", REPO_ROOT.parent / "vendor_models")
UNITREE_MUJOCO_DIR = _p("UNITREE_MUJOCO_DIR", REPO_ROOT.parent / "unitree_mujoco")

VARS = {
    "REPO_ROOT": REPO_ROOT,
    "ISAAC_PYTHON": ISAAC_PYTHON,
    "SIMREADY_DIR": SIMREADY_DIR,
    "VENDOR_USD_DIR": VENDOR_USD_DIR,
    "VENDOR_MODELS_DIR": VENDOR_MODELS_DIR,
    "UNITREE_MUJOCO_DIR": UNITREE_MUJOCO_DIR,
}
LINKS = {
    "simready_assets": SIMREADY_DIR,
    "vendor_usd": VENDOR_USD_DIR,
    "vendor_models": VENDOR_MODELS_DIR,
    "unitree_mujoco": UNITREE_MUJOCO_DIR,
}

# Isaac Sim on aarch64 (DGX Spark / Jetson) needs libgomp preloaded, or Kit fails to load its OpenMP plugins.
LIBGOMP = "/lib/aarch64-linux-gnu/libgomp.so.1"


def expand(value):
    """${VAR} / $VAR expansion in config values, with the defaults above for our variables"""
    if isinstance(value, str) and "$" in value:
        env = dict(os.environ)
        for k, v in VARS.items():
            env.setdefault(k, str(v))
        out = value
        for k in sorted(env, key=len, reverse=True):
            out = out.replace("${" + k + "}", env[k]).replace("$" + k, env[k])
        return out
    if isinstance(value, dict):
        return {k: expand(v) for k, v in value.items()}
    if isinstance(value, list):
        return [expand(v) for v in value]
    return value


def ensure_links(names=None) -> dict:
    """third_party/<name> -> the configured directory (created/repointed; a missing target is still linked so the
    error names the real path). Returns {name: target}."""
    THIRD_PARTY.mkdir(exist_ok=True)
    out = {}
    for name, target in LINKS.items():
        if names and name not in names:
            continue
        link = THIRD_PARTY / name
        try:
            if link.is_symlink():
                if os.readlink(link) == str(target):
                    out[name] = target
                    continue
                link.unlink()
            elif link.exists():  # a real directory placed there by hand: leave it alone
                out[name] = link
                continue
            link.symlink_to(target, target_is_directory=True)
        except OSError as e:  # read-only checkout etc.: loaders report the missing file later
            print(f"[paths] could not link third_party/{name} -> {target}: {e}")
        out[name] = target
    return out


def isaac_env(base: dict | None = None) -> dict:
    """environment for launching Isaac Sim (same as scripts/isaac/env.sh)"""
    env = dict(os.environ if base is None else base)
    env.setdefault("OMNI_KIT_ACCEPT_EULA", "YES")
    if platform.machine() == "aarch64" and Path(LIBGOMP).exists() and LIBGOMP not in env.get("LD_PRELOAD", ""):
        env["LD_PRELOAD"] = ":".join(filter(None, [env.get("LD_PRELOAD"), LIBGOMP]))
    return env


if __name__ == "__main__":  # python bridge/paths.py: show the resolved layout
    for k, v in VARS.items():
        print(f"{k:20s} {v}  {'ok' if Path(v).exists() else 'MISSING'}")
    for k, v in ensure_links().items():
        print(f"third_party/{k:15s} -> {v}")
