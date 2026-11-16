# Cosmos on this project

## What runs where

| What | Where | How |
|---|---|---|
| Cosmos-Reason2-2B (vLLM 0.13, OpenAI-compatible) | brain: this DGX Spark, `127.0.0.1:8000/v1` | `scripts/cosmos/serve_reason2.sh` (own venv `COSMOS_VENV`, default `~/cosmos-reason2/.venv`). Refuses to start while Isaac Sim runs |
| L3 judge client | brain relay (`brain/relay.py`) calls it for frames the edge flags | `brain/relay.py` (the one judge implementation) |
| Frame sampler | edge (venue PC) | `perception/sampler.py`: up to 640x360 JPEG, 2–5 fps, nothing in camera_off zones, never stored |
| Verdict handling | edge | `safety/supervisor.py` turns verdicts into SafetyFSM causes |

The edge never calls Cosmos directly. It sends frames to the brain relay, and the relay posts verdicts back to the edge's
`/verdict`. If the brain is unreachable, the judge counts as unavailable: the FSM drops to CAUTION (0.15 m/s), and
`guide_to` is refused only when `safety.judge_required: true`.

## Judge contract

* **Input:** one frame, the go-zone text (`safety.go_zone_description` in `config/d1w.yaml`), and the intended action (`forward
  2 m at 0.3 m/s toward <poi>`).
* **Output:** JSON only, `{"leaves_go_zone", "hazards", "people_within_2m", "stop", "confidence", "reason"}`.
* **Request:** temperature 0, max_tokens 150, timeout 8 s. The prompt asks for no `<think>` block, and the parser strips one
  if it appears anyway.
* **Parsing and failures:** the parser takes the first JSON object with schema fields and coerces the types. Results are
  cached by frame hash. A failed call returns `judge_unavailable`.
* **When the judge runs:** at `guide_to` start, every 2 m driven, on a new scene class, and when L2 first sees a hazard.

## Measured latency and quality (2026-11-14, this Spark)

`scripts/cosmos/judge_probe.py` runs the full edge path: at most 640x360 JPEG, base64, chat completion, parse. These
numbers were measured while a teammate's Isaac Sim GUI was also on the GPU, which breaks the never-both rule. Expect
them to be a little lower without it.

**Real spa frames.** 28 stills, one every 3 s, from the venue walkthrough `assets/venue/spa_walkthrough.mp4`
(extracted to `docs/venue/walk_*.jpg`). Neither the video nor the stills are committed: guests are recognisable.
Raw verdicts for the current prompt: `docs/cosmos_probe_venue_2026-11-14.json`.

| Prompt / go-zone text | p50 | p90 | Max | Prompt echo in `hazards` | People frames flagged | Staircase frames flagged |
|---|---|---|---|---|---|---|
| v2: go-zone text names the no-go areas (Relax level, Technik, turnstiles, stairs) | 2.52 s | 3.22 s | 4.08 s | **5 / 28** ("barefoot Relax level", "staff-only Technik doors", "doorway behind turnstiles") | 5 / 6 | 2 / 5 |
| **v3 (current)**: go-zone text "dry, flat public floor of the entrance hall: light tiles or wood", generic cues in the question | **1.91 s** | 2.26 s | 2.81 s | **0 / 28** | 6 / 6 (incl. "child" on 002 and 028) | **0 / 5** |

The two earlier runs on our Isaac hotel renders (22 frames, before the venue video) gave p50 3.40 s for v1 (with one
8 s cold timeout) and 2.45 s for v2: `docs/cosmos_probe_2026-11-14.json`.

What this means for the design:

* **L3 sees people and objects. It does not see boundaries.** With v3 the judge flagged a person or a child in the
  path on every frame that had one, with plausible counts. On stairs it failed every time. On walk_021 it wrote
  "Straight ahead is a staircase … the intended action violates the safety zone" and still returned
  `leaves_go_zone: false, stop: false`. `leaves_go_zone` came back `true` once in 56 venue verdicts, as an echo
  in v2. So **stairs, the Relax level and Technik are kept out by L1** (restricted zones in
  `policies/zones_therme.yaml`, enforced on every predicted command) **and by L2** (`floor_transition`: terracotta and
  the beige Relax-corridor tile are no-go floors). L3 only adds CAUTION for people and objects.
* **Naming the forbidden areas in the prompt makes them appear as hazards.** The go-zone text sent to the judge now
  describes only what is allowed.
* `confidence` sat at 0.96–0.99 on every frame. It is a format field, not a probability.
* Latency: at v3's p50 of 1.9 s, a robot at the 0.3 m/s guide pace covers about 0.6 m before the verdict lands. L3 is
  advisory on top of L1 (every command) and L2 (5 Hz).
* Supervisor rule (`safety/supervisor.py`): an L3 HOLD latches only on `leaves_go_zone`, or on `stop` with a hazard
  actually listed, at confidence 0.5 or more. A bare `stop` is CAUTION for 5 s.

### Floor-transition calibration from the same stills

`perception/floor_labels.json` holds 16 labelled floor boxes across 4 classes: concourse_tile, wood, terracotta, and
checker_tile (the beige Relax-corridor grid). No pool-deck footage exists, so **wet_deck is not calibrated** (and
the pool deck is camera_off anyway). Model: nearest exemplar over HSV histograms (`perception/floor_classes.json`).
Leave-one-frame-out accuracy was 0.77, against 0.62 for per-class mean histograms. On the 12 unlabelled stills, the
floor under the camera was right wherever a floor was visible. Walls came out "unknown". Distances in that check used
the phone's camera geometry, not the robot's. Re-label from the robot's own camera on Sunday
(`scripts/calibrate_floor.py`): the robot's camera is 0.3 m high and looks up, where the phone was at chest height
looking down.

## Training roadmap (after the hackathon)

1. **Collect.** `scripts/export_cosmos_dataset.py` turns robot sessions into action-labelled clips: video segments, the
   `cmd_vel` sequence, the outcome (arrived, held by L1/L2/L3, recovered, operator stop) and the judge verdict next to the
   human decision (Approve/Deny). The layout follows cosmos-framework post-training.
2. **Label from governance.** ClawMetry Approve/Deny decisions and operator Stops are the labels the judge should have
   produced. Disagreements between the judge and the operator are the high-value examples.
3. **SFT Reason2-2B** on the venue's floors, turnstiles and people seen from 0.3 m high, looking up. That viewpoint is the
   main domain gap; the team's YOLO has the same problem.
4. **Foresight (stretch).** Cosmos 3 Nano `--omni` predicts the next 2 s from one frame plus a planned action chunk. The
   L2 floor-transition detector then runs on the predicted frames, giving a predicted-vs-actual clip. The action format
   for mobile/camera motion in cosmos-framework is still unverified (# VERIFY).
