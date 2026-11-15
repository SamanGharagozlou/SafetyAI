# ClawMetry visitor frontend

React 19 + Vite + TypeScript + Tailwind, entirely inside `frontend/`. Local Demo mode only: no robot control or real backend authentication. The existing public Worker is unchanged by this update. Do not deploy, commit, push, or replace existing ZIP archives as part of this work.

## Run locally

Use Node 24 and npm. From the repository root:

```sh
cd frontend
npm ci --include=dev
npm run dev -- --host 127.0.0.1
```

Open the printed loopback URL with `/join` (normally `http://127.0.0.1:5173/join`). Loopback permits microphone APIs in supported browsers. Join as Guest or use any optional nickname; no real name/account/email is needed.

To inspect the guarded production artifact and its actual local security headers:

```sh
npm run typecheck
npm test
npm run build:demo
npm run audit:dist
npm run test:e2e
npm run preview:static
```

Open `http://127.0.0.1:4173/join` for the static preview. Stop it before browser tests, which manage their own port-4173 server. Playwright uses installed Google Chrome, or an existing binary selected with `PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH`; tests do not download browsers or open physical microphones. `npm run test:watch` watches unit tests. `npm run build` / `npm run preview` remain development integration tools; plain Vite preview does not apply hosting `_headers`.

For a phone, use a trusted HTTPS development host. `npm run dev -- --host 0.0.0.0` exposes Vite on your network, but plain HTTP on a LAN IP intentionally cannot capture audio. A phone’s localhost points to the phone. Do not bypass certificates or browser security. This task does not change the existing deployed demo.

## Conversation journey

1. `/join`: nickname or Guest → temporary in-memory demo session → `/session`.
2. Pre-call: original smiling robot, **Start Conversation**, truthful Demo label. No mic prompt, waveform activity, Mute, End, player, or primary Type action.
3. Start requests permission and opens a full-screen local call. The orb/waveform uses actual analyser RMS. Calibration takes about 500ms; speak after Listening appears. Mute/Unmute and End Call stay anchored at the bottom. The small timer measures conversation time, not a recording countdown.
4. Meaningful speech followed by about 1.2 seconds of silence ends a turn automatically. **Finish turn** is a discreet manual fallback. Each utterance is capped at 20 seconds; the call continues. Quiet buffers roll locally without creating review clips. After 60 seconds without detected speech, the mic pauses.
5. In the default disconnected demo, audio is cleared and a compact manual editor appears within the call. No speech transcript is invented and no audio is sent. Permission/device/browser failures also offer text. Visitors preferring text can expand **Need another way to communicate?** before starting; it is secondary, not a call toolbar button.
6. **Review request** → editable confirmation → **Confirm demo request**. Only confirmation creates an in-memory mock task. **Edit request** and **Cancel request** preserve explicit consent; empty text and expired sessions cannot submit.
7. GREET, ROBOT_INFO and HELP use the existing strict demo allowlist. Compound/movement requests are declined. Statuses queued, processing, awaiting approval, executing, completed, denied, and failed are prominently simulated. No physical robot is connected. Friendly responses are text only, not claimed spoken output.
8. After a terminal demo response, another listening turn starts after 2.5 seconds only in the same active, visible, unmuted call. **Another voice turn** is also available. Safari may require an explicit Unmute gesture if a new AudioContext cannot resume.
9. **Mute** stops tracks, closes analysis, and discards unfinished capture. During review/tasks it keeps that content but prevents automatic capture. **End Call** clears call history/audio/text/tasks and returns to pre-call, preserving the unexpired visitor session. **Leave session** also clears nickname/session and returns to welcome.

See [CALL_INTERFACE.md](./CALL_INTERFACE.md) for architecture, VAD tuning, cleanup guarantees, test evidence, and limitations.

## Sessions, routing, and privacy

`src/services/demoSession.ts` creates random simulated IDs and absolute expiry times, default 600 seconds. IDs are not credentials or backend authorization. Set public `VITE_DEMO_SESSION_SECONDS` to an integer 1–86400 for development; short durations help test expiry. Visitor data stays in page memory. Refresh does not restore it. Leaving the call for `/privacy` releases resources and clears call data; returning preserves only the unexpired visitor session. Browser Back to welcome ends the session.

Static entries/rewrites support `/join`, `/join/`, `/session`, `/session/`, `/privacy`, `/privacy/`. A fresh `/session` redirects to `/join` with a no-session message. Unknown pages and missing APIs/assets remain HTTP 404 with a recovery page. Preserve generated `404.html`, `_headers`, `_redirects`, route directories, and hashed assets together.

No application analytics, trackers, external fonts/audio services, cookies, localStorage, sessionStorage, IndexedDB, service worker, audio/request logging, or persistent visitor files are used. Audio buffers, actual transcripts, nickname, demo session and bounded six-turn history are memory only. End/navigation/expiry releases application references; browser/OS memory is not cryptographically wiped. Browser permissions are managed by the browser, not revoked by stopping tracks. Hosting providers may retain IP/user-agent/request logs; retention is unverified. No legal certification or backend deletion claim is made. The privacy page describes current operation.

## Production environment and hosting

Every `VITE_*` value is public. Never include secrets, privileged tokens, robot addresses, or credentials. Copy `.env.example` only for local development; review values without printing secrets. The guarded `npm run build:demo` rejects unknown public keys, connected transcription, altered task endpoints, and invalid session durations. It disables source maps, generates routes and `demo-config.json`, then audits the artifact.

| Public setting | Guarded Demo value |
| --- | --- |
| `VITE_DEMO_SESSION_SECONDS` | `600` for browser tests/release; 1–86400 supported |
| `VITE_TRANSCRIBE_ENDPOINT` | Unset/empty |
| `VITE_REQUESTS_ENDPOINT` | Unset or `/api/requests`, unwired |
| `VITE_REQUEST_STATUS_ENDPOINT` | Unset or `/api/requests/{request_id}`, unwired |
| Other `VITE_*` | None |

The strict static CSP retains `connect-src 'none'`, same-origin script/style, `media-src 'self' blob:`, and microphone self-only permission policy. Dynamic visualizer values are SVG attributes, not injected inline styles. No API is connected by changing an environment path. A separately reviewed integration profile is required for real endpoints. The artifact pattern audit is useful evidence, not proof against all secrets or dependency compromise.

[DEPLOYMENT_CHECKLIST.md](./DEPLOYMENT_CHECKLIST.md) contains exact static Cloudflare Pages settings, headers, HTTPS/routing checks, physical-phone acceptance tests, QR instructions and rollback. Existing hosting/Worker configuration is preserved. No deployment occurs here. Existing ZIP files are older artifacts and are not regenerated.

## Local QR generation

Uses the installed `qr` library, entirely locally. Supply the actual verified public HTTPS `/join` URL explicitly; there is no default/invented target:

```sh
mkdir -p qr-output
# zsh (bash: read -r -p 'Verified HTTPS /join URL: ' JOIN_URL)
read -r "JOIN_URL?Verified public HTTPS /join URL: "
npm run qr -- "$JOIN_URL" qr-output/clawmetry-join.svg
```

Check the printed target, retain the white quiet zone, print a proof, and scan on both phone platforms. Do not encode nickname/session/token or use third-party QR sites. `--allow-local` is only for explicit local development, never a printed public QR.

## Robot mode (live robot, `--mode robot`)

The same app can talk to the robot companion backend ([docs/companion_api.md](../docs/companion_api.md), [docs/companion_state_machine.md](../docs/companion_state_machine.md)). It is a **separate mode**: the default dev server and
`build:demo` are unchanged (same code paths, same guards, same tests), and nothing in robot mode weakens them.

```sh
npm run dev:robot            # vite --mode robot; /api is proxied to http://127.0.0.1:8780 (override: D1_COMPANION_URL=http://host:port)
npm run build:robot          # static build for the integration profile (needs a same-origin /api proxy and a CSP with connect-src 'self')
```
Open `/join?t=<QR token>` (the backend prints the token; the QR code on the robot encodes this URL). The token is read once, kept in memory, and removed from the address bar.

What it adds (all in `src/robot/`, styled with the existing palette in `styles.css`):
* **Join** with the QR token (`POST /api/session`), a backend-issued Bearer credential in memory only, ended on *Leave session*. Clear messages for a wrong QR code, a busy robot and an unavailable robot.
* **Where to?** a "radar" layout of the destinations: distance rings around the start point, glowing destination nodes with name pills, an animated route and the robot's position (SVG, works under the strict CSP, motion stops under reduced-motion). **No floor plan or map is ever requested or drawn.** Swipeable destination cards show "you are here" and the distance to each place. Tapping a dot or a card opens a bottom sheet ("Go to Left, 2 m?"); nothing is sent before the guest confirms. If the robot cannot start a walk (not placed on the map, low battery, paused) the app says so and disables the places.
* **Journey card** at the top while the robot moves: destination, distance left, a track with the robot travelling to the destination pin, and a **Stop** button (calls `POST /api/robot/stop` at once, never behind a confirmation). Arrival, problems and answers appear as sheets with a clear next step.
* **Typed requests** ("What can you do?", "Take me to the left") through the same request contract as `requestApiAdapter.ts` (`POST /api/requests` + `Idempotency-Key`, polling `GET /api/requests/{id}`), reviewed and confirmed first. The backend decides what the text means.
* **Talk** tab with the live Vapi call, when Vapi is configured (the call stays up while the guest looks at the places). *Unverified:* not tested with a real call.
* A **robot-mode privacy page** that states what really happens (data goes to the robot's server, memory only, no recordings, layout only).

The demo adapter, `CallController`, `RequestFlow` and their tests are not modified; robot mode has its own `robotApi.ts`, `robotTasks.ts` (the frontend's decoder rules plus the robot additions: actions `GO_TO_PLACE`, `STOP_ROBOT`,
`END_SESSION`, optional `place`, `message`, `progress`) and components. Folding them into `tasks/contracts.ts` is a clean-up for the frontend owners once the contract is agreed.
Tests: `npm test` (54 new tests in `src/robot/`: decoder rules, API client, layout, escort bar, request flow, whole screen against a fake backend, app in robot mode, demo unchanged). *Not covered:* Playwright e2e and a real phone.

## Backend preparation

[API_INTEGRATION.md](./API_INTEGRATION.md) describes **proposed, unverified** contracts. The default transcription adapter is disconnected and tasks always use `createMockTaskAdapter`.

A separately verified development transcription integration may use root-relative `VITE_TRANSCRIBE_ENDPOINT=/api/transcribe`. Each captured turn then requires its own **Send audio for transcription** authorization; optional playback is collapsed under Review audio before sending. Only a real validated `{text}` result becomes editable transcript text. Recording, automatic VAD completion, task confirmation, and subsequent turns never silently upload audio. Errors/timeouts offer manual entry and acknowledge that a server may already have received the audio. The current transcription proposal has no verified authentication mechanism; agree and implement it before real connection. Static Demo builds deliberately reject this setting.

Future task wiring must use backend-issued credentials separately from nicknames/demo IDs, server idempotency, actual status/approval semantics, safety enforcement, retention/error contracts, and reviewed CSP. Frontend End/Mute/Abort cannot cancel a real executing robot action or guarantee server deletion. No backend, SDK, TTS, real VAD speech model, physical commands, or privileged authorization is implemented.
