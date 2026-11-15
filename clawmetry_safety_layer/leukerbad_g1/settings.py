"""Edge/brain settings from the environment and the repo's .env (no python-dotenv dependency).

Real environment variables win over .env. Keys (see .env.example):
  EDGE_API_KEY      X-Api-Key the edge skill server requires on every mutating / remote endpoint
  BRAIN_API_KEY     X-Api-Key the brain (relay, console) requires; the edge sends it when calling the brain
  BRAIN_URL         http://spark-7a33:8780  (brain relay; tailnet MagicDNS name)
  EDGE_URL          http://edge:8765        (the brain calls back /verdict here)
  COSMOS_URL        http://127.0.0.1:8000/v1 (OpenAI-compatible vLLM; on the brain)
  BIND_HOST         interface the services bind to: a tailnet / LAN address, never 0.0.0.0 on a public interface
"""

from __future__ import annotations

import hmac
import ipaddress
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_LOADED: dict[str, str] | None = None


def _parse(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    try:
        text = path.read_text()
    except OSError:
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip().removeprefix("export ").strip()
        v = v.strip()
        if len(v) >= 2 and v[0] == v[-1] and v[0] in "'\"":
            v = v[1:-1]
        else:
            v = v.split(" #", 1)[0].strip()
        out[k] = v
    return out


def dotenv() -> dict[str, str]:
    global _LOADED
    if _LOADED is None:
        _LOADED = _parse(Path(os.environ.get("LEUKERBAD_ENV_FILE", str(ROOT / ".env"))))
    return _LOADED


def get(key: str, default: str = "") -> str:
    v = os.environ.get(key)
    if v is not None:
        return v
    return dotenv().get(key, default)


def key_ok(given: str | None, expected_env: str) -> bool:
    """Constant-time X-Api-Key check. An unset key on the server side refuses everything (fail closed)."""
    expected = get(expected_env)
    if not expected or not given:
        return False
    return hmac.compare_digest(given.encode(), expected.encode())


def bind_hosts(default: str = "127.0.0.1") -> list[str]:
    """BIND_HOST as a comma list (e.g. "<spark tailnet IP>,127.0.0.1": the tailnet for remote callers, loopback for local
    checks). Every entry is checked like bind_host(): no wildcard, ever."""
    raw = get("BIND_HOST", default) or default
    return [_checked(h.strip()) for h in raw.split(",") if h.strip()] or [default]


def bind_host(default: str = "127.0.0.1") -> str:
    """The first BIND_HOST entry (see bind_hosts)."""
    return bind_hosts(default)[0]


def serve(app, hosts: list[str], port: int) -> None:
    """uvicorn on every host in `hosts` (one socket each, same app)."""
    import socket

    import uvicorn

    socks = []
    for h in hosts:
        fam, _, _, _, addr = socket.getaddrinfo(h, port, type=socket.SOCK_STREAM)[0]
        s = socket.socket(fam, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(addr)
        s.listen(2048)
        socks.append(s)
    print(f"[serve] listening on {', '.join(f'{h}:{port}' for h in hosts)}")
    uvicorn.Server(uvicorn.Config(app, log_level="info")).run(sockets=socks)


def _checked(h: str) -> str:
    if h in ("0.0.0.0", "::", ""):
        raise ValueError(
            "BIND_HOST=0.0.0.0 is not allowed: bind to the tailnet address (tailscale ip -4) or the robot LAN address"
        )
    try:
        ip = ipaddress.ip_address(h)
    except ValueError:
        return h  # a hostname (MagicDNS); resolved by the server
    if ip.is_unspecified:
        raise ValueError(f"BIND_HOST={h} is a wildcard address")
    return h
