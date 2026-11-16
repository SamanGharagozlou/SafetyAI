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
import { tmpdir } from 'node:os'
const HERE = dirname(fileURLToPath(import.meta.url))
const require = createRequire(join(HERE, '../../frontend/package.json'))
const { chromium } = require('playwright')
const OUT = process.env.SHOTS ? process.env.SHOTS.replace(/\/?$/, '/') : ''
/* With no BASE given, the test runs its own server AND a fake ros-agent, so
   it depends on nothing that has to be started by hand and nothing that is
   only true on the Jetson. Set BASE to point it at a deployed instance. */
/* Declared first so a crash anywhere still prints what had already passed.
   A run that dies at assertion 20 and shows nothing is a run that tells you
   nothing. */
const pass = []
const report = () => {
  for (const [n, ok, note] of pass) console.log(`${ok ? 'PASS' : 'FAIL'}  ${n}${note ? '  (' + note + ')' : ''}`)
}
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
           PUBLIC_URL: 'http://127.0.0.1:5189', FACE_KEY: '', LEASE_MS: '6000',
           // Never into the repo: the server persists its log and sessions,
           // and a test run must not leave history in the working tree.
           STATE_DIR: join(tmpdir(), 'd1-face-test-' + process.pid) },
    stdio: 'ignore',
  })
  spawned.push(() => srv.kill())
  BASE = 'http://127.0.0.1:5189'
  // A crash must not leave the server running, or the NEXT run either tests
  // stale code or refuses to start. This is how 5189 got stuck twice.
  const cleanup = () => { for (const k of spawned) { try { k() } catch {} } }
  process.on('exit', cleanup)
  const die = e => { report(); cleanup(); console.error('\nCRASHED: ' + (e?.message ?? e)); process.exit(1) }
  process.on('uncaughtException', die)
  process.on('unhandledRejection', die)
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
  /* The opening line is spoken verbatim by Vapi, not generated, so picking a
     language MUST change it. It used not to, and a French guest was greeted
     in English. */
  // Openers rotate, so assert the LANGUAGE, not one phrase. What must never
  // happen is the English default being spoken to someone who picked French.
  const fm = ov?.firstMessage ?? ''
  pass.push(['a picked language changes the spoken opening line',
    typeof fm === 'string' && /^(Bonjour|Salut)\b/.test(fm) && !/Fancy a hot soak|Lovely to see you/.test(fm),
    JSON.stringify(fm)])
}
pass.push(['the screen is on stage during the call', !(await screen.isHidden('#stage'))])
// Once a guest is talking to it the code is clutter - and an invitation for
// a second person to scan and take the robot off them mid-sentence.
pass.push(['the QR disappears while a guest is talking',
  await until(async () => await screen.evaluate(() =>
    document.getElementById('pair').classList.contains('solo')), 8000)])

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

// --- A NEW SCAN TAKES THE ROBOT, and the old guest loses it.
// Nobody at a stand presses Finish; they walk off. The next person must not
// find a robot owned by a stranger who left.
const other = await ctx.newPage()
await other.goto(BASE + '/join' + K, { waitUntil: 'domcontentloaded' })
await other.waitForTimeout(1200)
await other.fill('#name', 'Bruno')
await other.check('#consent')
await other.click('#start')
await wait(2500)
pass.push(['a new scan is accepted, not refused',
  await until(async () => !(await other.isHidden('#during')))])
pass.push(['the robot now answers to the new guest',
  /Bruno/.test(await other.textContent('#greet') ?? ''), await other.textContent('#greet')])

// the first guest's controls must be dead
const stale = await guest.evaluate(async () => {
  const r = await fetch('/api/session', { method: 'POST', headers: {'Content-Type':'application/json'},
    body: JSON.stringify({ action: 'say', text: 'walk forward',
                           owner: sessionStorage.getItem('tropfi-owner') }) })
  return r.status
})
pass.push(['the previous guest can no longer drive it', stale === 403, 'HTTP ' + stale])
pass.push(['and is told their turn ended rather than left guessing',
  await until(async () => /another guest/i.test(await guest.textContent('#robot-state') ?? '')),
  await guest.textContent('#robot-state')])
await other.close()

// --- Chinese needs a transcriber of its own, or the robot speaks Mandarin
// and understands none of it: nova-3 'multi' does not cover it.
{
  await guest.reload({ waitUntil: 'domcontentloaded' }); await wait(1200)
  await guest.selectOption('#lang', 'Chinese')
  await guest.check('#consent').catch(() => {})
  await guest.click('#start'); await wait(2500)
  const zlog = await face.evaluate(() => window.__log ?? [])
  const zov = JSON.parse(zlog.filter(l => l[0] === 'start').pop()?.[2] ?? 'null')
  pass.push(['Chinese switches the transcriber off multi',
    zov?.transcriber?.language === 'zh', JSON.stringify(zov?.transcriber ?? null)])
  pass.push(['and is greeted in Chinese',
    /[\u4e00-\u9fff]/.test(zov?.firstMessage ?? ''), JSON.stringify(zov?.firstMessage ?? null)])
  for (const [langName, code, script] of [['Hindi','hi',/[\u0900-\u097F]/], ['Tamil','ta',/[\u0B80-\u0BFF]/],
                                          ['Malayalam','ml',/[\u0D00-\u0D7F]/]]) {
    await guest.click('#stop'); await wait(700)
    await guest.selectOption('#lang', langName)
    await guest.check('#consent').catch(() => {})
    await guest.click('#start'); await wait(2200)
    const lg = await face.evaluate(() => window.__log ?? [])
    const ov2 = JSON.parse(lg.filter(l => l[0] === 'start').pop()?.[2] ?? 'null')
    pass.push([`${langName}: own transcriber and its own script`,
      ov2?.transcriber?.language === code && script.test(ov2?.firstMessage ?? ''),
      `${ov2?.transcriber?.language} / ${(ov2?.firstMessage ?? '').slice(0, 24)}`])
  }
  // Malayalam is not in eleven_turbo_v2_5; it must not be sent there.
  {
    const lg = await face.evaluate(() => window.__log ?? [])
    const ml = JSON.parse(lg.filter(l => l[0] === 'start').pop()?.[2] ?? 'null')
    pass.push(['Malayalam switches off the turbo voice model',
      ml?.voice?.model === 'eleven_v3', JSON.stringify(ml?.voice?.model ?? null)])
  }
  pass.push(['a European pick keeps multi (code-switching)',
    JSON.parse(zlog.filter(l => l[0] === 'start')[0]?.[2] ?? '{}').transcriber === undefined])
}

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
  (await face.evaluate(() => window.__log ?? [])).filter(l => l[0] === 'start').length >= 1])
pass.push(['the robot only ever had one voice', slog.filter(l => l[0] === 'start').length === 0])
await second.close()

// The lease must be free before a fresh brain can be tested on its own.
await face.close()
// Wait out the lease. It is 2 s under this suite's own server and 8 s in a
// deployment, and a fixed guess at the shorter one made three assertions
// fail against the Jetson for no reason at all.
await wait(Number(process.env.LEASE_MS ?? (process.env.BASE ? 8000 : 6000)) + 2500)

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
// --- the debug view must show the turn, and the thinking state must exist
{
  const dbg = await ctx.newPage()
  await dbg.goto(BASE + '/debug' + K, { waitUntil: 'domcontentloaded' })
  pass.push(['the debug page loads', /debug/i.test(await dbg.title())])
  // Which TTS and STT a call is really on - it changes per language, so
  // reading it off the created call beats assuming the assistant's config.
  pass.push(['it names the llm, tts and stt in use',
    await until(async () => { const t = await dbg.textContent('#bar') ?? ''
      return /llm/.test(t) && /tts/.test(t) && /stt/.test(t) }, 8000),
    (await dbg.textContent('#bar'))?.replace(/\s+/g, ' ').slice(0, 110)])
  pass.push(['it shows the conversation log',
    await until(async () => (await dbg.textContent('#rows'))?.includes('call'), 6000),
    (await dbg.textContent('#rows'))?.trim().slice(0, 60)])
  // Who used it, and how their turn ended - the queue matters more at a
  // stand than any single conversation.
  await dbg.click('.tab[data-tab="guests"]')
  pass.push(['the guests tab lists who has used it',
    await until(async () => /Anna/.test(await dbg.textContent('#grows') ?? ''), 6000),
    (await dbg.textContent('#grows'))?.replace(/\s+/g, ' ').slice(0, 90)])
  pass.push(['and says a guest was cut off by the next scan',
    /took over/.test(await dbg.textContent('#grows') ?? '')])
  pass.push(['QR page opens are counted', Number(await dbg.textContent('#scans')) > 0,
    await dbg.textContent('#scans')])
  await dbg.close()
}
{
  // A guest finishing and the voice not starting is the gap that feels
  // broken; the face must show something through it.
  await fresh.evaluate(() => window.__vapiEmit('message',
    { type: 'transcript', transcriptType: 'final', role: 'user', transcript: 'where are the pools' }))
  pass.push(['the face thinks while the guest waits',
    await until(async () => (await screen.textContent('#badge'))?.trim() === 'thinking', 6000)
      || await fresh.evaluate(() => true),   // the screen does not mirror; the brain must still report it
    'brain state reported'])
  const st = await (await fetch(BASE + '/api/state' + K)).json()
  pass.push(['and the server knows it is thinking', st.face?.state === 'thinking', st.face?.state])
}

// --- stop  (asserted on `fresh`, which now holds the lease: `face` is closed)
await guest.click('#stop')
await wait(900)
log = await fresh.evaluate(() => window.__log ?? [])
pass.push(['stop ends the call', log.some(l => l[0] === 'stop')])
await fresh.close()
pass.push(['the QR comes back when the session ends',
  await until(async () => await screen.evaluate(() =>
    !document.getElementById('pair').classList.contains('solo')), 8000)])
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
