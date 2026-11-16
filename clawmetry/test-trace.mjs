/* Drives the Trace view in a real browser.
 *
 *   node clawmetry/test-trace.mjs               # starts its own collector
 *   BASE=http://127.0.0.1:8902 node ...         # against one already running
 *   SHOT=/tmp/t.png node ...                    # also save a screenshot
 *
 * The Python suite proves the numbers; this proves they are on the page. A
 * deviation correctly computed in predict.py and absent from the view is a
 * deviation nobody sees, and the whole claim of this view is that a person
 * can tell WHERE the time went and WHETHER the forecast held.
 *
 * It drives its own collector against a throwaway ledger and a copy of the
 * venue file, and feeds it one of each kind of action: gated-and-allowed,
 * ungated, and observed-after-the-fact (which the gate could not have
 * stopped). The assertions are about what the page says about those three.
 */
import { createRequire } from 'node:module'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { existsSync, mkdtempSync, copyFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
const HERE = dirname(fileURLToPath(import.meta.url))
const require = createRequire(join(HERE, '../frontend/package.json'))
const { chromium } = require('playwright')
const CHROME = process.env.CHROME
  ?? (process.platform === 'darwin'
    ? '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'
    : '/usr/bin/google-chrome')

const post = (b, p, body) => fetch(b + p, { method: 'POST',
  headers: { 'content-type': 'application/json' }, body: JSON.stringify(body) })

let B = process.env.BASE, child = null
if (!B) {
  const { spawn } = await import('node:child_process')
  const tmp = mkdtempSync(join(tmpdir(), 'cm-trace-'))
  copyFileSync(join(HERE, 'venue/leukerbad_therme.yaml'), join(tmp, 'venue.yaml'))
  const port = 8800 + Math.floor(Math.random() * 90)
  B = `http://127.0.0.1:${port}`
  // `fake` is the calibrated profile, so duration IS scored and the view has
  // a real sigma to show. On the robot's own uncalibrated profile the same
  // page has to say "not scored", which is the other branch asserted below.
  child = spawn('python3', ['-u', 'server.py', '--port', String(port), '--body', 'fake',
    '--venue', join(tmp, 'venue.yaml'), '--compliance', 'compliance.yaml',
    '--ledger', join(tmp, 'l.jsonl')], { cwd: HERE, stdio: 'ignore' })
  for (let i = 0; i < 40; i++) {
    try { await fetch(B + '/api/state'); break } catch { await new Promise(r => setTimeout(r, 250)) }
  }
  // A sentence first, so the commands that follow have something to be bound to.
  await post(B, '/vapi/events', { message: { type: 'transcript', role: 'user',
    transcriptType: 'final', transcript: 'can you walk me to the thermal pool',
    call: { id: 'c-trace-1' } } })
  await post(B, '/vapi', { message: { toolCallList: [{ id: 't1', name: 'walk_distance',
    arguments: { direction: 'forward', distance_cm: 120 } }] } })
  await post(B, '/vapi', { message: { toolCallList: [{ id: 't2', name: 'stop_robot',
    arguments: {} }] } })
  await post(B, '/observe/tool', { name: 'walk_distance',
    args: { direction: 'forward', distance_cm: 300 }, call_id: 't3', result: 'ok', code: 200 })
}

// -- the endpoint the view backfills from -------------------------------
// Covered here rather than in the Python suite because that suite drives the
// Hub directly and never stands up the HTTP layer.
const apiPass = []
{
  const all = await (await fetch(B + '/api/ledger?limit=600&skip=state')).json()
  apiPass.push(['skip=state removes the 2 Hz telemetry',
    all.events.every(e => e.kind !== 'state') && all.skipped.includes('state')])
  apiPass.push(['and leaves the decisions behind',
    all.events.some(e => e.kind === 'decision')])
  const five = await (await fetch(B + '/api/ledger?limit=5&skip=state')).json()
  apiPass.push(['limit returns the NEWEST n, not the oldest',
    five.events.length === 5
    && five.events.at(-1).seq === all.events.at(-1).seq])
  const r = await fetch(B + '/api/ledger?limit=abc')
  apiPass.push(['a limit that is not a number is a 400, not a 500', r.status === 400])
  const huge = await (await fetch(B + '/api/ledger?limit=99999')).json()
  apiPass.push(['an unbounded limit cannot be asked for', huge.events.length <= 2000])
}

const br = await chromium.launch(existsSync(CHROME) ? { executablePath: CHROME } : {})
const pg = await br.newPage({ viewport: { width: 1600, height: 1100 } })
const errs = []
pg.on('pageerror', e => errs.push('pageerror: ' + e.message))
pg.on('console', m => { if (m.type() === 'error') errs.push('console: ' + m.text()) })
await pg.goto(B, { waitUntil: 'domcontentloaded' })   // SSE is open: networkidle never fires

const pass = []
const txt = sel => pg.locator(sel).first().innerText()

await pg.click('.tab[data-v="trace"]')
await pg.waitForSelector('#turns .turn', { timeout: 8000 })

pass.push(['the live view is hidden on the trace tab', !(await pg.locator('#lview').isVisible())])
pass.push(['guardrails stays hidden too', !(await pg.locator('#gview').isVisible())])

const turns = pg.locator('#turns .turn')
pass.push(['one card per action', await turns.count() >= 3])

// -- the waterfall ------------------------------------------------------
pass.push(['every gated turn drew a stage waterfall',
  await pg.locator('#turns .turn .wf .seg').count() >= 3])
const keys = await pg.locator('#turns .turn').first().locator('.wfk').first().innerText()
pass.push(['the waterfall names the stage, not just a colour', /verdict|robot|predict|dispatch/.test(keys)])
pass.push(['and gives each stage a duration', /\d+(ms|s)/.test(keys)])

// -- prediction vs actual ----------------------------------------------
const walk = turns.filter({ hasText: 'distance_cm":120' }).first()
const pv = await walk.locator('table.pv').innerText()
pass.push(['predicted and actual sit side by side', /predicted/i.test(pv) && /actual/i.test(pv)])
pass.push(['the predicted end pose is shown', /x\s+1\.20/.test(pv)])
pass.push(['the predicted duration is shown', /6(\.0+)?\s*s/.test(pv)])
pass.push(['a deviation in sigma is shown', /σ/.test(pv)])
const wtxt = await walk.innerText()
pass.push(['position says WHY it was not scored, instead of showing a zero',
  /no pose|not scored/i.test(pv) && /no odometry/i.test(wtxt)])
pass.push(['the distance and confidence of the forecast are stated',
  /distance/.test(wtxt) && /confidence/.test(wtxt)])

// -- the honesty of the record -----------------------------------------
const watched = turns.filter({ hasText: 'tee_from_edge' }).first()
pass.push(['an action the gate could not stop says so', /watching only|could not have stopped/i
  .test(await watched.innerText())])
pass.push(['it is not badged as allowed, because nobody allowed it',
  /not enforced/i.test(await watched.innerText())
  && !/\ballowed\b/i.test((await watched.locator('.hd').innerText()))])
// The SSE replay and the backfill carry the same events; folding both doubled
// every breach line on the card. A card must list a breach once.
pass.push(['each breach is listed once per action', await pg.evaluate(() => {
  for (const t of document.querySelectorAll('#turns .turn')) {
    const seen = new Set()
    for (const w of t.querySelectorAll('.why')) {
      const s = w.textContent.trim()
      if (!s.startsWith('breach')) continue
      if (seen.has(s)) return false
      seen.add(s)
    }
  }
  return true
})])
const ungated = turns.filter({ hasText: 'stop_robot' }).first()
pass.push(['an ungated tool says there was nothing to forecast',
  /ungated|nothing to forecast/i.test(await ungated.innerText())])

// -- the headline numbers ----------------------------------------------
const stats = await txt('#tstats')
pass.push(['oversight cost has its own number', /oversight/i.test(stats)])
pass.push(['and so does what the guest waits for', /guest/i.test(stats)])
pass.push(['the worst deviation is on the header', /deviation/i.test(stats)])

// -- the river ---------------------------------------------------------
await pg.waitForSelector('#river tr[data-c]')
const rows = pg.locator('#river tr[data-c]')
pass.push(['the river lists raw ledger events', await rows.count() >= 10])
const river = await txt('#river')
pass.push(['every kind in the chain reaches the river',
  ['utterance', 'intent', 'prediction', 'decision', 'command', 'outcome']
    .every(k => river.includes(k))])
const kinds = () => pg.evaluate(() =>
  [...document.querySelectorAll('#river td.kd')].map(td => td.textContent.trim()))
pass.push(['state samples are hidden until asked for', !(await kinds()).includes('state')])
await pg.click('.filt button[data-f="state"]')
pass.push(['and appear when asked for', (await kinds()).includes('state')])
await pg.click('.filt button[data-f="state"]')
pass.push(['and hide again', !(await kinds()).includes('state')])

// clicking a row isolates that one action
const before = await turns.count()
await rows.filter({ hasText: 'prediction' }).first().click()
await pg.waitForSelector('#tclear')
pass.push(['clicking a river row isolates its action',
  await pg.locator('#turns .turn').count() === 1 && before > 1])
await pg.click('#tclear')
pass.push(['and show-all brings the rest back', await pg.locator('#turns .turn').count() === before])

// -- the gap column is the point of the view ---------------------------
pass.push(['gaps are measured between VISIBLE rows',
  await pg.evaluate(() => {
    const tds = [...document.querySelectorAll('#river tr[data-c]')].map(tr =>
      tr.querySelector('td.g').textContent.trim())
    return tds.filter(Boolean).length > 0 && tds[tds.length - 1] === ''
  })])

if (process.env.SHOT) await pg.screenshot({ path: process.env.SHOT, fullPage: true })
await br.close()
if (child) child.kill()

let bad = 0
for (const [n, ok] of apiPass) { if (!ok) bad++; console.log((ok ? '  ok  ' : '  FAIL') + '  ' + n) }
for (const [n, ok] of pass) { if (!ok) bad++; console.log((ok ? '  ok  ' : '  FAIL') + '  ' + n) }
for (const e of errs) { bad++; console.log('  FAIL  ' + e) }
const total = pass.length + apiPass.length
console.log(`\n${total - (bad - errs.length)}/${total} checks, ${errs.length} page errors`)
process.exit(bad ? 1 : 0)
