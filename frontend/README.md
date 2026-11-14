# ClawMetry frontend — Phase 3

Mobile-first visitor onboarding for the SafetyAI Robotics Hackathon. React, Vite, TypeScript, and Tailwind CSS. The Phase 1 avatar, palette, and card design are preserved.

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
- **End Session** clears the active in-memory session, nickname, recording, and typed request, returning to welcome. Browser Back to welcome also ends the session.
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
6. Typed requests are limited to 2000 characters. **Continue** shows a local preview; **Edit request** returns to the field. Nickname and the current demo session are preserved. Nothing is dispatched to a robot or AI service.

Denied permissions, absent/busy microphones, unsupported/insecure browsers, failed recording, empty audio, and playback errors offer a typed path. Canceling a pending native permission prompt cannot dismiss the browser's dialog; dismiss it manually. If permission is granted after Cancel, navigation, expiry, or unmount, newly returned tracks are stopped immediately. Another permission request is blocked until the outstanding one settles.

Switching away from the tab cancels active/pending recording; returning never restarts it automatically. Navigating to privacy, browser Back, End Session, expiry, page hide, refresh, and unmount release recording resources. Review playback pauses when the tab is hidden. Review URLs are revoked on retry/discard, default Continue to text, successful transcription, and leaving the session view. Browser permission settings themselves are controlled by the browser; stopping tracks does not revoke a previously granted site permission. JavaScript timers and cleanup run when the browser schedules them, so real device suspension behavior still needs verification.

The capture implementation follows the browser APIs documented in [getUserMedia](https://developer.mozilla.org/en-US/docs/Web/API/MediaDevices/getUserMedia) and [MediaRecorder](https://developer.mozilla.org/en-US/docs/Web/API/MediaRecorder).

### Optional transcription API preparation

**No endpoint has been verified or connected.** Keep `VITE_TRANSCRIBE_ENDPOINT` blank until the backend teammate confirms the contract and server privacy practices. To deliberately enable a confirmed endpoint, set a root-relative path in `.env.local`, then restart Vite (or rebuild production):

```dotenv
VITE_TRANSCRIBE_ENDPOINT=/api/transcribe
```

Paths are configurable, but cross-origin URLs, protocol-relative URLs, backslashes, query strings, and fragments are rejected. Audio is sent only to a same-origin path; deploy/reverse-proxy the teammate's FastAPI endpoint under that origin. No proxy/backend configuration is added here. Cookies are omitted and redirects are not followed. The UI labels the action **Continue — send for transcription**, displays the endpoint path, and explains that this button uploads audio.

**Proposed wire contract, pending agreement:** `POST` multipart/form-data, with `audio` containing the Blob and a format-based filename, `mime_type` containing the actual recording MIME type (including codec information), and `duration_ms` containing rounded duration. The browser sets the multipart boundary. No nickname or mock session ID is sent. Accept only a successful JSON response with a nonempty string `text` (at most 2000 characters). This is an interface proposal, not a claim that FastAPI already supports it. Runtime response validation rejects malformed/empty responses. Displayed transcripts come only from that response and can be edited before the local preview; test fixture responses exist only in tests.

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

## Production build and routes

```sh
npm run typecheck
npm test
npm run build
npm run preview
```

Build checks TypeScript, bundles local assets to `dist/`, and creates `dist/join/index.html`, `dist/session/index.html`, and `dist/privacy/index.html`. Hosts supporting directory indexes can serve direct/refresh visits without a special SPA rewrite (some redirect `/join` to `/join/`, which the app also handles). The app expects deployment at the domain root, not a URL subdirectory.

For a host without directory-index support, configure internal fallback rewrites for `/join`, `/session`, and `/privacy` to `/index.html`, preserving the requested browser path. Optionally use that fallback for other non-asset routes to show the in-app missing-page screen. Do not rewrite missing assets to HTML. FastAPI deployment/static-serving configuration belongs to the backend teammate; it is not changed here.

Preview normally serves **http://localhost:4173/join**. Deploy `dist/` only when deployment is authorized. On the real host, verify direct `/join` access and refresh from a phone before printing a QR code.

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
npm run build
npm run test:e2e
```

Vitest covers named and guest onboarding, retry errors, config validation, session expiry, wake-from-sleep expiry, ending sessions, privacy navigation, and missing/refresh session handling. QR tests verify validation, file generation, overwrite protection, and encoding/decoding round-trip, using a reserved test-only domain in temporary files.

Playwright runs against the production preview using **installed Google Chrome** (`channel: chrome`); it does not download a browser. Install Chrome separately if absent, or set `PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH` to an existing compatible binary. The preview port 4173 must be free. Tests cover direct `/join` and refresh, a 40-character name, voice/text controls, privacy, ending/rejoining, browser history, empty browser storage, reduced motion, and horizontal overflow at 320×568, 375×667, 390×844, 412×915, and 667×375. Long pages scroll naturally; nickname input text is 16px to avoid iOS focus zoom. Chrome viewport checks do not replace testing on actual iOS Safari and Android devices.

`npm run test:watch` runs the unit tests interactively. Phase 3 unit tests also cover capture transitions, MIME fallback, the 20-second limit, denied/missing/busy devices, pending permission races, stopped tracks, revoked URLs, cancel/retry, hidden tabs, navigation/expiry/End Session, typed editing, playback failure, upload consent, invalid API responses, and request aborts.

Phase 3 browser tests mock `getUserMedia` and `MediaRecorder`; **they never access your physical microphone**. A synthetic silent WAV exercises real local audio playback. Tests cover recording/review/text at 320×568, 390×844, and 667×375, errors and unsupported browsers, permission cancellation, the 20-second cap, navigation, and session cleanup. They check that disconnected journeys make no POST or unexpected external requests. Screenshots are saved under ignored `test-results/`. These tests cannot verify physical devices or the real FastAPI/Whisper service.

## Privacy and integration scope

Nicknames, mock session IDs, expiry times, typed requests, and temporary recordings remain in page memory. There are no cookies, local/session storage, IndexedDB, persistent audio files, analytics, trackers, external fonts, or robot controls. Microphone use is deliberate, time-limited, and stopped when leaving the view or hiding the tab. Transcription is disconnected by default; only an explicitly configured same-site endpoint and the labeled send action permit an audio upload. No audio or transcript is logged by application code. Typed Continue is a local preview, not AI interpretation or robot dispatch. Artwork is local SVG. The local QR generator encodes only the provided link. A hosting provider may retain standard request logs; its practices are unverified. There is no certified GDPR compliance claim or invented backend deletion behavior.

Remaining work: deploy to the real HTTPS host and verify its routes; generate and proof-scan the real QR; agree on the FastAPI session/transcription API and privacy/retention contract; connect real transcription, interpretation, robot responses, SDK, and hardware in a future phase. None of those integrations is simulated as backend authentication.

## Source layout

- `src/App.tsx`: onboarding, session-ready, privacy, and recovery views.
- `src/Artwork.tsx`: preserved Phase 1 robot and shield.
- `src/useRoute.ts`: client navigation and browser history; only paths enter history.
- `src/services/demoSession.ts`: modular mock session creation and expiry rules.
- `src/styles.css`: preserved Phase 1 styling plus responsive session, voice, and text controls.
- `src/voice/recorder.ts`: capture lifecycle, MIME negotiation, 20-second limit, and resource cleanup.
- `src/voice/useVoiceRecorder.ts`: React subscription plus visibility/unmount cleanup.
- `src/voice/VoiceInteraction.tsx`: voice review, explicit upload consent, and local typed preview.
- `src/services/transcription.ts`: optional same-origin multipart transcription adapter.
- `scripts/generate-qr.mjs`: explicit-target local printable SVG generation.
- `scripts/static-routes.mjs`: build-time directory entries for static hosting.
- `src/**/*.test.ts*`, `scripts/*.test.mjs`: unit/integration tests.
- `e2e/visitor.spec.ts`: production-preview mobile viewport checks.
- `.env.example`: documented public demo configuration.
