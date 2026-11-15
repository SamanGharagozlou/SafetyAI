import { describe, expect, it, vi } from 'vitest'
import { createRobotTaskClient, decodeRobotTask } from './robotTasks'

const base = { request_id: 'req-1', client_request_id: 'robot-request-abc', status: 'processing', action: 'GO_TO_PLACE', revision: 1, demo: false }
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } })
const auth = { accessToken: 'tok-123456789012345678', expiresAt: Date.now() + 60_000 }

describe('decodeRobotTask (the frontend decoder rules, plus the robot additions)', () => {
  it('accepts the robot actions, place, message and progress', () => {
    expect(decodeRobotTask({ ...base, status: 'executing', place: 'left_2m', progress: { goal_status: 'moving', distance_remaining: 1.2 } }).progress).toEqual({ goalStatus: 'moving', distanceRemaining: 1.2 })
    expect(decodeRobotTask({ ...base, status: 'completed', reply: 'We have arrived.' }).reply).toBe('We have arrived.')
    expect(decodeRobotTask({ ...base, status: 'failed', reason: 'robot_unavailable', message: 'The battery is too low.' }).message).toBe('The battery is too low.')
    for (const action of ['GREET', 'ROBOT_INFO', 'HELP', 'STOP_ROBOT', 'END_SESSION']) expect(decodeRobotTask({ ...base, action, status: 'completed', reply: 'ok' }).action).toBe(action)
  })
  it.each([
    ['a simulated task (demo: true)', { ...base, demo: true }],
    ['an unknown action', { ...base, action: 'MOVE_FORWARD' }],
    ['a reply before completion', { ...base, status: 'executing', reply: 'x' }],
    ['a reply on a failure', { ...base, status: 'failed', reply: 'x' }],
    ['a completed task without a reply', { ...base, status: 'completed' }],
    ['a completed task without an action', { ...base, status: 'completed', action: null, reply: 'x' }],
    ['an unknown reason', { ...base, status: 'failed', reason: 'because' }],
    ['an unknown status', { ...base, status: 'done' }],
    ['a bad request id', { ...base, request_id: 'a b' }],
    ['a negative revision', { ...base, revision: -1 }],
    ['a non-string message', { ...base, status: 'failed', message: 5 }],
    ['a bad place name', { ...base, place: '../x' }],
    ['bad progress', { ...base, progress: { distance_remaining: 'far' } }],
  ])('rejects %s', (_name, body) => { expect(() => decodeRobotTask(body)).toThrow() })
})

describe('createRobotTaskClient', () => {
  it('posts the confirmed request with the Bearer credential, an Idempotency-Key, no cookies and the tapped place', async () => {
    const fetchMock = vi.fn(async () => json({ ...base, client_request_id: 'c1' }, 201))
    vi.stubGlobal('fetch', fetchMock)
    const task = await createRobotTaskClient(() => auth).submit({ text: 'Take me to Left', source: 'typed', proposedAction: 'GO_TO_PLACE', place: 'left_2m', clientRequestId: 'c1' }, new AbortController().signal)
    expect(task.requestId).toBe('req-1')
    const [path, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit & { headers: Record<string, string> }]
    expect(path).toBe('/api/requests')
    expect(init).toMatchObject({ method: 'POST', credentials: 'omit', cache: 'no-store', redirect: 'error' })
    expect(init.headers).toMatchObject({ Authorization: `Bearer ${auth.accessToken}`, 'Idempotency-Key': 'c1' })
    expect(JSON.parse(init.body as string)).toEqual({ text: 'Take me to Left', source: 'typed', proposed_action: 'GO_TO_PLACE', client_request_id: 'c1', place: 'left_2m' })
  })
  it('refuses to send without a live credential, with bad input, or when the answer belongs to another request', async () => {
    const fetchMock = vi.fn(async () => json({ ...base, client_request_id: 'someone-else' }, 201))
    vi.stubGlobal('fetch', fetchMock)
    const signal = new AbortController().signal
    const ok = { text: 'hi', source: 'typed' as const, proposedAction: null, clientRequestId: 'c1' }
    await expect(createRobotTaskClient(() => null).submit(ok, signal)).rejects.toMatchObject({ code: 'session_expired' })
    await expect(createRobotTaskClient(() => ({ ...auth, expiresAt: Date.now() - 1 })).submit(ok, signal)).rejects.toMatchObject({ code: 'session_expired' })
    await expect(createRobotTaskClient(() => auth).submit({ ...ok, text: '  ' }, signal)).rejects.toMatchObject({ code: 'invalid_request' })
    await expect(createRobotTaskClient(() => auth).submit({ ...ok, clientRequestId: 'bad id' }, signal)).rejects.toMatchObject({ code: 'invalid_request' })
    await expect(createRobotTaskClient(() => auth).submit(ok, signal)).rejects.toMatchObject({ code: 'invalid_response' })
  })
  it('maps HTTP errors to the frontend error codes', async () => {
    const signal = new AbortController().signal
    const ok = { text: 'hi', source: 'typed' as const, proposedAction: null, clientRequestId: 'c1' }
    for (const [status, code] of [[401, 'session_expired'], [403, 'session_expired'], [409, 'conflict'], [500, 'service_unavailable']] as const) {
      vi.stubGlobal('fetch', vi.fn(async () => json({ error: 'x' }, status)))
      await expect(createRobotTaskClient(() => auth).submit(ok, signal)).rejects.toMatchObject({ code })
    }
  })
  it('polls the status of one request id only', async () => {
    const fetchMock = vi.fn(async () => json({ ...base, status: 'executing' }))
    vi.stubGlobal('fetch', fetchMock)
    const client = createRobotTaskClient(() => auth)
    expect((await client.status('req-1', new AbortController().signal)).status).toBe('executing')
    expect((fetchMock.mock.calls[0] as unknown as [string])[0]).toBe('/api/requests/req-1')
    await expect(client.status('../etc', new AbortController().signal)).rejects.toMatchObject({ code: 'invalid_request' })
    await expect(client.status('req-2', new AbortController().signal)).rejects.toMatchObject({ code: 'invalid_response' })
  })
})
