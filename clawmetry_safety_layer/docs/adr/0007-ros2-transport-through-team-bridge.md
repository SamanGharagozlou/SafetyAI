# 0007. D1 Ultra-W: ROS 2 through the team's bridge, not a second SDK connection

- Status: Proposed (2026-11-14)
- Applies to: `bridge/agibot_d1w_bridge.py`, `config/d1w.yaml`

## Context
* **The SDK:** the D1 SDK (`mc_sdk_zsl_1w_py`) accepts **one** client. It needs a keep-alive, because with no traffic
  for 3 s the robot damps and lies down. It refuses commands under 20 % battery, and it is CPython 3.10 only.
* **The team's bridge:** the SafetyAI team already runs `d1_cmd_vel_bridge` (ROS 2 Humble). It owns that connection and
  exposes `cmd_vel`, `odom`, `imu`, `battery`, `ctrl_mode` and `connected`, plus posture services.

## Decision
* **Default transport `ros2`:**
  * we publish capped, exact-zero `cmd_vel` at 20 Hz and `gait: move` once;
  * we subscribe to their state topics;
  * we create no client for `lie_down` or `passive`, and no publisher for `robot_cmd` or `attitude_cmd`;
  * `stand_up` and `damp` are only called with literal-`true` flags.
* **`sdk` transport, fallback only:** the operator sets `D1W_TRANSPORT=sdk` **and** the SDK's UDP port must be free on
  this host, which proves nobody else owns the SDK. It sends the keep-alive at 10 Hz.
* **`mock`:** a kinematic twin with the same frames and topics, for tests and rehearsal.

## Consequences
* Our code never fights their bridge for the SDK. Stop means zero `cmd_vel`, and the robot holds its stance.
* We depend on their node being up. No state for 1 s produces one `handover` event and every move is refused; there is
  no exception loop.
* Their `keyboard_teleop` can still publish `cmd_vel` around us. Our L1-on-measured-motion stops the robot near the edge,
  and the runbook says not to teleop while the edge guides.
