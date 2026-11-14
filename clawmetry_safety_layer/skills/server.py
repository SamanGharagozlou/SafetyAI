"""MCP server (stdio) exposing the seven robot skills as tools.

Register in Claude Code via .mcp.json (scripts/setup_core.sh writes it from .mcp.json.example) or in
OpenClaw as a tool provider. Tool names as seen by ClawMetry's hook are
`mcp__g1-skills__<tool>`.

Proxy mode: if SKILLS_API is set (e.g. http://127.0.0.1:8765), every tool is
forwarded to that running `uvicorn skills.api:app` instead of creating a bridge
in this process. Use it when several short-lived agent processes (`claude -p`)
must share one robot body, its state and its MuJoCo window
(scripts/record_demo.sh). Unset = the original in-process behaviour.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcp.server.fastmcp import FastMCP  # noqa: E402

from skills.skills import get_skills  # noqa: E402

mcp = FastMCP("g1-skills")
SKILLS_API = os.environ.get("SKILLS_API", "").rstrip("/")


def _j(r) -> str:
    return json.dumps(r.dict())


RATIONALE_DOC = " rationale: optional one-line reason for this call (recorded in the robot session trace)."


def _run(name: str, local, rationale: str = "", **body) -> str:
    """Call the skill in-process, or forward it to SKILLS_API (same JSON either way)."""
    if rationale:
        body["rationale"] = rationale
    if not SKILLS_API:
        get_skills().set_rationale(rationale or None)
        return _j(local())
    import httpx  # in requirements.txt

    try:
        r = httpx.post(f"{SKILLS_API}/skills/{name}", json=body, timeout=120.0)
        r.raise_for_status()
        return r.text
    except httpx.HTTPError as e:
        return json.dumps(
            {
                "ok": False,
                "skill": name,
                "message": f"skills API unreachable at {SKILLS_API}: {e}",
                "confirmed_state": None,
            }
        )


@mcp.tool()
def get_state(rationale: str = "") -> str:
    """Current robot state: pose, zone, battery, balance, what it is holding, zone policy, pending requests. Pass rationale = your one-line reason for this call."""
    return _run("get_state", lambda: get_skills().get_state(), rationale=rationale)


@mcp.tool()
def navigate_to(zone: str, max_s: float = 20, rationale: str = "") -> str:
    """Walk to a named zone (A, corridor, B, C). Restricted zones are refused by policy. Bounded by max_s seconds. Pass rationale = your one-line reason for this call."""
    return _run(
        "navigate_to", lambda: get_skills().navigate_to(zone, max_s), rationale=rationale, zone=zone, max_s=max_s
    )


@mcp.tool()
def guide_to(poi: str, lang: str = "en", rationale: str = "") -> str:
    """Guide a guest to a point of interest (D1 Ultra-W at Leukerbad Therme): kasse, shop, cafe, bath_entrance, concourse, lost_property. No-go places (pool_deck, sauna_world, pool_bar, massage_beauty, relax_level, technik, stairs, parking) are refused with the nearest allowed alternative; pool_deck also asks ClawMetry whether staff can escort the guest. lang: en | de | fr | it. Pass rationale = your one-line reason for this call."""
    return _run("guide_to", lambda: get_skills().guide_to(poi, lang), rationale=rationale, poi=poi, lang=lang)


@mcp.tool()
def pick(object: str = "tray", rationale: str = "") -> str:  # noqa: A002
    """Pick up an allowed object (tray, towels) with both arms. One attempt; returns ok=false if it slips. Pass rationale = your one-line reason for this call."""
    return _run("pick", lambda: get_skills().pick(object), rationale=rationale, object=object)


@mcp.tool()
def place(target: str = "table_B", rationale: str = "") -> str:
    """Place what you are holding on a known target (table_B, shelf_corridor, table_A). Pass rationale = your one-line reason for this call."""
    return _run("place", lambda: get_skills().place(target), rationale=rationale, target=target)


@mcp.tool()
def say(text: str, rationale: str = "") -> str:
    """Speak a short sentence (max 20 words) to the people nearby. Pass rationale = your one-line reason for this call."""
    return _run("say", lambda: get_skills().say(text), rationale=rationale, text=text)


@mcp.tool()
def look_at(target: str, rationale: str = "") -> str:
    """Turn the head / attention toward a target (e.g. 'guest', 'table_B'). Pass rationale = your one-line reason for this call."""
    return _run("look_at", lambda: get_skills().look_at(target), rationale=rationale, target=target)


@mcp.tool()
def hold_pose(reason: str, rationale: str = "") -> str:
    """Stop moving and hold a stable standing pose. Use when blocked by policy, after repeated failures, or to hand over to staff. Pass rationale = your one-line reason for this call."""
    return _run("hold_pose", lambda: get_skills().hold_pose(reason), rationale=rationale, reason=reason)


if __name__ == "__main__":
    mcp.run()
