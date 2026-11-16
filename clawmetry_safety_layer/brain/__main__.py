"""python -m brain: relay + mission-control console + concierge on BIND_HOST:BRAIN_PORT (default 8780)."""

from __future__ import annotations

from leukerbad_g1 import settings


def main():
    hosts = settings.bind_hosts()
    if not settings.get("BRAIN_API_KEY"):
        raise SystemExit("BRAIN_API_KEY is not set (.env): the brain refuses to serve without a key")
    from brain.relay import create_app

    settings.serve(create_app(), hosts, int(settings.get("BRAIN_PORT", "8780")))


if __name__ == "__main__":
    main()
