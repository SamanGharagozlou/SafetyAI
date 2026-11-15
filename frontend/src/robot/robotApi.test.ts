import { describe, expect, it, vi } from 'vitest'
import { createRobotSession, endRobotSession, fetchRobotView, stopRobot } from './robotApi'

const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } })
const auth = { accessToken: 'tok-123456789012345678', expiresAt: Date.now() + 60_000 }
const place = { name: 'left_2m', label: 'Left, 2 m', description: '', x: 0, y: 2, home: false }
const session = { accessToken: 'a'.repeat(32), expiresAt: Date.now() + 600_000, robot: { name: 'D1' }, nickname: 'Ana', places: [place] }
const view = { seq: 3, messages: [{ seq: 3, text: 'Follow me.' }], expiresAt: Date.now() + 1000, state: 'NAVIGATING', goal_status: 'moving', destination: 'left_2m', at: 'home',
  distance_remaining: 1.5, reason: '', options: [], robot: { x: 0, y: 0.5, yaw: 1.57, available: true } }

describe('createRobotSession', () => {
  it('sends the QR token and nickname, keeps no cookies, and decodes the credential and the layout', async () => {
    const fetchMock = vi.fn(async () => json(session, 201))
    vi.stubGlobal('fetch', fetchMock)
    const info = await createRobotSession('qr-token', '  Ana  ')
    expect(info.authorization.accessToken).toBe(session.accessToken)
    expect(info.places).toEqual([place])
    const [path, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit]
    expect(path).toBe('/api/session')
    expect(init).toMatchObject({ method: 'POST', credentials: 'omit', cache: 'no-store', referrerPolicy: 'no-referrer' })
    expect(JSON.parse(init.body as string)).toMatchObject({ token: 'qr-token', nickname: 'Ana' })
  })
  it.each([
    [403, { error: 'invalid_qr' }, 'invalid_qr'], [409, { error: 'robot_busy', message: 'busy' }, 'robot_busy'],
    [409, { error: 'robot_unavailable' }, 'robot_unavailable'], [500, { error: 'internal_error' }, 'service_unavailable'],
  ])('maps %i %j to %s', async (status, body, code) => {
    vi.stubGlobal('fetch', vi.fn(async () => json(body, status)))
    await expect(createRobotSession('t', '')).rejects.toMatchObject({ code })
  })
  it('needs a token, survives a network failure and rejects answers it cannot trust', async () => {
    await expect(createRobotSession('', '')).rejects.toMatchObject({ code: 'invalid_qr' })
    vi.stubGlobal('fetch', vi.fn(async () => { throw new TypeError('offline') }))
    await expect(createRobotSession('t', '')).rejects.toMatchObject({ code: 'service_unavailable' })
    for (const bad of [{ ...session, accessToken: 'short' }, { ...session, expiresAt: Date.now() - 1 }, { ...session, places: [{ ...place, x: 'far' }] }, { ...session, places: [{ ...place, name: '../x' }] }, 'nope']) {
      vi.stubGlobal('fetch', vi.fn(async () => json(bad, 201)))
      await expect(createRobotSession('t', '')).rejects.toMatchObject({ code: 'invalid_response' })
    }
  })
  it('never receives or accepts a map: a place with an image field is just the layout', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => json({ ...session, map: { url: '/map.png' } }, 201)))
    const info = await createRobotSession('t', '')
    expect(JSON.stringify(info)).not.toMatch(/map/)
  })
})

describe('the session calls', () => {
  it('reads the live view with the Bearer credential and decodes it', async () => {
    const fetchMock = vi.fn(async () => json(view))
    vi.stubGlobal('fetch', fetchMock)
    const v = await fetchRobotView(auth, 2)
    expect(v).toMatchObject({ seq: 3, state: 'NAVIGATING', destination: 'left_2m', distanceRemaining: 1.5, robot: { x: 0, y: 0.5, available: true } })
    expect(v.lines).toEqual([{ seq: 3, text: 'Follow me.' }])
    const [path, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit & { headers: Record<string, string> }]
    expect(path).toBe('/api/state?after=2')
    expect(init.headers.Authorization).toBe(`Bearer ${auth.accessToken}`)
  })
  it('treats 401 as an ended session and refuses to call with an expired credential', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => json({ error: 'session_expired' }, 401)))
    await expect(fetchRobotView(auth, 0)).rejects.toMatchObject({ code: 'session_expired' })
    const fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
    await expect(stopRobot({ ...auth, expiresAt: Date.now() - 1 })).rejects.toMatchObject({ code: 'session_expired' })
    expect(fetchMock).not.toHaveBeenCalled()
  })
  it('rejects a malformed state', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => json({ ...view, state: 'FLYING' })))
    await expect(fetchRobotView(auth, 0)).rejects.toMatchObject({ code: 'invalid_response' })
  })
  it('stop and end use the right verbs', async () => {
    const fetchMock = vi.fn(async () => json({ ok: true, reply: 'Goodbye!' }))
    vi.stubGlobal('fetch', fetchMock)
    await stopRobot(auth)
    expect(await endRobotSession(auth)).toBe('Goodbye!')
    const calls = fetchMock.mock.calls as unknown as Array<[string, RequestInit]>
    expect([calls[0][0], calls[0][1].method, calls[1][0], calls[1][1].method]).toEqual(['/api/robot/stop', 'POST', '/api/session', 'DELETE'])
  })
})
