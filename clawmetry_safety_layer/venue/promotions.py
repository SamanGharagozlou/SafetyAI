"""Promotions for the concierge (venue/promotions.yaml). The rules live here, not in the model's prompt:
at most one promotion per guided walk, never while a safety hold is active, only inside its time / season window,
each promotion at most once per guest session."""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent
HOLD_STATES = ("HOLD", "RECOVER")


def _hm(s: str) -> dt.time:
    h, m = (int(v) for v in str(s).split(":"))
    return dt.time(h, m)


class Promotions:
    def __init__(self, path: str | Path | None = None):
        raw = yaml.safe_load(Path(path or ROOT / "promotions.yaml").read_text()) or {}
        self.tz = raw.get("timezone", "Europe/Zurich")
        self.items: list[dict] = list(raw.get("promotions") or [])
        self.heard: dict[str, set[str]] = {}  # session -> promotion ids already mentioned

    def _now(self) -> dt.datetime:
        try:
            from zoneinfo import ZoneInfo

            return dt.datetime.now(ZoneInfo(self.tz))
        except Exception:  # noqa: BLE001  (no tzdata): local time
            return dt.datetime.now()

    def active(self, now: dt.datetime | None = None) -> list[dict]:
        now = now or self._now()
        out = []
        for p in self.items:
            w = p.get("window") or {}
            if w and not (_hm(w.get("from", "00:00")) <= now.time() < _hm(w.get("to", "23:59"))):
                continue
            if p.get("months") and now.month not in p["months"]:
                continue
            out.append(p)
        return out

    def for_walk(
        self, safety_state: str | None, lang: str = "en", session: str | None = None, now: dt.datetime | None = None
    ) -> dict | None:
        """The one promotion for a walk that has just started, or None (hold active, nothing in window, all heard).
        Call it once per guide_to start; the caller attaches the result to that walk only."""
        if safety_state in HOLD_STATES or safety_state is None:
            return None
        heard = self.heard.setdefault(session or "default", set())
        for p in self.active(now):
            if p["id"] in heard:
                continue
            heard.add(p["id"])
            txt = p.get("text") or {}
            return {"id": p["id"], "text": txt.get(lang) or txt.get("en", "")}
        return None
