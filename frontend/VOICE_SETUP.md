# Live voice — setup and current state

Picking up from `feat/visitor-qr-voice-ui`. This covers the opt-in Vapi voice
path added in `bcfeed5` and `d7d2a1e`. Everything you already built is intact
and still the default: with no keys configured the app behaves exactly as
before.

## What changed, in one paragraph

The call was voice-shaped but silent — `VITE_TRANSCRIBE_ENDPOINT` ships blank
and `build:demo` rejects a non-empty one, so every turn fell through to
"Speech recognition unavailable" and the guest typed into a textarea. There is
now a second path where the guest simply talks and is answered aloud, using
Vapi (Deepgram transcription → Claude → spoken reply) over WebRTC. It turns on
only when both Vapi keys resolve as UUIDs.

## Run it

```sh
cd frontend
npm ci --include=dev
npm run dev -- --host 127.0.0.1
# open http://127.0.0.1:5173/join → Continue as Guest → Start Conversation
# and ALLOW the microphone prompt
```

**No setup needed.** `src/voice/vapiDefaults.ts` bundles a working public key
and assistant id, so the branch runs as cloned. Both values are public by
design — a Vapi public key is meant to ship in a browser bundle — and this
repo is private. Rotate them when the hackathon ends.

To point at a different assistant, set these in `.env.local` (gitignored):

| variable | effect |
| --- | --- |
| `VITE_VAPI_PUBLIC_KEY` | overrides the bundled public key |
| `VITE_VAPI_ASSISTANT_ID` | overrides the bundled assistant |

Semantics are deliberate: **unset** means "nobody chose", so the bundled demo
assistant is used; an **empty string** means someone chose to switch live voice
off. That is how the test suite and the static demo pin it off.

### Changing how Vapi behaves

The private key is in `scripts/vapi/credentials.json` so you can reconfigure
the assistant yourself. It is **Node-only** and must never be imported from
`src/` or put in a `VITE_` variable — every `VITE_` value is baked into the
browser bundle, and this key can read every call transcript and delete
assistants. `scripts/vapi/boundary.test.mjs` fails the suite if it ever
reaches `src/`, and the build was checked against `dist/`.

```sh
node scripts/vapi/assistant.mjs show                 # current config
node scripts/vapi/assistant.mjs voices               # Elliot | Rohan | Leo
node scripts/vapi/assistant.mjs voice Leo 0.95       # swap voice + speed
node scripts/vapi/assistant.mjs pace 0.8             # wait before replying
node scripts/vapi/assistant.mjs greeting "Oh, hi!"   # first line
node scripts/vapi/assistant.mjs prompt ./my.txt      # replace the persona
node scripts/vapi/assistant.mjs calls 5              # recent calls + endedReason
```

Changes take effect on the next call — no rebuild, no redeploy. `calls` is the
diagnostic channel when something misbehaves: `endedReason` names the cause.

## Files

| file | role |
| --- | --- |
| `src/voice/vapiSession.ts` | typed wrapper over `@vapi-ai/web`. Owns the mic probe, the connect watchdog and event plumbing. |
| `src/call/LiveCallScreen.tsx` | the live call view. Reuses `CallVisualizer` and your Mute / End Call controls. |
| `src/voice/vapiDisabled.ts` | throwing stub the demo build aliases the SDK to. |
| `src/App.tsx` | renders `LiveCallScreen` only when `resolveVapiConfig` returns non-null. |

## Your static demo is still exactly as locked down

Two independent guards, because one was not enough:

1. `config/deployment.ts` rejects non-empty Vapi keys for the `static-demo`
   profile, the same way it already rejects a transcription endpoint.
2. `vite.config.ts` aliases `@vapi-ai/web` to a throwing stub in demo mode, so
   the third-party client is **not bundled at all**.

Guard 2 exists because guard 1 was not sufficient: the first build shipped a
342 kB Vapi chunk into `dist/` with daily.co origins in it, and **your
`audit:dist` caught it** — `Unreviewed URL origin in assets/vapi-*.js`. An env
check cannot stop a chunk from being emitted. It is now 0.12 kB and the audit
passes at 11 files.

There is a third: `App.tsx` refuses live voice outright when
`import.meta.env.MODE === 'demo'`, so the bundled default keys can never switch
it on in the static demo.

Because of guard 1, `npm run build:demo` **fails while real keys sit in
`.env.local`**. That is deliberate. `vitest.config.ts` and the Playwright
`webServer` both pin the two keys empty, so the suite always describes the
shipped artifact no matter what a developer has configured locally.

## The assistant lives on Vapi's servers, not in this repo

This is the least obvious part. The voice, its pacing and the persona are all
assistant-side config, so nothing in this repo explains why it sounds the way
it does. A redacted snapshot is in
[`docs/vapi-assistant.example.json`](./docs/vapi-assistant.example.json).

The settings that matter for how it *feels*:

| setting | value | why |
| --- | --- | --- |
| `voice.voiceId` | `Elliot` | Vapi's built-in provider has exactly three male voices: `Elliot`, `Rohan`, `Leo`. Everything else 400s. |
| `voice.speed` | `0.92` | unhurried without sounding slurred |
| `startSpeakingPlan.waitSeconds` | `1.2` | lets a guest finish a thought. The default ~0.4 is what makes agents feel like phone bots. |
| `smartEndpointingPlan` | on | waits for a natural sentence end, not merely silence |
| `stopSpeakingPlan.numWords` | `2` | two words from the guest and it stops talking |
| `silenceTimeoutSeconds` | `60` | quiet is allowed in a spa |

Timing, not timbre, is most of "sounds friendly". The system prompt also
carries an explicit **YOU ARE NOT ON A PHONE CALL** section, because a model
told merely to be "friendly" still writes call-centre dialogue.

## Known behaviour, so you don't chase it

- **The join takes ~15–20 s on some networks** and the screen says
  "Connecting…" throughout. This is real latency, not a hang — `call-start`
  only fires once the WebRTC join completes. It was reported as broken twice
  before we established that. **Worth fixing with real progress text**: the
  SDK emits `call-start-progress` with named stages
  (`web-call-creation`, `daily-call-join`) that the UI currently ignores.
- **An unanswered mic prompt never resolves `getUserMedia`** — it does not
  reject, it hangs. The watchdog is therefore armed *before* the probe: a hint
  naming the prompt at 7 s, and a truthful failure at 25 s that returns the
  guest to a working screen.
- **One `VapiSession` per call**, created on the button press. An instance held
  in a `useRef` initializer is already stopped by the time the user clicks,
  because StrictMode mounts, cleans up and remounts — the button was silently
  dead in dev until this was fixed.

## Not done yet

1. **The robot tools are not wired to your `TaskAdapter`.** The assistant has
   5 tools (`get_status`, `list_destinations`, `start_guiding_to`,
   `stop_guiding`, `call_staff`) but they currently call a separate Python
   webhook, not this frontend. Your `TaskAdapter` interface is the right seam;
   see the notes below before wiring it.
2. **`CallController.confirm()` has two hard demo-only locks** —
   `if (this.adapter.mode !== 'demo') throw` and `accept()` rejecting
   `!task.demo`. Both must be opened deliberately for a real adapter.
3. **The 12 s task deadline is shorter than a walk.** `until = Date.now() +
   12_000` will fire mid-journey and report "timed out" while the robot is
   still physically moving. A guided walk to the restaurant measures ~20 s.
4. **`decodeTask` forbids `reply` on any non-`completed` status**, so progress
   narration ("we're passing the sauna now") has nowhere to live in the
   current contract.
5. **The privacy page needs updating before any deployment that sets these
   keys.** It currently says "no third-party audio services", which is true of
   the default build and false of the voice build. Vapi is a third-party audio
   service. That copy is yours to write, so I have not touched it.

## Verified

With real keys present in `.env.local`: `npm run typecheck` clean,
`npm test` 259 passing (18 files), `npx playwright test` 52 passing,
`npm run build:demo` + `npm run audit:dist` pass at 11 files.

A live call was confirmed end to end: Deepgram transcribed the guest, Claude
answered from the venue knowledge pack, and the reply was spoken back.
