"""Run the robot's LLM agent headlessly for one request that came in from ClawMetry's Physical AI page.

The agent is Claude Code in print mode (`claude -p`) talking to the same seven skills over the MCP server in
proxy mode (skills/server.py with SKILLS_API set), so every skill call lands in the running skills API, where the
zone/behaviour policy, the predictor gate, the monitor and the ClawMetry journal already apply. Nothing in here can
move the robot by itself: the runner only produces text and tool calls, and `--restricted` removes Claude Code's
built-in tools so the only tools the agent has are the robot skills.

ROBOT_AGENT_CMD overrides the command (tests use a stub that prints the same stream-json lines). The prompt is
appended as the last argument. Stdout must be Claude Code's `--output-format stream-json`: one JSON object per
line with `type` in {system, assistant, user, result}.
"""

from __future__ import annotations

import json
import logging
import os
import shlex
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from bridge import ROOT

log = logging.getLogger("agent_runner")

MAX_TURNS = int(os.environ.get("ROBOT_AGENT_MAX_TURNS", "16"))
TIMEOUT_S = float(os.environ.get("ROBOT_AGENT_TIMEOUT_S", "600"))
SUMMARY_MAX = 500
SYSTEM_PROMPT = ROOT / "agent" / "system_prompt.txt"


def describe() -> dict:
    """What will run a request: shown on the ClawMetry ledger next to 'who asked'."""
    custom = os.environ.get("ROBOT_AGENT_CMD")
    if custom:
        return {"runner": "custom", "command": custom[:200]}
    return {
        "runner": "claude-code",
        "command": "claude -p --restricted --allowedTools mcp__g1-skills__*",
        "max_turns": MAX_TURNS,
    }


def _mcp_config(directory: Path) -> Path:
    cfg = {
        "mcpServers": {
            "g1-skills": {
                "command": sys.executable,
                "args": ["-m", "skills.server"],
                "cwd": str(ROOT),
                "env": {
                    "SKILLS_API": os.environ.get("SKILLS_API_URL", "http://127.0.0.1:8765"),
                    "G1_CONFIG": os.environ.get("G1_CONFIG", "config/fake.yaml"),
                },
            }
        }
    }
    path = directory / "mcp.json"
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(cfg, f)
    return path


def _command(prompt: str, requested_by: str, directory: Path) -> list[str]:
    custom = os.environ.get("ROBOT_AGENT_CMD")
    if custom:
        return [*shlex.split(custom), prompt]
    system = SYSTEM_PROMPT.read_text() if SYSTEM_PROMPT.exists() else ""
    system += (
        f"\n\nThis request was typed into the ClawMetry Physical AI console by {requested_by}. Say in one short "
        "sentence what you intend to do before calling any tool, then act. If a skill is blocked by policy, do not "
        "retry it; explain in one sentence. Finish with one sentence saying what happened."
    )
    return [
        "claude",
        "-p",
        prompt,
        "--output-format",
        "stream-json",
        "--verbose",
        "--restricted",
        "--strict-mcp-config",
        "--mcp-config",
        str(_mcp_config(directory)),
        "--allowedTools",
        "mcp__g1-skills__*",
        "--max-turns",
        str(MAX_TURNS),
        "--no-session-persistence",
        "--system-prompt",
        system,
    ]


def run(
    prompt: str,
    requested_by: str,
    *,
    on_intent=None,
    on_tool=None,
    cancel: threading.Event | None = None,
    timeout_s: float = TIMEOUT_S,
) -> dict:
    """Run one request to completion. Returns {ok, outcome, summary, turns, duration_ms, cost_usd, error}.

    on_intent(text) fires once, with the agent's first sentence (what it intends). on_tool(name, args) fires per tool
    call so the ledger can show the plan even when a call is later blocked. `cancel` (set by a ClawMetry Stop)
    terminates the agent process; the robot itself is stopped by the controller, not by this function.
    """
    t0 = time.time()
    out = {
        "ok": False,
        "outcome": "failed",
        "summary": "",
        "turns": 0,
        "duration_ms": 0,
        "cost_usd": None,
        "error": None,
    }
    intent_sent = False
    with tempfile.TemporaryDirectory(prefix="robot-agent-") as tmp:
        try:
            cmd = _command(prompt, requested_by, Path(tmp))
        except Exception as e:  # noqa: BLE001
            out["error"] = f"agent command unavailable: {e}"[:300]
            return out
        env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
        try:
            proc = subprocess.Popen(
                cmd,
                cwd=ROOT,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        except OSError as e:
            out["error"] = f"agent could not start: {e}"[:300]
            return out

        def watch():
            while proc.poll() is None:
                if (cancel is not None and cancel.is_set()) or time.time() - t0 > timeout_s:
                    proc.terminate()
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                    return
                time.sleep(0.2)

        threading.Thread(target=watch, daemon=True, name="robot-agent-watch").start()
        final_text = ""
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            kind = ev.get("type")
            if kind == "assistant":
                for block in (ev.get("message") or {}).get("content") or []:
                    if block.get("type") == "text" and block.get("text", "").strip():
                        final_text = block["text"].strip()
                        if not intent_sent and on_intent:
                            intent_sent = True
                            on_intent(final_text[:SUMMARY_MAX])
                    elif block.get("type") == "tool_use" and on_tool and str(block.get("name", "")).startswith("mcp__"):
                        # only the robot skills are ledger steps; the CLI's own helpers (tool lookup) are noise
                        on_tool(
                            str(block.get("name", ""))[:80],
                            block.get("input") if isinstance(block.get("input"), dict) else {},
                        )
            elif kind == "result":
                out["turns"] = int(ev.get("num_turns") or 0)
                out["cost_usd"] = ev.get("total_cost_usd")
                if isinstance(ev.get("result"), str) and ev["result"].strip():
                    final_text = ev["result"].strip()
                out["ok"] = not ev.get("is_error", False) and ev.get("subtype") == "success"
                if not out["ok"]:
                    out["error"] = str(ev.get("subtype") or "agent error")[:300]
        rc = proc.wait()
        stderr = proc.stderr.read()[-1000:] if proc.stderr else ""
    out["duration_ms"] = int((time.time() - t0) * 1000)
    out["summary"] = final_text[:SUMMARY_MAX]
    if cancel is not None and cancel.is_set():
        out.update(ok=False, outcome="stopped", error="stopped by ClawMetry")
    elif time.time() - t0 > timeout_s:
        out.update(ok=False, outcome="failed", error=f"agent exceeded {timeout_s:.0f} s")
    elif rc != 0 and not out["ok"]:
        out["error"] = out["error"] or (stderr.strip().splitlines() or [f"agent exited {rc}"])[-1][:300]
    elif out["ok"]:
        out["outcome"] = "completed"
    return out
