import { openTextFallback } from './textFallback'
import { expect, test } from '@playwright/test'
import { mockMedia } from './media'

test('production responses apply the static demo security policy', async ({ request }) => {
  const response = await request.get('/join')
  expect(response.status()).toBe(200)
  const headers = response.headers()
  expect(headers['content-security-policy']).toContain("connect-src 'none'")
  expect(headers['content-security-policy']).toContain("media-src 'self' blob:")
  expect(headers['content-security-policy']).toContain("frame-ancestors 'none'")
  expect(headers['content-security-policy']).not.toMatch(/unsafe-inline|unsafe-eval/)
  expect(headers['permissions-policy']).toBe('microphone=(self), camera=(), geolocation=()')
  expect(headers['referrer-policy']).toBe('no-referrer')
  expect(headers['x-content-type-options']).toBe('nosniff')
  expect(headers['x-frame-options']).toBe('DENY')
  expect(headers['strict-transport-security']).toBe('max-age=31536000')
  expect(headers['cache-control']).toBe('no-store')
  expect(headers['set-cookie']).toBeUndefined()
  const manifest = await request.get('/demo-config.json')
  expect(await manifest.json()).toEqual({ profile: 'static-demo', taskMode: 'demo', transcriptionConnected: false, sessionDurationSeconds: 600 })
})

for (const route of ['/join', '/join/', '/session', '/session/', '/privacy', '/privacy/']) {
  test(`direct visit and refresh ${route}`, async ({ page }) => {
    const response = await page.goto(route)
    expect(response?.status()).toBe(200)
    const name = route.startsWith('/privacy') ? 'Privacy, in plain words.' : /Hey there!/
    await expect(page.getByRole('heading', { level: 1, name })).toBeVisible()
    if (route.startsWith('/session')) {
      await expect(page).toHaveURL('/join')
      await expect(page.getByRole('status')).toContainText('no active demo session')
    }
    const refreshed = await page.reload()
    expect(refreshed?.status()).toBe(200)
    await expect(page.getByRole('heading', { level: 1, name })).toBeVisible()
  })
}

test('missing APIs/assets are HTTP 404 instead of fake SPA success', async ({ request, page }) => {
  for (const path of ['/api/requests', '/api/transcribe', '/assets/missing.js', '/.env', '/_headers']) {
    const response = await request.get(path)
    expect(response.status()).toBe(404)
  }
  const unknown = await page.goto('/unknown-page')
  expect(unknown?.status()).toBe(404)
  await page.getByRole('button', { name: 'Go to welcome' }).click()
  await expect(page).toHaveURL('/join')
})

test('CSP blocks fetch even to the same origin', async ({ page }) => {
  await page.goto('/join')
  const result = await page.evaluate(async () => {
    const violations: string[] = []
    document.addEventListener('securitypolicyviolation', event => violations.push(event.effectiveDirective))
    let blocked = false
    try { await fetch('/__static_csp_probe__') } catch { blocked = true }
    await new Promise(resolve => setTimeout(resolve, 50))
    return { blocked, violations }
  })
  expect(result.blocked).toBe(true)
  expect(result.violations).toContain('connect-src')
})

test('strict policy preserves deliberate capture and a mock-only task without storage or policy violations', async ({ page, context }) => {
  await mockMedia(page)
  await page.addInitScript(() => {
    const violations: string[] = []
    Object.defineProperty(window, '__deploymentViolations', { value: violations })
    document.addEventListener('securitypolicyviolation', event => violations.push(event.effectiveDirective))
  })
  await page.clock.install()
  const unexpected: string[] = []
  page.on('request', request => {
    const url = request.url()
    if (request.method() !== 'GET' || !(url.startsWith('http://127.0.0.1:4173/') || url.startsWith('blob:http://127.0.0.1:4173/'))) unexpected.push(url)
  })
  await page.goto('/join')
  await page.getByRole('button', { name: 'Continue as Guest' }).click()
  expect(await page.evaluate(() => window.__voiceTest.requests)).toBe(0)
  await expect(page.getByText('Demo mode', {exact:true})).toBeVisible()
  await expect(page.getByText('Local demo · no real robot connection')).toBeVisible()
  await page.getByRole('button', { name: 'Start Conversation' }).click()
  expect(await page.evaluate(() => window.__voiceTest.requests)).toBe(1)
  await page.getByRole('button', { name: 'End Call', exact: true }).click()
  expect(await page.evaluate(() => window.__voiceTest.stops)).toBe(1)
  await openTextFallback(page)
  await page.getByLabel('Your request', { exact: true }).fill('Hello')
  await page.getByRole('button', { name: 'Review request', exact: true }).click()
  await page.getByRole('button', { name: 'Confirm demo request' }).click()
  await page.clock.runFor(4500)
  await expect(page.getByRole('heading', { name: 'Demo response ready', exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Leave session' }).click()
  expect(await page.evaluate(() => ({ local: localStorage.length, session: sessionStorage.length }))).toEqual({ local: 0, session: 0 })
  expect(await page.evaluate(async () => await indexedDB.databases())).toEqual([])
  expect(await page.evaluate(async () => (await navigator.serviceWorker.getRegistrations()).length)).toBe(0)
  expect(await context.cookies()).toEqual([])
  expect(await page.evaluate(() => (window as unknown as { __deploymentViolations: string[] }).__deploymentViolations)).toEqual([])
  expect(unexpected).toEqual([])
})
