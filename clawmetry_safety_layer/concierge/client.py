"""Edge side of the concierge: POST /ask goes to the brain's concierge (BRAIN_URL/ask); when the brain is unreachable
it answers locally from the venue facts (concierge/local.py) and the reply carries `degraded`. Every exchange is a
journal event `concierge.turn` (guest text, language, reply, actions, source) in the robot session."""

from __future__ import annotations

import json
import threading
import urllib.request

from leukerbad_g1 import settings
from venue.promotions import Promotions

from .local import GuestSessions, LocalConcierge, hello


class ConciergeClient:
    def __init__(self, skills, runtime=None, post=None):
        self.sk = skills
        self.rt = runtime
        self.local = LocalConcierge(skills.venue())
        self.guests = GuestSessions()
        self.promos = Promotions()
        self._post = post or self._http

    def _http(self, body: dict) -> dict:
        url = settings.get("BRAIN_URL", "").rstrip("/") + "/ask"
        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode(),
            method="POST",
            headers={"content-type": "application/json", "x-api-key": settings.get("BRAIN_API_KEY")},
        )
        with urllib.request.urlopen(req, timeout=30.0) as r:
            return json.loads(r.read())

    def _guide(self, poi: str, lang: str):
        p = self.sk.venue().poi(poi)
        if p is not None and not p.get("no_go"):
            threading.Thread(target=self.sk.guide_to, args=(poi, lang), daemon=True, name="guide").start()

            class _Started:
                ok, message = True, f"guiding to {poi}"

            return _Started()
        return self.sk.guide_to(poi, lang)

    def _safety_state(self) -> str | None:
        sup = getattr(self.rt, "supervisor", None) if self.rt is not None else None
        return getattr(getattr(sup, "fsm", None), "state", None)

    def ask(self, text: str, lang: str | None = None, session: str | None = None, name: str | None = None) -> dict:
        text = (text or "").strip()[:500]
        name, lang, greet_now = self.guests.turn(session, name, lang)  # lang sticks for the session
        brain_down = self.rt is not None and self.rt.supervisor.brain_ok is False
        out = None
        err = ""
        if settings.get("BRAIN_URL") and not brain_down:
            try:
                out = self._post({"text": text, "lang": lang, "session": session, "name": name})
            except Exception as e:  # noqa: BLE001
                out = None
                err = f"brain unreachable: {e.__class__.__name__}: {e}"[:200]
        else:
            err = "brain unreachable (link watchdog)" if brain_down else "no BRAIN_URL"
        if out is None:
            out = self.local.ask(text, lang, guide=self._guide)
            out["degraded"] = err
            if greet_now and name:
                out["reply"] = hello(name, out.get("lang") or "en") + str(out.get("reply", ""))
            walk = any(a.get("type") == "guide_to" and a.get("started") for a in out.get("actions", []))
            promo = self.promos.for_walk(self._safety_state(), out.get("lang") or "en", session) if walk else None
            if promo:
                out["reply"] = f"{out.get('reply', '')} {promo['text']}".strip()
                out["promotion"] = promo["id"]
        reply = str(out.get("reply", ""))[:500]
        if reply:
            self.sk.bridge.say(reply)

        def scrub(t: str) -> str:  # the journal keeps that a name was given, never the name itself
            return t.replace(name, "<guest>") if name else t

        self.sk.emit(
            "concierge.turn",
            text=scrub(text[:300]),
            lang=out.get("lang"),
            reply=scrub(reply[:300]),
            actions=out.get("actions", [])[:5],
            source=out.get("source"),
            named=bool(name),  # the journal records that a name was given, not the name
            promotion=out.get("promotion"),
            degraded=out.get("degraded"),
        )
        return {**out, "reply": reply}
