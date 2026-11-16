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
const BASE = process.env.BASE ?? 'http://127.0.0.1:5180'
// The deployed server is key-guarded; a local one usually is not. The first
// page load carries the key and the server sets a cookie for the rest.
const K = process.env.FACE_KEY ? `?k=${encodeURIComponent(process.env.FACE_KEY)}` : ''
const wait = ms => new Promise(r => setTimeout(r, ms))

// A fake SDK. The real one would place a billable call to an assistant whose
// robot tools point at the live Jetson - a test must not be able to move a robot.
const STUB = `
export default class Vapi {
  constructor(k){ window.__log = []; this.h = {} ; window.__log.push(['ctor', k]) }
  on(e,f){ (this.h[e] ||= []).push(f) }
  emit(e,...a){ (this.h[e]||[]).forEach(f=>f(...a)) }
  async start(id, ov){ window.__log.push(['start', id, JSON.stringify(ov ?? null)]); this.emit('call-start'); return {} }
  async stop(){ window.__log.push(['stop']); this.emit('call-end') }
  send(m){ window.__log.push(['send', JSON.stringify(m)]) }
}`

const CHROME = process.env.CHROME ?? (process.platform === 'darwin'
  ? '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' : '/usr/bin/google-chrome')
const b = await chromium.launch({
  ...(existsSync(CHROME) ? { executablePath: CHROME } : {}),
  args: ['--use-fake-ui-for-media-stream', '--use-fake-device-for-media-stream'],
})
const ctx = await b.newContext({ permissions: ['microphone'] })
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
await face.goto(BASE + '/face' + K, { waitUntil: 'networkidle' })
pass.push(['face opens on the arm screen', !(await face.isHidden('#arm'))])
await face.click('#arm')
await wait(1500)
pass.push(['arming shows the QR', !(await face.isHidden('#idle'))])
pass.push(['QR encodes the join URL', (await face.locator('#qr canvas').count()) === 1
           || (await face.textContent('#qr-fallback'))?.includes('/join')])

// --- guest phone
await guest.goto(BASE + '/join' + K, { waitUntil: 'networkidle' })
pass.push(['Start is disabled before consent', await guest.isDisabled('#start')])
await guest.fill('#name', 'Anna')
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
pass.push(['face switched to the live screen', !(await face.isHidden('#live'))])

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

// --- stop
await guest.click('#stop')
await wait(900)
log = await face.evaluate(() => window.__log ?? [])
pass.push(['stop ends the call', log.some(l => l[0] === 'stop')])
pass.push(['face returns to the QR', !(await face.isHidden('#idle'))])

// a say with no session must be refused
const late = await guest.evaluate(async () => (await fetch('/api/session', { method:'POST',
  headers:{'Content-Type':'application/json'}, body: JSON.stringify({action:'say', text:'hi'}) })).status)
pass.push(['say is refused with no session', late === 409, 'HTTP ' + late])

if (OUT) await guest.screenshot({ path: OUT + 'guest.png', fullPage: true })

let bad = 0
for (const [n, ok, note] of pass) { if (!ok) bad++; console.log(`${ok?'PASS':'FAIL'}  ${n}${note?'  ('+note+')':''}`) }
console.log(`\n${pass.length-bad}/${pass.length} passed`)
if (errs.length) console.log('page errors:', errs.slice(0,8))
await b.close()
process.exit(bad ? 1 : 0)
