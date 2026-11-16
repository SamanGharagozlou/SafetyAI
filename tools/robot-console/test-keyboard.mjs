/**
 * Keyboard teleop contract for the D1 robot console.
 *
 * POINT THIS AT THE SIMULATOR, NEVER THE ROBOT. It presses movement keys, and
 * on real hardware that is a machine moving in a room with people in it.
 *
 *   node tools/robot-sim/server.mjs &
 *   ROBOT_URL=http://127.0.0.1:5175 D1_ROBOT_TOKEN=sim PORT=5177 \
 *     node tools/robot-console/server.mjs &
 *   node tools/robot-console/test-keyboard.mjs
 *
 * Playwright is resolved from frontend/node_modules and drives the installed
 * Google Chrome, so no browser download is needed.
 *
 * What it is really guarding: the keyboard must stay a thin layer over the
 * buttons. The moment a key calls the API directly it stops inheriting the
 * disabled state and the busy lock, and the console starts disagreeing with
 * the robot about whether it is moving.
 */
import { createRequire } from 'node:module'
import { existsSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
const HERE = dirname(fileURLToPath(import.meta.url))
// Playwright lives in the frontend workspace; this tool has no deps of its own.
const require = createRequire(join(HERE, '../../frontend/package.json'))
const { chromium } = require('playwright')
const B = process.env.CONSOLE ?? 'http://127.0.0.1:5177/'
// Use a browser that is already on the machine rather than a downloaded one.
// CHROME= overrides; falling back to Playwright's own build if it is installed.
const CHROME = process.env.CHROME
  ?? (process.platform === 'darwin'
    ? '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
    : '/usr/bin/google-chrome')
const browser = await chromium.launch(existsSync(CHROME) ? { executablePath: CHROME } : {})
const page = await browser.newPage()
const sent = []
page.on('request', r => {
  if (r.url().endsWith('/api/tool') && r.method() === 'POST') {
    try { sent.push(JSON.parse(r.postData())) } catch {}
  }
})
const errs = []
page.on('pageerror', e => errs.push(e.message))
await page.goto(B, { waitUntil: 'networkidle' })

const clear = () => { sent.length = 0 }
const last  = () => sent.map(s => `${s.name}:${JSON.stringify(s.args)}`)
const wait  = ms => new Promise(r => setTimeout(r, ms))
const pass  = []

// stand up first so needs-stand controls become enabled
await page.keyboard.press('t'); await wait(1500)
pass.push(['T = stand up', last().some(x => x.includes('stand_up'))])

// wait for the fwd button to be enabled by the status poll
await page.waitForSelector('button[data-move="forward"]:not([disabled])', { timeout: 15000 }).catch(() => {})

clear(); await page.keyboard.press('w'); await wait(600)
pass.push(['W = forward', last().some(x => x.includes('"direction":"forward"'))])

clear(); await page.keyboard.press('q'); await wait(600)
pass.push(['Q = turn_left', last().some(x => x.includes('turn_left'))])

clear(); await page.keyboard.press('ArrowUp'); await wait(600)
pass.push(['ArrowUp = forward', last().some(x => x.includes('"direction":"forward"'))])

clear(); await page.keyboard.press('1'); await wait(600)
pass.push(['1 = gesture nod', last().some(x => x.includes('"gesture":"nod"'))])

clear(); await page.keyboard.press('0'); await wait(600)
pass.push(['0 = gesture welcome', last().some(x => x.includes('"gesture":"welcome"'))])

clear(); await page.keyboard.press('Space'); await wait(500)
pass.push(['Space = stop', last().some(x => x.includes('stop_robot'))])

clear(); await page.keyboard.press('Escape'); await wait(500)
pass.push(['Esc = stop', last().some(x => x.includes('stop_robot'))])

// autorepeat must not queue commands
clear(); await page.keyboard.down('w'); await wait(1200); await page.keyboard.up('w'); await wait(800)
pass.push(['holding W sends once', sent.length === 1, `sent ${sent.length}`])

// typing must be safe
await page.evaluate(() => { const i = document.querySelector('#dist'); i.type='text'; i.focus() })
clear(); await page.keyboard.press('w'); await wait(400)
pass.push(['W while typing = nothing', sent.length === 0, `sent ${sent.length}`])
clear(); await page.keyboard.press('Escape'); await wait(400)
pass.push(['Esc while typing = stop', last().some(x => x.includes('stop_robot'))])
await page.evaluate(() => document.querySelector('#dist').blur())

// slider nudge
const d0 = await page.inputValue('#dur')
await page.keyboard.press('['); await wait(200)
const d1 = await page.inputValue('#dur')
pass.push(['[ lowers duration', Number(d1) < Number(d0), `${d0} -> ${d1}`])

// help overlay
await page.keyboard.press('?'); await wait(300)
pass.push(['? opens help', await page.isVisible('#kbd-help')])
await page.keyboard.press('?'); await wait(300)
pass.push(['? closes help', !(await page.isVisible('#kbd-help'))])

// Enter must activate the focused button, not Walk
await page.evaluate(() => document.querySelector('[data-args*="lie_down"]').focus())
clear(); await page.keyboard.press('Enter'); await wait(700)
pass.push(['Enter on focused button = that button', last().some(x => x.includes('lie_down')) && !last().some(x => x.includes('walk_distance')), last().join(',')])
await page.evaluate(() => document.activeElement.blur())
// lie_down just ran, so Walk is correctly disabled - stand up again first
// the typing test above left "w30" in the distance box, which the Walk
// handler correctly rejects - reset it to a valid value
await page.evaluate(() => { const i = document.querySelector('#dist'); i.type='number'; i.value='30' })
await page.keyboard.press('t'); await wait(1800)
await page.waitForSelector('#walk:not([disabled])', { timeout: 15000 }).catch(() => {})
clear(); await page.keyboard.press('Enter'); await wait(900)
pass.push(['Enter with nothing focused = Walk', last().some(x => x.includes('walk_distance')), last().join(',')])

// badges visible
pass.push(['key badges rendered', (await page.locator('.kb').count()) === 16, `${await page.locator('.kb').count()} badges`])

const SHOTS = process.env.SHOTS ?? ''   // SHOTS=/some/dir to save screenshots
if (SHOTS) await page.screenshot({ path: join(SHOTS, 'kb.png') })
await page.keyboard.press('?'); await wait(300)
if (SHOTS) await page.screenshot({ path: join(SHOTS, 'kb-help.png') })

let bad = 0
for (const [n, ok, note] of pass) { if (!ok) bad++; console.log(`${ok ? 'PASS' : 'FAIL'}  ${n}${note ? '  ('+note+')' : ''}`) }
console.log(`\n${pass.length - bad}/${pass.length} passed`)
if (errs.length) console.log('page errors:', errs)
await browser.close()
process.exit(bad ? 1 : 0)
