import { afterEach, describe, expect, it, vi } from 'vitest'
import { createRequestApiAdapter } from './requestApiAdapter'
import type { RequestSubmission } from './contracts'

const config = { submitPath: '/custom/requests', statusPathTemplate: '/custom/requests/{request_id}' }
const session = () => ({ id: 'demo-context-not-a-token', expiresAt: Date.now() + 60_000 })
const authorization = () => ({ accessToken: 'backend-issued-test-token', expiresAt: Date.now() + 60_000 })
const request: RequestSubmission = { text: 'Hello', source: 'typed', proposedAction: 'GREET', clientRequestId: 'client-request-test' }
const response = { request_id: 'server-request-1', client_request_id: request.clientRequestId, action: 'GREET', status: 'queued', revision: 0, demo: false }
const signal = () => new AbortController().signal
afterEach(() => vi.unstubAllGlobals())

describe('prepared and unwired request API', () => {
  it('does not fetch on construction, and keeps tokens separate from the visitor payload', async () => {
    const fetch = vi.fn().mockResolvedValue({ ok: true, json: async () => response })
    vi.stubGlobal('fetch', fetch)
    const adapter = createRequestApiAdapter(config, authorization)
    expect(fetch).not.toHaveBeenCalled()
    await adapter.submit(request, session(), signal())
    const [path, options] = fetch.mock.calls[0]
    expect(path).toBe('/custom/requests')
    expect(options).toMatchObject({ method: 'POST', cache: 'no-store', credentials: 'omit', redirect: 'error' })
    expect(options.headers.Authorization).toBe('Bearer backend-issued-test-token')
    expect(options.headers['Idempotency-Key']).toBe(request.clientRequestId)
    expect(JSON.parse(options.body)).toEqual({ text: 'Hello', source: 'typed', proposed_action: 'GREET', client_request_id: request.clientRequestId })
    expect(options.body).not.toContain('token')
    expect(options.body).not.toContain('nickname')
    await adapter.status('server-request-1', session(), signal())
    expect(fetch.mock.calls[1][0]).toBe('/custom/requests/server-request-1')
    expect(fetch.mock.calls[1][1].method).toBe('GET')
  })
  it.each([
    { submitPath: 'https://example.test/api/requests', statusPathTemplate: '/api/requests/{request_id}' },
    { submitPath: '//example.test/api', statusPathTemplate: '/api/requests/{request_id}' },
    { submitPath: '/api/requests', statusPathTemplate: '/api/requests' },
    { submitPath: '/api/requests', statusPathTemplate: '/api/{request_id}/{request_id}' },
    { submitPath: '/api/requests?token=test', statusPathTemplate: '/api/requests/{request_id}' },
  ])('rejects unsafe endpoint config', paths => {
    expect(() => createRequestApiAdapter(paths, authorization)).toThrow()
  })
  it.each([null, { accessToken: 'demo-not-real', expiresAt: Date.now() + 60_000 }, { accessToken: 'expired', expiresAt: 0 }])('requires a real, separately provided backend credential', async token => {
    const fetch = vi.fn()
    vi.stubGlobal('fetch', fetch)
    await expect(createRequestApiAdapter(config, () => token).submit(request, session(), signal())).rejects.toMatchObject({ code: 'session_expired' })
    expect(fetch).not.toHaveBeenCalled()
  })
  it('never fetches with an expired visitor session', async () => {
    const fetch = vi.fn()
    vi.stubGlobal('fetch', fetch)
    const adapter = createRequestApiAdapter(config, authorization)
    await expect(adapter.submit(request, { ...session(), expiresAt: 0 }, signal())).rejects.toMatchObject({ code: 'session_expired' })
    expect(fetch).not.toHaveBeenCalled()
  })
  it.each([
    { ...response, status: 'walking' }, { ...response, revision: -1 }, { ...response, request_id: 'http://internal.invalid/robot' },
    { ...response, client_request_id: 'wrong-id' }, { ...response, reply: 'Premature success' },
    { ...response, status: 'completed' }, { ...response, action: 'MOVE_FORWARD' },
  ])('rejects malformed, mismatched, or premature results', async body => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => body }))
    await expect(createRequestApiAdapter(config, authorization).submit(request, session(), signal())).rejects.toMatchObject({ code: 'invalid_response' })
  })
  it('does not expose backend diagnostics or unknown internal fields', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, status: 500, json: async () => ({ internal_address: 'internal.invalid', detail: 'private diagnostics' }) }))
    await expect(createRequestApiAdapter(config, authorization).submit(request, session(), signal())).rejects.toMatchObject({ code: 'service_unavailable', message: 'service_unavailable' })
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true, json: async () => ({ ...response, robot_network_address: 'internal.invalid' }) }))
    const result = await createRequestApiAdapter(config, authorization).submit(request, session(), signal())
    expect(result).not.toHaveProperty('robot_network_address')
  })
})
