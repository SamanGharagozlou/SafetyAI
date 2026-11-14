import { expect, test } from '@playwright/test'
import { mockMedia } from './media'

async function join(page: import('@playwright/test').Page) {
  await page.goto('/join')
  await page.getByLabel('What should I call you?').fill('Nova')
  await page.getByRole('button', { name: "Let's Get Started" }).click()
}

for (const viewport of [{ width: 320, height: 568 }, { width: 390, height: 844 }, { width: 667, height: 375 }]) {
  test(`private recording, playback, and text at ${viewport.width}×${viewport.height}`, async ({ page }, testInfo) => {
    await page.setViewportSize(viewport)
    await mockMedia(page)
    const unexpectedRequests: string[] = []
    const pageErrors: string[] = []
    page.on('request', request => {
      if (request.method() !== 'GET' || !(request.url().startsWith('http://127.0.0.1:4173/') || request.url().startsWith('blob:http://127.0.0.1:4173/'))) unexpectedRequests.push(request.url())
    })
    page.on('pageerror', error => pageErrors.push(error.message))
    await join(page)
    expect(await page.evaluate(() => window.__voiceTest.requests)).toBe(0)
    await page.getByRole('button', { name: 'Start Recording' }).click()
    await expect(page.getByRole('timer')).toContainText('00:00')
    await expect(page.getByRole('button', { name: 'Stop Recording' })).toBeFocused()
    await expect(page.locator('.recording-dot')).toHaveCSS('animation-name', 'none')
    await page.screenshot({ path: testInfo.outputPath('recording.png'), fullPage: true })
    await page.getByRole('button', { name: 'Stop Recording' }).click()
    await expect(page.getByRole('heading', { name: 'Recording ready' })).toBeFocused()
    expect(await page.evaluate(() => window.__voiceTest.stops)).toBe(1)
    const audio = page.getByLabel('Play your recording')
    await expect(audio).toHaveAttribute('src', /^blob:/)
    await expect.poll(() => audio.evaluate(element => (element as HTMLAudioElement).readyState)).toBeGreaterThan(0)
    await audio.evaluate(element => (element as HTMLAudioElement).play())
    await expect.poll(() => audio.evaluate(element => (element as HTMLAudioElement).paused)).toBe(false)
    await audio.evaluate(element => (element as HTMLAudioElement).pause())
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await page.screenshot({ path: testInfo.outputPath('review.png'), fullPage: true })
    await page.getByRole('button', { name: 'Retry recording' }).click()
    expect(await page.evaluate(() => window.__voiceTest.revoked)).toBe(1)
    await page.getByRole('button', { name: 'Cancel', exact: true }).click()
    expect(await page.evaluate(() => window.__voiceTest.stops)).toBe(2)
    await page.getByRole('button', { name: 'Start Recording' }).click()
    await page.getByRole('button', { name: 'Stop Recording' }).click()
    await page.getByRole('button', { name: 'Continue', exact: true }).click()
    await expect(page.getByText(/Your recording has not been uploaded/)).toBeVisible()
    await page.getByLabel('Your request', { exact: true }).fill('What can you tell me about yourself?')
    await page.getByRole('button', { name: 'Continue', exact: true }).click()
    await expect(page.getByRole('heading', { name: 'Your request is ready' })).toBeFocused()
    await page.getByRole('button', { name: 'Edit request' }).click()
    await expect(page.getByLabel('Your request', { exact: true })).toHaveValue('What can you tell me about yourself?')
    await expect(page.getByRole('heading', { name: 'Nice to meet you, Nova!' })).toBeVisible()
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await page.screenshot({ path: testInfo.outputPath('typed.png'), fullPage: true })
    await page.getByRole('button', { name: 'End Session' }).click()
    await expect(page.getByRole('status')).toContainText('has ended')
    expect(unexpectedRequests).toEqual([])
    expect(pageErrors).toEqual([])
    expect(await page.evaluate(() => ({ local: localStorage.length, session: sessionStorage.length }))).toEqual({ local: 0, session: 0 })
  })
}

for (const permission of ['NotAllowedError', 'NotFoundError', 'NotReadableError'] as const) {
  test(`recording permission/device error ${permission} still permits typed requests`, async ({ page }) => {
    await mockMedia(page, permission)
    await join(page)
    await page.getByRole('button', { name: 'Start Recording' }).click()
    await expect(page.getByRole('alert')).toBeVisible()
    await page.getByRole('button', { name: 'Type a request' }).click()
    await page.getByLabel('Your request', { exact: true }).fill('Hello from the keyboard.')
    await page.getByRole('button', { name: 'Continue', exact: true }).click()
    await expect(page.getByRole('heading', { name: 'Your request is ready' })).toBeVisible()
  })
}

test('unsupported browser keeps the typed alternative usable', async ({ page }) => {
  await page.addInitScript(() => Object.defineProperty(window, 'MediaRecorder', { configurable: true, value: undefined }))
  await join(page)
  await expect(page.getByRole('button', { name: 'Start Recording' })).toBeDisabled()
  await expect(page.getByText(/This browser cannot record audio/)).toBeVisible()
  await page.getByRole('button', { name: 'Type a request' }).click()
  await expect(page.getByLabel('Your request', { exact: true })).toBeFocused()
})

test('canceled permission grant is stopped without recording', async ({ page }) => {
  await mockMedia(page, 'pending')
  await join(page)
  await page.getByRole('button', { name: 'Start Recording' }).click()
  await expect(page.getByText('Permission requested', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Cancel', exact: true }).click()
  await page.evaluate(() => window.__voiceTest.resolve?.())
  await expect(page.getByRole('button', { name: 'Start Recording' })).toBeEnabled()
  expect(await page.evaluate(() => window.__voiceTest.stops)).toBe(1)
  expect(await page.evaluate(() => window.__voiceTest.requests)).toBe(1)
})

test('returning to the session does not duplicate a still-pending native permission request', async ({ page }) => {
  await mockMedia(page, 'pending')
  await join(page)
  await page.getByRole('button', { name: 'Start Recording' }).click()
  await page.getByRole('link', { name: 'Privacy information' }).click()
  await page.getByRole('button', { name: 'Back to session' }).click()
  await page.getByRole('button', { name: 'Start Recording' }).click()
  await expect(page.getByRole('alert')).toContainText('earlier microphone permission prompt')
  expect(await page.evaluate(() => window.__voiceTest.requests)).toBe(1)
  await page.evaluate(() => window.__voiceTest.resolve?.())
  expect(await page.evaluate(() => window.__voiceTest.stops)).toBe(1)
})

test('recording automatically stops after 20 seconds', async ({ page }) => {
  await mockMedia(page)
  await page.clock.install()
  await join(page)
  await page.getByRole('button', { name: 'Start Recording' }).click()
  await page.clock.runFor(20_000)
  await expect(page.getByRole('heading', { name: 'Recording ready' })).toBeVisible()
  expect(await page.evaluate(() => window.__voiceTest.stops)).toBe(1)
})

for (const leave of ['privacy', 'end', 'browser-back', 'expiry']) {
  test(`cleans up recording on ${leave}`, async ({ page }) => {
    await mockMedia(page)
    await page.clock.install()
    await join(page)
    await page.getByRole('button', { name: 'Start Recording' }).click()
    if (leave === 'privacy') {
      await page.getByRole('link', { name: 'Privacy information' }).click()
      await page.getByRole('button', { name: 'Back to session' }).click()
      await expect(page.getByRole('button', { name: 'Start Recording' })).toBeVisible()
    } else if (leave === 'end') await page.getByRole('button', { name: 'End Session' }).click()
    else if (leave === 'browser-back') await page.goBack()
    else await page.clock.fastForward(600_000)
    expect(await page.evaluate(() => window.__voiceTest.stops)).toBe(1)
    expect(await page.evaluate(() => window.__voiceTest.requests)).toBe(1)
    await expect(page.getByRole('button', { name: 'Stop Recording' })).toHaveCount(0)
  })
}
