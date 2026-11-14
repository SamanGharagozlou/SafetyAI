"""Human approvals through ClawMetry's Approve/Deny queue.

ClawMetry's local dashboard receives Claude Code PreToolUse events at POST /api/hooks/claude-code/pretooluse and
applies the user's Guard rules (require_approval). A matching rule parks the request in the same queue the
Approvals tab (and the phone channels) decide; the receiver answers {"status": "pending", "approval_id",
"retry_after_ms"} until a human decides, then {"hookSpecificOutput": {"permissionDecision": "allow" | "deny"}}.
We speak exactly that protocol (clawmetry/claude_code_gate.py hook_main) for robot-side decisions:

  tool "mcp__g1-skills__resume_after_hold"   resume after a policy hold (safety/fsm.py)
  tool "mcp__g1-skills__guide_to"            a guest asks for a restricted POI (staff escort, never the robot)

Answers: "allow" | "deny" | "no_rule" (no Guard rule matched: ClawMetry has no opinion -> NOT an approval; the
console's Approve button is then the way) | "unavailable" (dashboard down / timeout -> NOT an approval).
Fail closed: only an explicit human "allow" approves.
"""

from __future__ import annotations

import json
import time
import urllib.request


class Approvals:
    def __init__(self, base_url: str, session_id: str = "leukerbad-d1w", timeout_s: float = 300.0, post=None):
        self.url = base_url.rstrip("/") + "/api/hooks/claude-code/pretooluse"
        self.session_id = session_id
        self.timeout_s = timeout_s
        self._post = post or self._http

    def _http(self, payload: dict) -> dict:
        req = urllib.request.Request(
            self.url, data=json.dumps(payload).encode(), method="POST", headers={"content-type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=25.0) as r:
            return json.loads(r.read())

    def request(self, tool: str, tool_input: dict, timeout_s: float | None = None, cancelled=None) -> dict:
        """Blocking: {"decision": allow|deny|no_rule|unavailable, "approval_id", "reason", "waited_s"}."""
        payload = {
            "tool_name": tool,
            "tool_input": tool_input,
            "session_id": self.session_id,
            "cwd": "",
            "hook_event_name": "PreToolUse",
            "permission_mode": "default",
            "tool_use_id": f"robot-{int(time.time() * 1000)}",
        }
        t0 = time.time()
        limit = self.timeout_s if timeout_s is None else timeout_s
        fails = 0
        while True:
            if cancelled is not None and cancelled():
                return {"decision": "unavailable", "reason": "cancelled", "waited_s": round(time.time() - t0, 1)}
            if time.time() - t0 > limit:
                return {
                    "decision": "unavailable",
                    "reason": "no decision before timeout",
                    "approval_id": payload.get("approval_id"),
                    "waited_s": round(time.time() - t0, 1),
                }
            try:
                resp = self._post(payload)
                fails = 0
            except Exception as e:  # noqa: BLE001
                fails += 1
                if fails >= 3:
                    return {
                        "decision": "unavailable",
                        "reason": f"{e.__class__.__name__}: {e}"[:200],
                        "waited_s": round(time.time() - t0, 1),
                    }
                time.sleep(1.0)
                continue
            if not isinstance(resp, dict):
                return {"decision": "unavailable", "reason": "bad response"}
            if resp.get("status") == "pending" and resp.get("approval_id"):
                payload["approval_id"] = resp["approval_id"]
                try:
                    wait = max(0.2, float(resp.get("retry_after_ms") or 2000) / 1000.0)
                except (TypeError, ValueError):
                    wait = 2.0
                time.sleep(min(wait, 5.0))
                continue
            hso = resp.get("hookSpecificOutput") or {}
            dec = str(hso.get("permissionDecision") or hso.get("decision") or "").lower()
            reason = str(hso.get("permissionDecisionReason") or "")
            out = {
                "approval_id": payload.get("approval_id"),
                "reason": reason[:200],
                "waited_s": round(time.time() - t0, 1),
            }
            if dec == "allow" and "no matching policy" in reason.lower():
                return {**out, "decision": "no_rule"}
            if dec in ("allow", "deny"):
                return {**out, "decision": dec}
            return {**out, "decision": "unavailable" if not dec else "no_rule"}
