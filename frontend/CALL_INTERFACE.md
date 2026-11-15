# Final conversational voice interface — local implementation report

Branch: `feat/visitor-qr-voice-ui`. Only necessary frontend sources, tests and documentation changed. Existing frontend work was inspected and extended. No backend/robot SDK/safety system changes, commits, merges, pushes, deployments, production URL edits or archive replacements. The deployed Worker remains unchanged. Existing untracked root `.DS_Store`, `clawmetry-pages.zip` and `clawmetry-call-ui.zip` are preserved.

## Implemented journey

Guest/nickname onboarding and memory-only session → pre-call smiling robot and Start Conversation → explicit microphone permission → full-screen local call → calibrated microphone-driven speech-turn detection → automatic turn completion → actual transcript only after per-turn authorized configured upload, otherwise manual text in-call → review/edit/cancel → explicit confirmation → existing simulated allowlisted task → friendly Demo text response → another listening turn within the same call → End Call.

Mute/Unmute and red End Call are the only persistent primary active-call controls. Finish turn is a discreet fallback. No old mode toolbar, recording countdown/details card, Stop Recording form, permanent Continue button, or main audio player remains. Optional development transcription playback is collapsed under per-turn upload consent. Default static Demo retains no clip for playback and makes no API call.

## Components and state architecture

| File | Responsibility |
| --- | --- |
| `src/call/CallScreen.tsx` | Dedicated pre-call and active-call view; status, compact history, manual fallback, upload consent, review/confirmation, anchored controls and view lifecycle |
| `src/call/CallController.ts` | One call/capture/upload/task owner; external store for React; session guards, bounded history, generations, duplicate submission lock, polling/deadlines, next-turn scheduling and cleanup |
| `src/call/state.ts` | Typed states and explicit permitted transitions with truthful status labels |
| `src/call/vad.ts` | Bounded noise-floor calibration and amplitude threshold/hysteresis turn detector, independent of React |
| `src/call/audioMonitor.ts` | Web Audio context/source/analyser, PCM RMS sampling, interruption detection and resource disposal; never connects mic to speakers |
| `src/call/CallVisualizer.tsx` | Original robot, teal orb, actual level-driven SVG waveform; separate processing/response styles and reduced motion |
| `src/voice/recorder.ts` | Existing MIME negotiation/MediaRecorder lifecycle extended for per-turn reuse, byte bounds and interruptions |
| `src/voice/VoiceInteraction.tsx` | Compatibility re-export of CallScreen; old recorder UI removed |

States: `idle`, `requesting_permission`, `listening`, `speech_detected`, `finalizing_turn`, `awaiting_transcription`, `awaiting_review`, `awaiting_confirmation`, `processing`, `responding`, `muted`, `error`, `ended`. The store separately retains review/task content state while muted. Generation guards isolate capture, transcription, task polling and the whole call. Late analyser samples, permission grants and responses cannot resurrect an ended call. Confirm is locked before any async work; session expiry is checked before operations and after async responses. HTTP task integration remains unwired; only explicitly Demo adapter results are accepted.

## VAD tuning and resource bounds

`DEFAULT_VAD` in `src/call/vad.ts` is configurable through `CallOptions.vad`:

| Setting | Default |
| --- | --- |
| Noise calibration | 500ms, maximum 100 stored RMS samples |
| Minimum sustained above-release activity | 250ms |
| End-of-turn silence | 1200ms |
| Onset threshold | max(0.012 RMS, calibrated floor × 3) |
| Release threshold | max(0.008 RMS, floor × 1.8), capped below onset |
| Calibration floor | Lower 30th percentile; at least 0.002 RMS |
| Quiet floor adaptation | Slow bounded update; speech does not raise its own threshold |
| Maximum utterance / quiet buffer | 20 seconds |
| Audio Blob chunk bound | 8 MiB; excess fails safely and releases capture |
| Idle pause | 60 seconds without confirmed speech |
| Sampling / analysis window | At most 20Hz via animation frames; 1024 PCM samples |
| Waveform / history | 16 levels; at most 6 confirmed turns, requests ≤2000 characters |
| Response pause before another turn | 2500ms, only active/visible/unmuted/unexpired/free of pending work |
| Task / transcription deadline | 12 seconds / 30 seconds |

Speech candidates reset the silent prelude on the same stream. Short bursts are rejected. Sustained silence never creates an empty review clip or fake transcript. Quiet buffers are discarded/restarted without a new permission request. The maximum utterance finalizes only that turn. Manual Finish is available for noisy rooms or missing Web Audio. These thresholds estimate activity; they cannot identify speakers or distinguish all music/crowd noise from visitor speech. Tune on actual devices/venue noise; wait for calibration before speaking.

## Cleanup and privacy behavior

- Before Start: no getUserMedia, AudioContext, permission query, audio capture or upload. Text-only opt-out never activates these APIs.
- Mute during capture: immediately cancel/discard the unfinished recorder, stop all stream tracks, disconnect analysis, cancel animation frames, close the context, clear sample/chunk buffers. No background capture. Review/task content can remain muted, with automatic next-turn capture blocked.
- Turn finish: close analyser/context and stop tracks immediately, then await bounded native final data. Disconnected transcription clears Blob/object URL immediately. Configured development transcription retains only one clip for explicit per-turn send authorization; no automatic upload.
- End Call: stop capture, release nodes/context, cancel timers, revoke URLs, abort/ignore uploads and task callbacks, clear history/draft/transcript/task/audio. Keep only the underlying unexpired visitor session. Leave session additionally clears nickname/session.
- Navigation, privacy, browser Back, expiry, pagehide and unmount dispose the call. Tab hiding pauses capture/tracking, clears retained audio and requires a deliberate resume. Track end/mute, permission revocation where exposed, AudioContext interruption and recorder failure stop truthful Listening. Unsupported permission-query APIs fall back to track events.
- Pending native permission dialogs cannot be programmatically dismissed. A late granted stream is stopped immediately, and overlapping outstanding prompts are blocked across remounts.
- Optional playback pauses/detaches on clip removal; its URL is revoked. No automatic sound playback, synthetic speech or claimed spoken reply.
- No persistent visitor/audio storage, logs, tracking, external speech service, backend or physical command is introduced. Browser/OS buffers and scheduling remain platform-dependent; local cleanup is not a secure memory wipe or a server deletion guarantee.

End/Abort cannot undo a real server submission or executing physical action. Current tasks are entirely local simulations. Uploaded audio may already have reached a separately configured server; cancellation/deletion is not claimed. Static Demo still enforces `connect-src 'none'`, blank transcription configuration, reviewed public environment keys, and existing routing/headers.

## Validation and screenshots

Current local validation:

- `npm run typecheck`: passed.
- `npm test`: **246 tests passed, 17 files**.
- `npm run test:e2e`: **52 browser tests passed, 0 failed, 0 skipped, 0 flaky** (18.31s); mocked media only, no physical microphone.
- `npm run build:demo` and `npm run audit:dist`: passed, 10 audited files, no source maps or detected credential/private-address/unreviewed-origin patterns.
- `git diff --check`: passed.

Unit/integration coverage includes VAD calibration/noise/hysteresis/minimum speech/silence; 20-second and byte limits; state transitions; no pre-call access; permission/browser failures; mute/unmute/End; tab hiding, device/permission/context interruptions; navigation/expiry; native late grants; stale upload/task/analyser callbacks; confirmation/edit/cancel/duplicate lock; each task outcome; sequential turns and bounded history. Browser coverage checks actual built strict-CSP routes, no unexpected requests/uploads/storage, all task states, mocked automatic turns and microphone amplitude, reduced motion, interruptions and cleanup.

Browser layouts: 320×568, 375×667, 390×844, 430×932 and 667×375 short landscape. A real-animation-clock pass additionally captures all seven states at 390×844 in `test-results/conversation-review-and-fi-d0c2b-wser’s-real-animation-clock/`, with `-real-clock.png` filenames. This checks real animation scheduling as well as the mocked-clock regressions. Screenshots are full-page captures; viewport assertions independently verify anchored controls remain reachable. Screenshots are local, ignored artifacts under `test-results/conversation-complete-automatic-call-at-<width>×<height>/`. All seven requested states were visually reviewed: `pre-call.png`, `active-listening.png`, `muted.png`, `transcript-unavailable.png`, `confirmation.png`, `demo-response.png`, `ended-call.png`. Long review/history content uses normal page scrolling; call controls remain anchored without a nested scrolling pane. Keyboard and safe-area behavior still need real-device acceptance.

## Known hardware/browser limitations

Automated browser checks use installed desktop Chrome with synthetic media, analyser samples and permissions. **No physical iPhone, Android, actual microphone, Bluetooth device, Safari/WebKit browser, crowded-room VAD, real HTTPS host, or real API has been verified.** Secure-context/media APIs are required for voice; text remains usable otherwise. Web Audio/MediaRecorder formats vary; supported types are negotiated with a native default fallback. Safari can require another deliberate Unmute gesture after AudioContext suspension or automatic next-turn creation. Browser tab/device suspension delays JS; physical capture indicators and cleanup must be verified on phones. Local VAD is an amplitude heuristic and cannot guarantee perfect speech boundaries or background-noise rejection. Use Finish turn or text when unreliable.

## FastAPI integration still required

See [API_INTEGRATION.md](./API_INTEGRATION.md) for proposed typed contracts. No API existence or authentication has been verified.

1. Agree on HTTPS same-origin `POST /api/transcribe`, accepted Blob MIME/codecs, multipart `audio`, `mime_type`, `duration_ms`, limits/errors/timeouts and validated `{text}` response. Implement verified per-session authentication before connection; retain explicit per-turn upload authorization.
2. Agree on backend-issued credentials separate from nicknames/demo IDs, authoritative expiry/ownership, `POST /api/requests`, `GET /api/requests/{request_id}`, idempotency and revision ordering.
3. Define actual queued/processing/approval/execution/completion/denial/failure semantics, safe public response text, polling/timeouts and unavailable-robot behavior. Backend remains final safety/allowlist authority; frontend/AI suggestions are never authorization.
4. Define cancellation/interruption during already-submitted actions; do not equate local End with a physical stop. Verify audio/transcript/request retention, logging and deletion before updating privacy claims.
5. Deliberately wire a real task-capable controller/adapter and reviewed deployment/CSP profile, preserving API-disabled Demo. Keep robot network details/privileged tokens backend-only. Run separate integration and physical-device tests before a separately authorized deployment.

## Exact manual local checks

```sh
cd frontend
npm ci --include=dev
npm run dev -- --host 127.0.0.1
# Open printed loopback URL + /join; normally http://127.0.0.1:5173/join
```

Allow/deny microphone deliberately; speak after calibration, pause, use Finish, Mute/Unmute, review/edit/cancel/confirm Hello/Robot info/Help, inspect Demo responses, make another turn, End Call, Leave session. Also test privacy/Back, tab hiding, disabled/revoked mic, device interruption, silence, continuous 20-second speech and session expiry. For a short local expiry set `VITE_DEMO_SESSION_SECONDS=5` in ignored `.env.local`, restart Vite, then remove/reset it before rebuilding the default test artifact. Do not set a transcription endpoint for static Demo.

```sh
npm run typecheck
npm test
npm run build:demo
npm run audit:dist
npm run test:e2e
npm run preview:static
# Open http://127.0.0.1:4173/join; do not run preview during e2e.
```

Physical phones need a trusted HTTPS development host, not plain LAN HTTP or a certificate bypass. This task does not deploy one or alter the public Worker. Existing archive files remain untouched and must not be mistaken for the new artifact.

## Changed-file inventory and Git status

Complete reviewable frontend worktree inventory (including earlier authorized phase work carried forward):

- `frontend/API_INTEGRATION.md`
- `frontend/CALL_INTERFACE.md`
- `frontend/DEPLOYMENT_CHECKLIST.md`
- `frontend/README.md`
- `frontend/e2e/conversation.spec.ts`
- `frontend/e2e/deployment.spec.ts`
- `frontend/e2e/media.ts`
- `frontend/e2e/tasks.spec.ts`
- `frontend/e2e/textFallback.ts`
- `frontend/e2e/visitor.spec.ts`
- `frontend/e2e/voice.spec.ts`
- `frontend/src/App.test.tsx`
- `frontend/src/App.tsx`
- `frontend/src/call/CallController.test.ts`
- `frontend/src/call/CallController.ts`
- `frontend/src/call/CallScreen.tsx`
- `frontend/src/call/CallVisualizer.tsx`
- `frontend/src/call/audioMonitor.test.ts`
- `frontend/src/call/audioMonitor.ts`
- `frontend/src/call/state.test.ts`
- `frontend/src/call/state.ts`
- `frontend/src/call/vad.test.ts`
- `frontend/src/call/vad.ts`
- `frontend/src/styles.css`
- `frontend/src/tasks/RequestFlow.test.tsx`
- `frontend/src/tasks/voiceWorkflow.test.tsx`
- `frontend/src/test/callHarness.tsx`
- `frontend/src/test/textFallback.ts`
- `frontend/src/voice/Conversation.test.tsx`
- `frontend/src/voice/VoiceInteraction.test.tsx`
- `frontend/src/voice/VoiceInteraction.tsx`
- `frontend/src/voice/recorder.test.ts`
- `frontend/src/voice/recorder.ts`

Branch remains `feat/visitor-qr-voice-ui`. Worktree is intentionally uncommitted: 33 changed/new frontend files above, plus preserved untracked root `.DS_Store` and both frontend ZIP archives. No unrelated tracked files are changed. Ignored `dist/` and browser screenshots/test output are generated locally. No commits, pushes, merges, deployment or archive replacement occurred.

Audited current artifact aggregate SHA-256 (sorted filenames and bytes): `6cf114e52f5b016a37e31af63de324e839127c1a9b0ca16989a1c7233459b93a`. Re-audit the exact artifact before any separately authorized publication.

Reviewed screenshot examples: [pre-call](./test-results/conversation-review-and-fi-d0c2b-wser’s-real-animation-clock/pre-call-real-clock.png), [listening](./test-results/conversation-review-and-fi-d0c2b-wser’s-real-animation-clock/active-listening-real-clock.png), [muted](./test-results/conversation-review-and-fi-d0c2b-wser’s-real-animation-clock/muted-real-clock.png), [manual fallback](./test-results/conversation-review-and-fi-d0c2b-wser’s-real-animation-clock/transcript-unavailable-real-clock.png), [confirmation](./test-results/conversation-review-and-fi-d0c2b-wser’s-real-animation-clock/confirmation-real-clock.png), [Demo response](./test-results/conversation-review-and-fi-d0c2b-wser’s-real-animation-clock/demo-response-real-clock.png), [ended](./test-results/conversation-review-and-fi-d0c2b-wser’s-real-animation-clock/ended-call-real-clock.png). These artifacts are local and intentionally ignored by Git.
