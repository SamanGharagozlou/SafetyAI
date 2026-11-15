# Camera people-guard

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

Banned near a child are the gestures that lift a limb or shift the body —
`shake_hand` raises a paw to roughly the head height of a two-year-old, which
is the scenario this exists for.

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

## Tests

```bash
python3 test_guard_core.py      # 22 tests, no camera, no torch, no robot
```

`guard_core.py` is pure stdlib on purpose: the safety logic is testable on a
laptop while the robot is flat, which is most of the time. The scenarios
covered are the ones the obvious implementation gets wrong — the child who
walks into the blind spot, the feed that dies, the box clipped by the frame
bottom.

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
- **Mapping.** Deliberately skipped; see the top-level discussion. Monocular
  SLAM on a 122° lens with no wheel odometry is a research project, and a
  greeter that works a 3 m patch of pavement does not need a map.
