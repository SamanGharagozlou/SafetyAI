# Privacy: camera, guests, venue rules

The Therme's rule is **no photography or video anywhere in the complex**. Our robot carries a camera, so the stack is
built to look without keeping.

## What the camera feeds, and where it ends

| Data | Where it exists | Kept? |
|---|---|---|
| Live frames (RTSP / the team's MJPEG) | in memory in the edge's perception loop (newest frame only) | **no**: overwritten by the next frame |
| Frames sent to the brain | one JPEG of at most 640x360, in flight to `POST /frame` | **no**: dropped after the judge call; the relay writes nothing (`tests/test_brain.py::test_unflagged_frame_is_not_judged_flagged_is_and_goes_back` checks the working directory stays empty) |
| `/look` frame for the concierge | one JPEG, in flight to `POST /describe` | **no** |
| Detections, distances, floor classes, scene class | the `Observation` record, journal events | yes: metadata only, no pixels |
| Cosmos verdicts | the brain's ring (30), edge journal | yes: text and flags (`docs/cosmos_probe_*.json` hold verdicts only) |
| Concierge exchanges | `concierge.turn` events in the robot-session journal | yes: guest text and the reply, no image. A first name from the QR onboarding is replaced by `<guest>` in the journal (`named: true`); it goes to the brain and the Anthropic API only. The brain keeps the last 12 messages and the name / language per session in memory for context |

* **camera_off zones** (`pool_deck`, `relax_level` in `policies/zones_therme.yaml`): while the robot is in one, no frame
  is read, detected, classified or sent. The Observation says `camera_off: true`
  (`test_perception.py::test_pipeline_observation_and_camera_off`).
* **The team's detector** keeps an annotated MJPEG on their PC. Their `--json` writes only detection lines, and we
  don't ask them to store frames.
* **No recordings of the venue:**
  * The ClawMetry `camera.frame` replay exists only for simulation bodies (Isaac `demo_cam`), never for hardware.
  * Our demo recordings draw no camera images: the twin and the journal panes are rendered from state.

## The walkthrough video and its stills

`assets/venue/spa_walkthrough.mp4` (filmed on the concourse) and the stills extracted from it (`docs/venue/walk_*.jpg`)
show recognisable guests.

* **Not in git:** both are git-ignored, so they are never committed, pushed or exported to the SafetyAI repository.
* **Uses:** two only. Calibrating the floor classes, which keeps HSV histograms only (`perception/floor_classes.json`),
  and measuring the judge, which keeps verdict text only.
* **Afterwards:** delete them when calibration on the robot's own camera is done.

## Inside the swimming complex

There is no footage, by rule. So `wet_deck` stays uncalibrated by design, `pool_deck` is camera_off, and the pool deck
is protected by the L1 geofence and the route (docs/D1W-RUNBOOK.md). The training roadmap (docs/COSMOS.md) collects
data on the concourse only, and only with the venue's written permission.

## Keys and access

* **Auth:** every edge and brain endpoint needs `X-Api-Key` from another machine. The services bind to the tailnet or
  LAN, never `0.0.0.0`; Cosmos is the one exception and requires its own key.
* **Guest data:** the concierge sends guest text to the Anthropic API from the brain. It sends no images other than
  `/look` (a Cosmos call on the Spark, not Anthropic).
