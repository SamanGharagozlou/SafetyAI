# ClawMetry physical-AI collector + control-plane dashboard

One process, zero dependencies, that gives the deck's three promises — **Observe,
Govern, Evidence** — over a robot stack whose parts do not otherwise emit
anything comparable.

```bash
# against the simulator, no robot, no Jetson
node tools/robot-sim/server.mjs &
python3 clawmetry/server.py --upstream http://127.0.0.1:5175 --body agibot_d1w \
        --zones clawmetry/zones.example.json --port 8901
open http://127.0.0.1:8901

# against the real robot: point the tap at the Jetson edge and Vapi at the tap
python3 clawmetry/server.py --body agibot_d1w \
        --upstream http://127.0.0.1:8090 --token "$D1_ROBOT_TOKEN" \
        --guard http://127.0.0.1:8092 --zones /etc/clawmetry/zones.json --judge

python3 test_clawmetry.py        # 41 tests, no robot, no camera, no network
```

## Why a tap and not an SDK client

The D1 permits **one SDK client**, which the team bridge already holds. Anything
that opens a second one either fails or breaks the stack it is meant to watch.
So this never touches the SDK. It sits at the **tool-call boundary**, which is
the first point in the chain that runs on hardware you own:

```
guest phone ─▶ Vapi cloud (STT · LLM · TTS)      ← self-report, not observable
                     │  tool call
                     ▼
            ┌──────────────────┐   record  ─▶ hash-chained ledger
            │  THE TAP  :8901  │   predict ─▶ kinematic (gate) + Cosmos (judge)
            │  POST /vapi      │   gate    ─▶ allow / deny
            └──────────────────┘   forward ─▶ unchanged, upstream
                     │
                     ▼
     d1-vapi-server :8090 ─▶ d1-ros-agent ─▶ bridge ─▶ SDK ─▶ robot
                     ▲                                          │
       collectors ───┴── GET /status (confirmed state) ─────────┘
                     └── GET /guard  (people, breaches)
```

Everything upstream of the tap is a **claim**; everything downstream is
**ground truth**. Oversight is the business of correlating the two, which is
why the breach set below is mostly about disagreement between sources rather
than any single threshold.

## What it detects

| rule | the question it answers | needs |
|---|---|---|
| `unattributed_motion` | the robot moved and no authorised intent explains it | state + intents |
| `gate_bypass` | a command reached the robot the gate never adjudicated | commands |
| `denied_command_executed` | something denied was sent anyway | gate + commands |
| `predicted_breach` | this action **will** leave the go-zone, before it runs | zones |
| `prediction_deviation` | it moved, but not to where the model said | pose feed |
| `unverified_action` | it completed and could not be checked at all | — |
| `policy_breach` | confirmed speed/person distance violated a cap | state |
| `silence` | a feed stopped reporting | heartbeats |

`unattributed_motion` is the flagship: one check that catches a compromised
agent, a stuck teleop key, a rogue publisher and a plain bug. **It needs
`twist_mux`** — with four unranked `/cmd_vel` writers nothing can attribute
motion to a source.

## Three rules this thing follows, learned the hard way

**Silence is a finding, not a gap.** This project has twice shipped a system
whose failure looked like calm — the endpoint answering `200` with nulls while
the robot lay on the floor, and the camera guard's `no fresh frame -> FREEZE`.
A missing feed emits a `silence` breach; it never renders as an empty chart.

**Our ignorance is not the robot's anomaly.** The first run of this collector
reported **46.7 sigma** on a walk that went exactly as intended, because nobody
had measured the walk speed and the predictor guessed. Duration is now scored
only against a **calibrated** body; position only when a pose feed actually
exists. Where neither holds, the verdict is `unverified_action` — visible, and
not confused with either success or anomaly. `agibot_d1w` and `unitree_g1` ship
`calibrated: False`; measure before trusting a sigma.

**The observer never actuates.** No endpoint here commands the robot. It passes
calls through or refuses them, and it cannot originate motion. Fleet-wide
oversight that can also drive is a fleet-wide remote control — stop authority
stays with the local governor (`tools/robot-guard`, the edge FSM).

## Verified on the real robot (2026-11-15/16)

Calibration, five bounded walks on the D1W at the venue:

| measured | value |
|---|---|
| steady walk speed | **0.1373 m/s** |
| fixed overhead per walk | **4.11 s** (ramp-up + settle) |
| systematic undershoot | **4.1 cm ± 0.7 cm**, at every distance |
| validation walk | predicted 11.39 s / 95.9 cm, actual 11.58 s / 95.3 cm -> **0.13 sigma** |

Duration is `overhead + d/v`, not `d/v`: a legged gait spends its first strides
ramping and then waits out the settle while it coasts. The pre-calibration
guess of 0.041 m/s was wrong by 3.4x and produced a 46.7 sigma reading on a
walk that went exactly as intended.

Cosmos-Reason2 through the brain relay, live:

```
walk_distance 100cm  -> cosmos judged in 4.22s on a warm frame, clear, allowed
do_gesture nod       -> judge skipped (0 m translation), 0.067s
earlier, same frame  -> stop=true conf=0.97 "people are in the path ahead"
                        -> gate DENIED, the robot never received the command
```

## Cosmos: two models, on purpose

| | kinematic | Cosmos-Reason2 |
|---|---|---|
| when | every action, in the gate | behind the gate, best effort |
| latency | ~0 ms | seconds, over Tailscale to the Spark |
| answers | where this command puts the robot | is that a child, is the floor wet, is the doorway passable |
| failure | n/a | `available: False` — **never** an approval |

A judge on a remote box cannot sit in the path of a stop: at guide pace the
robot covers ~12 cm per 250 ms. So the kinematic model holds the gate and
Cosmos escalates behind it. When Cosmos is unreachable the run is recorded as
*kinematically gated, unjudged*, which is a weaker claim and reads as one.

## Evidence

`ledger.jsonl` is append-only and sha256-chained: each line carries the hash of
the one before. Editing, deleting or reordering any line breaks the chain from
that point, and `GET /api/verify` names the first break. Ingest time is the
primary timestamp with the source's claimed time kept as `src_ts` and the
difference as `skew_s`, because events arrive from a cloud, a Jetson, a robot
and a phone whose clocks disagree.

The dashboard's ledger view is the deck's slide 11: **who asked · what was
intended · what was predicted · who intervened · what it did**.

## Four bugs that only hardware found

Each one produced a confident, wrong answer; none would have surfaced in
simulation. They are the reason the tests now number 62.

1. **An inverted verdict.** The relay returns `{"ok","judged","verdict":{...}}`
   and this client read `stop` from the top level, where it does not exist. A
   real Cosmos verdict of `stop: true` at **0.98 confidence** - "backpack and
   blue object are in the path" - was handed to the gate as APPROVED. An
   unrecognised verdict shape now fails closed, with five tests pinning it.
2. **A silently unauthenticated collector.** The state poll sent no bearer
   token, got 401 for a whole session, produced zero samples, and the
   correlator then called a perfect 95 cm walk a lie - twice. (`:8090` returns
   401 on `GET /status` even *with* the right token, because its auth runs
   before routing; state comes from `:8091`, which needs none.)
3. **A collector that never starts was invisible.** `silence` only watched
   feeds that had reported at least once. Declared-but-absent feeds now raise
   `source_never_reported`, after a startup grace.
4. **A world model asked the wrong question.** Cosmos denied a *nod* because
   "people are in the path ahead, restricting forward movement" - true of
   walking, irrelevant to a gesture with four feet planted. In a room with
   eight people that denies every gesture the greeter has. Only translating
   actions are judged now.

The through-line: **every one of them turned an absence or a misread into an
approval.** That is the failure mode this whole layer exists to prevent, and it
kept reappearing inside the layer itself.

## Not done

- **Transcripts.** `utterance` events are defined and nothing emits them. The
  audio never touches our hardware, so this needs Vapi server messages — and
  arrives as the monitored party's self-report, which the dashboard labels.
- **OTLP export to the ClawMetry dashboard.** The ledger is local.
  `CLAWMETRY_DASHBOARD_URL` in the env file is still an unfilled placeholder and
  `CLAWMETRY_OTLP` points at `127.0.0.1:4318`, where nothing listens on the
  Orin. The exporter degrades to a **silent no-op** when wrong, so this needs
  the real URL before it can be trusted.
- **Synchronous judging.** Cosmos currently gates in-line, so a walk waits
  ~4 s before it starts. Defensible as a pre-flight check, but it contradicts
  the "never in the path of a stop" rule above; an async mode where the
  kinematic gate passes immediately and Cosmos escalates to a stop is the
  right end state.
- **Position scoring on hardware.** The ros-agent holds `/odom` internally but
  does not expose it in `/status`, so only duration is scored. Three lines in
  `~/vapi-robot/d1_ros_agent.py` would unlock it.
- **Reconciliation with `clawmetry_safety_layer`** (PR #1). That layer's
  `/events` vocabulary and `deviation_z` formula were deliberately reused here
  so the two converge rather than fork; they are still two processes.
- **Signing.** The chain detects tampering; it is not yet signed, so it proves
  internal consistency, not origin.
