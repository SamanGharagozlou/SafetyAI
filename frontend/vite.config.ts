import { readFileSync } from 'node:fs'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { defineConfig, loadEnv } from 'vite'
import type { Plugin } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { validateDemoEnvironment } from './config/deployment'

export default defineConfig(({ command, mode }) => {
  const demo = command === 'build' && mode === 'demo'
  if (demo && process.env.NODE_ENV && process.env.NODE_ENV !== 'production') {
    throw new Error('Static demo artifacts must use NODE_ENV=production.')
  }
  const config = demo ? validateDemoEnvironment(loadEnv(mode, process.cwd(), 'VITE_')) : null
  const demoPlugin: Plugin | null = config ? {
    name: 'static-demo-manifest',
    generateBundle() {
      this.emitFile({ type: 'asset', fileName: 'demo-config.json', source: `${JSON.stringify(config, null, 2)}\n` })
    },
  } : null
  // Robot mode (vite --mode robot): same-origin /api is proxied to the companion backend. Host header is kept so its same-origin check passes.
  const proxy = mode === 'robot' ? { '/api': { target: process.env.D1_COMPANION_URL || 'http://127.0.0.1:8780', changeOrigin: false } } : undefined
  return {
    base: '/',
    // phone_demo.sh --https: self-signed certificate so a phone treats the page as a secure context (needed for the microphone)
    server: proxy ? { proxy, ...(process.env.D1_HTTPS_KEY && process.env.D1_HTTPS_CERT ? { https: { key: readFileSync(process.env.D1_HTTPS_KEY), cert: readFileSync(process.env.D1_HTTPS_CERT) } } : {}) } : undefined,
    preview: proxy ? { proxy } : undefined,
    envPrefix: 'VITE_',
    build: { sourcemap: false },
    // The static demo must not ship the third-party voice client at all: no
    // chunk, no external origins, nothing to load. Enforced here, not by trust.
    resolve: demo ? { alias: { '@vapi-ai/web': resolve(dirname(fileURLToPath(import.meta.url)), 'src/voice/vapiDisabled.ts') } } : undefined,
    plugins: [react(), tailwindcss(), ...(demoPlugin ? [demoPlugin] : [])],
  }
})
