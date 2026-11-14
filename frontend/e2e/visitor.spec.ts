import { expect, test } from '@playwright/test'

for (const viewport of [{ width: 320, height: 568 }, { width: 375, height: 667 }, { width: 390, height: 844 }, { width: 412, height: 915 }, { width: 667, height: 375 }]) {
  test(`visitor journey at ${viewport.width}×${viewport.height}`, async ({ page }, testInfo) => {
    await page.setViewportSize(viewport)
    const errors: string[] = []
    const externalRequests: string[] = []
    page.on('request', request => {
      if (!request.url().startsWith('http://127.0.0.1:4173/')) externalRequests.push(request.url())
    })
    page.on('pageerror', error => errors.push(error.message))
    await page.goto('/join')
    await expect(page.getByRole('heading', { level: 1 })).toContainText('Hey there!')
    await page.reload()
    await expect(page.getByLabel('What should I call you?')).toHaveValue('')
    const assertNoOverflow = async () => {
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true)
    }
    await assertNoOverflow()
    await page.screenshot({ path: testInfo.outputPath('welcome.png'), fullPage: true })
    await expect(page.locator('.robot')).toHaveCSS('animation-name', 'none')
    await page.getByLabel('What should I call you?').fill('A'.repeat(40))
    await page.getByRole('button', { name: "Let's Get Started" }).click()
    await expect(page.getByRole('heading', { level: 1 })).toContainText(`Nice to meet you, ${'A'.repeat(40)}!`)
    await expect(page.getByText('Transcription not connected', { exact: true })).toBeVisible()
    await assertNoOverflow()
    await page.screenshot({ path: testInfo.outputPath('session.png'), fullPage: true })
    await page.getByRole('link', { name: 'Privacy information' }).click()
    await expect(page.getByRole('heading', { name: 'Privacy, in plain words.' })).toBeFocused()
    await assertNoOverflow()
    await page.screenshot({ path: testInfo.outputPath('privacy.png'), fullPage: true })
    await page.getByRole('button', { name: 'Back to session' }).click()
    await page.getByRole('button', { name: 'End Session' }).click()
    await expect(page.getByRole('status')).toContainText('has ended')
    await expect(page.getByLabel('What should I call you?')).toHaveValue('')
    await page.getByRole('button', { name: 'Continue as Guest' }).click()
    await expect(page.getByRole('heading', { name: 'Nice to meet you, Guest!' })).toBeVisible()
    await page.reload()
    await expect(page).toHaveURL('/join')
    await expect(page.getByRole('status')).toContainText('no active demo session')
    expect(await page.evaluate(() => ({ local: localStorage.length, session: sessionStorage.length }))).toEqual({ local: 0, session: 0 })
    expect(errors).toEqual([])
    expect(externalRequests).toEqual([])
  })
}

test('browser Back/Forward does not restore ended sessions', async ({ page }) => {
  await page.goto('/join')
  await page.getByRole('button', { name: 'Continue as Guest' }).click()
  await page.getByRole('link', { name: 'Privacy information' }).click()
  await page.goBack()
  await expect(page.getByRole('button', { name: 'End Session' })).toBeVisible()
  await page.goBack()
  await expect(page.getByRole('status')).toContainText('has ended')
  await page.goForward()
  await expect(page).toHaveURL('/join')
  await expect(page.getByRole('heading', { level: 1 })).toContainText('Hey there!')
})
