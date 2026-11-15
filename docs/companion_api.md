# Companion backend API for the visitor frontend

Backend half of the guest flow, built to the contract the frontend already prepared in [frontend/API_INTEGRATION.md](../frontend/API_INTEGRATION.md)
(`requestApiAdapter.ts`, `contracts.ts`). Implementation: `ros2_ws/src/d1_companion/d1_companion/server.py`. State machine and safety rules:
[companion_state_machine.md](companion_state_machine.md). **Not connected to the frontend yet; the changes the frontend needs are listed in section 4.**
Status: built and tested against the mock robot and in simulation, **not run with the real frontend, phone or robot** (*unverified*).

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
| `GET /`, `/phone.js`, `/phone.css`, `/healthz` | developer test client and health (no secrets) |

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

## 4. What the frontend needs to change (to agree with the frontend developer)
1. `contracts.ts`: add `GO_TO_PLACE`, `STOP_ROBOT`, `END_SESSION` to the action vocabulary (its decoder rejects any other `action` as `invalid_response`) and allow `place` in the submission.
2. `requestApiAdapter.ts`: send the optional `place`; read the optional `message` (failed/denied) and `progress`; allow long-running `executing`; keep 401/403 -> `session_expired`.
3. Session: obtain the credential with `POST /api/session` (QR token from the `/join` URL, never from the nickname); use the returned `expiresAt`.
4. A destinations view: draw the `places` as dots with rings from `GET /api/places` and the robot from `GET /api/state` (the developer test client `phone.js` is a reference implementation, ~200 lines, no map). Poll `/api/state`.
5. A Stop button calling `POST /api/robot/stop`, not the request flow.
6. A reviewed integration profile: the static demo build requires `connect-src 'none'` and rejects changed endpoints; a same-origin `/api` proxy and a CSP that allows it are needed.
Open questions: names of the new actions; polling interval and timeouts; whether the guest's confirmation step stays for escorts (recommended: yes); wording and languages of the replies.

## 5. Try it
```bash
ros2 run d1_companion companion --places maps/<map>/places.yaml --join-token <qr-token>        # + a gateway on :8765 (mock: ros2 launch d1_gateway gateway.launch.py mock:=true)
curl -s -XPOST localhost:8780/api/session -d '{"token":"<qr-token>","nickname":"Ana"}'          # -> accessToken
curl -s -XPOST localhost:8780/api/requests -H "Authorization: Bearer $A" -H "Idempotency-Key: r1" \
     -d '{"text":"take me to the front","source":"typed","proposed_action":"GO_TO_PLACE","client_request_id":"r1"}'
curl -s localhost:8780/api/requests/<request_id> -H "Authorization: Bearer $A"
```
Open `http://127.0.0.1:8780/?t=<qr-token>` for the developer test client.
