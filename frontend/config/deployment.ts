export interface DemoBuildConfig {
  profile: 'static-demo'
  taskMode: 'demo'
  transcriptionConnected: false
  sessionDurationSeconds: number
}

/** Values are public; reject unreviewed variables without printing their contents. */
export function validateDemoEnvironment(env: Record<string, string | undefined>): DemoBuildConfig {
  const allowed = new Set(['VITE_DEMO_SESSION_SECONDS', 'VITE_TRANSCRIBE_ENDPOINT', 'VITE_REQUESTS_ENDPOINT', 'VITE_REQUEST_STATUS_ENDPOINT', 'VITE_VAPI_PUBLIC_KEY', 'VITE_VAPI_ASSISTANT_ID'])
  for (const key of Object.keys(env)) {
    if (key.startsWith('VITE_') && !allowed.has(key)) throw new Error(`Unreviewed public environment key: ${key}. Do not expose secrets through VITE_*.`)
  }
  if (env.VITE_TRANSCRIBE_ENDPOINT) throw new Error('Static demo deployment requires VITE_TRANSCRIBE_ENDPOINT to be empty.')
  // Live Vapi voice sends visitor audio to a third party and needs network access
  // the static demo CSP (connect-src 'none') forbids. It is a separate build.
  if (env.VITE_VAPI_PUBLIC_KEY || env.VITE_VAPI_ASSISTANT_ID) throw new Error('Static demo deployment requires the Vapi voice keys to be empty. Clear VITE_VAPI_PUBLIC_KEY and VITE_VAPI_ASSISTANT_ID (they belong in .env.local for `npm run dev` only).')
  if (env.VITE_REQUESTS_ENDPOINT && env.VITE_REQUESTS_ENDPOINT !== '/api/requests') throw new Error('Static demo deployment requires the default, unwired request path.')
  if (env.VITE_REQUEST_STATUS_ENDPOINT && env.VITE_REQUEST_STATUS_ENDPOINT !== '/api/requests/{request_id}') throw new Error('Static demo deployment requires the default, unwired status path.')
  const seconds = env.VITE_DEMO_SESSION_SECONDS || '600'
  if (!/^\d+$/.test(seconds) || Number(seconds) < 1 || Number(seconds) > 86400) throw new Error('Demo session duration must be integer seconds from 1 to 86400.')
  return { profile: 'static-demo', taskMode: 'demo', transcriptionConnected: false, sessionDurationSeconds: Number(seconds) }
}
