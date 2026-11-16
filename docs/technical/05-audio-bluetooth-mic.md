# 05 · Audio: microphone, speaker, and the Bluetooth problem

[← 04 Guest journey](04-guest-journey-qr.md) · [index](../README.md) · next: [06 Perception](06-perception-and-guard.md)

Source: `tools/robot-face/README.md` and `server.mjs`,
`tools/robot-console/index.html` (the device pickers and the explainer the
operator reads at the venue).

> This chapter exists because the audio layout of this robot was decided by a
> hardware constraint nobody expected, it cost real hours at the venue, and the
> wrong fix is the obvious one. It is written so the next person does not repeat
> it.

---

## 1. The one-line rule

> **A phone may hold the Bluetooth speaker *or* the microphone. Never both.**
> A laptop, a desktop or the Jetson may hold both — desktop operating systems
> route input and output independently.

Everything below is the derivation and the consequences.

---

## 2. Why: it is the Bluetooth profile, not the phone

Measured on this project, on **iPhone and Android, Safari and Chrome**. The
first version of this note blamed iOS; Android failed identically, and the
commit that corrected it is `fix(robot-console): it is the Bluetooth profile,
not iOS — Android fails the same`.

| Profile | Direction | Quality | Has a mic channel |
|---|---|---|---|
| **A2DP** | playback only, one way | stereo, high quality | **no** |
| **HFP** | two-way | narrowband, call quality | yes |

A phone pairing with a Bluetooth *speaker* uses A2DP, and music plays
perfectly. **The moment anything calls `getUserMedia`**, the OS needs a
two-way route, so it tries to switch the device to HFP — and **most Bluetooth
speakers implement no usable HFP**. With no bidirectional route available, the
phone falls back to its own built-in speaker and microphone.

Nothing in a web page can override that. It is a profile negotiation below the
browser.

Two further browser facts stack on top:

- **Safari and iOS do not implement `setSinkId`**, so a page cannot choose an
  output device at all.
- **Chrome on Android does not expose output devices** to `enumerateDevices()`.

So on a phone there is neither a working Bluetooth route *nor* an API to pick
one.

### What actually works

| Option | Verdict |
|---|---|
| **A cable — AUX or USB-C** | **the fix.** Sidesteps Bluetooth entirely, quickest, used at the venue |
| A Bluetooth **headset** (not a speaker) | has HFP, routes correctly — but it is a headset, not a loudspeaker in a foyer |
| A **laptop or the Jetson** holding both | desktop OSes route in and out independently; an A2DP speaker plus any microphone is fine. **This is the setup used** |
| Muting the mic between turns to restore A2DP | would work, and **costs barge-in**, which is a worse trade for a robot that must stop when someone says "stop" |

---

## 3. The second constraint: echo

Mic and speaker **on the same device** is the exact case browser acoustic echo
cancellation (AEC) is built for.

Split them — phone mic here, Bluetooth speaker over there — and the speaker's
**100–200 ms latency puts the echo outside what AEC can cancel**. The assistant
then hears itself, transcribes itself, and interrupts itself mid-sentence.

This is not hypothetical. It is the same failure that forced
`stopSpeakingPlan.numWords` from 3 to 5 in [chapter 03](03-voice-vapi.md):

```
observed:  "This will just take a"   /   "as... It it it"   /   "take a sec."
```

One sentence shredded into fragments, because the robot's own voice came out of
the laptop speakers and back into a lapel mic a few inches away.

Tuning helps. **The real fix is physical**: headphones, or move the mic.

---

## 4. The third constraint: lip sync

People notice about **45 ms** of audio-visual offset.

The mouth on the robot's face is driven off a WebAudio analyser reading **the
assistant's own track, on the same device that is playing it**. There is no
network in that loop, so there is no race to lose.

Had the mouth been driven from a remote event stream, every network hiccup
would show as a face mouthing out of time — and worse, a face that keeps
mouthing after the audio has stopped is a face that **lies about what the robot
is doing**. See [04](04-guest-journey-qr.md) §3.

---

## 5. The layout these three constraints force

```
┌─────────────────────────────┐        ┌──────────────────────────┐
│  LAPTOP  /face?brain=1      │        │  PHONE ON THE ROBOT      │
│  · DJI lapel microphone     │        │  /face                   │
│  · loudspeaker (cabled)     │        │  · face + QR only        │
│  · the Vapi WebRTC call     │        │  · no mic, no playback   │
│  · lip-sync analyser        │        │                          │
└─────────────────────────────┘        └──────────────────────────┘
                 ▲                                   ▲
                 └───────── both served by ──────────┘
                        Jetson · robot-face :5180
```

### Why not the Jetson, which is the obvious home

It was tried. Three reasons it is not:

1. **No browser.** The Vapi web SDK needs one.
2. **The USB microphone was not enumerating.**
3. **The speaker would not pair.**

The laptop already worked. It stays the Jetson's job to host the pages and
report robot state.

This is a documented demo-rig compromise, not an architecture claim. A
production version would run the call on the Orin with a USB audio interface,
and the audio would then sit on the same machine as the gate — which is
strictly better for both latency and attribution.

---

## 6. What the operator console does about it

The console (`tools/robot-console`) is where a human drives the robot, and it
has its own voice button. It therefore has to solve the same problem, in front
of someone who is about to demo.

Commit history: `feat(robot-console): pick input AND output device before the
call, and say why Bluetooth speakers get dropped`.

- **Both pickers are presented *before* the call starts**, not buried in
  settings. Choosing a device after a call has begun is too late.
- **Chrome keeps its own per-site microphone choice**, independent of the macOS
  default — a trap that makes "I set it in System Settings" untrue. The console
  lists devices itself and remembers the choice in `localStorage`.
- **The chosen microphone is opened and proven to work** before the call, with
  the error surfaced if it will not open, rather than discovering it when a
  guest is standing there.
- **A speaker paired after permission was granted shows up only after a
  re-enumerate**, so the list is refreshed, not cached.
- The page carries the explanation above **in the UI**, in plain words, ending
  with the actionable line:

  > **No phone can send this call to a Bluetooth speaker.** Confirmed on iPhone
  > and Android, Safari and Chrome. It is a Bluetooth limitation, not a browser
  > one. Fastest fix: **plug the speaker in with a cable.**

A venue is a bad place to debug a profile negotiation. Printing the answer on
the page is cheaper than being right in a README nobody opens.

---

## 7. Microphone tuning

The microphone is opened with explicit constraints rather than browser
defaults, because the default assumption — one speaker, one listener, a quiet
room — is wrong outdoors next to a fountain with a loudspeaker a metre away.

The chosen device is pinned with `deviceId: { exact: … }` so a device change
under the page cannot silently swap the input.

Hardware at the venue: a **DJI lapel microphone**, because a built-in laptop
microphone in a foyer picks up the room, and a headset is not something you can
hand to a stranger.

---

## 8. Latency budget of a spoken turn

Where the time goes, and who owns each slice. These are the stage names that
appear in the ClawMetry Trace view ([12](12-dashboards.md)) and come from
Vapi's `artifact.performanceMetrics` plus the ledger's own timestamps.

| Stage | Typical | Owner |
|---|---|---|
| guest finishes speaking → endpointing fires | 0.8 s budget + smart endpointing | Vapi, tuned by us |
| STT (Deepgram nova-3, multi) | hundreds of ms | Vapi |
| LLM (Claude Sonnet, tool decision) | ~1 s | Vapi |
| tool call → ClawMetry gate verdict | **median 0 ms, observed 1 ms** | **us** |
| Cosmos world-model judge, when consulted | **~4–5 s** | us, off-box over Tailscale |
| dispatch → robot acknowledgement | tens of ms | us |
| `walk_distance` execution | blocks, ~11.6 s for 1 m | the robot |
| TTS first audio | hundreds of ms | Vapi |

Two readings of that table matter:

- **The governing layer is not the latency.** On the instrumented run the whole
  oversight slice — intent recorded, future predicted, verdict given — had a
  median of 0 ms and a worst case of 1 ms. When a robot is slow to react, it is
  the voice stack or the robot, and the Trace view says which.
- **The world model is the exception, and it is the known architectural
  problem.** 96 % of one measured 5.40 s action was Cosmos. It currently gates
  in line; it should escalate behind the kinematic gate. See
  [09](09-clawmetry-control-plane.md) §5 and [15](15-gaps-and-roadmap.md).

NFR-4 in [product_requirements.md](../product_requirements.md) sets the target:
**a voice answer starts within ~2 s of the end of speech.** Met on gesture and
status turns; not met on any turn that consults Cosmos.

---

## 9. Checklist for setting up audio at a venue

1. Laptop runs `/face?brain=1&k=…`. **Tap it once** to arm the microphone.
2. Speaker connected to the laptop **by cable**. Do not pair it to a phone.
3. Lapel mic connected to the laptop; select it explicitly in the picker and
   confirm the level test passes.
4. Phone on the robot runs `/face?k=…`. It needs no tap and makes no sound.
5. If the voice stutters or interrupts itself: move the mic away from the
   speaker before touching any setting.
6. If the call will not connect at all and Vapi blames the microphone: it is
   almost certainly **Tailscale**, not audio. See
   [03](03-voice-vapi.md) §9.

---

[← 04 Guest journey](04-guest-journey-qr.md) · [index](../README.md) · next: [06 Perception](06-perception-and-guard.md)
