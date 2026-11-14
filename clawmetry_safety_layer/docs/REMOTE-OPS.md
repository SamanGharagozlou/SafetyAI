# Remote ops: edge at the venue, brain in the UK

## Topology

```
 Leukerbad Therme (venue Wi-Fi Event_MyLAG)                       UK: DGX Spark (spark-7a33)
 ┌───────────────────────────────────────────────┐   tailnet      ┌───────────────────────────────────────────┐
 │ D1 Ultra-W 172.24.1.41 ── UDP 43988 (SDK) ──┐ │  (WireGuard)   │ brain :8780  relay /frame -> verdict      │
 │                                              │ │◀─────────────▶│              console (mission control)    │
 │ team PC 172.24.0.239 (Ubuntu 22.04)          │ │   goals,       │              concierge (Anthropic API)    │
 │  d1_cmd_vel_bridge (ROS 2, owns the SDK) ◀───┘ │   frames,      │ Cosmos-Reason2 :8000 (vLLM, --api-key)   │
 │  team detector :8090 (YOLO, GPU)               │   verdicts     │ ClawMetry (brain side)                    │
 │  OUR EDGE :8765  skills/MCP + safety FSM       │                └───────────────────────────────────────────┘
 │     + perception + concierge client + journal  │
 │  ClawMetry (sync daemon -> cloud: Stop/Reset,  │◀── ClawMetry cloud: sessions, Approve/Deny, Stop/Reset, audit
 │            Approve/Deny, audit)                │
 └───────────────────────────────────────────────┘
```

* **Velocity never crosses the internet.** Only goals go out (`guide_to`, `say`, `ask`, `pause`, `resume`, `approve`,
  `deny`, `reset`, policy edits), plus frames (at most 640x360 JPEG, never stored) and verdicts. The 20 Hz `cmd_vel`
  stream, the zone policy, the speed caps, the L1 geofence and the safety FSM all run on the team PC.
* **What each layer may do:** ClawMetry adds approvals, Stop/Reset and audit. The brain adds advice (verdicts) and
  goals (the concierge). Neither can make the robot faster or less constrained.

## Link method (found on this Spark, 2026-11-14)

* **Tailscale is installed and up** on the Spark. Its tailnet name is `spark-7a33`, full MagicDNS name
  `spark-7a33.<tailnet>.ts.net`. MagicDNS is on.
* There is no `~/.ssh/config` on the Spark, and `sshd` listens on :22. cloudflared is not installed.
* **Default: Tailscale.** The team PC joins the same tailnet and gets the MagicDNS name `edge`:
  ```bash
  curl -fsSL https://tailscale.com/install.sh | sh
  sudo tailscale up --hostname=edge          # log in with the tailnet owner's account (Eaby) or an auth key
  tailscale ping spark-7a33                  # expect "pong ... via DERP" or "direct"
  ```
* **Fallback 1: SSH tunnel.** Run this from the team PC: `bash scripts/remote/reverse_tunnel.sh <user>@<spark>`. It
  forwards the brain (:8780) and Cosmos (:8000) to the team PC, and reverse-forwards the edge (:8765) to the Spark.
* **Fallback 2: cloudflared**, for the brain's HTTP only: `cloudflared tunnel --url http://127.0.0.1:8780`. The keys
  still apply. The edge then has no inbound path, so verdicts arrive only as `/frame` responses (the sampler ignores
  them), and the judge counts as unavailable. Use it for the concierge only.

## Ports and keys

| Port | Service | Binds | Key |
|---|---|---|---|
| 8765 | edge (team PC) | `BIND_HOST` = the team PC's tailnet IP | `X-Api-Key: EDGE_API_KEY` (loopback exempt) |
| 8780 | brain (Spark) | `BIND_HOST` = `<spark tailnet IP>` | `X-Api-Key: BRAIN_API_KEY` (console: cookie after login) |
| 8000 | Cosmos vLLM (Spark) | `0.0.0.0` | `Authorization: Bearer COSMOS_API_KEY` (`--api-key`, required by `serve_reason2.sh`) |
| 8090 | team detector | the team PC | none (their tool, on the robot LAN; our edge reads it locally) |
| 43988/udp | D1 SDK | the team PC | the robot's own `sdk_config.yaml` `target_ip` |

* **`.env` setup:** `scripts/setup_edge.sh` and `scripts/setup_brain.sh` write a `.env` with fresh keys. Copy
  `EDGE_API_KEY`, `BRAIN_API_KEY` and `COSMOS_API_KEY` so both machines hold the same three values.
* **Binding:** `leukerbad_g1/settings.py` refuses to bind `0.0.0.0` for the edge and the brain.
* **Cosmos exception (decided 2026-11-14):** Cosmos binds all interfaces, so the team PC can reach it over Tailscale.
  That is why `serve_reason2.sh` refuses to start without a key.

## Degraded modes (implemented and tested)

| What fails | What the edge does | Test |
|---|---|---|
| Brain unreachable (2 missed heartbeats) | `link_lost`; FSM CAUTION (0.15 m/s), `judge_unavailable`; the concierge answers from the local venue facts (`source: local`); `guide_to` is refused only with `safety.judge_required: true` | `test_safety.py::test_caution_caps_speed_and_link_loss_never_faster`, `test_concierge.py::test_edge_api_keys_and_no_velocity` |
| No verdict within 10 s of a request | cause `judge_unavailable`: CAUTION | `safety/supervisor.py` (`JUDGE_WAIT_S`) |
| ClawMetry unreachable (sync heartbeat older than 60 s) | the journal keeps buffering locally (the sync daemon uploads it later); `on_control_plane_loss: hold_at_waypoint` (default) holds at the next `guide_to` waypoint, `continue_caution` drives on at 0.15 m/s | `test_safety.py::test_caution_caps_speed_and_link_loss_never_faster`, `::test_continue_caution_policy` |
| Team detector down or frozen (frame number unchanged for 1 s) | `detector_stale`; `local_yolo` if enabled, else `l2_people_disabled` and CAUTION | `test_perception.py::test_team_detector_staleness`, `::test_pipeline_observation_and_camera_off` |
| Team ROS bridge down | no state for 1 s: one `handover` event, zero velocity, every move refused. The SDK transport is used only if the operator sets `D1W_TRANSPORT=sdk` **and** the SDK port is free (nobody else owns the SDK) | `test_agibot_d1w_bridge.py::test_state_timeout_is_one_handover_event`, `::test_sdk_refused_when_someone_owns_the_port` |

## How Eaby runs mission control from the UK

1. On the Spark: `bash scripts/cosmos/serve_reason2.sh` (if it isn't running), then `make brain`.
2. In a browser on the tailnet, open `http://spark-7a33:8780/` and log in with `BRAIN_API_KEY`. The console shows:
   * the robot's state and its pose on the zone map;
   * the safety FSM and its causes;
   * the last Cosmos verdicts, the people count, the link round-trip time;
   * the ClawMetry session link;
   * buttons: Pause, Resume, Reset, Approve / Deny, Confirm, Guide to, Say, Ask. All of them are goals.
3. ClawMetry cloud (Physical AI page) shows the robot's sessions and holds Stop and Reset. Stop is a physical stop:
   zero `cmd_vel` and the robot holds its stance.
4. On the team PC, the venue operator keeps the controller and the e-stop. Remote staff never replace the person next to
   the robot.

## Edge fallback B (no team PC), documented and mock-tested only

* **Setup:** the edge runs on the robot itself: `ssh firefly@172.24.1.41`, aarch64; the cp310 `.so` files are present.
  Python 3.10 there is # VERIFY.
* **Install:** `scripts/setup_onrobot.sh` (a venv under `/home/firefly/clawmetry`, no ROS); `--run` starts the edge
  with the SDK transport, pinned and niced. The script header has the exact `sdk_config.yaml` change and the revert.
* **Transport:** `D1W_TRANSPORT=sdk` with `local_ip = robot_ip` (SDK doc 5.7). This needs `target_ip` changed in
  `/opt/export/config/sdk_config.yaml` to the robot's own IP, plus a reboot.
  * **Revert:** restore `sdk_config.yaml.bak_pre_ros`, the team's backup, and reboot.
* **Resources:** run pinned to cores 0–3 with `nice` (`taskset -c 0-3 nice -n 10 make edge`); the motion controller is
  pinned to core 7.
* **Perception:** reduced to the `floor_transition` boundary plus the scene class at 1 fps.
* **Brain link:** Tailscale (static aarch64 binary) on the robot, or any LAN laptop running our relay.
