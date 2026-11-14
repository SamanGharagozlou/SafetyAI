"""python -m brain: relay + mission-control console + concierge on BIND_HOST:BRAIN_PORT (default 8780)."""

from __future__ import annotations

import uvicorn

from leukerbad_g1 import settings


def main():
    host = settings.bind_host()
    if not settings.get("BRAIN_API_KEY"):
        raise SystemExit("BRAIN_API_KEY is not set (.env): the brain refuses to serve without a key")
    from brain.relay import create_app

    uvicorn.run(create_app(), host=host, port=int(settings.get("BRAIN_PORT", "8780")), log_level="info")


if __name__ == "__main__":
    main()
