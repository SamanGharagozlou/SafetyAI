/* Drives the guardrails view in a real browser.
 *
 *   node clawmetry/test-guardrails.mjs          # starts its own collector
 *   BASE=http://127.0.0.1:8902 node ...         # against one already running
 *   SHOT=/tmp/g.png node ...                    # also save a screenshot
 *
 * Playwright comes from frontend/node_modules and drives the installed Chrome;
 * CHROME= overrides the path. The Python suite covers the rules; this covers
 * the thing an operator actually touches, because a classification that is
 * correct in policy.py and unreachable in the page is a guardrail nobody can
 * set.
 *
 * It ALWAYS works on a throwaway copy of the venue file. A test that edited
 * the document the venue signs would be a test that changes what the robot
 * does.
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

let B = process.env.BASE, child = null
if (!B) {
  const { spawn } = await import('node:child_process')
  const tmp = mkdtempSync(join(tmpdir(), 'cm-guard-'))
  copyFileSync(join(HERE, 'venue/leukerbad_therme.yaml'), join(tmp, 'venue.yaml'))
  const port = 8900 + Math.floor(Math.random() * 90)
  B = `http://127.0.0.1:${port}`
  child = spawn('python3', ['-u', 'server.py', '--port', String(port),
    '--venue', join(tmp, 'venue.yaml'), '--compliance', 'compliance.yaml',
    '--ledger', join(tmp, 'l.jsonl')], { cwd: HERE, stdio: 'ignore' })
  for (let i = 0; i < 40; i++) {
    try { await fetch(B + '/api/venue'); break } catch { await new Promise(r => setTimeout(r, 250)) }
  }
}
const br = await chromium.launch(existsSync(CHROME) ? { executablePath: CHROME } : {})
const pg = await br.newPage()
const errs = []
pg.on('pageerror', e => errs.push('pageerror: ' + e.message))
pg.on('console', m => { if (m.type() === 'error') errs.push('console: ' + m.text()) })
await pg.goto(B, { waitUntil: 'domcontentloaded' })   // SSE is open: networkidle never fires
const pass = []
await pg.click('.tab[data-v="guard"]')
await pg.waitForSelector('#zones tbody tr', { timeout: 5000 })
pass.push(['guardrails tab renders zones', await pg.locator('#zones tbody tr td.zn, #zones tbody tr .zn').count() >= 9])
pass.push(['floor plan drew polygons', await pg.locator('svg.plan polygon').count() >= 9])
pass.push(['nothing pending at rest', (await pg.locator('#pending .empty').count()) === 1])
pass.push(['the live view is hidden on the guardrails tab',
  !(await pg.locator('#lview').isVisible())])

// tighten one cap
await pg.fill('input[data-k="zones.concourse.speed_cap"]', '0.18')
await pg.waitForSelector('#pending .chg.tighten', { timeout: 5000 })
pass.push(['a lower cap reads as more restraint', (await pg.locator('#pending .chg.tighten').count()) === 1])
pass.push(['no confirmation box for a tighten', (await pg.locator('#opconfirm').count()) === 0])
pass.push(['apply blocked without a name', await pg.locator('#opgo').isDisabled()])
await pg.fill('#opname', 'M. Brunner, Duty Manager')
pass.push(['apply enabled once named', !(await pg.locator('#opgo').isDisabled())])

// undo it by hand -> the pending change must disappear
await pg.fill('input[data-k="zones.concourse.speed_cap"]', '0.3')
await pg.waitForTimeout(500)
pass.push(['restoring the original clears the edit', (await pg.locator('#pending .empty').count()) === 1])

// A loosen that is NOT enough on its own: marking a zone measured cannot lift
// its speed while the venue itself is unsigned, because the unsigned clamp sits
// underneath. So sign off in the same change and watch the effective speed move.
await pg.selectOption('select[data-k="zones.reception.verified"]', 'on_site')
await pg.fill('input[data-k="venue.signed_off_by"]', 'M. Brunner, Duty Manager')
await pg.fill('input[data-k="venue.signed_off_date"]', '2026-11-16')
await pg.waitForSelector('#pending .chg.loosen', { timeout: 5000 })
pass.push(['"measured on site" and signing both read as more freedom',
  (await pg.locator('#pending .chg.loosen').count()) === 2])
pass.push(['a loosen demands confirmation', (await pg.locator('#opconfirm').count()) === 1])
await pg.fill('#opname', 'M. Brunner, Duty Manager')
await pg.fill('#opwhy', 'tape-measured at the 07:00 walkthrough')
pass.push(['still blocked before confirming', await pg.locator('#opgo').isDisabled()])
await pg.check('#opconfirm')
pass.push(['unblocked after confirming', !(await pg.locator('#opgo').isDisabled())])
await pg.click('#opgo')
await pg.waitForTimeout(1500)
const eff = await pg.locator('#zones tbody tr', { hasText: 'reception' }).first().locator('.eff').textContent()
pass.push(['effective speed rose once signed AND measured', eff.trim() === '0.30', `effective now ${eff.trim()}`])
const warn = await pg.locator('#vwarn').textContent()
pass.push(['the NOT SIGNED OFF warning cleared', !warn.includes('NOT SIGNED OFF')])
pass.push(['sign-off panel shows who signed', (await pg.locator('#signoff').textContent()).includes('M. Brunner')])
pass.push(['pending cleared after apply', (await pg.locator('#pending .empty').count()) === 1])

if (process.env.SHOT) await pg.screenshot({ path: process.env.SHOT, fullPage: true })
await br.close()
if (child) child.kill()
let bad = 0
for (const [n, ok, note] of pass) { if (!ok) bad++; console.log(`${ok ? ' ok ' : 'FAIL'}  ${n}${note ? '  (' + note + ')' : ''}`) }
if (errs.length) { console.log('\nJS ERRORS:'); errs.forEach(e => console.log('  ' + e)); }
console.log(`\n${pass.length - bad}/${pass.length} passed`)
process.exit(bad || errs.length ? 1 : 0)
