"""Poll leukerbad.ch/en/therme for live open / closed per area into venue/live.json (best effort, every 15 min).

    .venv/bin/python -m venue.refresh            # one fetch
    .venv/bin/python -m venue.refresh --loop     # every 15 minutes

The page layout is not under our control: the parser only looks for the area names from the facts file followed
within ~120 characters by "open"/"closed"/"geöffnet"/"geschlossen" or an HH:MM-HH:MM range. When nothing parses,
live.json is not written and the concierge keeps using the static hours (venue/facts.py). No personal data is sent:
a plain GET with a generic User-Agent.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import time
import urllib.request

from venue.facts import LIVE, Venue

URL = "https://www.leukerbad.ch/en/therme"
UA = "Mozilla/5.0 (venue-hours-check)"


def parse(text: str, areas: list[str]) -> dict:
    t = html.unescape(re.sub(r"<[^>]+>", " ", text))
    t = re.sub(r"\s+", " ", t)
    out: dict = {}
    for a in areas:
        for m in re.finditer(re.escape(a), t, flags=re.I):
            win = t[m.end() : m.end() + 120]
            hrs = re.search(r"(\d{1,2}[:.]\d{2})\s*[-–]\s*(\d{1,2}[:.]\d{2})", win)
            st = re.search(r"\b(open|geöffnet|ouvert|aperto|closed|geschlossen|fermé|chiuso)\b", win, flags=re.I)
            if hrs or st:
                is_open = None
                if st:
                    is_open = st.group(1).lower() in ("open", "geöffnet", "ouvert", "aperto")
                out[a] = {"open": is_open, "hours": f"{hrs.group(1)}-{hrs.group(2)}".replace(".", ":") if hrs else None}
                break
    return out


def fetch_once(url: str = URL, timeout: float = 15.0) -> dict | None:
    v = Venue()
    areas = sorted({a for row in v.raw.get("hours", []) for a in row["areas"]})
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        body = r.read().decode("utf-8", errors="replace")
    found = parse(body, areas)
    if not found:
        return None
    d = {"fetched_at": time.time(), "url": url, "areas": found}
    LIVE.write_text(json.dumps(d, indent=1, ensure_ascii=False))
    return d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--loop", action="store_true")
    ap.add_argument("--every-s", type=float, default=900)
    a = ap.parse_args()
    while True:
        try:
            d = fetch_once()
            print(json.dumps(d)[:400] if d else "no live hours parsed; static hours stay in use")
        except Exception as e:  # noqa: BLE001
            print(f"refresh failed: {e}")
        if not a.loop:
            break
        time.sleep(a.every_s)


if __name__ == "__main__":
    main()
