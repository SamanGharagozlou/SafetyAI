import { describe, expect, it } from 'vitest'
import { validateDemoEnvironment } from '../../config/deployment'

describe('static demo environment boundary', () => {
  it('defaults to mocks with no transcription or privileged values', () => {
    expect(validateDemoEnvironment({})).toEqual({ profile: 'static-demo', taskMode: 'demo', transcriptionConnected: false, sessionDurationSeconds: 600 })
  })
  it('keeps non-public build credentials out of the manifest', () => {
    expect(JSON.stringify(validateDemoEnvironment({ PRIVATE_BUILD_CREDENTIAL: 'synthetic-test-canary' }))).not.toContain('synthetic-test-canary')
  })
  it('rejects an unexpected VITE secret without echoing its value', () => {
    try {
      validateDemoEnvironment({ VITE_PRIVILEGED_TOKEN: 'synthetic-test-canary' })
      throw new Error('Expected validation to reject this environment')
    } catch (error) {
      expect((error as Error).message).toContain('VITE_PRIVILEGED_TOKEN')
      expect((error as Error).message).not.toContain('synthetic-test-canary')
    }
  })
  it.each([{ VITE_TRANSCRIBE_ENDPOINT: '/api/transcribe' }, { VITE_TRANSCRIBE_ENDPOINT: 'https://example.invalid/transcribe' }, { VITE_REQUESTS_ENDPOINT: '/unreviewed/path' }, { VITE_REQUEST_STATUS_ENDPOINT: '/unreviewed/{request_id}' }])('rejects backend-connected or unreviewed demo configuration', env => {
    expect(() => validateDemoEnvironment(env)).toThrow()
  })
  it.each(['0', '86401', '-1', '1.5', 'NaN', ' '])('rejects invalid expiry: %s', seconds => {
    expect(() => validateDemoEnvironment({ VITE_DEMO_SESSION_SECONDS: seconds })).toThrow()
  })
  it('allows the reviewed paths and an explicit test duration', () => {
    expect(validateDemoEnvironment({ VITE_TRANSCRIBE_ENDPOINT: '', VITE_REQUESTS_ENDPOINT: '/api/requests', VITE_REQUEST_STATUS_ENDPOINT: '/api/requests/{request_id}', VITE_DEMO_SESSION_SECONDS: '5' }).sessionDurationSeconds).toBe(5)
  })
})
