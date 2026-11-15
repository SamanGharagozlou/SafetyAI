# D1 Robot Console

A local operator UI for driving the robot over the Tailscale funnel: posture,
all 10 gestures, a movement d-pad, measured walks, gait, and a STOP button.

```bash
D1_ROBOT_TOKEN=<token> node tools/robot-console/server.mjs
# -> http://localhost:5174
```

The token lives on the Jetson in `~/vapi-robot/start_server.sh`. **Do not paste it
into a file in this repository.**

## Why there is a server and not just an HTML file

Two reasons, both measured rather than assumed:

1. **CORS makes a direct browser call impossible.** The Jetson sends no
   `Access-Control-*` headers, and an `OPTIONS` preflight to `/vapi` returns `401`
   because the auth check runs before any preflight handling. The browser's
   preflight fails before the real request is ever sent.
2. **The token must not reach the browser.** It is a bearer credential for moving a
   physical robot over a public URL. In a page it would sit in the DOM, in devtools,
   in any screenshot, and in the history of anyone the page were shared with. It
   stays in the Node process.

For the same reason this is **not** published as a shareable Artifact. A page with a
public URL that can walk a robot is not something to hand out a link to.

Zero npm dependencies, matching the Jetson's `server.js` — it needs to start on a
laptop at a demo, not survive an `npm install` on conference wifi.

## Voice

**🎙 Talk to robot** starts a live call with the `ClawMetry D1 Robot (voice)`
assistant (`19828665-3d22-47b3-a72f-079e19b45aca`), which carries the same seven
tools as the buttons. Say "stand up", "sit down", "give me your paw", "do a little
wiggle", "walk forward 30 centimetres", "stop".

The audio path is browser → Vapi; the **tool calls go from Vapi straight to the
Jetson**, never through this page or the proxy — which is why the missing CORS
headers don't affect voice. Every tool call the assistant makes is echoed into the
Activity log, so that log stays the one place recording everything the robot was
told to do, by button or by voice.

The Vapi public key and assistant id are served from `/api/voice-config`. Both are
public by design, unlike the robot bearer token.

### If voice won't connect, it is probably Tailscale

Measured on a tailnet Mac: Chrome's ICE gathering took **39.9 s with STUN versus
133 ms without**, because it probes the Tailscale interfaces and they never answer.
Vapi's room budget is 15 s, so the join dies — and the error Vapi reports blames the
*microphone*, which sends you hunting the wrong thing for hours.

Either quit Tailscale, or relaunch Chrome with:

```
--force-webrtc-ip-handling-policy=default_public_interface
```

The console's connect watchdog gives up at 25 s and names this cause explicitly
rather than repeating Vapi's misleading one. **The buttons keep working either
way** — voice is additive, never a dependency.

## Remote access — running it on the Jetson

Install with `deploy/jetson/install-console.sh`. The console then runs on the
Jetson and is published with **Tailscale Serve** at
`https://d1-jetson.tail5ec076.ts.net:8443/` — reachable from any device signed
into the tailnet, on any network, including a phone on cellular.

Two reasons this is better than running it on a laptop:

- **It is faster.** The proxy→robot leg becomes loopback (~3ms to the Python
  agent, ~49ms through the Node edge) instead of the ~1s wifi hop. Only the
  unavoidable you→Jetson leg remains.
- **It survives your laptop.** The console is up whenever the Jetson is.

### Serve, not Funnel — and loopback binding

**Tailscale Serve is tailnet-only; Funnel is public.** This uses Serve. Being on
the tailnet *is* the authentication, so there is no password to leak, guess or
share, and the URL cannot be probed by bots. A public login in front of a physical
robot is a much weaker position.

It is published on **port 8443, not a path under 443**, deliberately: Funnel
already owns `443 / → 8090`, and layering a tailnet-only path beneath a public
prefix on the same listener is exactly the kind of overlap that silently resolves
the wrong way. A separate port has no prefix to get wrong.

The server **binds to `127.0.0.1` only**, and that default is load-bearing — it is
what keeps the console off the venue wifi, where it would otherwise be an
unauthenticated robot-driving page in a room full of strangers. Tailscale reaches
it over localhost. If it seems unreachable, fix the Serve mapping; do not set
`HOST=0.0.0.0`. The server prints a warning if you do.

The token goes in `/etc/d1-console.env` (mode 0600), not in the unit file — unit
files are world-readable and `systemctl cat` would print it.

**Verify it is not public after installing.** The install script prints the three
checks; the important one is loading the URL from a phone with Tailscale *off* and
confirming it fails.

## Safety decisions

- **`damp` is not reachable.** The proxy allowlists seven tools and three posture
  commands; `damp` is in neither. It is the emergency release that makes the legs go
  limp so the robot drops to the floor — one misclick in a grid of otherwise
  harmless buttons. A human can still send it over plain REST.
- **STOP is never disabled**, and **spacebar is STOP**. A physical robot needs a
  reflex that isn't "find the mouse".
- **A timeout never reports "it didn't happen."** `walk_distance` blocks on the
  robot for up to 25 s, so the proxy allows 35 s; if it still times out the console
  says the robot *may still be moving* and to press STOP. Telling an operator
  "failed" while the robot is walking is the worst possible split.
- **Gestures and walks need the robot standing.** The robot refuses them otherwise.
  With auto stand-up ticked, the console performs the exact recovery the robot's own
  error message asks for — stand up, wait, retry once.
- **Unknown control modes are labelled unknown.** The robot emits `0/1/18/21/51`;
  the SDK documents only `0/1/3`, and `d1_dashboard`'s map has no entry for `21` and
  calls `18` "moving". The console prints `unknown (21)` rather than inventing a
  reassuring word.

## Verified against the real robot

`stand_up`, `lie_down`, `nod` and `greet` all driven through this proxy, confirmed
by `gesture_active`/`gesture_name` and `ctrl_mode` on the robot afterwards. The
gesture list is checked against the robot's own `gestures_available` rather than
against documentation — all 10 match exactly.
