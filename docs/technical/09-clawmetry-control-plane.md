# 09 · ClawMetry: the physical-AI control plane

[← 08 Adversarial agents](08-adversarial-agents.md) · [index](../README.md) · next: [10 Mapping and LiDAR](10-mapping-lidar-spike.md)

Source: `clawmetry/` — `server.py`, `ledger.py`, `correlate.py`, `predict.py`,
`venue.py`, `policy.py`, `compliance.py`, `frame.py`, `dashboard.html`,
`scenarios.py`. Author's own write-up: [clawmetry/README.md](../../clawmetry/README.md).

**One process. Zero dependencies. Pure Python standard library.** It has to
start on a venue laptop, a Jetson or a Mac with no install step and no network
— the same reason `tools/robot-console` has zero npm dependencies.

---

## 1. The idea in one paragraph

A robot stack is assembled from parts that do not emit anything comparable: a
voice cloud that reports on itself, an LLM that narrates what it believes it
did, a Node edge, a Python agent, a ROS bridge, and a robot with its own
firmware. Each can be logged. **Logging says what each component reported.
Oversight says whether those reports agree with each other and with the
robot's measured behaviour.** ClawMetry is the second thing: a tap at the
tool-call boundary that records intention, predicts the physical consequence,
decides, forwards, then scores what actually happened against what was
predicted — and raises a breach whenever two sources that should agree do not.

---

## 2. Why a tap, and not an SDK client

The D1 permits **one SDK client**, which the team bridge already holds.
Anything that opens a second one either fails or breaks the stack it is meant
to watch.

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

> Everything upstream of the tap is a **claim**; everything downstream is
> **ground truth**. Oversight is the business of correlating the two, which is
> why the breach set is mostly about disagreement between sources rather than
> any single threshold.

### It forwards even when it cannot gate

> An observer that breaks the robot when it fails is an observer nobody
> installs twice.

The single exception is an explicit deny, which is the whole point of the gate.

---

## 3. Four things in one process

| Component | What |
|---|---|
| **The tap** | `POST /vapi` is a drop-in replacement for the robot's tool endpoint, plus the whole rest of the edge's HTTP surface ([08](08-adversarial-agents.md) §5) |
| **The voice sink** | `POST /vapi/events` takes Vapi's own server messages, so *who asked* is the guest's recorded words rather than an inference drawn from a tool call ([03](03-voice-vapi.md) §7) |
| **The collectors** | background pollers for the bridge (confirmed state, 2 Hz) and the camera guard (people, breaches), plus `POST /ingest` for anything else — ROS, approvals, a second robot |
| **The ledger** | hash-chained, append-only, fsynced |
| **The dashboard** | `GET /` — one page, live over SSE ([12](12-dashboards.md)) |

*At a venue, four processes is three too many.*

---

## 4. The event model

Eleven event kinds, **named after the pitch's own Layered Authority Stack so
the ledger and the deck use one vocabulary**:

| kind | means |
|---|---|
| `utterance` | **who asked** — guest or staff speech |
| `intent` | **what was intended** — the agent's tool call, before anything moves |
| `prediction` | what we expect to happen, recorded **before** the action |
| `decision` | the gate's answer: allow / deny / hold |
| `intervention` | **who intervened** — a human tapped Approve / Deny / Pause |
| `command` | what was actually sent to the robot |
| `state` | confirmed physical state sample from the bridge |
| `outcome` | **what it did** — final state, scored against the prediction |
| `breach` | a rule was violated |
| `integrity` | the ledger's own health: gaps, silence, restarts |
| `policy` | **the rules themselves changed** — a guardrail was edited or signed |

`policy` is deliberately first class:

> An incident review has to be able to ask what the limits said at the time,
> and a config change that is not in the record is indistinguishable from one
> that never happened.

Nine actors, split finer than most systems bother with:

```
guest · staff · operator · agent · assistant · gate · robot · system · vapi
```

> `assistant` is the voice agent **speaking**, as distinct from `agent`
> **deciding** to call a tool, and `vapi` is the platform reporting on itself —
> three different authorities that a single "agent" would have blurred.
> `operator` is the person accountable for guest safety who signs the venue
> file — a different authority from `staff` working alongside the robot, and
> the only one whose name belongs on a policy change.

---

## 5. The two prediction models, and why there are two

| | **kinematic** | **Cosmos-Reason2** |
|---|---|---|
| when | every action, inside the gate | behind the gate, best effort |
| latency | ~0 ms | seconds, over Tailscale to a remote Spark |
| answers | where this command puts the robot | is that a child, is the floor wet, is the doorway passable |
| failure | n/a | `available: False` — **never an approval** |

> A judge on a remote box cannot sit in the path of a stop: at guide pace the
> robot covers ~12 cm per 250 ms. So the kinematic model holds the gate and
> Cosmos escalates behind it.

When Cosmos is unreachable the run is recorded as *kinematically gated,
unjudged*, which is a weaker claim and reads as one.

### Only translating actions are judged

A gesture is 0 m of translation, and the judge's question — *"is the path
ahead clear"* — simply does not apply to it. Asking anyway produced this, on
real hardware:

> Cosmos denied a **nod** because *"people are in the path ahead, restricting
> forward movement"* — true of walking, irrelevant to a gesture with four feet
> planted. In a room with eight people that denies every gesture the greeter
> has, and cost 8 s doing so.

```python
translates = pred is not None and pred.distance_m > 0.01
```

A skipped judge emits a `prediction` event saying so, rather than silently not
appearing.

### Measured, on the real robot, at the venue

Calibration, five bounded walks on the D1W (`clawmetry/baseline.json`):

| measured | value |
|---|---|
| steady walk speed | **0.1373 m/s** |
| fixed overhead per walk | **4.11 s** (ramp-up + settle) |
| systematic undershoot | **4.1 cm ± 0.7 cm**, at every distance |
| duration residual σ | 0.48 s |
| **validation walk** | predicted 11.39 s / 95.9 cm · actual 11.58 s / 95.3 cm → **0.13 σ** |

Duration is `overhead + d/v`, **not** `d/v`: a legged gait spends its first
strides ramping and then waits out the settle while it coasts.

> **Our ignorance is not the robot's anomaly.** The first run of this collector
> reported **46.7 σ** on a walk that went exactly as intended, because nobody
> had measured the walk speed and the predictor guessed 0.041 m/s — wrong by
> 3.4×.
>
> Duration is now scored only against a **calibrated** body; position only when
> a pose feed actually exists. Where neither holds, the verdict is
> `unverified_action` — visible, and not confused with either success or
> anomaly. `agibot_d1w` and `unitree_g1` ship `calibrated: False`; measure
> before trusting a sigma.

Cosmos through the brain relay, live:

```
walk_distance 100 cm  -> judged in 4.22 s on a warm frame, clear, allowed
do_gesture nod        -> judge skipped (0 m translation), 0.067 s
earlier, same frame   -> stop=true conf=0.97 "people are in the path ahead"
                         -> gate DENIED, the robot never received the command
```

### The deviation score

`max(position_error / pos_σ, |duration_error| / dur_σ)`, defaults 0.10 m and
15 % of predicted duration floored at 0.5 s. The formula is **deliberately
copied from the edge's own `telemetry/otel.py`** so a sigma means the same
thing in both dashboards —

> Two governance systems that disagree about what counts as an anomaly are
> worse than one.

`deviation_z` is `null`, not `0`, when nothing was comparable.

---

## 6. The operator's guardrails

`clawmetry/venue/leukerbad_therme.yaml` is **the spa's document**, written to
be read and signed by whoever is responsible for guest safety. It is the one
place that says where the robot may go, how fast, and what happens near
children.

```bash
python3 clawmetry/server.py --venue clawmetry/venue/leukerbad_therme.yaml ...
```

Three rules are **enforced in `venue.py`**, not merely stated in the file:

| rule | in practice |
|---|---|
| **Config can only tighten** | Every cap is clamped to the hard ceiling. A file asking for 2 m/s next to guests gets 0.4, and the discrepancy is **reported** rather than silently honoured |
| **Anything unset is forbidden** | A point inside no described zone resolves to `unknown`: restricted, camera-off, approval required. Forgetting to describe the corridor does not authorise the corridor |
| **Unsigned means cautious** | With `signed_off_by` empty the venue runs at the unverified speed and **refuses to guide at all**. Nobody has agreed to the numbers yet, so they are proposals |

Plus: a zone marked `verified: plan` came off a floor plan and has not been
measured on site, so it runs at `unverified_zone_speed` whatever its own cap
says. **All nine zones are currently `plan`.**

### The nine zones

| zone | cap | effective | restricted | camera off |
|---|---|---|---|---|
| `reception` | 0.3 | 0.15 | | |
| `concourse` | 0.3 | 0.15 | | |
| `shop_cafe` | 0.2 | 0.15 | | |
| `bath_entrance` | 0.2 | 0.15 | | |
| `pool_deck` | 0.15 | 0.15 | ✅ | ✅ |
| `sauna_world` | 0.15 | 0.15 | ✅ | ✅ |
| `relax_level` | 0.15 | 0.15 | ✅ | ✅ |
| `stairs` | 0.15 | 0.15 | ✅ | |
| `technik` | 0.15 | 0.15 | ✅ | |

`camera_off` is not "blur it later" — **no frame is captured there at all, not
even for the safety judge.** The complex forbids photography everywhere;
behind the turnstiles that is absolute.

Each restricted zone carries a `reason` the robot quotes back when it refuses:

```
Refused: path enters Pool deck, behind the turnstiles: Wet floor and guest
privacy. The robot has no ingress rating and no wet-floor traction data.
```

> A refusal quotes the venue, not the code. The person hearing it is a guest,
> and the person auditing it is the spa.

The `stairs` reason is the one worth reading twice: *"A wheeled-leg quadruped
must never approach a stairwell in a crowd. Nothing in the stack detects a
step, a handrail or the top of a flight."*

### Editing it without SSH

Full treatment in [07](07-child-safety.md) §6 and [12](12-dashboards.md).
Summary:

- `GET /api/venue` — zones, limits, people, warnings, and the editable schema
- `POST /api/venue/plan` — dry run: the **classified** diff, writes nothing
- `POST /api/venue/apply` — writes, records, reloads, **in that order**

The record goes on the ledger as a `policy` event with the operator's name, the
reason, the full before/after of every field and a `loosened` flag, **before**
the reload — so an edit that breaks the collector is still on the record. The
previous file is kept beside it. The reload is live: `limits.max_speed` reaches
the correlator without a restart.

Two engineering details that are easy to get wrong and were got right:

- **The document survives the edit.** The venue file is mostly prose.
  Round-tripping it through a YAML dumper would delete all of it and leave a
  machine-readable file no human would ever sign. So edits are *surgical text
  replacements of single scalar values*, and every byte not deliberately
  changed is byte-identical afterwards — including trailing comments, which are
  kept **and re-aligned**.
- **`_fmt()` asks YAML whether a bare scalar round-trips**, because a regex has
  to predict which bare scalars get retyped, and the case that caught it was
  `signed_off_date: 2026-11-16`, which parses as a `datetime.date` — not a
  string, not JSON-serialisable, and it broke the response **after** the file
  had already been written.

---

## 7. What it detects

| rule | the question it answers | severity | needs |
|---|---|---|---|
| `unattributed_motion` | the robot moved and no authorised intent explains it | critical | state + intents |
| `denied_command_executed` | something denied was sent anyway | critical | gate + commands |
| `child_proximity` | moving with a child inside the freeze radius | critical | guard |
| `policy_breach` | confirmed speed or person distance violated a cap | high / critical | state |
| `gate_bypass` | a command reached the robot the gate never adjudicated | high | commands |
| `predicted_breach` | this action **will** leave the go-zone, before it runs | high | zones |
| `prediction_deviation` | it moved, but not to where the model said | high | pose feed |
| `silence` | a feed stopped reporting | high | heartbeats |
| `source_never_reported` | a declared feed never started | high | — |
| `narration_divergence` | the agent told a guest it was moving; it was not | medium | state + intents |
| `unverified_action` | it completed and could not be checked at all | medium | — |
| `command_unattributed_to_speech` | a tool call with no sentence behind it | low | voice sink |

`unattributed_motion` is the flagship: **one check that catches a compromised
agent, a stuck teleop key, a rogue publisher and a plain bug.** It needs
`twist_mux` to reach full strength — with four unranked `/cmd_vel` writers
nothing can attribute motion to a source ([01](01-system-and-hardware.md) §7).

The correlator is **pure stdlib, no I/O, no clock of its own** — `now` is
always passed in. That is what lets the whole detector set be tested on a
laptop while the robot is flat, which is most of the time.

### Breach rule names are frozen on purpose

```python
# renamed itself and no dashboard filter on "policy_breach" matched it.
```

A detector that renames its own rule silently disappears from every filter
downstream.

---

## 8. Evidence: the hash chain

`ledger.jsonl` is append-only and sha256-chained: each line carries the hash of
the one before it, over a **deterministic canonical encoding** (sorted keys, no
spacing drift). Editing, deleting or reordering any line breaks the chain from
that point, and `GET /api/verify` names the first break.

```
chain BROKEN at 235
```

**Ingest time is the primary timestamp**, with the source's claimed time kept
as `src_ts` and the difference as `skew_s`, because events arrive from a cloud,
a Jetson, a robot and a phone whose clocks disagree — the MetaCam in this
project ships with a clock that is simply wrong.

### What it does not prove

> The chain detects tampering; it is **not yet signed**, so it proves internal
> consistency, not origin.

---

## 9. Live wiring, and the switch that decides whether oversight is real

On the Orin the tap binds `127.0.0.1:8901` and the only public door is the
Tailscale funnel in front of it. **One switch:**

```bash
tailscale funnel --bg 8901      # 443/ -> the tap    (GOVERNED)
tailscale funnel --bg 8090      # 443/ -> the robot  (BYPASSED)
```

> Until that funnel points at 8901, Vapi talks straight to `d1-vapi-server` and
> **the ledger sees nothing** — the tap is running and idle, which looks
> identical to a quiet robot. `GET /api/state` reporting a `vapi_tap` source
> age of hours is the tell.

This is stated at the top of `clawmetry/README.md` and in
[AGENTS.md](../../AGENTS.md) rather than buried, because it is the difference
between a governed demo and a demo with a dashboard next to it.

### When the funnel cannot be repointed: the edge tee

If the switch is not available — someone else owns the tunnel, or it is not a
change you want mid-demo — `d1-vapi-server` can instead send the collector a
**copy** of everything over loopback. No second public door.

```
Vapi ──► funnel 443 ──► d1-vapi-server :8090 ──► ROS ──► robot
                              │ copy (fire and forget, loopback)
                              ▼
                        the tap :8901   POST /observe/tool
```

`deploy/jetson/d1-vapi-server-clawmetry-tee.patch` is the edge-side change: a
`tee()` that never blocks, never retries and swallows its own errors, because
**oversight must not be able to delay or break the robot answering a guest.**
`CLAWMETRY_TEE=` turns it off.

**What this costs, stated plainly: the tap cannot refuse anything on this
path.** So every decision recorded this way carries `enforced: false`, and a
gate verdict of *deny* becomes an `integrity` event named
`gate_could_not_enforce` rather than a refusal:

> A ledger claiming it "allowed" a command it could not have stopped would be
> claiming an authority it did not have, and that is the first claim an auditor
> should test.

The attribution still works, which is the point:

```
utterance  guest   "can you nod at me please"
intent     agent   do_gesture   asked="can you nod at me please"
decision   gate    allow        enforced=false
command / outcome
```

---

## 10. Four bugs that only hardware found

Each produced a confident, wrong answer; none would have surfaced in
simulation. Three are detailed in [08](08-adversarial-agents.md) §4. The fourth
is worth its own note because it is the most ordinary:

> **A silently unauthenticated collector.** The state poll sent no bearer
> token, got 401 for a whole session, produced zero samples, and the correlator
> then called a perfect 95 cm walk a lie — twice.
>
> `:8090` returns 401 on `GET /status` **even with the right token**, because
> its auth runs before routing. State comes from `:8091`, which needs none.

The same quirk bit `tools/robot-face` ([04](04-guest-journey-qr.md) §8). It is
now documented in three places in the repository, which is roughly the right
number for a trap that costs a session each time.

---

## 11. Performance: the 1 Hz poll that cost the robot four seconds of CPU

`/api/state` is polled once a second by the dashboard. It was taking **4 to 12
seconds** on the Orin, so the header never filled in at all, and the robot
spent more CPU on being watched than on anything else.

Each poll walked the whole ledger five times: `verify()` re-hashed it,
`_counts()` re-parsed it, and the compliance pack was built twice.

> At 10 MB that is not a poll, it is a denial of service with a nice UI.

Three fixes, now **30 ms**:

- the ledger **counts kinds as it writes them** (three code paths append to it,
  and a counter kept outside the ledger missed one);
- the compliance pack is built at most every 30 s;
- `verify(max_age_s=20)` may return a cached verdict — **and the answer carries
  `checked_age_s`, which the header displays**, because *"the chain was intact
  7 seconds ago"* is a weaker claim than *"the chain is intact"* and must not
  be shown as the stronger one.

`/api/verify` and anything producing evidence pass nothing and always get the
full walk over the bytes on disk.

---

## 12. HTTP API

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/` | the dashboard (hands out a cookie if `?k=` was used) |
| `GET` | `/api/state` | the live snapshot: robot, chain, sources, breaches |
| `GET` | `/api/ledger?limit=&skip=state` | raw events; `skip=state` is the difference between a trace and a heartbeat |
| `GET` | `/api/verify` | full chain walk, names the first break |
| `GET` | `/api/compliance` | the evidence pack and the gap list |
| `GET` | `/api/venue` | zones, limits, people, warnings, editable schema |
| `GET` | `/api/events` | SSE stream |
| `GET` | `/health` `/status` `/call` | forwarded to the robot verbatim |
| `POST` | `/vapi` | **the tap** |
| `POST` | `/move` `/gait` `/gesture` `/command` `/stop` | the robot's REST surface, governed |
| `POST` | `/vapi/events` | the voice sink |
| `POST` | `/ingest` | any other collector |
| `POST` | `/observe/tool` | the edge tee (loopback only) |
| `POST` | `/api/venue/plan` / `/api/venue/apply` | guardrail editing |
| `POST` | `/intervention` | **who intervened** — a human tapped Approve / Deny / Pause |
| *anything else* | | **404, not tunnelled** |

### CLI

```bash
python3 clawmetry/server.py \
  --upstream http://127.0.0.1:8090       # the robot tool endpoint to forward to
  --state-url http://127.0.0.1:8091      # where confirmed state is polled
  --body agibot_d1w                      # device profile (also: unitree_g1, fake)
  --venue clawmetry/venue/leukerbad_therme.yaml
  --compliance clawmetry/compliance.yaml
  --guard http://127.0.0.1:8092          # the camera people-guard
  --judge --env-file ~/.clawmetry.env    # enable Cosmos; keys via file, not argv
  --inbound-token "$TOKEN"               # required whenever reachable off-host
  --redact-transcripts                   # store a length + sha256 prefix, not words
  --ledger /var/lib/clawmetry/ledger.jsonl
  --port 8901
```

Starting it prints the venue, the zone count, and **`NOT SIGNED OFF`** when
nobody has signed, plus every clamp warning. Binding a non-loopback host
without an inbound token prints:

```
! inbound auth=OFF and bound to 0.0.0.0: anyone who can reach this port
  can read guest speech and refuse robot commands
```

A port collision is a hard error, not a fallback:

```
cannot bind 127.0.0.1:8901 (Address already in use). Another collector is
probably still running - it would keep serving while this one wrote a ledger
nobody reads.
```

---

## 13. Reproducing every detector, with no robot

```bash
node tools/robot-sim/server.mjs &
python3 clawmetry/server.py --upstream http://127.0.0.1:5175 \
        --zones clawmetry/zones.example.json --port 8901 &
python3 clawmetry/scenarios.py --url http://127.0.0.1:8901
```

Ten scenarios, one per detector. **They drive real tool calls through the real
tap and the real `/ingest` door — nothing here is a test backdoor into the
server.**

Output from an actual run on 2026-11-16 (full transcript in
[14](14-testing-and-evidence.md)):

```
  chain    : chain intact (117 events)
  breaches : 11
     [critical] unattributed_motion     robot moving at 0.31 m/s with no authorised intent
     [critical] denied_command_executed walk_distance was denied and was sent anyway
     [critical] policy_breach           moving with a person 0.55 m away (minimum 1 m)
     [high    ] gate_bypass             walk_distance reached the robot with no gate decision
     [high    ] prediction_deviation    outcome deviates 9.4 sigma from the predicted end state
     [high    ] silence                 no event from guard for 2.1s - treat as unobserved
     [medium  ] narration_divergence    guide_to was accepted 6s ago and has not measurably moved
```

---

## 14. Three rules this thing follows, learned the hard way

**Silence is a finding, not a gap.** This project has twice shipped a system
whose failure looked like calm — the endpoint answering `200` with nulls while
the robot lay on the floor, and the camera guard's *no fresh frame → FREEZE*. A
missing feed emits a `silence` breach; it never renders as an empty chart.

**Our ignorance is not the robot's anomaly.** 46.7 σ on a perfect walk, because
the predictor guessed. Measure the body, or say `unverified`.

**The observer never actuates.** No endpoint here commands the robot. It passes
calls through or refuses them, and it cannot originate motion. Stop authority
stays with the local governor.

---

## 15. Not done

- **A restart forgets that speech is live.** `_replay()` rebuilds the breach
  feed and the robot view from the ledger but does not re-feed the correlator,
  so `command_unattributed_to_speech` stays silent until the first transcript
  of the new run.
- **Synchronous judging.** Cosmos currently gates in line, so a walk waits ~4 s
  before it starts. Defensible as a pre-flight check, and it contradicts the
  "never in the path of a stop" rule above. The right end state is async: the
  kinematic gate passes immediately and Cosmos escalates to a stop.
- **Position scoring on hardware.** The ros-agent holds `/odom` internally but
  does not expose it in `/status`, so only duration is scored. Three lines in
  `~/vapi-robot/d1_ros_agent.py` would unlock it.
- **OTLP export.** `CLAWMETRY_DASHBOARD_URL` is still an unfilled placeholder
  and `CLAWMETRY_OTLP` points at `127.0.0.1:4318`, where nothing listens on the
  Orin. The exporter **degrades to a silent no-op when wrong**, so this needs a
  real URL before it can be trusted.
- **Signing.** See §8.
- **Reconciliation with `clawmetry_safety_layer`** (the earlier PR). That
  layer's `/events` vocabulary and `deviation_z` formula were deliberately
  reused here so the two converge rather than fork; they are still two
  processes.

---

[← 08 Adversarial agents](08-adversarial-agents.md) · [index](../README.md) · next: [10 Mapping and LiDAR](10-mapping-lidar-spike.md)
