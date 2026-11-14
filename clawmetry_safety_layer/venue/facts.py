"""Venue facts loader and lookups (no network): the static facts in venue/leukerbad_therme.yaml, plus live open/closed
from venue/live.json when venue/refresh.py has written one recently."""

from __future__ import annotations

import datetime as _dt
import json
import time
import unicodedata
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
DEFAULT = ROOT / "venue" / "leukerbad_therme.yaml"
LIVE = ROOT / "venue" / "live.json"
LANGS = ("en", "de", "fr", "it")


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s.lower())
    return "".join(c for c in s if not unicodedata.combining(c))


class Venue:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path or DEFAULT)
        self.raw = yaml.safe_load(self.path.read_text())
        self.pois: dict[str, dict] = self.raw.get("pois", {})

    # ------------------------------------------------------------ POIs
    def poi(self, key: str) -> dict | None:
        k = (key or "").strip()
        if k in self.pois:
            return {"id": k, **self.pois[k]}
        nk = _norm(k).replace("_", " ")
        for pid, p in self.pois.items():
            names = [pid.replace("_", " ")] + list((p.get("names") or {}).values())
            if any(nk == _norm(n) or (len(nk) > 3 and nk in _norm(n)) for n in names):
                return {"id": pid, **p}
        return None

    def name(self, pid: str | None, lang: str = "en") -> str:
        if not pid:
            return ""
        p = self.pois.get(pid) or {}
        return (p.get("names") or {}).get(lang) or (p.get("names") or {}).get("en") or pid

    def text(self, pid: str | None, key: str, lang: str = "en") -> str | None:
        if not pid:
            return None
        v = (self.pois.get(pid) or {}).get(key)
        if isinstance(v, dict):
            return v.get(lang) or v.get("en")
        return v

    def list_pois(self, lang: str = "en") -> list[dict]:
        return [
            {
                "id": pid,
                "name": self.name(pid, lang),
                "zone": p.get("zone"),
                "no_go": bool(p.get("no_go")),
                "nearest": p.get("nearest"),
            }
            for pid, p in self.pois.items()
        ]

    # ------------------------------------------------------------ facts
    def live(self, max_age_s: float = 6 * 3600) -> dict | None:
        try:
            d = json.loads(LIVE.read_text())
        except (OSError, ValueError):
            return None
        if time.time() - float(d.get("fetched_at", 0)) > max_age_s:
            return None
        return d

    def open_now(self, area: str, now: _dt.datetime | None = None) -> dict:
        """{"area", "open": bool|None, "hours": "08:00-20:00", "source": "live"|"static"}"""
        now = now or _dt.datetime.now()
        lv = self.live()
        if lv and area in (lv.get("areas") or {}):
            a = lv["areas"][area]
            return {"area": area, "open": a.get("open"), "hours": a.get("hours"), "source": "live"}
        na = _norm(area)
        dow = now.strftime("%a")
        for row in self.raw.get("hours", []):
            if any(na in _norm(x) or _norm(x) in na for x in row["areas"]):
                days = row["days"]
                if days == "Mon-Fri" and dow in ("Sat", "Sun") or days == "Sat" and dow != "Sat":
                    continue
                t = now.strftime("%H:%M")
                return {
                    "area": area,
                    "open": row["open"] <= t < row["close"],
                    "hours": f"{row['open']}-{row['close']}",
                    "source": "static",
                    **({"phone": row["phone"]} if row.get("phone") else {}),
                }
        return {"area": area, "open": None, "hours": None, "source": "static"}

    def lookup(self, topic: str) -> dict:
        """Everything the facts file says about a topic (prices, hours, rules, temperatures, floors, a POI, ...).
        Matching is plain keyword search over the YAML: the concierge model phrases the answer."""
        t = _norm(topic)
        out: dict = {}
        sections = ("venue", "hours", "temperatures_c", "prices_chf", "rules", "facilities", "floors", "extras")
        keys = {
            "prices_chf": (
                "price",
                "cost",
                "chf",
                "ticket",
                "preis",
                "prix",
                "prezzo",
                "kosten",
                "family",
                "child",
                "kids",
                "towel",
                "rent",
                "magic",
                "guest card",
            ),
            "hours": ("open", "hour", "close", "time", "offnung", "heure", "orari", "when"),
            "temperatures_c": ("temp", "warm", "hot", "cold", "degree", "grad", "°"),
            "rules": (
                "rule",
                "photo",
                "video",
                "dog",
                "animal",
                "glass",
                "alcohol",
                "school",
                "lost",
                "textile",
                "allowed",
            ),
            "facilities": ("sauna", "massage", "beauty", "bar", "shop", "cafe", "facial", "fontisana"),
            "floors": ("floor", "level", "parking", "etage", "ebene", "niveau", "piano"),
            "venue": ("address", "phone", "email", "where", "contact", "altitude", "largest", "water"),
            "extras": ("volley", "beach"),
        }
        for sec in sections:
            if any(k in t for k in keys.get(sec, ())):
                out[sec] = self.raw.get(sec)
        p = self.poi(topic)
        if p:
            out["poi"] = p
        if not out:  # nothing matched a section: give the model the compact essentials
            out = {k: self.raw.get(k) for k in ("venue", "hours", "prices_chf")}
        lv = self.live()
        if lv:
            out["live"] = {"fetched_at": lv.get("fetched_at"), "areas": lv.get("areas")}
        return out
