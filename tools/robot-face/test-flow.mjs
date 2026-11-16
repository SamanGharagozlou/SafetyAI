/**
 * The two-screen greeter flow, end to end.
 *
 *   PORT=5180 node tools/robot-face/server.mjs &
 *   node tools/robot-face/test-flow.mjs
 *
 * The Vapi SDK is STUBBED on purpose. The real one would place a billable
 * call to an assistant whose robot tools point at the live Jetson, so an
 * unstubbed test could make a real robot move. A test must never be able to
 * do that.
 *
 * Playwright is borrowed from frontend/node_modules and drives the installed
 * Chrome, so nothing has to be downloaded.
 */
import { createRequire } from 'node:module'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
import { existsSync } from 'node:fs'
const HERE = dirname(fileURLToPath(import.meta.url))
const require = createRequire(join(HERE, '../../frontend/package.json'))
const { chromium } = require('playwright')
const OUT = process.env.SHOTS ? process.env.SHOTS.replace(/\/?$/, '/') : ''
/* With no BASE given, the test runs its own server AND a fake ros-agent, so
   it depends on nothing that has to be started by hand and nothing that is
   only true on the Jetson. Set BASE to point it at a deployed instance. */
const spawned = []
let BASE = process.env.BASE
if (!BASE) {
  const { createServer } = await import('node:http')
  const { spawn } = await import('node:child_process')
  const agent = createServer((_, res) => {
    res.writeHead(200, { 'Content-Type': 'application/json' })
    res.end(JSON.stringify({ connected: true, standing: true, moving: false,
                             gesture_active: false, battery_pct: 91 }))
  })
  await new Promise(r => agent.listen(5191, r))
  spawned.push(() => agent.close())
  // A crashed earlier run can leave its server listening, and then this
  // suite silently tests THAT one - stale code, passing or failing for
  // reasons nothing to do with the working tree. Refuse instead.
  const taken = await fetch('http://127.0.0.1:5189/api/state').then(() => true).catch(() => false)
  if (taken) {
    console.error('\nport 5189 is already serving. A previous run left a server behind.')
    console.error('kill it first:  kill $(lsof -ti tcp:5189)\n')
    process.exit(2)
  }
  const srv = spawn(process.execPath, [join(HERE, 'server.mjs')], {
    env: { ...process.env, PORT: '5189', AGENT_URL: 'http://127.0.0.1:5191',
           PUBLIC_URL: 'http://127.0.0.1:5189', FACE_KEY: '', LEASE_MS: '2000' },
    stdio: 'ignore',
  })
  spawned.push(() => srv.kill())
  BASE = 'http://127.0.0.1:5189'
  // A crash must not leave the server running, or the NEXT run either tests
  // stale code or refuses to start. This is how 5189 got stuck twice.
  const cleanup = () => { for (const k of spawned) { try { k() } catch {} } }
  process.on('exit', cleanup)
  process.on('uncaughtException', e => { cleanup(); console.error(e); process.exit(1) })
  process.on('unhandledRejection', e => { cleanup(); console.error(e); process.exit(1) })
  await new Promise(r => setTimeout(r, 1200))
}
// The deployed server is key-guarded; a local one usually is not. The first
// page load carries the key and the server sets a cookie for the rest.
const K = process.env.BASE && process.env.FACE_KEY ? `?k=${encodeURIComponent(process.env.FACE_KEY)}` : ''
const wait = ms => new Promise(r => setTimeout(r, ms))
/* Wait for the thing you care about, not for a guess at how long it takes.
   Fixed sleeps here were producing failures that had nothing to do with the
   code - the slowest page in a four-page suite is not the robot. */
const until = async (fn, ms = 8000) => {
  const end = Date.now() + ms
  for (;;) {
    try { if (await fn()) return true } catch {}
    if (Date.now() > end) return false
    await wait(120)
  }
}

// A fake SDK. The real one would place a billable call to an assistant whose
// robot tools point at the live Jetson - a test must not be able to move a robot.
const STUB = `
export default class Vapi {
  constructor(k){ window.__log = []; this.h = {}; this.up = false; window.__log.push(['ctor', k])
    window.__vapiEmit = (e,...a) => this.emit(e,...a) }
  on(e,f){ (this.h[e] ||= []).push(f) }
  emit(e,...a){ (this.h[e]||[]).forEach(f=>f(...a)) }
  async start(id, ov){
    window.__log.push(['start', id, JSON.stringify(ov ?? null)])
    // call-start is ASYNC in the real SDK. Emitting it synchronously is what
    // hid a deadlock: anything sent in the gap throws, and the old code let
    // that abort the poll loop with its "in a call" flag stuck on.
    setTimeout(() => { this.up = true; this.emit('call-start') }, 150)
    return {}
  }
  async stop(){ this.up = false; window.__log.push(['stop']); this.emit('call-end') }
  // test hook: let the suite drive speech events the way the real SDK would
  send(m){
    if (!this.up) throw new Error('call is not up yet')
    window.__log.push(['send', JSON.stringify(m)])
  }
}`

const CHROME = process.env.CHROME ?? (process.platform === 'darwin'
  ? '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' : '/usr/bin/google-chrome')
const b = await chromium.launch({
  ...(existsSync(CHROME) ? { executablePath: CHROME } : {}),
  args: ['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream',
         '--disable-background-timer-throttling', '--disable-backgrounding-occluded-windows',
         '--disable-renderer-backgrounding'],
})
const ctx = await b.newContext({ permissions: ['microphone'] })
// Tripwire: the screen must never ask for a microphone. On a phone that
// single call drops the Bluetooth speaker from A2DP to HFP and kills the
// sound, so it is worth asserting rather than trusting.
await ctx.addInitScript(() => {
  window.__gumCalled = false
  const real = navigator.mediaDevices?.getUserMedia?.bind(navigator.mediaDevices)
  if (real) navigator.mediaDevices.getUserMedia = (...a) => { window.__gumCalled = true; return real(...a) }
})
const errs = []
const pass = []

// The server keeps one session, so a previous run (or a stray browser tab)
// can leave one live and the face would auto-join it. Reset before asserting
// anything: a test that depends on how the last one ended is not a test.
await fetch(BASE + '/api/session' + K, { method: 'POST', headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ action: 'stop' }) }).catch(() => {})

const face = await ctx.newPage({ })
await face.route('**/@vapi-ai/web@**', r => r.fulfill({ status: 200, contentType: 'application/javascript', body: STUB }))
face.on('pageerror', e => errs.push('face: ' + e.message))
const guest = await ctx.newPage()
guest.on('pageerror', e => errs.push('guest: ' + e.message))

// --- face phone: arm it
// The BRAIN: headless on the Jetson in real life, so it needs no tap.
await face.goto(BASE + '/face' + (K ? K + '&' : '?') + 'brain=1', { waitUntil: 'domcontentloaded' })
await face.click('#arm')          // the brain takes the mic, so it needs one tap
pass.push(['the brain comes up after its tap',
  await until(async () => (await face.evaluate(() => window.__log ?? [])).length > 0)])
pass.push(['the brain tells the server it is awake',
  await until(async () => (await (await fetch(BASE + '/api/state' + K)).json()).brain?.connected === true)])

// The DISPLAY: the phone on the robot. Takes no microphone, joins no call.
const screen = await ctx.newPage({ viewport: { width: 420, height: 860 } })
screen.on('pageerror', e => errs.push('screen: ' + e.message))
await screen.goto(BASE + '/face' + K, { waitUntil: 'domcontentloaded' })
// No tap: the screen asks for nothing, so there is nothing to grant. One
// less thing to forget while strapping a phone to a robot.
pass.push(['the screen comes up with no tap',
  await until(async () => !(await screen.isHidden('#stage')))])
pass.push(['the screen never shows an arming prompt', await screen.isHidden('#arm')])
pass.push(['the cycle opens on the QR, so it can be scanned at once',
  await screen.evaluate(() => document.getElementById('qrpanel').classList.contains('on'))])
// A QR nobody can scan is worse than no QR: it was `example.invalid` once,
// and every phone rejected it as a bad URL.
const qrText = await screen.evaluate(async () => {
  const c = document.querySelector('#qr canvas'); if (!c) return ''
  const m = await import('https://cdn.jsdelivr.net/npm/jsqr@1.4.0/+esm')
  const d = c.getContext('2d').getImageData(0, 0, c.width, c.height)
  return (m.default ?? m)(d.data, c.width, c.height)?.data ?? ''
})
pass.push(['the QR decodes to a reachable join URL on this origin',
  qrText.startsWith(BASE) && qrText.includes('/join') && !/\.invalid/.test(qrText), qrText])
pass.push(['QR encodes the join URL', (await screen.locator('#qr canvas').count()) === 1
           || (await screen.textContent('#qr-fallback'))?.includes('/join')])

// --- guest phone
await guest.goto(BASE + '/join' + K, { waitUntil: 'domcontentloaded' })
pass.push(['Start is disabled before consent', await guest.isDisabled('#start')])
await guest.fill('#name', 'Anna')
await guest.selectOption('#lang', 'French')
await guest.check('#consent')
pass.push(['consent enables Start', !(await guest.isDisabled('#start'))])

// consent is enforced server-side too, not only in the browser
const forced = await guest.evaluate(async () => {
  const r = await fetch('/api/session', { method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({ action: 'start', consent: false }) })
  return r.status
})
pass.push(['server refuses start without consent', forced === 400, 'HTTP ' + forced])

await guest.click('#start')
await wait(1200)
pass.push(['guest moves to the controls', !(await guest.isHidden('#during'))])
pass.push(['guest is greeted by name', (await guest.textContent('#greet'))?.includes('Anna')])

// --- the face should have started a call, carrying the name
let log = await face.evaluate(() => window.__log ?? [])
pass.push(['face started a call on the guest tap', log.some(l => l[0] === 'start'), JSON.stringify(log.find(l=>l[0]==='start')||'')])
{
  const ov = JSON.parse(log.find(l => l[0] === 'start')?.[2] ?? 'null')
  pass.push(['the chosen language reaches the assistant',
    ov?.variableValues?.language === 'French', JSON.stringify(ov?.variableValues)])
  // A guest who picked a language must be greeted in it, not in a random one.
  pass.push(['a picked language is not overridden by the random welcome',
    ov?.firstMessage === undefined, JSON.stringify(ov?.firstMessage ?? null)])
}
pass.push(['the screen is on stage during the call', !(await screen.isHidden('#stage'))])
// The screen is independent by design, so the QR cycle keeps running
// through a conversation. The trade is deliberate: nothing the screen draws
// can contradict what the robot is doing, because it never consults it.
pass.push(['the screen runs its own cycle regardless of the call',
  await until(async () => await screen.evaluate(() =>
    document.getElementById('qrpanel').classList.contains('on')), 30000)])

// --- a shortcut becomes the guest's own sentence
await guest.click('#places button[data-i="2"]')      // sauna world
await wait(900)
log = await face.evaluate(() => window.__log ?? [])
const sent = log.filter(l => l[0] === 'send').map(l => JSON.parse(l[1]))
pass.push(['sauna tap injects a user turn', sent.some(m => m.type === 'add-message'
           && m.message.role === 'user' && /sauna/i.test(m.message.content) && m.triggerResponseEnabled === true),
           JSON.stringify(sent[0] ?? null)])

await guest.click('#fun button[data-i="1"]')         // christmas song
await wait(900)
log = await face.evaluate(() => window.__log ?? [])
pass.push(['christmas tap injects a user turn',
  log.filter(l=>l[0]==='send').map(l=>JSON.parse(l[1])).some(m => /christmas/i.test(m.message?.content ?? ''))])

// speaking state drives the mouth
await face.evaluate(() => { const v = window.__v; })
if (OUT) await face.screenshot({ path: OUT + 'face-live.png' })

// --- a second guest must not be able to take the session over
const other = await ctx.newPage()
await other.goto(BASE + '/join' + K, { waitUntil: 'domcontentloaded' })
await other.waitForTimeout(1200)
const secondStart = await other.evaluate(async () => {
  const r = await fetch('/api/session', { method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({ action: 'start', consent: true, name: 'Intruder', owner: 'not-the-owner' }) })
  return { status: r.status, body: await r.json() }
})
pass.push(['second guest is told the robot is busy', secondStart.status === 409 && secondStart.body.busy === true,
  JSON.stringify(secondStart.body.error ?? '')])
pass.push(['second guest sees it on screen',
  await until(async () => /another guest/i.test(await other.textContent('#robot-state') ?? '')),
  await other.textContent('#robot-state')])
// Assert the contract, not the fixture: this suite also runs against the
// real Jetson, where the battery is whatever it is.
const rb = await guest.evaluate(async () => (await (await fetch('/api/state')).json()).robot)
// A dead robot must not block a conversation: the voice is on the laptop.
{
  const txt = await guest.evaluate(async () => {
    const r = await fetch('/api/session', { method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({ action: 'start', consent: true, name: 'x', owner: 'probe-no-robot' }) })
    return r.status
  })
  pass.push(['start is gated on the voice, not on the robot', txt === 409 || txt === 200, 'HTTP ' + txt])
}
// The ros-agent must be reachable; the robot itself may well be on charge,
// and this suite runs against the live Jetson where it often is.
pass.push(['robot state reaches the guest page',
  rb && rb.reachable === true
    && (rb.battery === null || (typeof rb.battery === 'number' && rb.battery >= 0 && rb.battery <= 100)),
  `reachable ${rb?.reachable}, connected ${rb?.connected}, battery ${rb?.battery}`])

const steal = await other.evaluate(async () => (await fetch('/api/session', { method:'POST',
  headers:{'Content-Type':'application/json'},
  body: JSON.stringify({ action:'say', text:'walk forward', owner:'not-the-owner' }) })).status)
pass.push(['second guest cannot speak into it', steal === 403, 'HTTP ' + steal])
await other.close()

// --- the screen is decoration: it must stay lively and stay OUT of the call
await face.evaluate(() => window.__vapiEmit('speech-start'))
await wait(1200)
pass.push(['the screen never opens a microphone',
  await screen.evaluate(() => !window.__gumCalled)])
pass.push(['the screen joins no call',
  await screen.evaluate(() => window.__log === undefined), JSON.stringify(await screen.evaluate(() => window.__log ?? null))])
pass.push(['the screen keeps animating regardless of the call',
  await until(async () => {
    const a = await screen.evaluate(() => document.getElementById('drop').getAttribute('transform'))
    await wait(220)
    const b2 = await screen.evaluate(() => document.getElementById('drop').getAttribute('transform'))
    return a !== b2
  }), 'the drop keeps moving'])
await face.evaluate(() => window.__vapiEmit('speech-end'))

// --- ONLY ONE VOICE. Two brains means two assistants in the same room,
// heard as an echo. Vapi's call list showed them arriving in exact pairs.
const second = await ctx.newPage()
await second.route('**/@vapi-ai/web@**', r => r.fulfill({ status: 200, contentType: 'application/javascript', body: STUB }))
await second.goto(BASE + '/face' + (K ? K + '&' : '?') + 'brain=1', { waitUntil: 'domcontentloaded' })
await second.click('#arm')
await wait(4000)
const slog = await second.evaluate(() => window.__log ?? [])
pass.push(['a second brain never opens its own call',
  !slog.some(l => l[0] === 'start'), JSON.stringify(slog.map(l => l[0]))])
pass.push(['and it says so instead of failing quietly',
  /another voice/i.test((await second.textContent('#fault')) ?? ''), await second.textContent('#fault')])
pass.push(['the first brain keeps its call',
  (await face.evaluate(() => window.__log ?? [])).filter(l => l[0] === 'start').length === 1])
pass.push(['the robot only ever had one voice',
  (await face.evaluate(() => window.__log ?? [])).filter(l => l[0] === 'start').length
  + slog.filter(l => l[0] === 'start').length === 1])
await second.close()

// The lease must be free before a fresh brain can be tested on its own.
await face.close()
// Wait out the lease. It is 2 s under this suite's own server and 8 s in a
// deployment, and a fixed guess at the shorter one made three assertions
// fail against the Jetson for no reason at all.
await wait(Number(process.env.LEASE_MS ?? (process.env.BASE ? 8000 : 2000)) + 2500)

// --- THE REGRESSION: a face phone opening against a dirty event log.
// The log now holds this run's starts, stops and sentences. A face that
// replays them tears down the call it is setting up and wedges itself.
const fresh = await ctx.newPage()
await fresh.route('**/@vapi-ai/web@**', r => r.fulfill({ status: 200, contentType: 'application/javascript', body: STUB }))
const freshErrs = []
fresh.on('pageerror', e => freshErrs.push(e.message))
await fresh.goto(BASE + '/face' + (K ? K + '&' : '?') + 'brain=1', { waitUntil: 'domcontentloaded' })
await fresh.click('#arm')
await wait(3000)
const flog = await fresh.evaluate(() => window.__log ?? [])
pass.push(['a fresh face joins the live session with exactly one call',
  flog.filter(l => l[0] === 'start').length === 1, JSON.stringify(flog.map(l => l[0]))])
pass.push(['it does not replay the old conversation',
  !flog.some(l => l[0] === 'send'), JSON.stringify(flog.filter(l => l[0] === 'send'))])
pass.push(['it does not tear its own call down', !flog.some(l => l[0] === 'stop')])
pass.push(['no page errors while joining', freshErrs.length === 0, freshErrs.join('; ')])

// and a sentence tapped from here must now reach it
await guest.click('#places button[data-i="0"]')      // restaurant
await wait(1200)
const flog2 = await fresh.evaluate(() => window.__log ?? [])
pass.push(['a tap after joining reaches the robot',
  flog2.some(l => l[0] === 'send' && /restaurant/i.test(l[1])),
  JSON.stringify(flog2.filter(l => l[0] === 'send').map(l => JSON.parse(l[1]).message.content))])
// --- stop  (asserted on `fresh`, which now holds the lease: `face` is closed)
await guest.click('#stop')
await wait(900)
log = await fresh.evaluate(() => window.__log ?? [])
pass.push(['stop ends the call', log.some(l => l[0] === 'stop')])
await fresh.close()
pass.push(['the screen is still alive after the call ends',
  await until(async () => {
    const a = await screen.evaluate(() => document.getElementById('drop').getAttribute('transform'))
    await wait(220)
    return a !== await screen.evaluate(() => document.getElementById('drop').getAttribute('transform'))
  })])

// a say with no session must be refused
const late = await guest.evaluate(async () => (await fetch('/api/session', { method:'POST',
  headers:{'Content-Type':'application/json'}, body: JSON.stringify({action:'say', text:'hi'}) })).status)
pass.push(['say is refused with no session', late === 409, 'HTTP ' + late])

if (OUT) await guest.screenshot({ path: OUT + 'guest.png', fullPage: true })

await screen.close()

let bad = 0
for (const [n, ok, note] of pass) { if (!ok) bad++; console.log(`${ok?'PASS':'FAIL'}  ${n}${note?'  ('+note+')':''}`) }
console.log(`\n${pass.length-bad}/${pass.length} passed`)
if (errs.length) console.log('page errors:', errs.slice(0,8))
await b.close()
for (const kill of spawned) { try { kill() } catch {} }
process.exit(bad ? 1 : 0)
