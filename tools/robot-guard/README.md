# Camera people-guard

> ## Not yet reconciled with `d1_follow` / `human_perception`
>
> This package was built in parallel with the perception stack in
> `ros2_ws/src/human_perception` and `ros2_ws/src/d1_follow`, and the two have
> not been merged. Before anyone deploys this, three real conflicts need a
> decision:
>
> **1. Two detectors.** This runs its own YOLO on the RTSP stream;
> `human_perception` publishes `/perception/humans/detections` from the same
> camera. Running both is wasteful and, on a Jetson with no torch installed,
> currently impossible. The right shape is for this guard to **consume those
> detections** instead of inferring its own.
>
> **2. `perception.md` says inference never runs on the robot** - it runs on a
> laptop or companion machine. That is a reasonable call (the Jetson has no
> torch and no CUDA wheels set up), but it means a guard hosted off-board
> inherits the wifi hop, and a stop that arrives a second late is close to
> useless. Either inference moves onto the Jetson (opencv-dnn with an ONNX
> model avoids torch entirely; OpenCV 4.8 is already there), or the override
> loop has to live wherever the inference does.
>
> **3. `perception.md` explicitly says "do not use it to gate robot motion or
> any safety function" - and gating motion is exactly what this does.** The
> positions are not as far apart as they look: this README says the same thing
> about not being a safety system, and argues a best-effort gate still reduces
> how often a handler has to be quick. But it is a documented project rule and
> crossing it is the owner's decision to make, not this package's.
>
> Also overlapping, though less directly: `d1_follow` makes the robot **follow a
> guest**, with proper OSNet re-ID. `escort_core.py` here does the opposite -
> the robot **leads** and checks the guest is still behind. Different
> behaviours, but the identity machinery is duplicated, and theirs is better.
> This one should borrow their embedder rather than keep a colour histogram.


Stops the greeter walking into guests, and stops it offering a paw to a
toddler, using the camera the robot already has.

## What the robot actually has

From the vendor spec sheet (§1.3) and confirmed against the SDK headers:

| | |
|---|---|
| camera | **one** wide-angle monocular — DFOV 122°, HFOV 122°, VFOV 70° |
| IMU | standard |

That is the whole sensor table. No stereo, no depth camera, no LiDAR, no
ultrasonic, no ToF. `highlevel.h` exposes motion, joints, attitude and battery
— there is no obstacle API to call. The docs contain no mention of avoidance,
following, mapping or navigation (`避障`, `跟随`, `建图`, `导航`: zero hits).
Autonomy is not a feature being switched on here. It is a feature being added.

The feed is H.264 over RTSP (vendor §5.1):

```
rtsp://192.168.168.168:8554/test    # wired
rtsp://192.168.234.1:8554/test      # wifi
```

## How distance works without a depth sensor

People stand on the floor, and the floor is a known plane. Given camera height
and tilt, the pixel row of someone's **feet** fixes their range. Accuracy where
it matters:

| range | resolution |
|---|---|
| 1 m | 0.6 cm / px |
| 2 m | 2.0 cm / px |
| 3 m | 4.5 cm / px |
| 4 m | 7.9 cm / px |
| 8 m | 31 cm / px |

Centimetres inside the safety envelope, useless at the far end — which is the
right shape of error. The question is "is someone within two metres", never "is
that person 7.3 or 7.8 metres away".

Detection range is not the constraint. A 0.85 m toddler is ~73 px tall at 6 m,
well above any detector's floor.

## The blind spot, which is the whole problem

A camera 0.40 m up with VFOV 70° cannot see the ground nearer than:

| tilt down | blind inside |
|---|---|
| 0° | 0.57 m |
| 10° | 0.40 m |
| 15° | 0.34 m |
| 30° | 0.19 m |

**A two-year-old who toddles up to touch the robot crosses that line and
vanishes from the frame.** The naive implementation reads the empty frame as
"all clear" and resumes walking — into the child it was looking at a moment
ago. Disappearance at close range does not mean absence. It means *closer*.

So the guard **latches**: a person lost at close range is presumed still there,
in the blind spot, for several seconds. A small person is latched longer (5 s)
and harder (full freeze). Confidence decays towards danger, not convenience.

The same rule covers a dead feed. This stack already shipped that bug once —
the public endpoint answered `200` with nulls while the robot lay on the floor,
and the voice agent reported it as "stationary". **No fresh frame → FREEZE.**

## States

| state | walking | gestures |
|---|---|---|
| `GO` | full speed | all |
| `SLOW` | capped 0.25 m/s | all |
| `HOLD` | none | four-feet-down only (nod, tilt, sit) |
| `FREEZE` | none | none |

Triggers, taking the worst person ahead (±45°):

- beyond 4 m → `GO`
- 2–4 m → `SLOW`
- 0.9–2 m → `HOLD`
- inside 0.9 m, or feet clipped by the frame bottom → `FREEZE`
- **a child (< 1.25 m tall) within 2 m → `FREEZE`**, no paw, no wave
- closing fast enough to arrive within 2 s → escalates early
- no frame for 0.5 s → `FREEZE`

`HOLD` keeps soft gestures deliberately. A robot that goes completely inert the
moment someone approaches is a worse greeter than one that nods at them.

What gets banned is decided by one thing only: **does a foot leave the
ground.** Checked against the real library in
`agibot_d1_bridge/d1w_gestures.py`, not guessed.

All eight `GESTURES` are net-zero `attitudeControl` profiles with the **feet
planted** — `nod`, `shake`, `tilt`, `bow`, `squat`, `wiggle`, `look_around`,
`sway_circle`. Even `bow` (20° pitch, body down 6 cm) and `squat` (down 10 cm)
are slow attitude changes on four feet, so all eight stay available while
someone is standing close. Note `shake` is a 14° **yaw** — the head shaking
"no", not a handshake. An earlier version of this file banned it on that
misreading and cost the greeter a harmless gesture.

The two that are banned are the two that sound friendliest:

```python
"greet":   [("gesture", "bow"), ("sdk", "shakeHand", 4.0)]
"welcome": same as greet
```

`shakeHand()` **raises a paw to roughly the head height of a two-year-old** —
precisely the scenario this exists for. An earlier version of the gate allowed
anything not on its banned list, which let both of these straight through.

So the gate **fails closed**: in `HOLD`, only names known to keep four feet
down are allowed. A gesture nobody has classified is a gesture nobody has
checked, and the cost of refusing it is one missed wag.

## Two layers, and why the second is the real one

`walk_distance` **blocks on the robot for up to 25 s**. By the time a guest
steps in front of the dog, the command was accepted long ago and nothing is
asking permission any more. Refusing the *next* command does nothing about the
one already running.

1. **Gate** (`GUARD_URL` in the console) — refuses to *start* something already
   unsafe. Cheap, but only covers new commands.
2. **Override** (this service) — sends `stop_robot` every tick while unsafe,
   interrupting what is in flight. This is the one that matters.

The override races the walk rather than gating it, because the bridge and
`d1-vapi-server` are not in this repo. If this graduates past the demo, the
gate belongs inside the agent, before `cmd_vel` is published.

`stop_robot` and `robot_status` are never gated.

## Calibrate first

Everything rests on camera height and tilt. Wrong values are not noisy, they
are **confidently wrong by a constant factor** — 1.2 m reported as 2 m, all
day, without a flicker of doubt. Until `--calibrated` is passed the guard caps
the robot to slow.

```bash
# robot standing on flat floor; tape on the ground straight ahead at 1.0 m and 3.0 m
python3 calibrate.py grab --rtsp rtsp://192.168.168.168:8554/test
# read the pixel row of each mark out of frame.png
python3 calibrate.py solve --near 1.0 449 --far 3.0 320
```

Then verify against a **third** mark you did not fit to, and check the guard's
`nearest_m` agrees to a few centimetres.

## Run

```bash
pip install ultralytics          # opencv ships with JetPack
D1_ROBOT_TOKEN=... python3 guard.py \
    --rtsp rtsp://192.168.168.168:8554/test \
    --robot http://127.0.0.1:8090 \
    --cam-height 0.40 --cam-tilt 12.0 --calibrated
```

As a unit: `deploy/jetson/d1-guard.service` (token goes in
`/etc/d1-guard.env`, mode 0600 — not in the unit file, which is
world-readable).

Then point the console at it:

```bash
GUARD_URL=http://127.0.0.1:8092 node tools/robot-console/server.mjs
```

The console grows a strip under the command bar showing state, nearest person,
and whether a reading is latched rather than live.

Useful flags: `--dry-run` decides and logs but never sends stop.
`--allow-blind` reports `GO` when the camera or detector is missing, so a bad
install cannot brick a demo — **it leaves the robot unguarded and should be off
whenever the public is present.**

## Looking around, and leading people

Two more pieces, both built on the same awkward fact: **the camera is bolted to
the body.** There is no pan-tilt head — the spec lists no head servo and the
SDK has no pan call — so "look left" means "turn the robot left".

| | |
|---|---|
| `attitudeControl` | twist in place, yaw ±0.5 rad/s. Does not move the feet, so only a glance. |
| `move(vx, vy, yaw)` | a real turn, yaw up to ±3.0 rad/s. At a sane 1.0 rad/s near people: 60° ≈ 1 s, 180° ≈ 3.1 s — and it must be undone. |

So a left-right sweep costs ~3 s and a look behind ~6 s round trip. **Far too
expensive to spend every metre on principle** — which is why scanning is
triggered by not being able to see, rather than by distance travelled.

### `awareness.py` — do I have visibility, and where should I look?

A ring of bearing sectors, each remembering when a *usable* frame last covered
it and how far it could see. Not a map: it holds no geometry and survives no
movement. It answers only "how recently did I look that way, and what did I
see" — which for a greeter working a few metres of pavement is the useful
question. Building a metric map to answer it would be a research project
(monocular, 122° lens, no wheel odometry) for no extra benefit.

"No visibility" is made of measurable things:

- **frame unusable** — blown out, too dark, too blurred, too flat. The
  entrance is glass; backlit glass saturates a 122° lens, and a saturated
  frame is not a view of an empty forecourt.
- **occluded** — somebody close enough to fill the frame. Nothing behind them
  is visible at any range.
- **stale** — we have not pointed the camera that way recently enough to
  believe anything about it. People move.

**The rule that makes it honest: a bad frame never refreshes the map.** If a
blown-out frame counted as "I looked and it was clear", the robot would talk
itself into confidence it had not earned, and the staler its knowledge got the
more certain it would become. Unusable frames mark the direction unknown and
demand a fresh look.

The sweep planner only aims at sectors that are actually stale, thins aims to
roughly one lens-width apart, and orders them cheapest-turn-first — so a 240°
arc costs two to four looks, not sixteen.

### `escort_core.py` + `escort.py` — "follow me"

The problem, stated plainly: **the camera faces forward and the follower is
behind.** While the robot leads, the person it is leading is in the one place
it cannot see. That is geometry, not model quality.

Three ways out:

1. **Look back at checkpoints** — implemented. Walk a leg, stop, turn,
   confirm, turn back. A full 180° is not needed: at HFOV 122° the frame spans
   ±61°, so **135° puts straight-behind comfortably inside the frame** and
   saves a quarter of every check.
2. **Crab-walk**, facing across the direction of travel. `move()` takes a
   sideways `vy`, so the guest stays in frame the whole way with no stutter —
   but the direction of travel then sits at the extreme edge of the lens,
   where distortion is worst and the people-guard least reliable. Trading
   forward visibility for rear visibility, walking towards stairs, is not
   obviously a win. Left as an option.
3. **A second camera on the spare USB port.** The honest fix, ~£30, and then
   none of this is necessary.

**"Following" is a trend, not a presence.** Someone who stops to read a sign is
still visible behind the robot, at a growing range. So the range at each
checkpoint is kept: holding steady is following, growing every time is being
left behind — and then the robot waits and says so rather than arriving alone.
It verifies once more *at* the destination, because arriving alone is the
failure the whole module exists to prevent.

Identity is deliberately weak — a 4×4×4 colour histogram of the torso compared
by cosine similarity. Enough to prefer the right person out of two over a few
seconds; nowhere near re-identification. With a dozen people about it can be
fooled, so the asymmetry is built in: **being unsure means waiting and asking,
never walking on.**

```bash
python3 escort.py --guard http://127.0.0.1:8092 --robot http://127.0.0.1:8090
curl -XPOST localhost:8093/start -d '{"distance_cm":450,"destination":"the restaurant"}'
curl localhost:8093/        # phase, follower range and trend, visibility
curl -XPOST localhost:8093/stop
```

The people-guard outranks the escort at every step. An escort in progress is
not a reason to walk into somebody.

### Two things that want the same one-line fix

**Turns are open-loop.** `move_robot` takes a direction, a duration and a speed
*fraction* — not an angle. So every turn is dead-reckoned and the ring's
bearings drift with it. Measure the rate before trusting any of it:

```bash
python3 calibrate.py turnrate --robot http://127.0.0.1:8090
```

That commands a known turn and reads the actual rotation off the camera by
phase correlation — the camera as its own protractor. It needs texture in view;
a blank wall gives it nothing to lock onto.

**It cannot speak.** Speech belongs to the Vapi layer on the Jetson, outside
this repo, so `say` actions surface as `pending_say` in the escort's status for
the voice layer to pick up. Until that is wired, an escort that loses its guest
stops and waits *silently* — safe, but rude.

Both disappear if the agent forwards `getRPY()`: turns close the loop, bearings
stop drifting.

## Tests

```bash
python3 test_guard_core.py      # 22  the people-guard
python3 test_awareness.py       # 18  visibility and scan planning
python3 test_escort.py          # 15  follow-me
```

55 tests, no camera, no torch, no robot.

All three cores are pure stdlib on purpose: the logic is testable on a laptop
while the robot is flat, which is most of the time. The scenarios covered are
the ones the obvious implementation gets wrong — the child who walks into the
blind spot, the feed that dies, the box clipped by the frame bottom, the
blown-out frame that must not count as having looked, the guest who stops
following.

## What this is not

It is a monocular camera, a person detector and a timer. It cannot see the
floor nearer than ~0.4 m, it cannot see behind, it will miss people in odd
poses and in hard backlight — and the entrance at Leukerbad is glass, which
reflects and backlights. Stream latency plus one tick is ~250 ms; at 0.5 m/s
the robot covers ~12 cm before a stop even issues.

**A human handler within arm's reach of the remote is the actual protection.**
This makes the handler's job easier. It does not replace them, and it is not a
reason to let the dog walk unattended near small children.

## Not done

- **Obstacle avoidance for things that are not people.** A person detector sees
  people. Pushchairs, luggage, glass doors and the top of a stairwell are
  invisible to it. The walkway outside the entrance has stairs down to the
  reception — nothing here knows that.
- **Mapping.** Deliberately skipped. Monocular SLAM on a 122° lens with no
  wheel odometry is a research project, and a greeter that works a 3 m patch of
  pavement does not need a map — `awareness.py` answers the actual question
  without one.
- **Closed-loop turns.** Needs `getRPY()` forwarded from the agent.
- **Escort speech.** Needs the Vapi layer to read `pending_say`.
- **Leading anyone down the stairs to reception.** The walkthrough video shows
  the route; `set_gait('climb')` exists. Nothing here detects a step, a
  handrail or the top of a flight, and a 16 kg quadruped descending stairs
  beside a guest is not a thing to switch on untested.
