import { openTextFallback } from './textFallback'
import { expect, test } from '@playwright/test'
import { mockMedia } from './media'
async function join(page: import('@playwright/test').Page) {
  await page.goto('/join'); await page.getByRole('button', { name: 'Continue as Guest' }).click()
}
for (const permission of ['NotAllowedError', 'NotFoundError', 'NotReadableError'] as const) {
  test(`microphone error ${permission} permits manual in-call requests`, async ({ page }) => {
    await mockMedia(page, permission); await join(page)
    await page.getByRole('button', { name: 'Start Conversation' }).click()
    await expect(page.getByRole('alert')).toBeVisible()
    await page.getByLabel('Your request', {exact:true}).fill('Hello')
    await page.getByRole('button', {name:'Review request'}).click()
    await expect(page.getByRole('heading', {name:'Confirm your request'})).toBeVisible()
  })
}
test('unsupported browser keeps the non-primary text alternative usable', async ({ page }) => {
  await page.addInitScript(() => Object.defineProperty(window, 'MediaRecorder', { configurable: true, value: undefined }))
  await join(page)
  await expect(page.getByRole('button', {name:'Start Conversation'})).toBeDisabled()
  await expect(page.getByText(/This browser cannot record audio/)).toBeVisible()
  await openTextFallback(page)
  await expect(page.getByLabel('Your request', {exact:true})).toBeFocused()
})
test('navigation cannot duplicate an unresolved native permission prompt', async ({page}) => {
  await mockMedia(page, 'pending'); await join(page)
  await page.getByRole('button', {name:'Start Conversation'}).click()
  await page.getByRole('link', {name:'Privacy information'}).click()
  await page.getByRole('button', {name:'Back to session'}).click()
  await page.getByRole('button', {name:'Start Conversation'}).click()
  await expect(page.getByRole('alert')).toContainText('earlier microphone permission prompt')
  expect(await page.evaluate(() => window.__voiceTest.requests)).toBe(1)
  await page.evaluate(() => window.__voiceTest.resolve?.())
  expect(await page.evaluate(() => window.__voiceTest.activeTracks)).toBe(0)
})
for (const leave of ['privacy', 'end', 'browser-back', 'expiry']) {
  test(`capture resources close on ${leave}`, async ({page}) => {
    await mockMedia(page); await page.clock.install(); await join(page)
    await page.getByRole('button', {name:'Start Conversation'}).click()
    await expect(page.locator('.call-screen')).toHaveAttribute('data-call-state','listening')
    if(leave==='privacy') await page.getByRole('link',{name:'Privacy information'}).click()
    else if(leave==='end') await page.getByRole('button',{name:'Leave session'}).click()
    else if(leave==='browser-back') await page.goBack()
    else await page.clock.fastForward(600_000)
    expect(await page.evaluate(() => ({active:window.__voiceTest.activeTracks, open:window.__voiceTest.contexts-window.__voiceTest.closedContexts}))).toEqual({active:0,open:0})
    await expect(page.getByRole('button',{name:'Finish turn'})).toHaveCount(0)
  })
}
