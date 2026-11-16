# The robot's face, and the guest's phone

Two screens and one wire between them.

| | |
|---|---|
| `/face` | The phone strapped to the robot. A living face by default; the QR comes over it every so often. **Owns the microphone, the loudspeaker and the Vapi call.** |
| `/join` | What the guest's phone opens after scanning. Consent, Start, Stop, and tap-shortcuts. **No audio role at all.** |

```bash
node tools/robot-face/server.mjs          # :5180
# robot phone  -> https://<host>/face     (tap once to arm)
# the QR then encodes https://<host>/join
```

Env: `VAPI_PUBLIC_KEY`, `VAPI_ASSISTANT_ID` (defaults to the Leukerbad greeter
`a366d544`, never the robot-only assistant), `PORT`, `PUBLIC_URL`.

## The face is the default, the QR is the interruption

Nobody crosses a foyer to look at a barcode. They cross it to look at
something with eyes. So the water drop — floppy dog ears, because the robot
is a dog and the venue is thermal water, and one shape should say both — is
on screen all the time, blinking, glancing around, ears lagging behind the
sway. Every 18 seconds the QR slides over it for 7, to tell the person who
already walked up what to do next, then hands back to the face.

It opens on the QR so that whoever is setting the robot up can scan it
immediately. It never covers a live conversation.

## One device, one microphone

Like any humanoid robot: **you talk to the dog.** The guest's phone has no
microphone and no playback. That is not a simplification, it is the correct
engineering choice, for two reasons.

**Echo.** Mic and speaker on the same device is the exact case browser echo
cancellation is built for. Split them — phone mic here, Bluetooth speaker
there — and the speaker's 100–200 ms latency puts the echo outside what AEC
can cancel. The assistant then hears itself, transcribes itself and interrupts
itself mid-sentence.

**Lip sync.** People notice about 45 ms of audio-visual offset. The mouth is
read off a WebAudio analyser on the assistant's own track, on the same device
that is playing it, so there is no network in the loop to lose that race.

### The price of that choice: no Bluetooth, anywhere

This page holds the microphone, so it **cannot** also drive a Bluetooth
speaker. Measured on this project on both iPhone and Android: `getUserMedia`
drops the phone from A2DP to HFP, and most Bluetooth *speakers* implement no
usable HFP, so the sound falls back to the phone's own speaker.

> A phone may hold the speaker **or** the microphone, never both.
> A laptop or the Jetson may hold both — desktop OSes route in and out
> independently.

If the phone is not loud enough for the room, **plug the speaker in with a
cable** (AUX or USB-C). There is no Bluetooth in this design.

## Serve it over HTTPS

A browser will not give the microphone to an `http://` page on a LAN address —
it is not a secure context — and the failure reads like a permissions bug
rather than a protocol one. Use the Tailscale funnel, a cloudflared tunnel, or
`tools/phone_demo.sh --https`.

## Arm it once

Browsers refuse a page the microphone, and refuse to play sound, until
somebody has tapped it. An unattended screen that was never tapped is silent
and gives no clue why — so the tap is the **first** screen, not a mystery
later. One tap during setup covers the rest of the session.

## Why the shortcuts are sentences, not commands

Each button on `/join` sends the exact words a guest would have said
(`"Can you take me to the sauna world?"`), injected into the conversation as
their turn with `add-message` and `triggerResponseEnabled`. The assistant then
answers in character and reaches for its own tools.

So a tap and a spoken request behave identically, and there is no second
command path to keep in step with the persona. Adding a shortcut is adding a
string.

`do_gesture`, `walk_distance` and the rest are reached this way too — the
greeter's `PERFORMING` rules in `frontend/scripts/vapi/concierge.mjs` turn
"do a dance" into `sway_circle` + `wiggle`, and a Christmas song into a few
sung lines. On real hardware `guide_to` does not exist (no map), so "take me
to the restaurant" leads a metre with `walk_distance` and gives the rest in
words — see `REAL_ROBOT_RULES`.

## Consent

Checked on the server, not only in the guest's browser. A consent control that
can be skipped by replaying a request is not a consent control, and this one
covers recording a stranger's voice.

There is deliberately **no camera**. `docs/feature_privacy_anonymization.md`
is explicit that face images processed to *identify* people are biometric
special-category data under Art. 9, and the guests here are in swimwear. If
face capture is ever added, the only defensible version is a transient re-ID
embedding held in RAM for the session, never stored, never uploaded, and said
plainly on screen.

## Tests

```bash
PORT=5180 node tools/robot-face/server.mjs &
node tools/robot-face/test-flow.mjs
```

15 assertions across both screens: arming, the QR, server-side consent
enforcement, start/stop, and that a tapped shortcut arrives as a user turn.
The Vapi SDK is **stubbed** — the real one would place a billable call to an
assistant whose robot tools point at the live Jetson, and a test must not be
able to move a robot.

`/face?demo=1` runs the whole face off a synthetic speech envelope with no
network, no microphone and no Vapi. That is how the animation was reviewed.
