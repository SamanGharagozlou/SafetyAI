import { detectSpeechTurn, openTextFallback } from './textFallback'
import { expect, test } from '@playwright/test'
import type { Page } from '@playwright/test'
import { mockMedia } from './media'

async function draft(page: Page, text = 'Hello') {
  await mockMedia(page)
  await page.goto('/join')
  await page.getByLabel('What should I call you?').fill('Nova')
  await page.getByRole('button', { name: "Let's Get Started" }).click()
  await openTextFallback(page)
  await page.getByLabel('Your request', { exact: true }).fill(text)
  await page.getByRole('button', { name: 'Review request', exact: true }).click()
}
async function confirm(page: Page) {
  await page.getByRole('button', { name: 'Confirm demo request' }).click()
  await page.clock.runFor(150)
}

for (const viewport of [{ width: 320, height: 568 }, { width: 390, height: 844 }, { width: 667, height: 375 }]) {
  test(`confirmed local task journey at ${viewport.width}×${viewport.height}`, async ({ page }, testInfo) => {
    await page.setViewportSize(viewport)
    await page.clock.install()
    const requests: string[] = []
    const errors: string[] = []
    page.on('request', request => {
      if (request.method() !== 'GET' || !request.url().startsWith('http://127.0.0.1:4173/')) requests.push(request.url())
    })
    page.on('pageerror', error => errors.push(error.message))
    await draft(page)
    await expect(page.getByRole('heading', { name: 'Confirm your request' })).toBeFocused()
    await expect(page.getByText('Demo request ID', { exact: true })).toHaveCount(0)
    await page.getByRole('button', { name: 'Edit request' }).click()
    await page.getByLabel('Your request', { exact: true }).fill('Robot info')
    await page.getByRole('button', { name: 'Review request', exact: true }).click()
    await expect(page.locator('.call-confirmation code')).toBeVisible()
    await expect(page.getByText('Demo mode', {exact:true})).toBeVisible()
    await page.screenshot({ path: testInfo.outputPath('confirmation.png'), fullPage: true })
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await confirm(page)
    await expect(page.getByText('Demo request queued', {exact:true})).toBeVisible()
    await page.clock.runFor(1100)
    await expect(page.locator('.task-current')).toHaveText('processing')
    await page.clock.runFor(1000)
    await expect(page.locator('.task-current')).toHaveText('awaiting approval')
    await page.screenshot({ path: testInfo.outputPath('awaiting-approval.png'), fullPage: true })
    await page.clock.runFor(1000)
    await expect(page.locator('.task-current')).toHaveText('executing')
    await page.clock.runFor(1000)
    await expect(page.getByRole('heading', { name: 'Demo response ready', exact: true })).toBeVisible()
    await expect(page.locator('.history-assistant')).toContainText('robot')
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true)
    await page.screenshot({ path: testInfo.outputPath('completed.png'), fullPage: true })
    await page.getByRole('button', { name: 'Another voice turn' }).click()
    await openTextFallback(page)
    await expect(page.getByLabel('Your request', { exact: true })).toHaveValue('')
    await expect(page.getByText('Nice to meet you, Nova!')).toHaveCount(1)
    await page.getByLabel('Your request', { exact: true }).fill('Help')
    await page.getByRole('button', { name: 'Review request', exact: true }).click()
    await page.getByRole('button', { name: 'Cancel request' }).click()
    await expect(page.getByLabel('Your request', { exact: true })).toHaveValue('')
    await page.getByRole('button', { name: 'Leave session' }).click()
    await expect(page.getByRole('status')).toContainText('has ended')
    expect(await page.evaluate(() => ({ local: localStorage.length, session: sessionStorage.length }))).toEqual({ local: 0, session: 0 })
    expect(requests).toEqual([])
    expect(errors).toEqual([])
  })
}

for (const [scenario, title] of [
  ['denied', 'denied'], ['failed', 'failed'], ['robot_unavailable', 'failed'],
  ['timeout', 'unavailable'], ['service_error', 'unavailable'],
]) {
  test(`demo outcome: ${scenario}`, async ({ page }) => {
    await page.clock.install()
    await draft(page)
    await page.getByText('Try a demo outcome', { exact: true }).click()
    await page.getByLabel('Simulated outcome').selectOption(scenario)
    await confirm(page)
    await page.clock.runFor(scenario === 'timeout' ? 12_000 : 4500)
    await expect(page.locator('.history-user span')).toHaveText(`You · ${title}`)
    await expect(page.getByRole('heading', { name: 'Demo response ready', exact: true })).toHaveCount(0)
  })
}

test('unsupported movement is declined rather than treated as a greeting', async ({ page }) => {
  await page.clock.install()
  await draft(page, 'Hello then walk forward')
  await expect(page.getByText(/Unsupported in this demo/)).toBeVisible()
  await confirm(page)
  await page.clock.runFor(3000)
  await expect(page.locator('.history-user span')).toHaveText('You · denied')
  await expect(page.getByText(/Movement is not supported/)).toBeVisible()
})

for (const finish of ['expiry', 'end', 'privacy']) {
  test(`clears a pending task on ${finish}`, async ({ page }) => {
    await page.clock.install()
    await draft(page)
    await confirm(page)
    if (finish === 'expiry') await page.clock.fastForward(600_000)
    else if (finish === 'end') await page.getByRole('button', { name: 'Leave session' }).click()
    else await page.getByRole('link', { name: 'Privacy information' }).click()
    await page.clock.runFor(5000)
    await expect(page.getByText('Demo request ID', { exact: true })).toHaveCount(0)
    await expect(page.getByRole('heading', { name: 'Demo response ready', exact: true })).toHaveCount(0)
    if (finish === 'expiry') await expect(page.getByRole('status')).toContainText('has expired')
    if (finish === 'end') await expect(page.getByRole('status')).toContainText('has ended')
    if (finish === 'privacy') {
      await page.getByRole('button', { name: 'Back to session' }).click()
      await openTextFallback(page)
      await expect(page.getByLabel('Your request', { exact: true })).toHaveValue('')
    }
  })
}

test('disconnected voice leads to manual text and explicit task confirmation', async ({ page }) => {
  await mockMedia(page)
  await page.clock.install()
  const posts: string[] = []
  page.on('request', request => { if (request.method() === 'POST') posts.push(request.url()) })
  await page.goto('/join')
  await page.getByRole('button', { name: 'Continue as Guest' }).click()
  await page.getByRole('button', { name: 'Start Conversation' }).click()
  await detectSpeechTurn(page)
  await expect(page.getByLabel('Your request', { exact: true })).toHaveValue('')
  await expect(page.getByText(/no audio was uploaded/)).toBeVisible()
  await page.getByLabel('Your request', { exact: true }).fill('Hello')
  await page.getByRole('button', { name: 'Review request', exact: true }).click()
  await expect(page.getByRole('heading', { name: 'Confirm your request' })).toBeVisible()
  await expect(page.getByText('Demo request ID', { exact: true })).toHaveCount(0)
  await confirm(page)
  await page.clock.runFor(4500)
  await expect(page.getByRole('heading', { name: 'Demo response ready', exact: true })).toBeVisible()
  expect(posts).toEqual([])
})
