import { defineConfig } from 'vitest/config'

export default defineConfig({
  test: {
    environment: 'jsdom',
    // Pin live voice OFF for the suite. Vite loads .env.local, so a developer
    // with real Vapi keys would otherwise silently test a different app.
    // The live path is covered by src/voice/vapiSession.test.ts.
    env: { VITE_VAPI_PUBLIC_KEY: '', VITE_VAPI_ASSISTANT_ID: '' },
    setupFiles: ['./src/test/setup.ts'],
    include: ['src/**/*.test.ts', 'src/**/*.test.tsx', 'scripts/**/*.test.mjs'],
    restoreMocks: true,
  },
})
