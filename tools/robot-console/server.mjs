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
const ROBOT = 'https://d1-jetson.tail5ec076.ts.net'
const PORT = Number(process.env.PORT ?? 5174)
const TOKEN = process.env.D1_ROBOT_TOKEN

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

    if (req.method === 'GET' && (url.pathname === '/' || url.pathname === '/index.html')) {
      const html = await readFile(join(HERE, 'index.html'), 'utf8')
      res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' })
      return res.end(html)
    }

    if (req.method === 'GET' && url.pathname === '/api/voice-config') {
      return json(res, 200, { publicKey: VAPI_PUBLIC_KEY, assistantId: VAPI_ASSISTANT_ID })
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
}).listen(PORT, () => {
  console.log(`Robot console  ->  http://localhost:${PORT}`)
  console.log(`Proxying       ->  ${ROBOT}`)
  console.log('Token is held in this process and never sent to the browser.')
})
