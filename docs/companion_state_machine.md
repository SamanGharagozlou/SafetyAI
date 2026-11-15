# Companion state machine (branch `state-machine`)

Connects the **guest's phone**, the **robot gateway** and Nav2 into one flow: QR join, talk or tap, pick a place, escort, arrival, next step.
This is the "companion behaviour" box of [system_architecture.md](system_architecture.md). Package: `ros2_ws/src/d1_companion` (plain Python, no ROS needed
to run it; it talks to `d1_gateway` over WebSocket).

```
guest phone ──WebSocket JSON──▶ d1_companion (state machine) ──WebSocket JSON──▶ d1_gateway ──ROS 2──▶ Nav2 / bridge
            ◀── state, say, map ──                            ◀── goal_received / accepted / progress / reached ──
```

## Guest flow (what the guest sees)
1. Scans the QR code on the robot. The URL is `https://<robot-host>/?t=<join token>`. The token proves they are at the robot; wrong token: "this QR code is not valid".
2. Presses *Start* (optional nickname). The robot greets them and lists its places. If another guest is already paired: "the robot is already with another guest".
3. Talks or types ("take me to the left", "what can you do?") or taps a **dot on the map** (each place is a dot with a ring; blue = place, orange pulsing = heading there,
   green = robot is here, black arrow = the robot) or a place button, then confirms *Take me there*.
4. The robot says "Follow me…"; the phone shows *On the way*, a progress bar and the distance left. **Stop** is always visible while moving.
5. On arrival: "We have arrived at X. Where next?" with the other places, back to the start, or *Finish*. The next step depends on the guest's input.
6. *Finish* (or 2 min of silence: prompt at 60 s, end at 120 s) says goodbye, the robot drives back to the start point and the session data is dropped.

## States
| state | meaning | leaves when |
|---|---|---|
| `IDLE` | no guest, robot waits at home | a guest joins |
| `AWAITING_INPUT` | guest paired, robot listens | pick a place / question / finish / silence timeout |
| `PREPARING` | robot gets ready (stands up if lying) | stand-up done -> `NAVIGATING`; failed or 20 s -> `BLOCKED` |
| `NAVIGATING` | a goal is active (`sending` -> `received` -> `moving`) | `goal_reached` -> `ARRIVED`; failed/rejected/timeout -> `BLOCKED`; stop -> `STOPPED`; new pick re-routes |
| `ARRIVED` | goal reached, offers next step | pick / question / finish / silence |
| `BLOCKED` | goal failed or was refused; says why; offers *Try again*, other places, finish | any input |
| `STOPPED` | guest pressed stop | any input |
| `RETURNING_HOME` | no guest, driving back to `home` | reached -> `IDLE`; a new guest joining stops the return and takes over |
| `HOLD` | robot unavailable: gateway down, SDK link down, or **damped by staff (E-STOP)** | condition clears -> `AWAITING_INPUT` / `IDLE` |

Timeouts (all configurable in `Config`): gateway must answer a goal within 3 s, Nav2 must accept within 15 s, one escort at most 180 s, stand-up 20 s.

## Safety rules built into the machine (tested)
* **One goal at a time.** A new pick re-routes; events of the old goal id are ignored (the gateway preempts it).
* **A damped robot is never stood up automatically.** `mode == damping` (staff E-STOP) -> `HOLD`, no `stand_up`, joins refused. Only a `lying` robot is stood up, and only before a pick.
* **Guest can always stop.** `stop` is sent to the gateway (cancels the goal, zero velocity); late `goal_canceled` events are ignored.
* **No escort when the robot cannot do it:** battery < 20 % (`min_battery`), localizer says *not placed*, Nav2 not running. The guest is told why; nothing is sent to the robot.
* **Failure never silently continues:** failed / rejected / timed-out goal -> robot stopped, guest told, options offered (FR-X6).
* **Gateway or link lost mid-escort -> stop and `HOLD`**, recovers when it comes back.
* The gateway is only ever sent `goal`, `stop` and named `intent`s, never velocities. `estop` is not reachable from the phone.
* Only one guest session per robot (FR-S2); sessions live in memory and are dropped at the end (FR-S3).

## Phone protocol (WebSocket, JSON, same port as the page)
Guest -> server: `join {token, nickname, lang}` or `resume {session}`; then `select {destination}`, `say {text}`, `stop`, `end`, `ping`.
Server -> guest: `joined {session, robot_name, places[{name,x,y,yaw,label,description,home}], map{url,w,h,res,x0,y0}}`, `join_rejected {reason}`,
`state {state, goal_status, destination, at, distance_remaining, message, reason, options[], robot{pose,battery,mode,connected,localization,gateway_up}}`,
`say {text}`, `session_ended {reason}`, `error {message}`.
Staff: `admin {token, action: end_session | stop}` when `--admin-token` is set. HTTP: `/` (page), `/map.png?s=<session>` (needs a live session), `/healthz`.
Page security: strict CSP (`default-src 'none'`, same-origin scripts), no cookies/storage (the session id sits in the URL fragment so a refresh can resume), no third-party code.

## Places and the test map
`maps/metacam_lite_2026-11-14/places.yaml` (same file for the gateway `--places` and the companion): `home` (the map origin) and four test destinations
**2 m front (+x), back (-x), right (-y), left (+y)** with labels and aliases. `back_2m` is only ~0.34 m from the edge of the scanned area: check it before a physical run.

## Run
```bash
# robot side (Jetson, container) – each as its own process; private ROS_DOMAIN_ID on a shared machine
ros2 launch d1_nav static_navigation.launch.py map:=$MAP/map.yaml lidar_x:=.. lidar_y:=.. lidar_yaw:=..      # Nav2 + lidar localizer (place the robot first)
ros2 run d1_gateway gateway --port 8765 --places $MAP/places.yaml
ros2 run d1_companion companion --places $MAP/places.yaml --map $MAP/map.yaml --port 8780      # prints the join URL for the QR code
```
Defaults bind to `127.0.0.1`. Publish the phone page with **Tailscale Serve (HTTPS, needed for the microphone), never Funnel**; keep the join token secret
(`--join-token` / `D1_COMPANION_JOIN_TOKEN`; random if empty). A rotating per-session token (open question 6 in the requirements) would be a small change in `server.py`.

## Tests
* `pytest -p no:anyio ros2_ws/src/d1_companion/test` (32): state machine scenarios (full tour, failure + retry, stop, re-route, stand-up, E-STOP hold, gateway loss,
  low battery, not localized, timeouts, silence, takeover while returning), a randomized invariant test (60 runs x 250 events), and end-to-end over real WebSockets
  (phone <-> companion <-> gateway + mock robot): token, tour, busy robot, stop, not localized, gateway loss, silence, resume, staff end, bad input.
* `pytest -p no:anyio ros2_ws/src/d1_gateway/test` (8) includes the new "goal rejected when not localized".
* **Simulation (2026-11-15)**: sim robot (odometry drifting 1 deg/s, 8 % scale error) + fake MetaCam + Nav2 static navigation on the lidar map + gateway (ROS mode) + companion.
  A scripted guest tapped front, back, right, left, each followed by "take me home", then finish. All 8 legs arrived; true position error 0.02-0.26 m
  (Nav2 goal tolerance 0.25 m). Take about 10-17 s per 2 m leg at the Nav2 speed limits. The phone page was driven in a 420 px headless Firefox: join, select, navigate, arrive, chat, finish.

## Gateway change made for this
The gateway now reports `localization` (from the lidar localizer's status) in `state` and rejects goals when a localizer is running and the robot is not placed.

## Not done / limits (be aware)
* **Not run on the real robot, with a real phone, or with real speech.** The phone page uses the browser's speech recognition/synthesis if available (unverified on phones).
* **The React app in `frontend/` is not connected.** It is demo-only by design (CSP `connect-src 'none'`, "no backend"), there is no Node on the development PC to build or test it, and its docs
  require a reviewed integration profile first. The protocol above is the contract; `frontend/src/voice` (Vapi) can call the same `select`/`say` messages, or a `go_to_place` Vapi tool can call
  the companion. `phone.html` is a complete reference client in the meantime.
* Language understanding is a small rule-based parser (`nlu.py`); questions get an honest "I don't know" plus the list of places: there is **no facility knowledge base** (FR-I1) and no TTS on the robot (`--robot-speech true` only sends text to the gateway `say` intent).
* No follow-check (FR-X4), no person-approach greeting trigger (FR-E1), no rotating QR, no staff UI for sessions beyond the `admin` message, no multi-language strings yet.
* One robot, one guest at a time; the escort speed is whatever Nav2 is configured for (0.30 m/s forward limit).
