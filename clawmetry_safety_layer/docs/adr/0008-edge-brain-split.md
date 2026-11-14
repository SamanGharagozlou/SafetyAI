# 0008. Edge / brain split: safety at the venue, advice from the UK

- Status: Proposed (2026-11-14)
- Applies to: `skills/edge.py`, `brain/`, `concierge/`, `safety/link.py`, `docs/REMOTE-OPS.md`

## Context
* **Compute:** the GPU (Cosmos-Reason2, Isaac, recordings) is a DGX Spark in the UK. The robot is in Leukerbad, on venue
  Wi-Fi.
* **People:** the operator who can supervise the whole run is also in the UK.
* **The link:** it can lag, or drop.

## Decision
* **Edge (team PC, next to the robot):** the skill server (MCP + REST), zone policy, speed caps, safety FSM, L1, L2,
  the 20 Hz `cmd_vel`, the concierge client with a local fallback, and the robot journal (ClawMetry on that machine).
* **Brain (Spark):** the Cosmos judge (the only implementation, in `brain/relay.py`), the mission-control console, the
  concierge's Anthropic tool loop, and dataset export.
* **Across the link go only:** goals (`guide_to`, `say`, `ask`, `pause`, `resume`, `approve`, `deny`, `reset`,
  policy), frames (at most 640x360, never stored) and verdicts. **No velocities.** Every call carries `X-Api-Key`.
  Services bind to the tailnet; Cosmos binds all interfaces, but only with its own key.
* **Link loss never loosens anything:**
  * Brain down: CAUTION and judge_unavailable; the concierge answers locally.
  * ClawMetry down: hold at the next waypoint (default) or continue at CAUTION; the journal buffers.

## Consequences
* The robot stays safe with the UK side gone. It is slower and more constrained, never less.
* The UK operator steers with goals and approvals, not a joystick. Physical emergencies belong to the person with the
  controller and the e-stop at the venue.
* Two `.env` files must hold the same three keys (checklist in docs/D1W-RUNBOOK.md).
