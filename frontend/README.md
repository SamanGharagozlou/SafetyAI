# ClawMetry frontend — Phase 5A deployment readiness

Mobile-first visitor onboarding for the SafetyAI Robotics Hackathon. React, Vite, TypeScript, and Tailwind CSS. The Phase 1 avatar, palette, and card design are preserved.

## Static HTTPS deployment readiness

See [DEPLOYMENT_CHECKLIST.md](./DEPLOYMENT_CHECKLIST.md) for exact Cloudflare Pages settings, environment rules, route/HTTPS acceptance tests, real-phone checks, QR generation, rollback, and privacy/security limitations. **Nothing has been deployed and no production URL is assumed.**

For a backend-free deployable artifact:

```sh
cd frontend
npm ci --include=dev
npm run typecheck
npm test
npm run build:demo
npm run test:e2e
```

`build:demo` validates the public environment, requires disconnected transcription/default unwired request paths, creates a non-sensitive demo manifest, disables source maps, packages static routes/security rules, and audits the output for unreviewed files, common credentials/private addresses, and URL origins. This is a best-effort audit, not a guarantee against every secret or first-party compromise. Deploy only `frontend/dist/` after separate authorization. The publishing build uses Vite's `demo` mode, not `.env.production`.

`public/_headers` enforces CSP with **`connect-src 'none'`**, same-origin scripts/styles, local Blob audio playback, top-level-only microphone permission policy, framing restrictions, and other headers. These headers deliberately block API connections on the static demo. Future verified integration requires a different reviewed deployment profile/policy. Optional transcription remains available for development; it cannot be enabled in the Phase 5A release by setting an endpoint.

`public/_redirects` covers exact `/join`, `/session`, and `/privacy` routes (including trailing slashes). A generated HTTP 404 entry prevents missing API/asset URLs from returning fake SPA success. Browser tests now use `npm run preview:static`, a loopback-only read-only QA server applying this project's header/routing rules. It does not emulate Cloudflare's edge, implement a backend, deploy anything, or verify TLS.

## Run locally

Use Node.js 22.12+ (Node 24 recommended) and npm. From the repository root:

```sh
cd frontend
npm ci
npm run dev
```

Open **http://localhost:5173/join** (or the port Vite prints). `/` also opens welcome. Direct visits and refresh on `/join` work with Vite. For a phone on the same trusted network:

```sh
npm run dev -- --host 0.0.0.0
```

Use the actual network URL printed by Vite, with `/join` appended. A phone's `localhost` points to the phone, not your computer. This command exposes the dev server to your network. Microphone capture requires a secure context: HTTPS, or localhost/loopback for desktop development. Plain HTTP on a LAN IP supports the typed path only. Test phone recording on the actual HTTPS deployment (or a trusted HTTPS development host); do not bypass browser security or ignore certificate errors.

## Visitor journey and demo sessions

- Enter an optional nickname (40 characters maximum), leave it blank, or choose **Continue as Guest**. Whitespace-only names become Guest.
- The session-ready screen says **Demo mode**, greets the visitor, explains the simulation, and offers deliberate browser recording or typed input. No robot is connected.
- **End Session** clears the active in-memory session, nickname, recording, typed request, and simulated task, returning to welcome. Browser Back to welcome also ends the session.
- Privacy opens a separate view; returning preserves an unexpired session but clears recording/transcript/request state and releases the microphone. Expiry while reading privacy clears the session without interrupting the page; Back then returns to welcome with an expiry message.
- Refreshing `/join` starts fresh. Refreshing `/session`, opening it in a new tab, or navigating forward to an ended session returns to welcome. No session is restored.
- Unknown client routes provide a welcome-page recovery button (requires the hosting fallback below for direct unknown URLs).
- Failed demo-session creation produces an accessible error and allows retry. Page navigation moves focus to the heading. Controls support keyboard navigation and reduced motion.

`src/services/demoSession.ts` is an explicitly simulated synchronous service. It creates random `demo-…` IDs locally and records an absolute expiry timestamp. IDs are not credentials; no backend has authenticated or authorized visitors. State lives only in React. There is no API call or server session to delete. Real backend lifecycle and error contracts must be agreed with the integration teammate later.

### Configure expiry

Default: **600 seconds (10 minutes)**. Copy `.env.example` to `.env.local` and edit:

```dotenv
VITE_DEMO_SESSION_SECONDS=30
```

Allowed range: integer seconds from 1 through 86400. Invalid values prevent demo-session creation and show the retry/help message. Restart Vite after changing the value. Production values are embedded at build time; this is public configuration, not a secret. The app rechecks the absolute deadline on focus and visibility changes to handle sleeping phones. Automatic UI updates wait until the browser resumes execution.

## Phase 3: local recording and typed requests

1. Join with a nickname or as Guest. The app does not request microphone access on mount or automatically check it by recording.
2. Choose **Start Recording** and respond to the browser's permission prompt. Supported WebM/Opus, MP4, Ogg/Opus, and WebM types are checked with `MediaRecorder.isTypeSupported`; if none match, the browser recorder's default format is used and its actual MIME type is retained.
3. Recording shows a live timer and animated indicator, with **Stop Recording** and **Cancel**. It stops automatically at 20 seconds. Reduced-motion preferences suppress animation.
4. **Stop Recording** immediately releases microphone tracks, then prepares the final Blob and local playback URL. No automatic playback or upload occurs. **Retry recording** discards the old clip and deliberately starts another capture; **Discard** clears the Blob and revokes its URL.
5. With default configuration, **Continue** says **Transcription not connected**, clears the unsent audio, and focuses the typed-request alternative. No speech is invented or recognized. You can also choose **Type a request** at any time, which cancels capture and clears audio.
6. Typed requests are limited to 2000 characters. **Continue** opens confirmation; **Edit request** returns to the field. **Cancel request** clears the draft. **Confirm demo request** explicitly submits to the in-memory task simulation. Nickname and the current demo session are preserved. Nothing is dispatched to a robot or AI service.

Denied permissions, absent/busy microphones, unsupported/insecure browsers, failed recording, empty audio, and playback errors offer a typed path. Canceling a pending native permission prompt cannot dismiss the browser's dialog; dismiss it manually. If permission is granted after Cancel, navigation, expiry, or unmount, newly returned tracks are stopped immediately. Another permission request is blocked until the outstanding one settles.

Switching away from the tab cancels active/pending recording; returning never restarts it automatically. Navigating to privacy, browser Back, End Session, expiry, page hide, refresh, and unmount release recording resources. Review playback pauses when the tab is hidden. Review URLs are revoked on retry/discard, default Continue to text, successful transcription, and leaving the session view. Browser permission settings themselves are controlled by the browser; stopping tracks does not revoke a previously granted site permission. JavaScript timers and cleanup run when the browser schedules them, so real device suspension behavior still needs verification.

The capture implementation follows the browser APIs documented in [getUserMedia](https://developer.mozilla.org/en-US/docs/Web/API/MediaDevices/getUserMedia) and [MediaRecorder](https://developer.mozilla.org/en-US/docs/Web/API/MediaRecorder).

### Optional transcription API preparation

**No endpoint has been verified or connected.** Keep `VITE_TRANSCRIBE_ENDPOINT` blank until the backend teammate confirms the contract and server privacy practices. To deliberately enable a confirmed endpoint, set a root-relative path in `.env.local`, then restart Vite (or rebuild production):

```dotenv
VITE_TRANSCRIBE_ENDPOINT=/api/transcribe
```

This is a development/future integration option; the audited static release rejects any nonempty transcription endpoint and its CSP blocks network connections. Paths are configurable, but cross-origin URLs, protocol-relative URLs, backslashes, query strings, and fragments are rejected. Audio is sent only to a same-origin path; deploy/reverse-proxy the teammate's FastAPI endpoint under that origin. No proxy/backend configuration is added here. Cookies are omitted and redirects are not followed. The UI labels the action **Continue — send for transcription**, displays the endpoint path, and explains that this button uploads audio.

**Proposed wire contract, pending agreement:** `POST` multipart/form-data, with `audio` containing the Blob and a format-based filename, `mime_type` containing the actual recording MIME type (including codec information), and `duration_ms` containing rounded duration. The browser sets the multipart boundary. No nickname or mock session ID is sent. Accept only a successful JSON response with a nonempty string `text` (at most 2000 characters). This is an interface proposal, not a claim that FastAPI already supports it. Runtime response validation rejects malformed/empty responses. Displayed transcripts come only from that response and can be edited before confirmation; test fixture responses exist only in tests.

Uploads time out after 30 seconds. Failure keeps the review available for retry or typed input. Cancel transcription, Discard, switching input method, navigation, expiry, or End Session abort the browser request and ignore late responses. **Abort does not guarantee that the server has not received/processed audio, and no server deletion is promised.** Clearing browser memory cannot clear backend copies.

Backend integration still needs confirmation of the multipart field names, accepted containers/codecs, duration/size limits, response schema, authentication/session binding, errors, HTTPS routing, retention/deletion, and transcript/audio logging practices. The mock session ID is not an authorization credential. No Whisper, AI interpretation, FastAPI, or robot SDK implementation is added.

### Manual local browser test

```sh
cd frontend
npm ci
npm run dev
```

Open `http://localhost:5173/join` in a current browser with MediaRecorder support (use the port Vite prints). With default blank transcription configuration:

- Join and confirm that no microphone prompt appears yet.
- Start Recording, allow access, speak briefly, and Stop. Confirm the browser's active recording indicator clears; play the clip locally.
- Retry, Cancel, and Discard. Leave one recording running to verify automatic stop at 20 seconds.
- Continue after review: verify **Transcription not connected**, no network POST, and focus in the empty typed request. Type, Continue, and Edit request; no robot action should occur.
- Repeat with browser microphone permission blocked, then reset permission via the browser's site settings if desired. Text input must still work.
- While recording, open privacy, press browser Back, switch tabs, or End Session. Confirm capture stops and never restarts on its own. Temporarily set `VITE_DEMO_SESSION_SECONDS=5`, restart, and verify expiry cleanup, then restore the normal duration.
- For iPhone Safari/Android Chrome, use a real trusted HTTPS URL; plain LAN HTTP will intentionally disable capture. Check narrow portrait and short landscape layouts, native playback, permission prompts, and device interruption behavior.

## Phase 4: review, confirmation, and simulated tasks

The complete journey is **Join → Speak or Type → Review → Confirm → Demo task status → Friendly text response**. Actual transcription is used only if the separately configured transcription adapter returns text. Otherwise the visitor types manually; no recognized speech is invented.

Try **Hello**, **Robot info**, or **Help**. The demo vocabulary is deliberately small:

| Demo action | Example input | Simulated result |
| --- | --- | --- |
| `GREET` | Hello / Say hello / GREET | Friendly text greeting |
| `ROBOT_INFO` | Robot info / Tell me about yourself / ROBOT_INFO | Introduction to this robot demo |
| `HELP` | Help / What can I ask / HELP | Supported request suggestions |

The classifier accepts only exact normalized phrases in `src/tasks/vocabulary.ts`, not substrings or model-generated commands. “Hello then walk forward” is unsupported, even if a forged proposed action says GREET. The mock independently resolves the request. All other commands, including locomotion, are declined. This frontend allowlist is **not** real authorization; the real backend remains the final authority.

On confirmation, check the request and proposed demo response. **Edit request** returns to editing; **Cancel request** clears it without submitting. **Confirm demo request** is the only task-submission action. A synchronous submission lock and stable client request ID prevent accidental duplicates. The mock also deduplicates identical client IDs and rejects a changed payload with a reused key. No task endpoint is called by the app.

The default simulation follows `queued → processing → awaiting_approval → executing → completed`, with simulated approval and execution explicitly labeled. Only `completed` has a friendly demo text response. A mock request ID starts with `demo-request-`; it is not a robot command ID or authorization credential. Status observations have monotonic revisions; older results cannot overwrite a newer view.

Open **Try a demo outcome** before confirming to exercise **Friendly response**, **Approval denied**, **Task failure**, **Robot unavailable**, **Status timeout**, or **Service unavailable**. These choices change only a local simulation; they cannot grant real approval. Unsupported requests are declined regardless of the chosen outcome. The UI supports all seven task statuses: `queued`, `processing`, `awaiting_approval`, `executing`, `completed`, `denied`, and `failed`.

Task status is checked sequentially, roughly every 500ms plus mock latency. Default phase steps are 900ms; a response usually completes in about four seconds. An absolute 12-second frontend deadline handles stalled creation/status. Timeout does not pretend the task completed or change its last known status to a fabricated backend failure. Task identity, client request ID, and revision guard stale results. Failed/unavailable outcomes never display a completed response.

**Start another interaction** clears the old local simulation and draft, preserving the demo visitor session. Leaving for privacy, browser Back, refresh, End Session, expiry, or unmount stops polling, aborts pending frontend work, and disposes the in-memory mock. A final expiry check blocks confirmation, polling, audio upload, and late results even before suspended session timers resume. No request text, task ID, nickname, or token goes into the URL or browser storage. Leaving this demo does not assert cancellation or deletion on a future real backend.

### Manual task walkthrough

```sh
cd frontend
npm ci
npm run dev
```

1. Open `http://localhost:5173/join`, then join with a nickname or as Guest.
2. Choose **Type a request**, enter `Hello`, and Continue. Verify no task ID exists yet.
3. Edit to `Robot info`, Continue, and select **Confirm demo request**. Watch the labeled demo states through the final text response.
4. Start another interaction. Enter `Help`, Continue, then Cancel; verify the draft clears and the visitor session remains.
5. Try each demo outcome under the expandable selector. Try `Walk forward` and verify it is declined, not executed.
6. End Session during a pending task. Repeat with a short configured session duration and verify expiry clears the interaction.
7. Optionally record audio with default disconnected transcription: Continue clears the clip and opens manual text; review and confirm separately. No automatic task submission or invented transcript should appear.

The prepared request/status adapter is separate and **never selected by this UI**, even when `VITE_REQUESTS_ENDPOINT` and `VITE_REQUEST_STATUS_ENDPOINT` are set. See [API_INTEGRATION.md](./API_INTEGRATION.md) for exact proposed fields, authorization, idempotency, and required backend decisions.

## Production build and routes

```sh
npm run typecheck
npm test
npm run build
npm run preview
```

This standard developer build checks TypeScript, bundles local assets to `dist/`, and creates `dist/join/index.html`, `dist/session/index.html`, and `dist/privacy/index.html`. Hosts supporting directory indexes can serve direct/refresh visits without a special SPA rewrite (some redirect `/join` to `/join/`, which the app also handles). The app expects deployment at the domain root, not a URL subdirectory.

For Cloudflare Pages, the included `_redirects` already provides exact route rewrites. A generated `404.html` boots the recovery screen for unknown routes while keeping HTTP 404; missing API/assets are not rewritten to success. The Phase 5A publishing command is `npm run build:demo`, not the developer build above. FastAPI deployment/static-serving configuration belongs to the backend teammate; it is not changed here.

Vite preview normally serves **http://localhost:4173/join** but does not apply hosting rules. Use `npm run build:demo` then `npm run preview:static` for the policy-aware local QA server at **http://127.0.0.1:4173/join**. Deploy `dist/` only when deployment is authorized. On the real host, verify direct `/join` access and refresh from a phone before printing a QR code.

## Generate a QR code locally

The script uses the maintained [paulmillr/qr library](https://github.com/paulmillr/qr) (`qr` on npm). It runs entirely on your computer; no QR-generation website, redirect service, or external QR processing is used. It requires **both an explicit target URL and SVG output path**, with no default target. It prints the exact encoded URL, uses a four-module quiet zone, black on white, and quartile error correction. Existing files are not overwritten.

After deployment to a **real HTTPS URL**, supply that actual site's `/join` URL:

```sh
mkdir -p qr-output
read -r "JOIN_URL?Paste the deployed HTTPS URL ending in /join: "
npm run qr -- "$JOIN_URL" qr-output/clawmetry-join.svg
```

The `read` command above is for zsh. In bash use `read -r -p 'Deployed HTTPS /join URL: ' JOIN_URL`. Never put a nickname, token, or session ID in the QR URL. The script rejects query parameters, fragments, credentials, paths other than `/join`, and local/non-HTTPS targets by default. It validates URL shape, **not DNS ownership, deployment success, or reachability**. It cannot tell whether a supplied public hostname is real, so verify it yourself. No final public QR is supplied because a deployed URL has not been provided.

For an intentional local development target only:

```sh
read -r "JOIN_URL?Paste the actual reachable development URL ending in /join: "
npm run qr -- "$JOIN_URL" qr-output/development-only.svg --allow-local
```

`--allow-local` explicitly permits local/HTTP targets; the exact target and a development warning are printed. A localhost QR will not reach your computer from another device. Use the actual reachable LAN address. Do not print this development QR for event visitors.

Open the SVG in a local viewer and print without cropping its white border, at least 35 mm square for a short URL (larger for dense/long URLs). Scan a proof print with the actual visitor phones before final use. Generated outputs in `qr-output/` are ignored by Git.

## Tests and mobile checks

```sh
npm run typecheck
npm test
npm run build:demo
npm run test:e2e
```

Vitest covers named and guest onboarding, retry errors, config validation, session expiry, wake-from-sleep expiry, ending sessions, privacy navigation, and missing/refresh session handling. QR tests verify validation, file generation, overwrite protection, and encoding/decoding round-trip, using a reserved test-only domain in temporary files.

Playwright runs against the built artifact through the policy-aware static QA server using **installed Google Chrome** (`channel: chrome`); it does not download a browser. Install Chrome separately if absent, or set `PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH` to an existing compatible binary. The preview port 4173 must be free. Tests cover direct `/join` and refresh, a 40-character name, voice/text controls, privacy, ending/rejoining, browser history, empty browser storage, reduced motion, and horizontal overflow at 320×568, 375×667, 390×844, 412×915, and 667×375. Long pages scroll naturally; nickname input text is 16px to avoid iOS focus zoom. Chrome viewport checks do not replace testing on actual iOS Safari and Android devices.

`npm run test:watch` runs the unit tests interactively. Phase 3 unit tests also cover capture transitions, MIME fallback, the 20-second limit, denied/missing/busy devices, pending permission races, stopped tracks, revoked URLs, cancel/retry, hidden tabs, navigation/expiry/End Session, typed editing, playback failure, upload consent, invalid API responses, and request aborts.

Phase 3 browser tests mock `getUserMedia` and `MediaRecorder`; **they never access your physical microphone**. A synthetic silent WAV exercises real local audio playback. Tests cover recording/review/text at 320×568, 390×844, and 667×375, errors and unsupported browsers, permission cancellation, the 20-second cap, navigation, and session cleanup. They check that disconnected journeys make no POST or unexpected external requests. Screenshots are saved under ignored `test-results/`. These tests cannot verify physical devices or the real FastAPI/Whisper service.

Phase 4 adds tests for explicit confirmation, editing/canceling, duplicate submission, every task state/outcome, independent allowlist checks, unsupported requests, polling failures, absolute timeouts, session expiry, stale/late results, request identity/revisions, separate authorization credentials, endpoint validation, and actual returned-transcript confirmation. Phase 4 had **143 unit/integration tests** and **33 browser tests**; Phase 5A adds 25 environment/artifact checks and 10 deployment browser checks, for **168 unit/integration tests** and **43 browser tests**. All earlier tests remain unchanged.

The task browser journeys verify confirmation, awaiting-approval, and completion layouts at 320×568, 390×844, and 667×375; mock task journeys make no POSTs or unexpected external requests. Existing onboarding and media viewport checks remain in the suite. Production build and typecheck pass. Physical iPhone/Android hardware, actual microphone devices, and real FastAPI services still require integration testing.

## Privacy and integration scope

Nicknames, mock session IDs, expiry times, typed requests, and temporary recordings remain in page memory. There are no cookies, local/session storage, IndexedDB, persistent audio files, analytics, trackers, external fonts, or robot controls. Microphone use is deliberate, time-limited, and stopped when leaving the view or hiding the tab. Transcription is disconnected by default; only an explicitly configured same-site endpoint and the labeled send action permit an audio upload. No audio or transcript is logged by application code. Typed Continue opens confirmation. Only Confirm demo request submits to the in-memory task service; it performs no AI interpretation or robot dispatch. Mock tasks and IDs stay in memory and are cleared with the interaction/session; they are not logged. Artwork is local SVG. The local QR generator encodes only the provided link. A hosting provider may retain standard request logs; its practices are unverified. There is no certified GDPR compliance claim or invented backend deletion behavior.

Remaining work: deploy to the real HTTPS host and verify its routes; generate and proof-scan the real QR; agree on the FastAPI session/transcription/request/status APIs and privacy/retention contract; connect real transcription, interpretation, robot responses, SDK, and hardware in a future phase. None of those integrations is simulated as backend authentication.

## Source layout

- `src/App.tsx`: onboarding, session-ready, privacy, and recovery views.
- `src/Artwork.tsx`: preserved Phase 1 robot and shield.
- `src/useRoute.ts`: client navigation and browser history; only paths enter history.
- `src/services/demoSession.ts`: modular mock session creation and expiry rules.
- `src/styles.css`: preserved Phase 1 styling plus responsive session, voice, and text controls.
- `src/voice/recorder.ts`: capture lifecycle, MIME negotiation, 20-second limit, and resource cleanup.
- `src/voice/useVoiceRecorder.ts`: React subscription plus visibility/unmount cleanup.
- `src/voice/VoiceInteraction.tsx`: voice review, explicit upload consent, typed editing, and entry to request confirmation.
- `src/tasks/contracts.ts`: typed task, submission, and session context contracts.
- `src/tasks/vocabulary.ts`: exact-phrase demonstration allowlist.
- `src/tasks/mockTaskAdapter.ts`: network-free task lifecycle and demo outcomes.
- `src/tasks/RequestFlow.tsx`: confirmation, status conversation, expiry guards, polling, and cleanup.
- `src/tasks/requestApiAdapter.ts`: configurable, validated, **unwired** future request/status API adapter.
- `API_INTEGRATION.md`: exact proposed FastAPI contract and remaining integration decisions.
- `src/services/transcription.ts`: optional same-origin multipart transcription adapter.
- `scripts/generate-qr.mjs`: explicit-target local printable SVG generation.
- `scripts/static-routes.mjs`: build-time directory entries for static hosting.
- `src/**/*.test.ts*`, `scripts/*.test.mjs`: unit/integration tests.
- `e2e/visitor.spec.ts`, `e2e/voice.spec.ts`, `e2e/tasks.spec.ts`: production-preview visitor, mocked-media, and task journeys.
- `.env.example`: documented public demo configuration.
- `config/deployment.ts`: public-environment validation for the demo release.
- `public/_headers`, `public/_redirects`: static Cloudflare Pages policy/routing.
- `scripts/audit-dist.mjs`: deployment artifact allowlist, pattern checks, and digest.
- `scripts/serve-static.mjs`: loopback-only read-only QA of the built static policy.
- `DEPLOYMENT_CHECKLIST.md`: hosting, phone acceptance, QR, rollback, and audit findings.
