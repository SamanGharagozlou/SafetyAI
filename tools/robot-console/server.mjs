#!/usr/bin/env node
/**
 * Operator console for the D1 robot. Serves the UI and proxies to the Jetson.
 *
 *   D1_ROBOT_TOKEN=... node tools/robot-console/server.mjs
 *   -> http://localhost:5174
 *
 * WHY A PROXY RATHER THAN FETCHING THE FUNNEL FROM THE BROWSER:
 *
 * 1. CORS. Measured 2026-11-15: the Jetson sends no Access-Control-* headers at
 *    all, and an OPTIONS preflight to /vapi returns 401 because the auth check
 *    runs before any preflight handling. A browser therefore cannot call it
 *    directly - the preflight fails before the real request is ever sent.
 *
 * 2. The token. It is a bearer credential for MOVING A PHYSICAL ROBOT over a
 *    public URL. Shipping it into a page would put it in the DOM, in devtools,
 *    in any screenshot of the console and in the browser history of anyone the
 *    page is ever shared with. It stays in this process, read from the
 *    environment, and is never sent to the browser.
 *
 * Zero npm dependencies, matching the Jetson's server.js: this needs to start
 * on a laptop at a demo, not survive an npm install on bad conference wifi.
 *
 * CONNECTION REUSE IS LOAD-BEARING - DO NOT REPLACE fetch WITH ANYTHING THAT
 * OPENS A FRESH CONNECTION PER CALL. Measured against the funnel, 10 calls each:
 *
 *   through this proxy (pooled)     median ~0.47s   range 0.18-1.15
 *   fresh connection every call     median ~2.0s    range 0.69-4.12
 *
 * The cause is NOT TLS and NOT Tailscale's ingress, though the cold-call
 * breakdown (dns 4ms / tcp 0.23-0.58s / TLS 0.56-1.17s) makes it look that
 * way. Measured from inside the Jetson, the ladder is:
 *
 *   python agent, on-box loopback      ~3ms
 *   node server, on-box loopback      ~49ms
 *   Mac -> tailnet IP, NO TLS, direct  ~1.0s   <-- the whole cost appears here
 *
 * That last hop is plain WireGuard over venue wifi, with no TLS and no funnel
 * in the path, and ICMP to the same box measured 57/107/142ms on a LAN that
 * should be under 2ms. So the latency is the wifi between the two machines.
 *
 * Pooling matters MORE because of that, not less: every handshake costs
 * several round trips across exactly that slow hop. Node's global fetch pools
 * connections, which is why this console beats curl against the same endpoint.
 */
import { createServer } from 'node:http'
import { readFile } from 'node:fs/promises'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = dirname(fileURLToPath(import.meta.url))

/**
 * Where the robot is. Two deployments:
 *
 *   laptop  ROBOT_URL unset -> the public funnel. Every call crosses the wifi
 *           hop to the Jetson, which measured ~1s.
 *   Jetson  ROBOT_URL=http://127.0.0.1:8090 -> loopback. The on-box Node hop
 *           measured ~49ms and the Python agent ~3ms, so running the console
 *           ON the Jetson removes the slow leg entirely. Only the unavoidable
 *           you -> Jetson leg remains.
 */
const ROBOT = (process.env.ROBOT_URL ?? 'https://d1-jetson.tail5ec076.ts.net').replace(/\/$/, '')

const PORT = Number(process.env.PORT ?? 5174)

/**
 * Binds to LOOPBACK ONLY by default, and that default is load-bearing.
 *
 * On the Jetson this process holds a token that drives a physical robot and
 * has no login of its own. Tailscale Serve is what makes it reachable, and it
 * connects over localhost - so binding to 127.0.0.1 means the ONLY way in is
 * through Tailscale, authenticated as a device on the tailnet. Binding
 * 0.0.0.0 would additionally expose it to anyone on the venue wifi, with no
 * password, which at a hackathon is a room full of strangers.
 *
 * Do not set HOST=0.0.0.0 to "make it reachable". If it is not reachable,
 * the Tailscale Serve mapping is what needs fixing.
 */
const HOST = process.env.HOST ?? '127.0.0.1'

const TOKEN = process.env.D1_ROBOT_TOKEN

/**
 * ACCESS KEY. Required whenever this is reachable from anywhere but loopback.
 *
 * The console has no other authentication: whoever loads the page can move
 * the robot. That was acceptable while it drove a simulator on localhost. It
 * is not acceptable on a public tunnel attached to a real machine standing in
 * front of the public, so the server REFUSES to bind a non-loopback address
 * without a key rather than letting that happen by omission.
 *
 * One shared key, delivered in the URL once and then held in a cookie: enough
 * to stop a bot that finds the hostname, and simple enough to open on a phone.
 * It is NOT a substitute for Tailscale Serve, which is the real answer because
 * it never exposes the thing publicly at all.
 */
const ACCESS_KEY = process.env.CONSOLE_KEY
const LOOPBACK = HOST === '127.0.0.1' || HOST === 'localhost' || HOST === '::1'

if (!LOOPBACK && !ACCESS_KEY) {
  console.error(`REFUSING TO START: bound to ${HOST} with no CONSOLE_KEY.\n`)
  console.error('This console moves a physical robot and has no other login, so it will not')
  console.error('listen on a public address unprotected. Set CONSOLE_KEY=<random string>,')
  console.error('or leave HOST unset to stay on loopback.')
  process.exit(1)
}

/** Constant-time-ish compare, so the key cannot be guessed a character at a time. */
function keyMatches(given) {
  if (!given || given.length !== ACCESS_KEY.length) return false
  let diff = 0
  for (let i = 0; i < given.length; i++) diff |= given.charCodeAt(i) ^ ACCESS_KEY.charCodeAt(i)
  return diff === 0
}

const authorised = req => {
  if (!ACCESS_KEY) return true
  const cookie = /(?:^|;\s*)d1key=([^;]+)/.exec(req.headers.cookie ?? '')
  return cookie ? keyMatches(decodeURIComponent(cookie[1])) : false
}

/**
 * Voice. Both values are PUBLIC by design - a Vapi public key is meant to ship
 * in a browser bundle, and an assistant id is not a secret. Unlike the robot
 * bearer token, these are safe to hand to the page.
 *
 * The assistant already carries all 7 robot tools, each pointed at the Jetson
 * funnel with its own Authorization header held on Vapi's side. So a voice
 * tool call goes Vapi -> Jetson directly and never touches this proxy or the
 * browser - which is also why it is unaffected by the missing CORS headers.
 */
const VAPI_PUBLIC_KEY = process.env.VAPI_PUBLIC_KEY ?? '2cdfae81-9e4a-4215-a17b-7ec2b7bf6aa4'
const VAPI_ASSISTANT_ID = process.env.VAPI_ASSISTANT_ID ?? '19828665-3d22-47b3-a72f-079e19b45aca'

/**
 * OPTIONAL, for tuning only: the Vapi PRIVATE key, which unlocks the
 * post-call timeline at /api/last-call. Vapi stamps every event with
 * secondsFromStart, so this is the only place with true end-to-end timings -
 * the browser cannot see when the model decided, only when audio arrived.
 *
 * Like the robot token it stays in this process and is NEVER sent to the
 * browser; /api/last-call returns transcript text and timings only. The
 * console works fine without it - the button just reports it is not set.
 */
const VAPI_PRIVATE_KEY = process.env.VAPI_PRIVATE_KEY

if (!TOKEN) {
  console.error('D1_ROBOT_TOKEN is not set.\n')
  console.error('  D1_ROBOT_TOKEN=<token> node tools/robot-console/server.mjs\n')
  console.error('The token lives on the Jetson in ~/vapi-robot/start_server.sh.')
  console.error('Do not paste it into a file in this repository.')
  process.exit(1)
}

/**
 * Only these reach the robot. An allowlist rather than a pass-through, because
 * this process holds the token: anything it forwards is authorised by it.
 *
 * `damp` is absent on purpose - it is the emergency release that drops the
 * robot to the floor. A console button for it is one misclick from a fall, and
 * the UI it would sit in is a grid of harmless gesture buttons.
 */
const TOOLS = new Set([
  'robot_status', 'robot_command', 'do_gesture',
  'move_robot', 'walk_distance', 'set_gait', 'stop_robot',
])
const COMMANDS = new Set(['stand_up', 'lie_down', 'shake_hand'])

const json = (res, code, body) => {
  res.writeHead(code, { 'Content-Type': 'application/json' })
  res.end(JSON.stringify(body))
}

async function robotStatus() {
  const response = await fetch(`${ROBOT}/status`, {
    headers: { Authorization: `Bearer ${TOKEN}` },
    signal: AbortSignal.timeout(8000),
  })
  return response.json()
}

async function callTool(name, args) {
  // These BLOCK on the robot until the movement finishes, so each timeout must
  // exceed the real duration or we report a failure while the robot is still
  // moving - the worst possible split between what we say and what is.
  //
  // walk_distance: server caps the walk at 25s.
  // do_gesture: gestures normally return in about a second, but one call to
  //   look_around stalled past 15s and was reported as failed - and an
  //   identical retry came back in 1.5s. So the stall was transient, NOT an
  //   inherently long gesture. The headroom is for that transient: a gesture
  //   wrongly reported as failed is how an operator ends up double-sending.
  const TIMEOUTS = { walk_distance: 35000, do_gesture: 30000 }
  const timeout = TIMEOUTS[name] ?? 15000
  const response = await fetch(`${ROBOT}/vapi`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${TOKEN}`, 'Content-Type': 'application/json' },
    body: JSON.stringify({
      message: { type: 'tool-calls', toolCallList: [{ id: 'ui', name, arguments: args }] },
    }),
    signal: AbortSignal.timeout(timeout),
  })
  const data = await response.json()
  return data?.results?.[0]?.result ?? JSON.stringify(data)
}

const readBody = req => new Promise((resolve, reject) => {
  let raw = ''
  req.on('data', chunk => {
    raw += chunk
    if (raw.length > 1e5) { reject(new Error('body too large')); req.destroy() }
  })
  req.on('end', () => resolve(raw))
  req.on('error', reject)
})

createServer(async (req, res) => {
  try {
    const url = new URL(req.url, `http://localhost:${PORT}`)

    // ?k=<key> exchanges the key for a cookie, once, then drops it from the
    // address bar so it does not linger in screenshots or shared links.
    if (ACCESS_KEY && url.searchParams.has('k')) {
      if (keyMatches(url.searchParams.get('k'))) {
        res.writeHead(302, {
          'Set-Cookie': `d1key=${encodeURIComponent(ACCESS_KEY)}; Path=/; HttpOnly; SameSite=Lax; Max-Age=86400`,
          Location: '/',
        })
        return res.end()
      }
      console.warn(`[auth] rejected key from ${req.socket.remoteAddress}`)
    }

    if (!authorised(req)) {
      res.writeHead(401, { 'Content-Type': 'text/html; charset=utf-8' })
      return res.end('<body style="font:16px system-ui;background:#0e1116;color:#e6edf3;padding:40px">'
        + '<h2>Access key required</h2><p>This console moves a physical robot. '
        + 'Open the link that includes <code>?k=…</code>.</p></body>')
    }

    if (req.method === 'GET' && (url.pathname === '/' || url.pathname === '/index.html')) {
      const html = await readFile(join(HERE, 'index.html'), 'utf8')
      res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' })
      return res.end(html)
    }

    if (req.method === 'GET' && url.pathname === '/api/voice-config') {
      // `simulated` drives a banner in the page. When this console is public
      // (a tunnel, for a phone), the single most important thing a viewer can
      // know is whether the buttons move a real machine or a fake one.
      return json(res, 200, {
        publicKey: VAPI_PUBLIC_KEY,
        assistantId: VAPI_ASSISTANT_ID,
        simulated: /127\.0\.0\.1|localhost/.test(ROBOT) && !ROBOT.includes(':8090'),
        robot: ROBOT,
      })
    }

    if (req.method === 'GET' && url.pathname === '/api/last-call') {
      if (!VAPI_PRIVATE_KEY) {
        return json(res, 200, { error: 'VAPI_PRIVATE_KEY is not set, so the post-call timeline is unavailable. Restart the console with it to enable this.' })
      }
      const list = await (await fetch(
        `https://api.vapi.ai/call?assistantId=${VAPI_ASSISTANT_ID}&limit=1`,
        { headers: { Authorization: `Bearer ${VAPI_PRIVATE_KEY}` }, signal: AbortSignal.timeout(15000) },
      )).json()

      const call = Array.isArray(list) ? list[0] : null
      if (!call) return json(res, 200, { error: 'no calls recorded for this assistant yet' })

      // Normalise Vapi's role soup into one ordered timeline. `at` is
      // secondsFromStart, which is what makes the gaps meaningful.
      const timeline = []
      for (const m of call.messages ?? []) {
        const at = m.secondsFromStart ?? null
        if (m.role === 'user') timeline.push({ at, kind: 'user', text: m.message ?? '' })
        else if (m.role === 'bot') timeline.push({ at, kind: 'bot', text: m.message ?? '' })
        else if (m.role === 'tool_calls') {
          for (const c of m.toolCalls ?? []) {
            timeline.push({ at, kind: 'tool', name: c.function?.name, args: c.function?.arguments })
          }
        } else if (m.role === 'tool_call_result') {
          timeline.push({ at, kind: 'result', text: String(m.result ?? '') })
        }
      }
      return json(res, 200, {
        id: call.id,
        endedReason: call.endedReason ?? null,
        startedAt: call.startedAt ?? null,
        timeline,
      })
    }

    if (req.method === 'GET' && url.pathname === '/api/status') {
      try {
        return json(res, 200, await robotStatus())
      } catch (error) {
        // A failure HERE means the funnel or the laptop's network, not the
        // robot. The UI must not render it as "the robot is down".
        return json(res, 200, { ok: false, unreachable: true, error: String(error.message ?? error) })
      }
    }

    if (req.method === 'POST' && url.pathname === '/api/tool') {
      const { name, args = {} } = JSON.parse(await readBody(req) || '{}')

      if (!TOOLS.has(name)) return json(res, 400, { error: `tool not allowed: ${name}` })
      if (name === 'robot_command' && !COMMANDS.has(args.command)) {
        return json(res, 400, { error: `command not allowed: ${args.command}` })
      }

      try {
        const result = await callTool(name, args)
        console.log(`[tool] ${name} ${JSON.stringify(args)} -> ${result}`)
        return json(res, 200, { result })
      } catch (error) {
        const message = String(error.message ?? error)
        console.log(`[tool] ${name} FAILED: ${message}`)
        // Never say "did not happen". A timed-out walk may still be in progress.
        return json(res, 200, {
          result: `Request failed: ${message}. The robot may still be moving - press STOP.`,
          failed: true,
        })
      }
    }

    json(res, 404, { error: 'not found' })
  } catch (error) {
    json(res, 500, { error: String(error.message ?? error) })
  }
}).listen(PORT, HOST, () => {
  console.log(`Robot console  ->  http://${HOST}:${PORT}`)
  console.log(`Proxying       ->  ${ROBOT}`)
  console.log('Token is held in this process and never sent to the browser.')
  if (HOST !== '127.0.0.1' && HOST !== 'localhost') {
    console.warn(`\n!! WARNING: bound to ${HOST}, not loopback.`)
    console.warn('!! This console has no login. Anyone who can reach this port can drive the robot.')
  }
})
