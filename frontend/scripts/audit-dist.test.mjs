// @vitest-environment node
import { mkdtemp, mkdir, readFile, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { afterEach, describe, expect, it } from 'vitest'
import { auditDist } from './audit-dist.mjs'

const directories = []
afterEach(async () => { await Promise.all(directories.splice(0).map(path => rm(path, { recursive: true, force: true }))) })
async function fixture() {
  const root = await mkdtemp(join(tmpdir(), 'clawmetry-artifact-test-'))
  directories.push(root)
  const html = '<!doctype html><html><body><script type="module" src="/assets/index-test.js"></script></body></html>'
  await mkdir(join(root, 'assets'))
  for (const name of ['join', 'session', 'privacy']) {
    await mkdir(join(root, name))
    await writeFile(join(root, name, 'index.html'), html)
  }
  await writeFile(join(root, 'index.html'), html)
  await writeFile(join(root, '404.html'), html)
  await writeFile(join(root, 'assets/index-test.js'), 'export const fixture = true;')
  await writeFile(join(root, 'demo-config.json'), JSON.stringify({ profile: 'static-demo', taskMode: 'demo', transcriptionConnected: false, sessionDurationSeconds: 600 }))
  await writeFile(join(root, '_headers'), await readFile(new URL('../public/_headers', import.meta.url)))
  await writeFile(join(root, '_redirects'), await readFile(new URL('../public/_redirects', import.meta.url)))
  return root
}

describe('deployment artifact audit', () => {
  it('accepts reviewed static assets and returns a reproducible fingerprint', async () => {
    const root = await fixture()
    const first = await auditDist(root)
    expect(first.count).toBe(9)
    expect((await auditDist(root)).sha256).toBe(first.sha256)
  })
  it.each(['.env', 'secret.pem', 'assets/index-test.js.map', '_worker.js'])('rejects unpublished or privileged files: %s', async name => {
    const root = await fixture()
    await writeFile(join(root, name), 'synthetic test fixture')
    await expect(auditDist(root)).rejects.toThrow(/Unreviewed file/)
  })
  it.each([
    'const address = "192.168.10.40";',
    'const url = "https://example.invalid/analytics";',
    'const token = "sk-proj-syntheticTestingCanaryNotARealCredential000";',
    'const key = "-----BEGIN PRIVATE KEY-----";',
  ])('rejects address, unreviewed URL, or credential-like content without printing it', async content => {
    const root = await fixture()
    await writeFile(join(root, 'assets/index-test.js'), content)
    try { await auditDist(root); throw new Error('Expected rejection') } catch (error) {
      expect(error.message).toMatch(/detected|Unreviewed URL/)
      expect(error.message).not.toContain(content)
    }
  })
  it('requires demo-only metadata and matching route entries', async () => {
    const root = await fixture()
    await writeFile(join(root, 'demo-config.json'), JSON.stringify({ profile: 'static-demo', taskMode: 'api', transcriptionConnected: true, sessionDurationSeconds: 600 }))
    await expect(auditDist(root)).rejects.toThrow(/configuration manifest/)
    await writeFile(join(root, 'demo-config.json'), JSON.stringify({ profile: 'static-demo', taskMode: 'demo', transcriptionConnected: false, sessionDurationSeconds: 600 }))
    await writeFile(join(root, 'join/index.html'), '<html>stale route</html>')
    await expect(auditDist(root)).rejects.toThrow(/Stale or different route/)
  })
  it('requires the demo CSP rather than silently accepting network access', async () => {
    const root = await fixture()
    await writeFile(join(root, '_headers'), "/*\n  Content-Security-Policy: default-src 'self'; connect-src 'self'\n")
    await expect(auditDist(root)).rejects.toThrow(/CSP/)
  })
})
