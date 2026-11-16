/**
 * The greeter's two screens, and the one wire between them.
 *
 *   /face   the phone strapped to the robot: QR when idle, a face when talking.
 *           It owns the microphone, the loudspeaker and the Vapi call.
 *   /join   what the guest's phone opens after scanning. Deliberately almost
 *           empty: consent, Start, Stop.
 *
 * WHY THE GUEST'S PHONE IS NEARLY EMPTY
 * The guest should be looking at the dog, not at a screen. Every control we
 * add to their phone is a reason to look down. So their phone carries only
 * what genuinely cannot live on the robot: the consent they have to give
 * themselves, and a Stop button that belongs in the hand of the person
 * standing next to a moving machine.
 *
 * WHY ALL THE AUDIO IS ON ONE DEVICE
 * Mic and speaker on the same device is the case browser echo cancellation
 * is actually built for. Split them across two devices - a phone mic and a
 * Bluetooth speaker - and the speaker's 100-200 ms latency puts the echo
 * outside what AEC can cancel, so the assistant hears itself, transcribes
 * itself and interrupts itself. One device also means the lip sync is read
 * off the very audio being played, with no network in between.
 *
 * The cost of that choice: the robot phone holds the microphone, so it can
 * NOT also hold a Bluetooth speaker (getUserMedia drops A2DP to HFP, and
 * most speakers have no usable HFP - measured on iPhone and Android here).
 * If the phone is not loud enough, the speaker is plugged in with a cable.
 *
 * Transport is Server-Sent Events, not WebSocket: it is one direction, it is
 * in the standard library on both ends, and it reconnects by itself.
 *
 *   VAPI_PUBLIC_KEY=... VAPI_ASSISTANT_ID=... node tools/robot-face/server.mjs
 */
import { createServer } from 'node:http'
import { readFile } from 'node:fs/promises'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { networkInterfaces } from 'node:os'

const HERE = dirname(fileURLToPath(import.meta.url))
const PORT = Number(process.env.PORT ?? 5180)
/**
 * Loopback by default, per the project rule: a service is exposed through a
 * tunnel, not by listening on every interface. Tailscale Funnel proxies from
 * 127.0.0.1, so the default is also what the funnelled deployment wants.
 * BIND=0.0.0.0 is only for reaching it directly across the tailnet before a
 * funnel exists, and FACE_KEY should always be set when it is.
 */
const BIND = process.env.BIND ?? '127.0.0.1'

const PUBLIC_KEY = process.env.VAPI_PUBLIC_KEY ?? '2cdfae81-9e4a-4215-a17b-7ec2b7bf6aa4'
// Default to the Leukerbad greeter, never the robot-only assistant. A dropped
// env var silently costing the spa persona has already happened once here.
const ASSISTANT_ID = process.env.VAPI_ASSISTANT_ID ?? 'a366d544-fcf3-4b98-a436-d1b8c458891a'

/**
 * FACE_KEY guards the whole thing when it is exposed on a tunnel.
 *
 * This is not a page that merely shows information: anyone who opens /join
 * can start a billable call to an assistant whose tools move a real robot.
 * A tunnel hostname is obscure, not secret, so it is not a control.
 *
 * The guest never meets the key - it is baked into the QR - so the cost of
 * having it is zero and the cost of not having it is a stranger walking the
 * dog. Unset it and everything is open, which is the right default on a
 * laptop bound to loopback.
 */
const KEY = process.env.FACE_KEY
const cookieOf = req => /(?:^|;\s*)fkey=([^;]+)/.exec(req.headers.cookie ?? '')?.[1]
const authed = (req, url) => !KEY || url.searchParams.get('k') === KEY || cookieOf(req) === KEY

/** The LAN address a phone can actually reach, for the QR. */
function lanAddress() {
  for (const list of Object.values(networkInterfaces())) {
    for (const n of list ?? []) {
      if (n.family === 'IPv4' && !n.internal) return n.address
    }
  }
  return '127.0.0.1'
}
const PUBLIC_URL = (process.env.PUBLIC_URL ?? `http://${lanAddress()}:${PORT}`).replace(/\/$/, '')

/**
 * One robot, one guest at a time. A session map keyed by token would be the
 * honest thing for a fleet, and the wrong thing for a hackathon: it would add
 * a pairing failure mode to a demo whose whole risk is failing in front of
 * people. One session, reset on stop.
 */
let session = { live: false, name: '', consent: false, startedAt: 0, owner: '' }

/**
 * ROBOT STATE, read from the ros-agent on the Jetson.
 *
 * :8091 answers /status with no token and is the honest source - the edge on
 * :8090 runs auth before routing and 401s a GET even with the right token.
 * Polled rather than asked on demand so a slow or dead agent delays nothing
 * a guest is waiting on; `reachable` going false IS the answer in that case.
 */
const AGENT = process.env.AGENT_URL ?? 'http://127.0.0.1:8091'
let robot = { reachable: false, connected: false, standing: false, moving: false,
              gesture: null, battery: null, checkedAt: 0 }

async function pollRobot() {
  try {
    const r = await fetch(`${AGENT}/status`, { signal: AbortSignal.timeout(2500) })
    const d = await r.json()
    robot = {
      reachable: true,
      connected: d.connected === true,
      standing: d.standing === true,
      moving: d.moving === true,
      gesture: d.gesture_active ? (d.gesture_name ?? 'moving') : null,
      battery: typeof d.battery_pct === 'number' ? d.battery_pct : null,
      checkedAt: Date.now(),
    }
  } catch {
    robot = { ...robot, reachable: false, checkedAt: Date.now() }
  }
}
pollRobot()
setInterval(pollRobot, 2000).unref?.()

/** What a guest's phone is allowed to know, and what the face reacts to. */
const publicState = () => ({
  live: session.live,
  name: session.name,
  startedAt: session.startedAt,
  // "Busy" to a second guest means someone else is already talking to it.
  // The robot physically moving is NOT busy for this purpose - it moves
  // constantly while talking to the person it is already with.
  busy: session.live,
  robot: { reachable: robot.reachable, connected: robot.connected, standing: robot.standing,
           moving: robot.moving, gesture: robot.gesture, battery: robot.battery },
  // Whether anything is actually able to talk. Without this a guest can tap
  // Start, tap six buttons and get silence with nothing on screen to explain
  // it - which is exactly what happened the first time this ran.
  brain: { connected: brainUp() },
  face: faceState,
})

/**
 * A sequenced event log, polled - NOT Server-Sent Events.
 *
 * SSE was the obvious choice and it works perfectly on the LAN, but a
 * Cloudflare quick tunnel buffers `text/event-stream` so completely that
 * nothing arrives at all: measured here, zero bytes in twelve seconds,
 * including the frames the server writes on connect. The guest's Start
 * button would have done nothing, and only on the deployed URL.
 *
 * Polling is immune to whatever a proxy decides to do with a long-lived
 * response. The only latency-critical thing in this system is lip sync, and
 * that never leaves the robot phone - a Start button or a tapped shortcut is
 * perfectly happy with a sub-second poll.
 */
let seq = 0
const events = []

/**
 * LONG POLLING, not SSE and not a fixed interval.
 *
 * SSE was tried first and a Cloudflare quick tunnel buffered it to nothing.
 * A fixed 700 ms interval was fine for a Start button but is far too slow
 * for a mouth: the face now lives on a different device from the audio, and
 * a mouth that starts moving 700 ms after the voice is worse than one that
 * does not move at all.
 *
 * A long poll is a normal, complete HTTP response, so no proxy can decide to
 * buffer it the way it can a stream, and a waiting client hears about an
 * event within milliseconds of it happening.
 */
const waiters = new Set()
const wake = () => {
  for (const w of waiters) { clearTimeout(w.timer); try { w.send() } catch {} }
  waiters.clear()
}
const broadcast = event => {
  events.push({ seq: ++seq, ...event })
  if (events.length > 40) events.splice(0, events.length - 40)   // a demo, not an audit log
  wake()
}

/**
 * What the brain reports about the conversation, for the screen to draw.
 *
 * Only onsets travel: "speaking", "listening", and the line being said. The
 * syllable flutter is generated on the screen itself. Nobody can tell a real
 * envelope from a plausible one, but everybody notices a mouth moving in
 * silence - so the wire carries the thing that must be right and the device
 * drawing generates the thing that merely has to look right.
 */
let faceState = { state: 'idle', caption: '', at: 0 }
let brainSeenAt = 0
const brainUp = () => Date.now() - brainSeenAt < 30000   // a long poll returns at 20s

const json = (res, code, body) => {
  res.writeHead(code, { 'Content-Type': 'application/json', 'Cache-Control': 'no-store' })
  res.end(JSON.stringify(body))
}

const readBody = req => new Promise((resolve, reject) => {
  let raw = ''
  req.on('data', c => { raw += c; if (raw.length > 1e4) { reject(new Error('body too large')); req.destroy() } })
  req.on('end', () => resolve(raw))
  req.on('error', reject)
})

async function page(res, file, extra = {}) {
  try {
    const html = await readFile(join(HERE, file), 'utf8')
    res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-store', ...extra })
    res.end(html)
  } catch (e) {
    res.writeHead(500, { 'Content-Type': 'text/plain' })
    res.end(`cannot read ${file}: ${e.message}`)
  }
}

createServer(async (req, res) => {
  const url = new URL(req.url, `http://${req.headers.host}`)
  const p = url.pathname

  try {
    if (!authed(req, url)) {
      return json(res, 401, { error: 'unauthorized: this URL needs its key (?k=...)' })
    }
    // Hand back a cookie so the page's own fetches and the SSE stream work
    // without the key being repeated on every request - EventSource cannot
    // carry headers, and a key in every URL ends up in logs.
    const setCookie = url.searchParams.get('k') === KEY
      ? { 'Set-Cookie': `fkey=${KEY}; Path=/; SameSite=Lax; Max-Age=86400` } : {}

    if (p === '/' || p === '/face') return void await page(res, 'index.html', setCookie)
    if (p === '/join')              return void await page(res, 'join.html', setCookie)

    if (p === '/api/config') {
      // The QR carries the key, so a guest scans their way in and never sees it.
      const joinUrl = `${PUBLIC_URL}/join${KEY ? `?k=${encodeURIComponent(KEY)}` : ''}`
      return json(res, 200, { publicKey: PUBLIC_KEY, assistantId: ASSISTANT_ID, joinUrl })
    }

    if (p === '/api/state') return json(res, 200, { ...publicState(), seq })

    // The face phone polls this. `since` is the last seq it has acted on, so
    // a dropped request costs nothing: the next one still carries the event.
    if (p === '/api/events') {
      const since = Number(url.searchParams.get('since') ?? 0) || 0
      // The brain's poll doubles as its heartbeat. Timers get throttled when
      // a page is backgrounded; a request that actually arrives does not.
      if (url.searchParams.get('role') === 'brain') brainSeenAt = Date.now()
      const reply = () => json(res, 200, { seq, session: publicState(), events: events.filter(e => e.seq > since) })
      const pending = events.some(e => e.seq > since)
      // wait=1 asks to be held until something happens. Anything that has
      // never polled before gets an immediate answer so it can sync.
      if (pending || url.searchParams.get('wait') !== '1') return reply()
      const w = { send: reply, timer: null }
      w.timer = setTimeout(() => { waiters.delete(w); reply() }, 20000)
      waiters.add(w)
      res.on('close', () => { clearTimeout(w.timer); waiters.delete(w) })
      return
    }

    // The brain reports what the conversation is doing, so the screen on the
    // robot can draw it. Separate from /api/session because this is the
    // machine talking about itself, not a guest asking for something.
    if (p === '/api/face' && req.method === 'POST') {
      let body
      try { body = JSON.parse(await readBody(req) || '{}') }
      catch (e) { return json(res, 400, { error: `body is not JSON: ${e.message}` }) }
      brainSeenAt = Date.now()
      const next = {
        state: ['idle', 'listening', 'thinking', 'speaking', 'happy'].includes(body.state) ? body.state : 'idle',
        caption: String(body.caption ?? '').slice(0, 300),
        at: Date.now(),
      }
      // Only wake the screens when something actually changed, or a heartbeat
      // every few seconds would restart every long poll for nothing.
      const changed = next.state !== faceState.state || next.caption !== faceState.caption
      faceState = next
      if (changed) broadcast({ type: 'face', state: next.state, caption: next.caption })
      return json(res, 200, { ok: true, live: session.live, name: session.name })
    }

    if (p === '/api/session' && req.method === 'POST') {
      let body
      try { body = JSON.parse(await readBody(req) || '{}') }
      catch (e) { return json(res, 400, { error: `body is not JSON: ${e.message}` }) }

      if (body.action === 'start') {
        // Consent is checked on the server, not only in the guest's browser.
        // A consent control that can be skipped by replaying a request is not
        // a consent control, and this one covers recording a stranger's voice.
        if (body.consent !== true) return json(res, 400, { error: 'consent is required before starting' })

        // One robot, one guest. Without this, a second person scanning the
        // QR mid-conversation silently takes over the first person's session
        // - and the first person is standing right there, still talking.
        if (!brainUp()) {
          return json(res, 503, { error: 'The robot is not awake yet — its voice is not connected.', ...publicState() })
        }
        if (session.live && body.owner !== session.owner) {
          return json(res, 409, { error: 'The robot is with another guest just now.', busy: true, ...publicState() })
        }
        const owner = body.owner || Math.random().toString(36).slice(2, 12)
        session = { live: true, name: String(body.name ?? '').slice(0, 24), consent: true, startedAt: Date.now(), owner }
        broadcast({ type: 'start', name: session.name })
        // `owner` goes to this guest alone; it is what lets them stop their
        // own session later and stops anyone else ending it for them.
        return json(res, 200, { ...publicState(), owner })
      }
      // A tapped button is just a sentence the guest did not have to say.
      // It is injected into the conversation as their turn, so the assistant
      // answers in character and reaches for its own tools - no second
      // command path, no intent parser, nothing to keep in sync with the
      // persona. "Take me to the sauna" means the same whether it was
      // spoken or tapped.
      if (body.action === 'say') {
        if (!session.live) return json(res, 409, { error: 'no session is running' })
        if (body.owner !== session.owner) return json(res, 403, { error: 'this is not your session' })
        const text = String(body.text ?? '').slice(0, 200).trim()
        if (!text) return json(res, 400, { error: 'text is required' })
        broadcast({ type: 'say', text })
        return json(res, 200, { ok: true })
      }

      if (body.action === 'stop') {
        // Anyone may stop. A Stop that checks credentials is a Stop that
        // fails when it is most needed, and the worst case here is a session
        // ending early next to a machine that can walk.
        session = { live: false, name: '', consent: false, startedAt: 0, owner: '' }
        broadcast({ type: 'stop' })
        return json(res, 200, publicState())
      }
      return json(res, 400, { error: `unknown action '${body.action}'` })
    }

    res.writeHead(404, { 'Content-Type': 'application/json' })
    res.end(JSON.stringify({ error: 'not found' }))
  } catch (e) {
    json(res, 500, { error: e.message })
  }
}).listen(PORT, BIND, () => {
  const k = KEY ? `?k=${encodeURIComponent(KEY)}` : ''
  console.log(`\nRobot face   ->  ${PUBLIC_URL}/face${k}`)
  console.log(`Guest join   ->  ${PUBLIC_URL}/join${k}   (what the QR encodes)`)
  console.log(`Key          ->  ${KEY ? 'set' : 'NOT SET - anyone with the URL can start the robot'}`)
  console.log(`Listening    ->  ${BIND}:${PORT}${BIND === '0.0.0.0' && !KEY ? '   ** every interface, no key **' : ''}`)
  console.log(`Assistant    ->  ${ASSISTANT_ID}`)
  console.log(`\nOpen /face on the robot phone and tap once to arm it.`)
  console.log(`Browsers only allow a page to take the microphone after a tap,`)
  console.log(`so an unattended screen that was never tapped stays silent.\n`)
})
