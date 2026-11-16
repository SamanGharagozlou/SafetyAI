# 14 · Testing, evidence, and reproducing all of it

[← 13 Privacy and compliance](13-privacy-and-compliance.md) · [index](../README.md) · next: [15 Gaps and roadmap](15-gaps-and-roadmap.md)

> **The design rule underneath every suite below:** keep the logic out of the
> node, so it can be tested on a laptop while the robot is flat — which is most
> of the time. `human_perception`, `d1_gateway`, `correlate.py` and
> `guard_core.py` all follow it.

---

## 1. What was actually run, and when

Everything in this table was executed on **2026-11-16** on the development Mac
unless stated otherwise. Numbers are the real output, not a target.

| Suite | Command | Result |
|---|---|---|
| **ClawMetry control plane** | `cd clawmetry && python3 -m pytest -q` | **169 passed** in 0.52 s |
| **Camera people-guard** | `cd tools/robot-guard && python3 -m pytest -q` | **62 passed** in 0.04 s |
| **Credential scan** | `python3 scripts/audit_secrets.py` | **348 files, 0 findings, 7 documented exceptions** |
| **Secret-scanner's own tests** | `python3 -m pytest scripts/test_audit_secrets.py -q` | **14 passed, 60 subtests passed** |
| **Ten-detector scenario drive** | `python3 clawmetry/scenarios.py --url …` | **chain intact, 117 events, 11 breaches** across all ten scenarios |
| Gateway protocol | `pytest -p no:anyio ros2_ws/src/d1_gateway/test` | **not run here** — needs `pip install websockets`, which is not installed on this machine |
| ROS packages | `colcon test --packages-select human_perception d1_gateway` | **not run here** — needs ROS 2 Humble |
| Frontend | `cd frontend && npm ci && npm run typecheck && npm test && npm run test:e2e` | **not run here** — needs `npm ci` |

> **231 tests pass with no robot, no camera and no network.** That is the
> number worth quoting, and it is the two suites at the top of the table.
>
> `clawmetry/README.md` says 41 and later 62 in two places; both are stale.
> The suite has grown to 169 and the README was not updated. Fixing that is in
> [15](15-gaps-and-roadmap.md).

---

## 2. Test inventory by package

### Python

| File | Tests | Covers |
|---|---|---|
| `clawmetry/test_clawmetry.py` | **169** (whole suite) | the ledger chain, every detector, the predictor and verifier, the venue loader and its three clamping rules, the policy classifier, the compliance pack, the tap's gate pipeline, the voice sink, the trace stages |
| `tools/robot-guard/test_guard_core.py` | 29 | ranging, the blind radius, the state machine, child height and radius, the 5 s/3 s latch split, truncated boxes, the fail-closed gesture allowlist |
| `tools/robot-guard/test_awareness.py` | 18 | the awareness layer |
| `tools/robot-guard/test_escort.py` | 15 | lead-and-look-back, give-up-after-2-lost-checks |
| `ros2_ws/src/d1_follow/test/` | 47 across 7 files | `test_target_lock.py` alone is 23 — the `IDLE → LOCKING → TRACKING → LOST` machine, re-ID seeding, ambiguity, release |
| `ros2_ws/src/human_perception/test/` | 83 across 12 files | config validation (20), capture and reconnect (9), detection filtering (10), the node (10), the image codec (7), backoff and metrics (7), the benchmark (5), the pipeline (6), conversions (3), **plus flake8 and pep257 as tests** |
| `ros2_ws/src/d1_gateway/test/test_gateway.py` | 8 | the whole protocol end to end over a **real WebSocket** against the mock robot, no ROS |
| `scripts/test_audit_secrets.py` | 14 (+60 subtests) | the credential scanner, including that it never prints a matched value |

### JavaScript

| File | Covers |
|---|---|
| `tools/robot-face/test-flow.mjs` | **15 assertions across both screens**: arming, the QR, **server-side consent enforcement**, start/stop, and that a tapped shortcut arrives as a user turn. The Vapi SDK is **stubbed** |
| `tools/robot-console/test-keyboard.mjs` | **17 assertions in a real Chrome against the simulator** |
| `clawmetry/test-trace.mjs` | 15 — the Trace view's stage computation and gap arithmetic |
| `clawmetry/test-guardrails.mjs` | the Guardrails tab: classification, the loosening confirmation, the effective-vs-cap column |
| `frontend/scripts/vapi/boundary.test.mjs` | 4 — the assistant's tool boundary |
| `frontend/scripts/generate-qr.test.mjs` | 2 |
| `frontend/scripts/audit-dist.test.mjs` | 3 — the built bundle contains no secret |

### Two rules the test suites themselves follow

> **A test must not be able to move a robot.** `test-flow.mjs` stubs the Vapi
> SDK, because the real one would place a billable call to an assistant whose
> robot tools point at the live Jetson. `test-keyboard.mjs` carries the
> instruction *"Point it at the simulator, never the robot: it presses movement
> keys."*

> **The simulator is not a yes-machine.** Every refusal, quirk and unit in
> `tools/robot-sim/server.mjs` was observed on the real robot — *a simulator
> that always succeeds would tune the assistant into something that breaks on
> contact with hardware.* It refuses gestures unless `ctrl_mode == 1` with the
> real error string, it **settles out of standing when idle**, `walk_distance`
> **blocks and undershoots**, speed is a **fraction** not m/s, and it reports
> the undocumented control modes 18 and 21.

---

## 3. The ten-scenario detector drive

```bash
node tools/robot-sim/server.mjs &
python3 clawmetry/server.py --upstream http://127.0.0.1:5175 \
        --zones clawmetry/zones.example.json --port 8901 &
python3 clawmetry/scenarios.py --url http://127.0.0.1:8901
```

Each scenario is **one thing a reviewer should be able to see happen**. Real
tool calls go through the real tap; the rest are injected on `/ingest`, which
is **the same door the ROS, guard and approval collectors use — nothing here
is a special test path into the server.**

| # | Scenario | Shows |
|---|---|---|
| 1 | A normal, authorised action | intent → prediction → allow → command → outcome scored. The quiet case |
| 2 | Predicted breach | the kinematic model runs **before** the robot moves, so a misfire is refused, not merely recorded |
| 3 | A stop is never gated | *gating a stop is how you get a robot that cannot be stopped* |
| 4 | Unattributed motion | a rogue publisher, a stuck teleop key, or a compromised agent — one check, all three |
| 5 | A command the gate never saw | `gate_bypass` |
| 6 | A denied command executed anyway | the worst case in the set |
| 7 | Narration divergence | the agent told the guest it was moving; odometry says it never did — **caught while the call is still in flight** |
| 8 | Prediction deviation | sigmas from measured hardware, not a guess |
| 9 | Policy breach on confirmed state | judged on what the robot **did**, regardless of what any component believed |
| 10 | A feed that never starts, and one that dies | absence is a finding |

Real output, 2026-11-16:

```
  chain    : chain intact (117 events)
  breaches : 11
     [high    ] silence                    no event from guard for 2.1s - treat as unobserved
     [critical] policy_breach              moving with a person 0.55 m away (minimum 1 m)
     [medium  ] narration_divergence       walk_distance completed with no measured motion
     [high    ] prediction_deviation       outcome deviates 9.4 sigma from the predicted end state
     [medium  ] narration_divergence       guide_to was accepted 6s ago and has not measurably moved
     [critical] denied_command_executed    walk_distance was denied and was sent anyway
     [high    ] gate_bypass                walk_distance reached the robot with no gate decision
     [critical] unattributed_motion        robot moving at 0.31 m/s with no authorised intent
```

Run one in isolation with `--only <n>`.

---

## 4. Evidence measured on the real robot

| Measurement | Value | When |
|---|---|---|
| Steady walk speed | **0.1373 m/s** | 2026-11-15, 5 walks, battery 49 % |
| Fixed overhead per walk | **4.11 s** (ramp-up + settle) | same |
| Systematic undershoot | **4.1 cm ± 0.7 cm**, at every distance | same |
| Duration residual σ | 0.48 s | same |
| **Validation walk** | predicted 11.39 s / 95.9 cm → actual 11.58 s / 95.3 cm = **0.13σ** | 2026-11-16 |
| Cosmos judge, warm frame | **4.22 s**, clear, allowed | 2026-11-16 |
| Cosmos skipped on a gesture | 0.067 s (0 m translation) | 2026-11-16 |
| Cosmos denial honoured | `stop=true conf=0.97` → **gate DENIED, robot never received it** | 2026-11-16 |
| RTSP over Ethernet | **15 fps, 0 read failures** | 2026-11-15 |
| RTSP over Wi-Fi | dropped frames, ~1.35 s latency | earlier |
| `/api/state` poll on the Orin | 4–12 s → **30 ms** after three fixes | 2026-11-16 |
| Chrome ICE gathering on a tailnet | **39.9 s with STUN vs 133 ms without** | — |
| `/connected` aliasing | 19.98 Hz in a `false,false,true` pattern — 218 F to 76 T over 10 s | — |
| Gestures verified against `gestures_available` | **10 of 10 match exactly** | — |
| `stand_up`, `lie_down`, `nod`, `greet` through the console proxy | confirmed by `gesture_active` / `ctrl_mode` afterwards | — |
| Dashboard workflow (robot lying) | start, map, save, load, goal → plan, cancel, clear | 2026-11-14 |
| Monocular RTAB-Map loop closures | **0 accepted, all variants** | 2026-11-14 |

Raw calibration samples: `clawmetry/baseline.json` and
`clawmetry/calib_samples_d1w.json`.

---

## 5. The credential scanner

`scripts/audit_secrets.py` — repo-wide, no network, no dependencies.

```bash
python3 scripts/audit_secrets.py            # every tracked file
python3 scripts/audit_secrets.py --staged   # only what is about to be committed
make install-hooks                          # as a pre-commit hook, opt-in per clone
```

Three statements from its own docstring are worth quoting, because they are
the difference between a scanner and a compliance prop:

> **Why this exists.** Every real credential in this repo is read from the
> environment, and the rule in AGENTS.md says to keep it that way. **A rule is
> not a control**: the only thing that has ever kept a token out of a commit
> here is someone remembering. This makes it mechanical.

> **What it never does: print a matched value.** A scanner that echoes the
> secret it found moves the secret into CI logs, scrollback and bug reports. It
> prints file, line and rule name only.

> **What it cannot do: prove the absence of a secret.** A 32-char token with
> no recognisable shape, pasted into a variable named `x`, passes. **This
> raises the floor; it does not replace reading the diff.**

The hook is **opt-in per clone**, because git hooks are not versioned and
cannot be imposed by a merge — and `--no-verify` is left working deliberately:
*the hook is there to catch an accident, not to win an argument with the
person typing.*

Current result: **348 files scanned, 0 findings, 7 documented exceptions.**

---

## 6. Bringing the whole demo up with no hardware

```bash
bash tools/demo-up.sh
```

Starts the simulator, tunnels it, repoints the Vapi assistant at it, stands the
simulated robot up, starts both consoles, tunnels the public one, and prints
the phone URL. Nothing in it touches the robot.

> **Why this exists:** every piece of this has broken at least once in a way
> that produced **no error anywhere** — a `trycloudflare` hostname that stopped
> routing while its process stayed alive, a console restart that dropped
> `VAPI_ASSISTANT_ID` and silently downgraded the greeter into a robot remote
> that shrugged at spa questions. Each was a few minutes to find and a second
> to fix. Holding the whole arrangement in one script is cheaper than
> rediscovering it.

Two details in that script worth stealing:

- **A console key is generated if not supplied**, because the public console
  binds `0.0.0.0` and drives a robot with no login of its own — *so there is no
  way to end up with an unprotected public console by forgetting a variable.*
- **It waits for DNS, not for the hostname to be printed.** cloudflared prints
  the name a couple of seconds before the record exists, and on macOS one early
  lookup gets the NXDOMAIN cached by the system resolver — after which curl
  fails with "could not resolve host" for minutes while `dig` answers
  perfectly. *That looked exactly like a dead tunnel and was self-inflicted.*

### Or piece by piece

```bash
# the governed stack
node tools/robot-sim/server.mjs &
python3 clawmetry/server.py --upstream http://127.0.0.1:5175 \
        --venue clawmetry/venue/leukerbad_therme.yaml \
        --compliance clawmetry/compliance.yaml --port 8901 &
python3 clawmetry/scenarios.py --url http://127.0.0.1:8901
open http://127.0.0.1:8901

# the guest-facing half
PORT=5180 PUBLIC_URL=http://127.0.0.1:5180 node tools/robot-face/server.mjs
#   /face?demo=1  ·  /join  ·  /debug

# the operator console, against the simulator
ROBOT_URL=http://127.0.0.1:5175 D1_ROBOT_TOKEN=sim PORT=5174 \
  node tools/robot-console/server.mjs

# the gateway protocol, no robot and no ROS
ros2 launch d1_gateway gateway.launch.py mock:=true
python3 -m websockets ws://127.0.0.1:8765

# fake odometry for localization work
ros2 launch d1_localization localize.launch.py x:=1.0 y:=2.0 yaw:=0.5
python3 tools/fake_d1_odom.py
```

---

## 7. Against the real robot

```bash
# the control plane, live on the Orin
bash clawmetry/live.sh status      # health, no browser
bash clawmetry/live.sh open        # SSH tunnel + dashboard
bash clawmetry/live.sh push        # rsync and run the tests on the Orin

# the full ROS stack
docker/d1-jetson/run.sh
source /opt/ros/humble/setup.bash && source /ws/ros2_ws/install/setup.bash
ros2 launch d1_dashboard dashboard.launch.py \
  robot_ip:=192.168.168.168 local_ip:=192.168.168.10 external_robot:=true
```

Safety preconditions before any of it
([AGENTS.md](../../AGENTS.md), [01](01-system-and-hardware.md) §7):

1. **One SDK client.** Never start a second bridge; use `external_robot:=true`.
2. **One `/cmd_vel` writer at a time.** No arbitration exists. Do not run a
   voice walk while Nav2 is navigating.
3. **Do not raise speed limits** without an explicit request.
4. **Never `tailscale funnel 8443`.**
5. **Never commit images, rosbags, MCAP recordings or audio of real people.**

---

## 8. What is not covered by any test

Stated so a reviewer does not have to infer it:

- **No test exercises the child detector against a child.** The policy is
  tested; the sensing is not. ([07](07-child-safety.md) §8)
- **No test runs against the physical robot in CI.** There is no CI.
- **No end-to-end test of the governed live path**, because that path depends
  on which port the Tailscale funnel points at
  ([09](09-clawmetry-control-plane.md) §9).
- **No load or soak test.** NFR-5 asks for 30 minutes unattended; that has not
  been measured.
- **No test of the ROS 2 DDS surface**, which is the largest security hole
  ([08](08-adversarial-agents.md) §11).
- **`human_perception` and `d1_gateway` suites were not run while writing this
  document** (missing ROS and `websockets` on this machine), so their counts
  above come from reading the files, not from executing them.

---

[← 13 Privacy and compliance](13-privacy-and-compliance.md) · [index](../README.md) · next: [15 Gaps and roadmap](15-gaps-and-roadmap.md)
