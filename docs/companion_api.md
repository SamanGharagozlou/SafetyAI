# Companion backend API for the visitor frontend

Backend half of the guest flow, built to the contract the frontend already prepared in [frontend/API_INTEGRATION.md](../frontend/API_INTEGRATION.md)
(`requestApiAdapter.ts`, `contracts.ts`). Implementation: `ros2_ws/src/d1_companion/d1_companion/server.py`. State machine and safety rules:
[companion_state_machine.md](companion_state_machine.md). **The frontend side is implemented in `frontend/src/robot/` (robot mode, see [frontend/README.md](../frontend/README.md#robot-mode-live-robot---mode-robot)); the items the frontend owners still need to agree are in section 4.**
Status: built and tested against the mock robot and in simulation, **driven end to end by the real React app in a mobile-sized browser** (join, tap a place, confirm, escort, arrival, typed request, stop). **Not run on a real phone or the real robot** (*unverified*).

## 1. Principles
* **Guests never see the map.** Only the layout of the destinations is sent: `name, label, description, x, y, home`. There is no map/floor-plan endpoint (`/map.png` is 404; tested).
  The guest's robot position is sent as `x, y, yaw` only. Battery, mode, localization and gateway details stay internal.
* **Same-origin, no CORS, no cookies.** The frontend's CSP and adapter require root-relative same-origin paths and `credentials: 'omit'`: put this server behind the same
  origin as the app (a proxy for `/api/*`, e.g. through the hosting worker or Tailscale Serve). Cross-origin writes are rejected (403).
* **One guest per robot.** The QR code carries a secret join token; joining returns a backend-issued, unguessable access token (192 bit) that is the Bearer credential. It is independent of the nickname.
* **Nothing about a guest is kept.** Requests, replies and robot lines are in memory and dropped when the session ends (immediately, also on expiry or staff end).
* **The backend decides.** `text` is interpreted by the backend (rule-based parser today); `proposed_action` and `place` are hints. If the text and a tapped place disagree, nothing is sent to the robot (`denied`).
* **Stop is never behind a confirmation.** `POST /api/robot/stop` acts at once. The robot only moves for a request the guest confirmed in the UI.

## 2. Endpoints
All JSON, `Authorization: Bearer <accessToken>` unless stated. Errors: `{"error": code, "message"?}`; `401` means the session is gone (the adapter maps 401/403 to `session_expired`).

| call | purpose |
|---|---|
| `POST /api/session` `{token, nickname?, lang?}` (no auth) | join with the QR token. `201 {accessToken, expiresAt (epoch ms), robot:{name}, nickname, places[]}`. `403 invalid_qr`, `409 robot_busy` / `robot_unavailable`. With a valid Bearer it returns `200` with the same token (page reload) |
| `DELETE /api/session` | end the session; `{ok, reply}`. The robot drives back to the start point |
| `GET /api/places` | `{places:[{name,label,description,x,y,home}]}` (layout only) |
| `GET /api/state?after=<seq>` | live view, poll every 0.6-1 s: `{seq, messages:[{seq,text}], expiresAt, state, goal_status, destination, at, distance_remaining, reason, options[], robot:{x,y,yaw,available}}` |
| `POST /api/requests` (+ `Idempotency-Key` = `client_request_id`) | the frontend's request: `{text, source: typed\|transcript, proposed_action, client_request_id, place?}`. `201` new task, `200` replay of the same id and payload, `409 conflict` for the same id with another payload, `400 invalid_request`, `429` |
| `GET /api/requests/{request_id}` | task status (see 3) |
| `POST /api/robot/stop` | immediate stop; `{ok, state}` |
| `POST /api/admin/end` (header `X-Admin-Token`) | staff ends the session (only when `--admin-token` is set) |
| `GET /healthz` | health (no secrets). The server serves no pages: the visitor frontend is the UI |

`state` values: `IDLE, AWAITING_INPUT, PREPARING, NAVIGATING, ARRIVED, BLOCKED, STOPPED, RETURNING_HOME, HOLD`. `options` lists what the guest can do now (`place`, `retry`, `stop`, `end`).

## 3. Task lifecycle (what `completed` means)
Task JSON (the frontend's wire shape): `{request_id, client_request_id, status, action, revision, demo:false, reply?, reason?}` plus **optional additions** `place`, `message`, `progress`.
`reply` appears only when `completed` (the frontend's decoder rejects it otherwise); the visitor-safe text for `failed` / `denied` is in `message`.

| request | result |
|---|---|
| `GREET`, `ROBOT_INFO`, `HELP` (or text that means it) | `completed` at once with a `reply` |
| `GO_TO_PLACE` (text like "take me to the front", or `place` + `proposed_action: GO_TO_PLACE`) | `processing` (goal sent / robot getting up) -> `executing` (robot moving; `progress.distance_remaining`) -> `completed` **when the robot has arrived** (`reply` = arrival text) |
| the guest is already there | `completed` ("We are already at ...") |
| robot cannot (low battery, not placed on the map, link down, damped by staff) | `failed`, `reason: robot_unavailable`, `message` says why; nothing was sent |
| Nav2 failed / blocked / timed out | `failed`, `reason: execution_failed` |
| a newer request re-routed it, the guest stopped, or the session ended | `failed`, `reason: execution_failed`, `message` |
| text not understood / unknown place / text and place disagree | `denied`, `reason: unsupported_request`, `message` (honest "I don't know", no invented facts) |
| `STOP_ROBOT` ("stop") / `END_SESSION` ("finish") | `completed` (stop is also available without a request) |

`awaiting_approval` is not used; it could carry a staff approval for escorts later. An escort takes 10-20 s per 2 m at the Nav2 speed limit and is cut off after 180 s, so the
frontend's polling must allow minutes for `GO_TO_PLACE` (its `completed` means arrival). Idempotency is per session; a retry never sends a second goal.

## 4. Frontend: what was done, what is still to agree
Implemented on this branch as an **additive robot mode** (`vite --mode robot`; the demo build, its guards and its tests are untouched): the session call, `POST /api/requests` with the new actions
and optional `place`, the extra response fields (`message`, `progress`, `place`), the destinations layout (dots with rings, no map), the always-visible Stop, the escort bar, the Talk tab (Vapi) and a truthful privacy page.
Still for the frontend owners to agree or do:
1. Fold the robot additions (`robotTasks.ts`) into `tasks/contracts.ts` / `requestApiAdapter.ts` if they prefer one adapter, and confirm the action names `GO_TO_PLACE`, `STOP_ROBOT`, `END_SESSION`.
2. A reviewed **integration profile**: a same-origin `/api` proxy (hosting worker or Tailscale Serve) and a CSP with `connect-src 'self'` for `build:robot` (the demo keeps `connect-src 'none'`). `npm run dev:robot` proxies `/api` for development.
3. Polling interval and timeouts (the robot flow waits up to 240 s for an escort), wording and languages of the replies, and whether voice (Vapi) should be able to start an escort through the companion (a `go_to_place` tool) so that voice and taps share one path.
4. Playwright e2e for robot mode and a test on a real phone.
Open question: keep the guest's confirmation step for escorts (recommended: yes, it is what the frontend's own principle asks for).

## 5. Try it
```bash
ros2 run d1_companion companion --places maps/<map>/places.yaml --join-token <qr-token>        # + a gateway on :8765 (mock: ros2 launch d1_gateway gateway.launch.py mock:=true)
curl -s -XPOST localhost:8780/api/session -d '{"token":"<qr-token>","nickname":"Ana"}'          # -> accessToken
curl -s -XPOST localhost:8780/api/requests -H "Authorization: Bearer $A" -H "Idempotency-Key: r1" \
     -d '{"text":"take me to the front","source":"typed","proposed_action":"GO_TO_PLACE","client_request_id":"r1"}'
curl -s localhost:8780/api/requests/<request_id> -H "Authorization: Bearer $A"
```
With the frontend: `cd frontend && npm run dev:robot`, then open `http://127.0.0.1:5173/join?t=<qr-token>` (the QR code on the robot encodes this URL).
