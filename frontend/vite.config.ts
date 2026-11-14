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
  return {
    base: '/',
    envPrefix: 'VITE_',
    build: { sourcemap: false },
    plugins: [react(), tailwindcss(), ...(demoPlugin ? [demoPlugin] : [])],
  }
})
