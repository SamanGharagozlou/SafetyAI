"""Spa concierge on the brain: an Anthropic API tool loop (manual loop, anthropic SDK) over the venue facts and the
robot's governed goals.

Tools: venue_lookup (facts file), get_state, guide_to, say, look, alert_staff. The robot-facing tools call the EDGE
API (EDGE_URL + EDGE_API_KEY), where the local zone policy, speed caps and the safety FSM apply exactly as for the
MCP tools: the concierge can only ask for goals, never velocities. Persona: friendly, 1-2 sentences, the guest's
language, no medical advice, always offers the nearest allowed alternative when refusing.

Model: CONCIERGE_MODEL (default claude-opus-5-5) at effort "low" (a short reply, latency matters), with server-side
refusal fallbacks enabled ("default" routing). Without ANTHROPIC_API_KEY (or any other API failure) ask() falls back
to concierge/local.py and says so in `source`.
"""

from __future__ import annotations

import json
import time
from typing import Any

from leukerbad_g1 import settings
from venue.facts import Venue

from .local import LocalConcierge, detect_lang

SYSTEM = """You are the concierge voice of a small wheeled-leg guide robot on the level-4 concourse of Leukerbad Therme,
the largest thermal bath in Europe. You talk to guests standing next to you.

How you answer:
- One or two short sentences, spoken aloud. No lists, no markdown, no emojis.
- Always answer in the guest's language (German, French, Italian or English; follow the guest).
- Facts (prices, hours, rules, temperatures, floors) come only from venue_lookup. If it does not say, tell the guest
  to ask at the Kasse; never guess a price or an opening time.
- No medical advice (pregnancy, heart, blood pressure, children's health): suggest asking the staff, and 144 in an
  emergency.
- You can guide guests on the dry concourse: the Kasse, the shop, the café and the Therme entrance. You never go past
  the turnstiles, onto pool decks, onto stairs, into the barefoot Relax level or staff areas. When a guest asks for
  such a place, call guide_to anyway (the robot refuses it and asks staff whether someone can escort them), then
  say briefly why you can't go and offer the nearest place you can go.
- Call guide_to only when the guest wants to be taken somewhere; it starts the walk and returns at once.
- Use alert_staff when a guest needs a person (lost child, accident, complaint, accessibility help).
"""

TOOLS: list[dict[str, Any]] = [
    {
        "name": "venue_lookup",
        "description": "Look up Leukerbad Therme facts: prices, opening hours (live when available), temperatures, rules, facilities, floors, points of interest. Pass a short topic such as 'family price', 'sauna hours', 'pool temperatures', 'parking'.",
        "input_schema": {
            "type": "object",
            "properties": {"topic": {"type": "string"}},
            "required": ["topic"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "name": "get_state",
        "description": "The robot's current zone, safety state (NORMAL / CAUTION / HOLD / RECOVER), battery, and how many people are near.",
        "input_schema": {"type": "object", "properties": {}, "additionalProperties": False},
        "strict": True,
    },
    {
        "name": "guide_to",
        "description": "Walk the guest to a point of interest. poi is one of: kasse, shop, cafe, bath_entrance, concourse, lost_property, or a no-go place (pool_deck, sauna_world, pool_bar, massage_beauty, relax_level, technik, stairs, parking) which the robot refuses with the nearest alternative. Starts the walk and returns immediately.",
        "input_schema": {
            "type": "object",
            "properties": {"poi": {"type": "string"}, "lang": {"type": "string", "enum": ["en", "de", "fr", "it"]}},
            "required": ["poi", "lang"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "name": "say",
        "description": "Say something out loud through the robot's speaker in addition to your reply (rarely needed: your reply is already spoken).",
        "input_schema": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "name": "look",
        "description": "What the robot's camera sees right now: scene class, detected objects, and a short description including the text of any signs.",
        "input_schema": {
            "type": "object",
            "properties": {"question": {"type": "string"}},
            "required": ["question"],
            "additionalProperties": False,
        },
        "strict": True,
    },
    {
        "name": "alert_staff",
        "description": "Call a staff member to the robot (lost child, accident, complaint, accessibility help, anything a person must handle).",
        "input_schema": {
            "type": "object",
            "properties": {"reason": {"type": "string"}},
            "required": ["reason"],
            "additionalProperties": False,
        },
        "strict": True,
    },
]


class EdgeAPI:
    """Goals to the edge skill server (same local policy as the MCP tools). Never velocities."""

    def __init__(self, base: str | None = None, key: str | None = None, timeout: float = 15.0):
        self.base = (base or settings.get("EDGE_URL", "http://127.0.0.1:8765")).rstrip("/")
        self.key = settings.get("EDGE_API_KEY") if key is None else key
        self.timeout = timeout

    def call(self, path: str, body: dict | None = None) -> dict:
        import urllib.error
        import urllib.request

        req = urllib.request.Request(
            self.base + "/" + path,
            data=None if body is None else json.dumps(body).encode(),
            method="GET" if body is None else "POST",
            headers={"content-type": "application/json", "x-api-key": self.key},
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            return {"ok": False, "error": f"edge {e.code}: {e.read()[:200].decode(errors='replace')}"}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": f"edge unreachable: {e}"[:200]}


class Concierge:
    MAX_TURNS = 6

    def __init__(self, venue: Venue | None = None, edge: EdgeAPI | None = None, client=None, model: str | None = None):
        self.venue = venue or Venue()
        self.edge = edge or EdgeAPI()
        self.model = model or settings.get("CONCIERGE_MODEL", "claude-opus-5-5")
        self._client = client
        self.local = LocalConcierge(self.venue)
        self.sessions: dict[str, list] = {}

    def client(self):
        if self._client is None:
            import anthropic

            key = settings.get("ANTHROPIC_API_KEY")
            self._client = anthropic.Anthropic(api_key=key) if key else anthropic.Anthropic()
        return self._client

    # ------------------------------------------------------------ tools
    def run_tool(self, name: str, args: dict, actions: list) -> str:
        if name == "venue_lookup":
            return json.dumps(self.venue.lookup(str(args.get("topic", ""))), ensure_ascii=False)[:6000]
        if name == "get_state":
            s = self.edge.call("state")
            if "robot" not in s:
                return json.dumps(s)
            return json.dumps(
                {
                    "zone": s["robot"]["zone"],
                    "safety": s["safety"]["state"],
                    "battery_pct": s["robot"]["battery_pct"],
                    "people_within_2m": s["observation"]["people_within_2m"],
                }
            )
        if name == "guide_to":
            r = self.edge.call("guide_to", {"poi": args.get("poi", ""), "lang": args.get("lang", "en"), "wait": False})
            actions.append(
                {
                    "type": "guide_to",
                    "poi": args.get("poi"),
                    "ok": r.get("ok"),
                    "message": str(r.get("message") or r.get("error", ""))[:160],
                    "nearest": r.get("nearest"),
                    "staff_escort": r.get("staff_escort"),
                }
            )
            return json.dumps(
                {
                    k: r.get(k)
                    for k in ("ok", "message", "started", "nearest", "nearest_name", "staff_escort", "error")
                    if k in r
                }
            )
        if name == "say":
            r = self.edge.call("say", {"text": str(args.get("text", ""))[:300]})
            actions.append({"type": "say", "text": args.get("text")})
            return json.dumps({"ok": r.get("ok", False)})
        if name == "look":
            r = self.edge.call("look", {"question": args.get("question", "")})
            return json.dumps(r)[:2000]
        if name == "alert_staff":
            r = self.edge.call("alert", {"reason": str(args.get("reason", ""))[:300]})
            actions.append({"type": "alert_staff", "reason": args.get("reason")})
            return json.dumps({"ok": r.get("ok", False)})
        return json.dumps({"error": f"unknown tool {name}"})

    # ------------------------------------------------------------ loop
    def ask(self, text: str, lang: str | None = None, session: str | None = None, context: dict | None = None) -> dict:
        t0 = time.time()
        actions: list[dict] = []
        hist = self.sessions.setdefault(session or "default", [])[-12:]
        user = text if not lang else f"[guest language: {lang}] {text}"
        messages: list[Any] = [*hist, {"role": "user", "content": user}]
        try:
            client = self.client()
            reply = ""
            for _ in range(self.MAX_TURNS):
                resp = client.beta.messages.create(
                    model=self.model,
                    max_tokens=4096,
                    system=SYSTEM,
                    tools=TOOLS,
                    messages=messages,
                    output_config={"effort": "low"},
                    betas=["server-side-fallback-2026-07-01"],
                    fallbacks="default",
                )
                if resp.stop_reason == "refusal":
                    raise RuntimeError("model refused")
                messages.append({"role": "assistant", "content": resp.content})
                if resp.stop_reason != "tool_use":
                    reply = " ".join(b.text for b in resp.content if b.type == "text").strip()
                    break
                results = []
                for b in resp.content:
                    if b.type == "tool_use":
                        try:
                            out = self.run_tool(b.name, dict(b.input), actions)
                            results.append({"type": "tool_result", "tool_use_id": b.id, "content": out})
                        except Exception as e:  # noqa: BLE001
                            results.append(
                                {"type": "tool_result", "tool_use_id": b.id, "content": f"error: {e}", "is_error": True}
                            )
                messages.append({"role": "user", "content": results})
            if not reply:
                raise RuntimeError("no reply within the tool-turn limit")
            self.sessions[session or "default"] = [
                *hist,
                {"role": "user", "content": user},
                {"role": "assistant", "content": reply},
            ]
            return {
                "reply": reply,
                "actions": actions,
                "lang": lang or detect_lang(text),
                "source": "brain",
                "model": self.model,
                "latency_s": round(time.time() - t0, 2),
            }
        except Exception as e:  # noqa: BLE001  (no key, API down, refusal): the local fallback still answers
            fb = self.local.ask(text, lang, guide=lambda poi, lg: _EdgeGuide(self.edge, poi, lg))
            fb.update(
                degraded=f"concierge model unavailable: {e.__class__.__name__}: {e}"[:200],
                latency_s=round(time.time() - t0, 2),
            )
            return fb


class _EdgeGuide:
    """Adapter so the local fallback on the brain can start a guide on the edge (returns a SkillResult-like object)."""

    def __init__(self, edge: EdgeAPI, poi: str, lang: str):
        r = edge.call("guide_to", {"poi": poi, "lang": lang, "wait": False})
        self.ok = bool(r.get("ok"))
        self.message = str(r.get("message") or r.get("error", ""))
