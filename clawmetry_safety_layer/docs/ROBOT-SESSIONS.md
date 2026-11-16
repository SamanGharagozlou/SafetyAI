# Robot sessions with prediction

What the skills API now records for every robot task, how the behaviour policy, the predictor and the continuous
monitor enforce it, and how it shows up on ClawMetry's Physical AI page (`http://localhost:8900/robotics`).

| Piece | File |
|---|---|
| Behaviour policy (what is normal / forbidden) | `policies/behaviour.yaml`, loader + validation + checks in `skills/behaviour.py` |
| Predictor (world-model interface + v1) | `bridge/predictor.py` (`Predictor` ABC, `KinematicPredictor`) |
| Command gate (predict → check → send → record) | `skills/gate.py` |
| Enforcement in the skills (same places zones were enforced) | `skills/skills.py` |
| Continuous monitor (200 ms) | `skills/monitor.py`, started by `skills/api.py` |
| Session / turn trace writer + ClawMetry controller | `skills/clawmetry_robotics.py` |
| Policy API | `GET /policy`, `PUT /policy` in `skills/api.py` |
| Anomaly feed | `telemetry/otel.py` (`skill.deviation_z` folded into `skill.anomaly_z`) |
| Tests | `tests/test_robot_sessions.py` (9), `tests/test_clawmetry_controller.py` (6) |

## 1. Sessions and turns

A **session** is one robot task. It opens on the first skill call after an idle gap and closes on
`place` succeeding, on `hold_pose` with "handover" in the reason, or after 30 s without a skill call
(`SESSION_IDLE_S`). Each skill call is one **turn** and writes these events, in order:

| # | kind | data (besides `session_id`, `turn`, `turn_id`, `controller_id`) |
|---|---|---|
| 1 | `turn.observe` | `skill`, `snapshot {x, y, yaw, zone, battery_pct, balance_ok, holding, mode, arm_q}`, `thumbnail` (JPEG path from `demo_cam`, 320×180, Isaac body only; `null` otherwise) |
| 2 | `turn.decide` | `tool`, `args`, `rationale` (the agent's one-line reason, from the optional `rationale` argument every MCP tool / REST body now takes) |
| 3+ | `predict` | before **every** actuator command segment: `command`, `prediction {model, horizon_s, t[], x[], y[], yaw[], v[], zones[], speed_mps, yaw_rate, hold_height_m, duration_s, end, path_m}`, `breach` (bool), `time_compression` |
| | `breach_predicted` | only when a prediction breaks the policy: `command`, `breaches [{rule, severity, zone, value, cap, detail, t}]`, `prediction_model`, `action` |
| | `turn.command` | what was actually sent: `type: velocity` → `vector [vx, vy, vyaw]`, `vector_mean`, `duration_s`, `n_commands`, `speed_scale`; `type: arm` → `targets` (14), `ramp_s`, `pose`, `grasp_height_m`, `duration_s`; `type: hold_pose` → `reason` |
| n-2 | `deviation` | predicted vs actual for the turn (only if a command was executed): `scope` (`goto_plan` for navigate_to, `commands` otherwise), `position_error_m`, `yaw_error_rad`, `duration_error_s`, `predicted_duration_s`, `actual_duration_s`, `predicted_end`, `actual_end` |
| n-1 | `turn.feedback` | `confirmed_state`, `duration_ms`, `path_len_m` + `path_source` (`skill` / `monitor` / `displacement`), `deviation` summary |
| n | `turn.outcome` | `outcome` ∈ `ok`, `blocked_by_policy`, `failed`, `paused`; `ok`; `message` |

Session events: `session.started {opened_by, idle_close_s, policy, policy_version}` and
`session.ended {reason: place | handover | idle | shutdown, turns, outcomes, duration_s}`.
Other events in the same journal: the existing 1 Hz `observation` (kept), `breach` from the monitor,
`policy.changed`, the `control.*` Stop/Reset events, and `action.blocked` / `robot.error` mirrors of blocked /
failed outcomes (so the page's "Blocked commands" counter and "Errors" filter work).

Velocity commands are sent at 10–50 Hz; they are merged into **segments** (a new segment when the command changes
by > 0.05 m/s or > 0.15 rad/s), one `predict` + one `turn.command` per segment. Every individual command is still
checked against the policy; a breach on any of them emits its own `predict` + `breach_predicted`.

All events are `clawmetry.robotics.v1` journal lines (`~/.clawmetry/robotics/journal/<run_id>.jsonl`, one run per
skills-API process), validated by ClawMetry's own `store._validate` in the tests.

## 2. Behaviour policy (`policies/behaviour.yaml`)

Zone geometry and the `restricted` / `camera_off` flags stay in `policies/zones.yaml`; the behaviour file holds
the numbers: per-zone speed caps, near-person cap, allowed objects and place targets, `max_duration_s` and
`max_path_m` per skill, `max_retries`, forbidden actions (`enter_restricted_zones` — cannot be disabled,
`enter_camera_off_zones`, `max_hold_height_m: 1.2`, `max_speed_while_holding_mps: 0.3`, `max_tilt_deg: 15`),
monitor margins, prediction horizon, and quiet hours.

Where it is enforced (the old `zones.yaml` checks are now policy checks at the same lines in `skills/skills.py`):

* `navigate_to`: forbidden target zone, straight path through a forbidden zone, per-zone caps (with the holding /
  near-person / quiet-hours minimum), `max_duration_s` caps `max_s`, `max_path_m` checked on the prediction and
  while walking, 0.5 m look-ahead into forbidden zones.
* `approach` (docking for pick/place): forbidden target / docking point / path, `max_path_m`.
* `pick` / `place`: allowed objects / place targets.
* every skill: quiet-hours `forbid_skills`, `max_retries` (a skill that *failed* — not blocked — more than
  `max_retries` times in a row with the same args is refused), `max_duration_s` overrun → soft `breach` event.

Choices to check:

* `max_speed_while_holding_mps` is 0.3, not lower. The demo carries the tray at the corridor/B cap of 0.3, and a
  lower value would block it.
* Quiet hours are off by default (`enabled: false`), so tests and demos behave the same at any hour. When on,
  22:00–07:00 forbids `say` and caps speed at 0.2.
* `near_person_speed_cap` applies only when the body reports `near_person()`. No body has a person detector yet.

**API.**
* `GET /policy` returns `{path, policy, zones, quiet_now}`.
* `PUT /policy` takes the whole document. It validates it and returns 422 with `{"errors": [...]}` on a bad
  document. On success it writes the file atomically (comment header kept), bumps `version`, and emits:

  `policy.changed {policy_name, version, previous_version, changed_by, changed_at, changed_keys, changes{key: {from, to}}}`

  "Who" is the `X-Operator` header, else `changed_by` in the body, else the client address. Changes apply to the
  next command; the monitor reads the policy every tick.

## 3. Predictive breach detection

`bridge/predictor.py` defines the interface: `Predictor.predict(state, command, horizon_s, dt, zone_of)` and
`Predictor.predict_goto(state, plan, …)` return a `Prediction`. Predictor v1, `KinematicPredictor`, is the
FakeBridge integrator: body-frame velocity, yaw first, 50 Hz substeps, with the bridge's `speed_scale`. It does no
learning. A learned world model replaces it by implementing the same two methods and being assigned to
`skills.gate.predictor`.

* **Velocity commands** carry their own termination: `until_m` (distance still to go) or `until_yaw` (angle still
  to turn). The rollout therefore stops where the skill will stop. It does not extrapolate a decelerating command
  past the docking point.
* **navigate_to** is predicted as a whole before the first command (`GotoPlan`). The skill's steering law runs at
  10 Hz under the policy caps. The trajectory is sampled over the 3 s horizon; `duration_s`, `end` and `path_m`
  cover the whole walk, and the deviation event uses them.
* **Arm commands** predict the grasp-site height at the end of the ramp. Heights come from the IK solve
  (`bridge.base.GRASP_Z`; LIFT = CLOSE + 0.10 m); vendor bodies can supply their own `GRASP_Z`.

Each prediction is checked against the policy sample by sample: forbidden zone, speed against that sample's zone
cap, and carry height. A breach emits `breach_predicted`, raises `PredictedBreach` before the bridge is called,
and the skill runs `hold_pose`'s body (`stop_and_settle`, logged as a `turn.command type: hold_pose`). The result
is `blocked_by_policy: predicted breach: …`.

**Deviation.** After the turn, the predicted end pose and duration are compared with what happened. The span gets
`skill.deviation_pos_m`, `skill.deviation_dur_s` and `skill.deviation_z`:

`deviation_z = max(pos_err / 0.10 m, |dur_err| / max(0.5 s, 15 % of predicted))`

The sigmas come from `baseline.json` (`deviation_pos_std` / `deviation_dur_std`) when present. `skill.anomaly_z`
is now the larger of the duration z and the deviation z.

`time_compression`: the test configs run the fake/sim bodies 8× fast (`speed_scale: 8`). With
`time_compression: 8`, the predictor and the monitor judge speeds in real-robot time. It is refused on the
`g1` / `go2` bodies.

## 4. Continuous monitor (`skills/monitor.py`)

A background thread in the skills API (`BEHAVIOUR_MONITOR=0` disables it) runs every `monitor.period_s`
(200 ms), independent of skill calls. It checks:

* speed (finite difference of the bridge pose) against the cap for the current zone, holding state, nearby person
  and quiet hours: soft above `cap × 1.1`, hard above `cap × 1.5`;
* entry into a forbidden zone (hard);
* carry height (hard);
* tilt, on bodies that implement `tilt_deg()` (hard).

It emits `breach {source: monitor, active, severity, rule, zone, value, cap, action, state}`. The event fires on
the rising edge, every 2 s while the breach lasts, and as `active: false` when it clears.

On a hard breach it calls `Skills.monitor_stop(reason)`, so the running skill stops issuing commands and returns
`stopped by monitor: …`. Then it calls `bridge.stop_and_settle()`, the body's own stop; it never damps and never
touches the e-stop. Pose jumps over 1 m in one period (sim resets) are ignored. The monitor also accumulates the
turn's path length for `turn.feedback`.

## 5. Example session

The full task on the fake body through the REST API (`uvicorn skills.api:app`, `config/fake.yaml`):
`get_state` → `navigate_to corridor` → `pick tray` → `navigate_to B` → `place table_B`. The session closed on the
place: `session.ended {reason: place, turns: 5, outcomes: {ok: 5}, duration_s: 20.3}`.

The complete session (46 events) is in `docs/robot_sessions/example_session.jsonl`. Turn 2 is below, with arrays
shortened and `controller_id` omitted:

```json
{"seq": 6,  "kind": "session.started", "data": {"session_id": "b6a77bd0bf08463d", "opened_by": "get_state", "idle_close_s": 30.0, "policy": "leukerbad-spa-default", "policy_version": 1}}
{"seq": 11, "kind": "turn.observe", "data": {"session_id": "b6a77bd0bf08463d", "turn": 2, "turn_id": "b6a77bd0bf08463d-2", "skill": "navigate_to",
             "snapshot": {"x": 1.0, "y": 1.0, "yaw": 0.0, "zone": "A", "battery_pct": 100.0, "balance_ok": true, "holding": null, "mode": "idle", "arm_q": "[14 × 0.0]"}, "thumbnail": null}}
{"seq": 12, "kind": "turn.decide", "data": {"turn": 2, "tool": "navigate_to", "args": {"zone": "corridor"}, "rationale": "towels are on the tray on the corridor shelf"}}
{"seq": 13, "kind": "predict", "data": {"turn": 2, "command": {"type": "goto", "goal": [3.0, 1.0], "zone": "corridor", "max_s": 20.0},
             "prediction": {"model": "kinematic-v1", "horizon_s": 3.0, "t": [0.0, 0.1, 0.2, "…"], "x": [1.0, 1.03, 1.06, "…"], "y": [1.0, 1.0, 1.0, "…"],
                            "v": [0.3, 0.3, 0.3, "…"], "zones": ["A"], "speed_mps": 0.3, "hold_height_m": null, "duration_s": 6.3, "end": [2.71, 1.0, 0.0], "path_m": 1.71},
             "breach": false, "time_compression": 1.0}}
{"seq": 14, "kind": "predict", "data": {"turn": 2, "command": {"type": "velocity", "vector": [0.3, 0.0, 0.0], "until_m": 1.7}, "prediction": {"duration_s": 5.667, "end": [2.7, 1.0, 0.0], "…": "…"}, "breach": false}}
{"seq": 21, "kind": "turn.command", "data": {"turn": 2, "type": "velocity", "vector": [0.3, 0.0, 0.0], "vector_mean": [0.3, 0.0, 0.0], "duration_s": 5.74, "n_commands": 57, "speed_scale": 1.0}}
{"seq": 23, "kind": "deviation", "data": {"turn": 2, "skill": "navigate_to", "scope": "goto_plan", "position_error_m": 0.012, "yaw_error_rad": 0.0, "duration_error_s": 0.095,
             "predicted_duration_s": 6.3, "actual_duration_s": 6.395, "predicted_end": [2.71, 1.0, 0.0], "actual_end": [2.722, 1.0, 0.0]}}
{"seq": 24, "kind": "turn.feedback", "data": {"turn": 2, "skill": "navigate_to", "confirmed_state": {"x": 2.722, "y": 1.0, "yaw": 0.0, "zone": "corridor", "mode": "standing", "holding": null},
             "duration_ms": 6394.8, "path_len_m": 1.72, "path_source": "skill", "deviation": {"position_error_m": 0.012, "duration_error_s": 0.095}}}
{"seq": 25, "kind": "turn.outcome", "data": {"turn": 2, "skill": "navigate_to", "outcome": "ok", "ok": true, "message": "arrived"}}
```

Turn 3 (`pick tray`) has four arm `predict` / `turn.command` pairs:

| Pose | Ramp | Grasp height | Predicted carry height |
|---|---|---|---|
| REACH_SHELF | 1.5 s | 0.936 m | — |
| CLOSE_SHELF | 0.4 s | 0.936 m | — |
| LIFT_SHELF | 0.5 s | 1.036 m | 1.036 m |
| HOLD | 1.0 s | 1.068 m | 1.068 m |

The carry-height check applies from the grasp on. The turn's `deviation {scope: commands}` was 3.4 s predicted
against 3.442 s actual.

A **blocked** turn is in `docs/robot_sessions/example_breach_predicted_turn.jsonl`. The robot starts at the north
edge of reception (1.0, 1.8) facing the pool deck, and `navigate_to C` is called. The goto prediction enters zone C
at t = 2.8 s, which produces:

* `predict {breach: true}`;
* `breach_predicted {rule: forbidden_zone, zone: C, severity: hard, t: 2.8}`;
* `turn.command {type: hold_pose}`;
* `turn.outcome {outcome: blocked_by_policy}`;
* `action.blocked`.

No velocity command was sent, and no deviation event was written because nothing ran.

## 6. On the Physical AI page

The page lists our runs as `leukerbad-fake · Simulation`. It renders kinds it does not know under their own name,
with the raw event under *Trace → Raw event*. `action.blocked` shows in red as "Command blocked" and counts toward
"Blocked commands". `policy.changed` is a kind the page knows: it shows "Motion policy: <name>". Screenshots,
taken with headless Firefox against the live dashboard:

| | |
|---|---|
| `docs/robot_sessions/page_activity.png` | Activity list of the blocked turn: turn observe → turn decide → **predict** → **breach_predicted** → turn command (hold_pose) → turn feedback → turn outcome → Command blocked |
| `docs/robot_sessions/page_predict.png` | **predict** (event 13): goto into C, `breach: true`, predicted trajectory |
| `docs/robot_sessions/page_breach_predicted.png` | **breach_predicted** (event 14): `forbidden_zone`, zone C, `t: 2.8`, action "blocked; hold_pose before the command was sent" |
| `docs/robot_sessions/page_deviation.png` | **deviation** (event 23 of the task session): goto plan predicted 6.3 s / end (2.71, 1.0), actual 6.395 s / (2.722, 1.0) |

Limits of the page:

* Its own description line for these kinds is generic ("Recorded by the controller or tracing integration").
* The joint chart is for SO-101 arms, so it shows "No observations" for our runs.
* The 3-D pose viewer only draws `rp1_mujoco` runs.

The data is all there; a custom view would need ClawMetry-side changes.

## 7. Not verified yet

* Thumbnails: the code path calls `IsaacBridge.screenshot(demo_cam, 320×180)`. It has not run, because nothing may
  render in Isaac until the pass-2 progress file says DONE. MuJoCo has no thumbnail.
* Stop, the monitor and the predictor on the real G1 (`tilt_deg()` is not implemented on any bridge yet).
* `tests/test_isaac_props.py` under the new gate: it waits for DONE (GPU rule). Its config now sets
  `time_compression: 8` like the others.

## guide_to and the D1 Ultra-W edge events (2026-11-14)

`guide_to(poi, lang)` (MCP tool, `POST /skills/guide_to`, edge `POST /guide_to`) is an orchestration of traced turns, not
one long turn: `say(intro)` → one `navigate_to` per leg (direct, or via a hub zone from `venue/leukerbad_therme.yaml`
when the straight line passes within 0.5 m of a no-go zone) → `say(arrival)`. Each leg is checked by the zone policy, the
L1 geofence (2 s prediction), the speed caps (CAUTION 0.15 m/s) and the safety FSM. No-go POIs are refused before
anything moves, with the nearest allowed POI. A POI with `requires_approval` also files a ClawMetry approval
(`mcp__g1-skills__guide_to`) asking whether STAFF should escort the guest; the robot never enters either way.

Extra journal event kinds from the edge stack: `guide` (request / refused / stopped / arrived), `concierge.turn`,
`fsm.transition` {from, to, reason, source}, `breach_predicted` (now with `source: L1`), `person_near`, `hazard`,
`judge_verdict`, `recover`, `approval`, `alert_staff`, `link_lost` / `link_restored`, `handover` / `state_restored`,
`tilt_breach`, `pose.refix`, `landmark_seen`.
