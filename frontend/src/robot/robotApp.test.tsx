import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

// The app picks robot mode from the Vite mode at import time, so each test imports a fresh copy.
async function loadApp(mode: string) {
  vi.resetModules()
  vi.stubEnv('MODE', mode)
  return (await import('../App')).App
}
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } })
const place = { name: 'left_2m', label: 'Left, 2 m', description: '', x: 0, y: 2, home: false }
const home = { name: 'home', label: 'Start point', description: '', x: 0, y: 0, home: true }
let calls: Array<{ path: string; method: string; auth?: string; body?: unknown }>
let sessionResponse: () => Response

beforeEach(() => {
  calls = []
  sessionResponse = () => json({ accessToken: 'z'.repeat(32), expiresAt: Date.now() + 600_000, robot: { name: 'D1' }, nickname: 'Maria', places: [home, place] }, 201)
  vi.stubGlobal('fetch', vi.fn(async (path: string, init?: RequestInit & { headers?: Record<string, string> }) => {
    calls.push({ path, method: init?.method ?? 'GET', auth: init?.headers?.Authorization, body: init?.body ? JSON.parse(init.body as string) : undefined })
    if (path === '/api/session' && init?.method === 'POST') return sessionResponse()
    if (path === '/api/session' && init?.method === 'DELETE') return json({ ok: true, reply: 'Goodbye!' })
    if (path.startsWith('/api/state')) return json({ seq: 1, messages: [{ seq: 1, text: 'Hello Maria!' }], expiresAt: Date.now() + 600_000, state: 'AWAITING_INPUT', goal_status: null, destination: null, at: 'home',
      distance_remaining: null, reason: '', options: [], robot: { x: 0, y: 0, yaw: 0, available: true } })
    return json({ error: 'not_found' }, 404)
  }))
})

describe('robot mode (vite --mode robot)', () => {
  it('joins with the QR token, removes it from the address bar and shows the places', async () => {
    window.history.replaceState(null, '', '/join?t=qr-secret-token')
    const App = await loadApp('robot')
    render(<App/>)
    fireEvent.change(screen.getByLabelText(/What should I call you/), { target: { value: 'Maria' } })
    fireEvent.click(screen.getByRole('button', { name: /Let's Get Started/ }))
    expect(await screen.findByRole('heading', { name: 'Where to?' })).toBeInTheDocument()
    expect(await screen.findByText('Hello Maria!')).toBeInTheDocument()
    const join = calls.find(c => c.path === '/api/session' && c.method === 'POST')!
    expect(join.body).toMatchObject({ token: 'qr-secret-token', nickname: 'Maria' })
    expect(window.location.pathname).toBe('/session')
    expect(window.location.href).not.toContain('qr-secret-token')
    expect(window.location.search).toBe('')
    expect(screen.getByText('LIVE ROBOT')).toBeInTheDocument()
    expect(screen.queryByText(/Demo mode/i)).toBeNull()
    expect(screen.queryByText(/no real robot connection/i)).toBeNull()
  })
  it('asks for the QR code when there is no token, and never calls the robot', async () => {
    window.history.replaceState(null, '', '/join')
    const App = await loadApp('robot')
    render(<App/>)
    fireEvent.click(screen.getByRole('button', { name: 'Continue as Guest' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Please scan the QR code on the robot')
    expect(calls).toHaveLength(0)
  })
  it.each([
    [403, { error: 'invalid_qr' }, /QR code is not valid/], [409, { error: 'robot_busy' }, /with another guest/],
    [409, { error: 'robot_unavailable' }, /not available right now/], [502, { error: 'x' }, /couldn’t reach the robot/],
  ])('explains a failed join (%i %j) in plain words and stays on the welcome page', async (status, body, text) => {
    window.history.replaceState(null, '', '/join?t=tok')
    sessionResponse = () => json(body, status)
    const App = await loadApp('robot')
    render(<App/>)
    fireEvent.click(screen.getByRole('button', { name: 'Continue as Guest' }))
    expect(await screen.findByRole('alert')).toHaveTextContent(text)
    expect(window.location.pathname).toBe('/join')
  })
  it('ends the session on the robot when the guest leaves', async () => {
    window.history.replaceState(null, '', '/join?t=tok')
    const App = await loadApp('robot')
    render(<App/>)
    fireEvent.click(screen.getByRole('button', { name: 'Continue as Guest' }))
    await screen.findByRole('heading', { name: 'Where to?' })
    fireEvent.click(screen.getByRole('button', { name: 'Leave session' }))
    await waitFor(() => expect(calls.some(c => c.path === '/api/session' && c.method === 'DELETE')).toBe(true))
    expect(calls.find(c => c.method === 'DELETE')!.auth).toBe(`Bearer ${'z'.repeat(32)}`)
    expect(await screen.findByText(/Scan the code on the robot to start again/)).toBeInTheDocument()
  })
  it('tells the truth about privacy: data goes to the robot, nothing is recorded, only a layout is shown', async () => {
    window.history.replaceState(null, '', '/privacy')
    const App = await loadApp('robot')
    render(<App/>)
    expect(await screen.findByRole('heading', { name: 'Privacy, in plain words.' })).toBeInTheDocument()
    expect(screen.getByText(/sent to this robot’s own server/)).toBeInTheDocument()
    expect(screen.getByText(/never a floor plan/)).toBeInTheDocument()
    expect(screen.queryByText(/robot remains disconnected/)).toBeNull()
  })
})

describe('the demo is unchanged', () => {
  it('never calls the robot API and keeps its simulation wording outside robot mode', async () => {
    window.history.replaceState(null, '', '/join?t=whatever')
    const App = await loadApp('test')
    render(<App/>)
    fireEvent.click(screen.getByRole('button', { name: 'Continue as Guest' }))
    expect(await screen.findByText('Demo mode', { exact: true })).toBeInTheDocument()
    expect(screen.getByText(/no real robot connection/)).toBeInTheDocument()
    expect(calls).toHaveLength(0)
  })
})
