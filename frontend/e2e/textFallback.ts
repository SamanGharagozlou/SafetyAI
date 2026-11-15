import type { Page } from '@playwright/test'

export async function openTextFallback(page: Page) {
  if (await page.getByLabel('Your request', { exact: true }).isVisible()) return
  const button = page.getByRole('button', { name: 'Write a request instead', exact: true })
  if (!await button.isVisible()) await page.getByText(/Need another way/).click()
  await button.click()
}
export async function detectSpeechTurn(page: Page) {
  await page.evaluate(() => { window.__voiceTest.rms = 0.003 })
  await page.clock.runFor(650)
  await page.evaluate(() => { window.__voiceTest.rms = 0.1 })
  await page.clock.runFor(350)
  await page.evaluate(() => { window.__voiceTest.rms = 0.002 })
  await page.clock.runFor(1300)
}
