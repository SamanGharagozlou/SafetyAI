import { defineConfig } from '@playwright/test'

export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  use: {
    baseURL: 'http://127.0.0.1:4173',
    reducedMotion: 'reduce',
    // Uses installed Chrome by default; set PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH
    // only if a custom browser binary is needed. No browser is downloaded by tests.
    channel: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH ? undefined : 'chrome',
    launchOptions: { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH },
  },
  webServer: {
    command: 'npm run preview:static -- --port 4173',
    url: 'http://127.0.0.1:4173/join',
    reuseExistingServer: false,
  },
})
