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

const PUBLIC_KEY = process.env.VAPI_PUBLIC_KEY ?? '2cdfae81-9e4a-4215-a17b-7ec2b7bf6aa4'
// Default to the Leukerbad greeter, never the robot-only assistant. A dropped
// env var silently costing the spa persona has already happened once here.
const ASSISTANT_ID = process.env.VAPI_ASSISTANT_ID ?? 'a366d544-fcf3-4b98-a436-d1b8c458891a'

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
let session = { live: false, name: '', consent: false, startedAt: 0 }

const clients = new Set()
const broadcast = event => {
  const line = `data: ${JSON.stringify(event)}\n\n`
  for (const res of clients) { try { res.write(line) } catch { clients.delete(res) } }
}

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

async function page(res, file) {
  try {
    const html = await readFile(join(HERE, file), 'utf8')
    res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-store' })
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
    if (p === '/' || p === '/face') return void await page(res, 'index.html')
    if (p === '/join')              return void await page(res, 'join.html')

    if (p === '/api/config') {
      return json(res, 200, { publicKey: PUBLIC_KEY, assistantId: ASSISTANT_ID, joinUrl: `${PUBLIC_URL}/join` })
    }

    if (p === '/api/state') return json(res, 200, session)

    // The face phone subscribes here and is told when to start and stop.
    if (p === '/api/events') {
      res.writeHead(200, {
        'Content-Type': 'text/event-stream',
        'Cache-Control': 'no-store',
        Connection: 'keep-alive',
        'X-Accel-Buffering': 'no',
      })
      res.write(`retry: 1000\n\n`)
      res.write(`data: ${JSON.stringify({ type: 'state', ...session })}\n\n`)
      clients.add(res)
      // Proxies and phone radios drop an idle stream; a comment every 15 s is
      // cheap and keeps it open. Without it the face silently stops listening.
      const beat = setInterval(() => { try { res.write(': ping\n\n') } catch {} }, 15000)
      req.on('close', () => { clearInterval(beat); clients.delete(res) })
      return
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
        session = { live: true, name: String(body.name ?? '').slice(0, 24), consent: true, startedAt: Date.now() }
        broadcast({ type: 'start', ...session })
        return json(res, 200, session)
      }
      // A tapped button is just a sentence the guest did not have to say.
      // It is injected into the conversation as their turn, so the assistant
      // answers in character and reaches for its own tools - no second
      // command path, no intent parser, nothing to keep in sync with the
      // persona. "Take me to the sauna" means the same whether it was
      // spoken or tapped.
      if (body.action === 'say') {
        if (!session.live) return json(res, 409, { error: 'no session is running' })
        const text = String(body.text ?? '').slice(0, 200).trim()
        if (!text) return json(res, 400, { error: 'text is required' })
        broadcast({ type: 'say', text })
        return json(res, 200, { ok: true })
      }

      if (body.action === 'stop') {
        session = { live: false, name: '', consent: false, startedAt: 0 }
        broadcast({ type: 'stop', ...session })
        return json(res, 200, session)
      }
      return json(res, 400, { error: `unknown action '${body.action}'` })
    }

    res.writeHead(404, { 'Content-Type': 'application/json' })
    res.end(JSON.stringify({ error: 'not found' }))
  } catch (e) {
    json(res, 500, { error: e.message })
  }
}).listen(PORT, () => {
  console.log(`\nRobot face   ->  ${PUBLIC_URL}/face     (the phone on the robot)`)
  console.log(`Guest join   ->  ${PUBLIC_URL}/join     (what the QR encodes)`)
  console.log(`Assistant    ->  ${ASSISTANT_ID}`)
  console.log(`\nOpen /face on the robot phone and tap once to arm it.`)
  console.log(`Browsers only allow a page to take the microphone after a tap,`)
  console.log(`so an unattended screen that was never tapped stays silent.\n`)
})
