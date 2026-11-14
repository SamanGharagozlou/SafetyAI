# ClawMetry safety layer + spa concierge for the AgiBot D1 Ultra-W

A governed brain on top of the SafetyAI team's robot interface. It runs next to your `d1_cmd_vel_bridge` and adds:
* zone policy, speed caps and a NORMAL / CAUTION / HOLD / RECOVER safety FSM, all enforced locally;
* perception: people distance, floor transitions, landmark fixes;
* a Cosmos-Reason2 judge on a remote brain;
* a multilingual spa concierge;
* ClawMetry Stop / Reset / Approve and an audit journal.

It sends **goals only**; velocities never leave the team PC. Apache-2.0 (see NOTICE).

## Quick start on the team PC (Ubuntu 22.04, ROS 2 Humble)
```bash
git clone <this repo> && cd SafetyAI/clawmetry_safety_layer
bash scripts/setup_edge.sh                  # Python 3.10 venv (+ ROS system packages), .env with fresh keys, self-check
nano .env                                   # same EDGE/BRAIN/COSMOS keys as the brain; BRAIN_URL, BIND_HOST (tailscale ip -4)
# 1. your bridge:   ros2 launch agibot_d1_bridge bridge.launch.py local_ip:=172.24.0.239 robot_ip:=172.24.1.41
# 2. your detector: perception/.venv/bin/python perception/detect.py --source rtsp://172.24.1.41:8554/test --no-show
# 3. the operator stands the robot with the controller (our code never calls stand_up)
make edge                                   # skill server + safety + perception on BIND_HOST:8765
curl -s -H "X-Api-Key: $EDGE_API_KEY" http://$BIND_HOST:8765/state | jq .safety
python -m bridge.agibot_d1w_bridge straight # first motion rung (bounded 0.2 m/s x 1 s), edge stopped
```

**Launch order:** your bridge, then your detector, then `make edge`. The brain (`make brain`) runs on the DGX Spark.
**No robot?** `make mock` runs everything on one machine: a kinematic twin plus a video file as the camera.

| Read | For |
|---|---|
| `docs/D1W-RUNBOOK.md` | the Sunday checklist, calibration (no floor tape), the 30-minute ladder |
| `docs/INTEGRATION-SAFETYAI.md` | which of your topics and services we use and never use; where SLAM, detection, voice and the app plug in |
| `docs/ADAPTER-API.md` | every HTTP endpoint and event (state, events, ask, guide_to, verdicts) |
| `docs/REMOTE-OPS.md` | edge / brain topology, Tailscale, keys, degraded modes |
| `docs/DEMO-SCRIPT.md`, `docs/PRIVACY.md`, `docs/COSMOS.md` | the five demo beats, camera rules, judge latencies |

Safety rules in code (tested):
* stop means zero `cmd_vel` and the robot keeps its stance. We never call `lie_down` or `passive`, and never publish
  `robot_cmd` or `attitude_cmd`.
* the gait stays "move".
* caps: 0.4 m/s forward (0.3 guide pace), 0.15 m/s lateral, 0.6 rad/s. Below the SDK minimum a component is sent as
  exactly 0.
* a person within 1–2 m means 0.15 m/s; closer than 1 m means HOLD.
* a tilt over 15°, or no state for 1 s, means stop.
* only ONE SDK client: the sdk transport refuses if your bridge holds the port.
