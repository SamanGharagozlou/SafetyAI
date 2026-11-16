# 15 · Gaps, risks and roadmap

[← 14 Testing and evidence](14-testing-and-evidence.md) · [index](../README.md)

> This chapter is part of the submission, not an appendix to it. A system that
> governs a robot and cannot say what it does not know is making exactly the
> claim it exists to prevent.
>
> `readiness()` in `clawmetry/compliance.py` returns **a gap list and never a
> score**, because a percentage invites rounding up and a gap list does not.
> This chapter is the human-readable version of the same discipline.

---

## 1. Blocking — must be closed before a guest is near the robot unsupervised

| # | Gap | Where | Why it blocks |
|---|---|---|---|
| B1 | **No `/cmd_vel` arbitration.** Nav2, the dashboard pad, keyboard teleop and the ROS agent can all publish with nothing ranking them | [01](01-system-and-hardware.md) §7 | Two writers can fight over a moving robot. It also weakens `unattributed_motion`, the flagship detector: with four unranked writers nothing can attribute motion to a source |
| B2 | **No emergency remote while the robot is on our Wi-Fi.** The robot's hotspot, which the physical remote uses, is off | [10](10-mapping-lidar-spike.md) §7 | There is no hardware stop. *This must be solved before any autonomous motion* |
| B3 | **The child detector has never been tested against a child** | [07](07-child-safety.md) §8 | The policy is enforced; the sensing behind it is unproven. `child_safety_verified` |
| B4 | **The camera people-guard is `inactive` on the Jetson** | [06](06-perception-and-guard.md) §7 | Every rule in chapter 07 is code that is not running on the robot today |
| B5 | **No face anonymization.** `privacy_filter` is a spec | [13](13-privacy-and-compliance.md) §4 | FR-P4 is *must, before any deployment with guests* |
| B6 | **No DPIA, no ISO 12100 risk assessment, nobody has signed the venue file** | [13](13-privacy-and-compliance.md) §7 | The three non-technical blocking gaps the system reports about itself |
| B7 | **No PL/SIL determination** | [13](13-privacy-and-compliance.md) §7 | *A camera, a detector and a timer on a best-effort network is not a rated safety function and must not be sold as one.* This one is not "close it", it is "never claim otherwise" |

### Ranked, if there is time for one thing

**B1.** It is a day of work, it is purely ours, it unblocks Nav2 testing, and
it upgrades the single most valuable detector in the control plane from "the
robot moved unexplained" to "the robot moved and *this* writer did it".

---

## 2. High — wrong answers in production, not injuries

| # | Gap | Where |
|---|---|---|
| H1 | **The tap only governs while the funnel points at 8901.** Point it at 8090 and oversight silently becomes observation | [09](09-clawmetry-control-plane.md) §9 |
| H2 | **Cosmos gates synchronously**, so a walk waits ~4 s. Defensible as a pre-flight check, and it contradicts the "never in the path of a stop" rule. Async escalation is the right end state | [09](09-clawmetry-control-plane.md) §15 |
| H3 | **Position is never scored on hardware.** The ros-agent holds `/odom` internally but does not expose it in `/status`. *Three lines in `~/vapi-robot/d1_ros_agent.py`* | [09](09-clawmetry-control-plane.md) §15 |
| H4 | **`d1-ros-agent` and `d1-vapi-server` are not in this repository.** They live in `~/vapi-robot/` on the Jetson | [02](02-jetson-and-ros.md) §4 |
| H5 | **The `connected` latch fix exists only on the Jetson.** The merged `cmd_vel_bridge.py` has no `connected_raw`. **The next deploy from this tree loses it** | [02](02-jetson-and-ros.md) §5 |
| H6 | **No live localization.** Odometry-only drift over a 50 m escort is unbounded; LOC-2 asks for ≤ 0.3 m / ≤ 5° | [11](11-navigation-localization.md) §7 |
| H7 | **No keepout zones in the Nav2 costmap.** The venue file forbids five zones; the planner knows about none of them, so a forbidden path is refused at the gate rather than planned around | [11](11-navigation-localization.md) §7 |
| H8 | **The camera mount has never been measured.** Two chapters derive different blind-spot numbers (0.40–0.57 m vs ~1.07 m) from two different *estimated* tilts of the same camera | [11](11-navigation-localization.md) §4 |
| H9 | **`robot-guard` and `human_perception` / `d1_follow` are unreconciled** — two detectors on one camera, and a documented project rule being crossed | [06](06-perception-and-guard.md) §7 |
| H10 | **The ROS 2 DDS surface is unauthenticated.** SROS2 is not enabled | [08](08-adversarial-agents.md) §11 |
| H11 | **`networkmanager-cleanup.service` wipes saved Wi-Fi on every boot**, so `wlan0` has no IP when `mc_ctrl` starts and the SDK port fails to bind | [02](02-jetson-and-ros.md) §5 |

### H8 deserves a sentence of its own

A tape measure and an afternoon would retire two sets of derived numbers at
once: the guard's ranging table and the costmap study's blind zone. It is the
cheapest high-value work outstanding, and it is the same class of error as the
uncalibrated walk speed that once produced a 46.7σ reading on a perfect walk.

---

## 3. Medium — known wrong, bounded impact

| # | Gap | Where |
|---|---|---|
| M1 | **`_replay()` does not re-feed the correlator.** After a restart, `command_unattributed_to_speech` stays silent until the first new transcript. Conservative in the right direction, still wrong | [09](09-clawmetry-control-plane.md) §15 |
| M2 | **The ledger is not signed.** The chain proves internal consistency, not origin | [09](09-clawmetry-control-plane.md) §8 |
| M3 | **OTLP export is an unfilled placeholder** and `CLAWMETRY_OTLP` points where nothing listens. The exporter **degrades to a silent no-op when wrong** | [09](09-clawmetry-control-plane.md) §15 |
| M4 | **`CHILD_FIELDS["height_under_m"]` is classified `up: LOOSEN`** and appears to be inverted — so *reducing* child protection currently needs only a name, not the loosening confirmation. One-word fix plus a test per field | [07](07-child-safety.md) §6 |
| M5 | **`clawmetry/README.md` quotes 41 and 62 tests**; the suite is at **169**. Stale in two places | [14](14-testing-and-evidence.md) §1 |
| M6 | **No follow-check.** The robot cannot see behind itself (FR-X4). `escort_core.py` implements the right behaviour and is unverified | [11](11-navigation-localization.md) §7 |
| M7 | **The `frontend/` React app is demo-only**, `connect-src 'none'`. The working guest surface is `tools/robot-face` | [04](04-guest-journey-qr.md) §6 |
| M8 | **No rate limiting on the tap** | [08](08-adversarial-agents.md) §11 |
| M9 | **The QR is a static key**, not rotated per session | [04](04-guest-journey-qr.md) §2 |
| M10 | **Vapi ticket prices are second-hand.** `leukerbad.ch` returns 403 to automated fetching; the figures came from tourism listings that disagreed. **Confirm against the real tariff sheet before a paying guest hears them** | [03](03-voice-vapi.md) §6 |
| M11 | **Audio lives on a laptop**, not the Orin: no browser, the USB mic did not enumerate, the speaker would not pair | [05](05-audio-bluetooth-mic.md) §5 |
| M12 | **No soak test.** NFR-5 asks for 30 minutes unattended; unmeasured | [14](14-testing-and-evidence.md) §8 |
| M13 | **`/opt/app_launch/start_navigation.sh` and friends have never been inspected.** The robot may already support a LiDAR | [10](10-mapping-lidar-spike.md) §1 |

M13 is a twenty-minute read-only SSH session that could change the sensor
decision entirely, and it keeps not happening.

---

## 4. Risks that are not gaps — things that will bite regardless

| Risk | Mitigation today |
|---|---|
| **Battery.** Observed 15 % and the robot shut down. The Orin is powered from the same battery, so a flat robot takes the whole stack down | `min_battery_pct: 25` in the venue file; the SDK refuses commands under 20 %. Nothing recovers the Jetson |
| **Control authority is exclusive**, and low battery hands control to the physical remote automatically | documented, not handled |
| **Firmware updates reset the camera stream settings** (patched to 15 fps / 5 Mbit/s) | re-patch `/opt/app_launch/stream_4cams_rtsp.sh` |
| **Venue Wi-Fi is unreliable** | nothing latency-critical crosses it; robot ↔ Orin is wired |
| **Glass, mirrors and water surfaces** affect both LiDAR and camera — a thermal spa is close to a worst case for both | unknown; open question for the venue |
| **Wheel-foot terrain limits** are not modelled by 2D costmaps | the venue file forbids the stairwell outright |
| **Tailscale breaks WebRTC** — 39.9 s ICE gathering vs 133 ms | the console names the real cause at 25 s; buttons keep working |

---

## 5. Roadmap

### Next (days) — close the cheap, high-leverage gaps

1. **`twist_mux`** with priorities `estop/stop > operator teleop > Nav2 >
   behaviours`, each input with a timeout. **(B1)**
2. **Measure the camera mount.** One afternoon, retires H8 and tightens both
   chapter 06 and chapter 11.
3. **Commit the `connected` latch fix** from the Jetson into
   `cmd_vel_bridge.py`. **(H5)**
4. **Expose `/odom` in the ros-agent's `/status`** — three lines, and position
   scoring starts working on hardware. **(H3)**
5. **Fix `height_under_m`'s direction** and add a test pinning the direction of
   every editable field. **(M4)**
6. **Update the stale test counts** in `clawmetry/README.md`. **(M5)**
7. **Read-only inspection of `/opt/app_launch/`.** **(M13)**

### Then (weeks) — make the safety story real rather than designed

8. **Activate `robot-guard` on the Jetson**, with inference on the Orin (ONNX
   via opencv-dnn avoids torch entirely; OpenCV 4.8 is already there) so the
   override loop does not inherit a Wi-Fi hop. **(B4, H9)**
9. **Reconcile the two detectors**: the guard consumes
   `/perception/humans/detections` instead of running its own YOLO. **(H9)**
10. **Build `privacy_filter`**, fail-closed, with the PRIV-4 bag-profile test.
    **(B5)**
11. **Test the child rule against a child** — with consenting families, off
    the venue floor, measuring height-estimate error and detection recall at
    0.5–3 m. **(B3)**
12. **Async Cosmos**: the kinematic gate passes immediately; the world model
    escalates to a stop. **(H2)**
13. **Sign the ledger.** **(M2)**
14. **Keepout zones generated from the venue file into the Nav2 costmap**, so
    one document drives both the gate and the planner. **(H7)**

### After (the real product)

15. **A live localization sensor.** The recommendation stands: Livox Mid-360 →
    FAST-LIO2 → 2D grid → Nav2 MPPI Omni. Bench it on a stand first. **(H6)**
16. **Solve the emergency-remote problem** with a wired setup that keeps the
    hotspot available. **(B2)**
17. **`go_to_place` through the gateway**, so voice gains a named destination
    and never a velocity. **(M6, and it is what makes escort real)**
18. **Scan the actual spa**, add `places.yaml` and a keepout mask, and mark the
    measured zones `verified: on_site` — which is itself a classified,
    confirmed loosening.
19. **Get the venue file signed**, which is the single step that moves the
    robot off 0.15 m/s and lets it guide at all.
20. **Hand the evidence pack to an assessment body** and work their gap list,
    not ours.

---

## 6. What we would tell another team building this

1. **Decide early that the agent is adversarial.** Every control in
   [chapter 08](08-adversarial-agents.md) is cheap if designed in and
   near-impossible to retrofit.
2. **Measure the body before you trust a number about it.** 46.7σ on a perfect
   walk, because the predictor guessed.
3. **Allowlist, never denylist.** Both gesture bugs in this repository were
   denylists, and both let a raised paw through.
4. **Make silence loud.** This project shipped a calm-looking failure twice
   before it stopped doing it.
5. **Write the venue's rules in the venue's language**, in a file the venue
   can sign — and then enforce them in exactly one place.
6. **Never render a weaker claim as a stronger one.** `not enforced` is not
   *allowed*; `not scored` is not *passed*; *"intact 7 seconds ago"* is not
   *"intact"*.
7. **Keep the gap list in the product**, not in a slide. Ours is on the
   dashboard, and it is the panel an auditor will trust first.

---

[← 14 Testing and evidence](14-testing-and-evidence.md) · [index](../README.md)
