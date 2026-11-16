# 0006. Three-layer go zone (L1 geofence, L2 perception, L3 judge) under one safety FSM

- Status: Proposed (2026-11-14)
- Applies to: `safety/` (fsm, geofence, supervisor, recover), `perception/`, `brain/relay.py`, `skills/gate.py`, `skills/monitor.py`

## Context
* **The venue:** the D1 Ultra-W guides guests on a spa concourse next to a wet pool deck, stairs, a barefoot zone and
  staff-only doors. No floor tape is allowed, and no camera footage from inside the swimming complex.
* **Localisation:** odometry only, so it drifts.
* **The camera:** low and tilted up.
* **Cosmos-Reason2-2B:** measured on venue frames (docs/COSMOS.md), it is slow (about 2 s), sees people well, and
  misses stairs. It is also inconsistent about `stop`.

## Decision
The robot may only be in the **go zone**: the union of the zones the behaviour policy allows. Three independent layers
feed one state machine (`safety/fsm.py`: NORMAL, CAUTION, HOLD, RECOVER):

* **L1, geometry (every command, plus measured motion):**
  * The command gate rolls each velocity command 2 s ahead, and the supervisor does the same with the measured motion,
    which catches teleop and slip. A predicted exit is a policy **HOLD** before the line.
  * The monitor stops on a measured entry into a forbidden zone.
  * Landmark re-fixes bound the drift.
* **L2, perception (5 Hz, CPU, the edge):**
  * People at 1–2 m give CAUTION (0.15 m/s). People under 1 m give a HOLD that clears by itself.
  * A no-go floor class or marker ahead while moving gives a policy HOLD.
  * A dead detector gives CAUTION with an explicit degraded flag.
* **L3, judge (Cosmos on the brain, on triggers):** `leaves_go_zone` at confidence 0.5 or more gives a policy HOLD.
  `stop`, hazards and people give CAUTION only. An unavailable judge gives CAUTION, and blocks `guide_to` only with
  `judge_required: true`.
* **Policy holds latch:** Reset runs `recover_to_go_zone` (straight back, 0.15 m/s, at most 1.5 m / 8 s, until 0.3 m
  inside). Then a human Approves (ClawMetry queue or console) before NORMAL.

## Consequences
* No single layer must be perfect. L1 is the barrier, L2 sees what the map can't, and L3 adds semantics and
  training labels.
* Every transition is a journal event with its source, so ClawMetry shows which layer acted.
* The judge cannot make the robot faster. It can only add caution or a hold.
* Recovery is the only automatic motion after a hold. It is bounded, backwards along the robot's own path, and checked
  by the same gate.
