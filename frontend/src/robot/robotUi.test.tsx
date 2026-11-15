import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { EscortBar } from './EscortBar'
import { PlaceCards } from './PlaceCards'
import { PlacesMap } from './PlacesMap'
import { Sheet } from './Sheet'
import { RobotRequestFlow } from './RobotRequestFlow'
import { RobotSession } from './RobotSession'
import type { RobotPlace, RobotSessionInfo, RobotView } from './robotApi'
import type { RobotTask, RobotTaskClient } from './robotTasks'

const places: RobotPlace[] = [
  { name: 'home', label: 'Start point', description: '', x: 0, y: 0, home: true },
  { name: 'front_2m', label: 'Front, 2 m ahead', description: '', x: 2, y: 0, home: false },
  { name: 'left_2m', label: 'Left, 2 m', description: '', x: 0, y: 2, home: false },
]
const view = (over: Partial<RobotView> = {}): RobotView => ({ seq: 1, lines: [], expiresAt: Date.now() + 600_000, state: 'AWAITING_INPUT', goalStatus: null, destination: null, at: 'home',
  distanceRemaining: null, reason: '', ready: true, notice: '', robot: { x: 0, y: 0, yaw: 0, available: true }, ...over })
const task = (over: Partial<RobotTask> = {}): RobotTask => ({ requestId: 'req-1', clientRequestId: 'robot-request-x', status: 'processing', action: 'GO_TO_PLACE', revision: 1, ...over })


/** A fake backend client: answers with the request id it was given (the flow rejects answers for any other id). */
function fakeClient(first: Partial<RobotTask> | (() => never), next: Array<Partial<RobotTask>> = []) {
  let cid = ''
  const rest = [...next]
  const submit = vi.fn(async (request: { clientRequestId: string }) => {
    cid = request.clientRequestId
    if (typeof first === 'function') first()
    return task({ ...(first as Partial<RobotTask>), clientRequestId: cid })
  })
  const status = vi.fn(async () => task({ ...(rest.length > 1 ? rest.shift()! : rest[0] ?? {}), clientRequestId: cid }))
  return { client: { submit, status } as unknown as RobotTaskClient, submit, status }
}

describe('PlacesMap: layout only, never a map', () => {
  it('draws every place as a labelled, keyboard-reachable dot with a ring and no image', () => {
    const { container } = render(<PlacesMap places={places} view={view()} selected={null} onSelect={() => undefined}/>)
    expect(screen.getAllByRole('button')).toHaveLength(3)
    expect(screen.getByRole('button', { name: /Start point, the robot is here/ })).toBeInTheDocument()
    expect(container.querySelectorAll('.place-ring')).toHaveLength(3)
    expect(container.querySelectorAll('.place-dot')).toHaveLength(3)
    expect(container.querySelector('image, img, canvas')).toBeNull()
    expect(container.querySelector('.map-robot')).not.toBeNull()
  })
  it('selects on click and on Enter / Space, and does nothing when disabled', () => {
    const onSelect = vi.fn()
    const { rerender } = render(<PlacesMap places={places} view={view()} selected={null} onSelect={onSelect}/>)
    fireEvent.click(screen.getByRole('button', { name: /Left, 2 m/ }))
    fireEvent.keyDown(screen.getByRole('button', { name: /Front/ }), { key: 'Enter' })
    fireEvent.keyDown(screen.getByRole('button', { name: /Front/ }), { key: ' ' })
    expect(onSelect.mock.calls.map(c => c[0])).toEqual(['left_2m', 'front_2m', 'front_2m'])
    onSelect.mockClear()
    rerender(<PlacesMap places={places} view={view()} selected={null} disabled onSelect={onSelect}/>)
    fireEvent.click(screen.getByRole('button', { name: /Left, 2 m/ }))
    expect(onSelect).not.toHaveBeenCalled()
  })
  it('marks the destination, the selected place and draws the route', () => {
    const { container } = render(<PlacesMap places={places} view={view({ state: 'NAVIGATING', destination: 'left_2m', robot: { x: 0, y: 0.8, yaw: 1.57, available: true } })} selected="left_2m" onSelect={() => undefined}/>)
    expect(container.querySelector('.place.is-target')).not.toBeNull()
    expect(container.querySelector('.place.is-selected')).not.toBeNull()
    expect(container.querySelector('.map-route')).not.toBeNull()
    expect(screen.getByRole('button', { name: /Left, 2 m, heading there/ })).toHaveAttribute('aria-pressed', 'true')
  })
  it('works before the first update and with one place', () => {
    const { container } = render(<PlacesMap places={[places[0]]} view={null} selected={null} onSelect={() => undefined}/>)
    expect(container.querySelector('.map-robot')).toBeNull()
    expect(screen.getAllByRole('button')).toHaveLength(1)
  })
})

describe('EscortBar', () => {
  it('is hidden when nothing moves, and offers Stop while it walks', () => {
    const { container, rerender } = render(<EscortBar view={view()} places={places} stopping={false} offline={false} onStop={() => undefined}/>)
    expect(container).toBeEmptyDOMElement()
    const onStop = vi.fn()
    rerender(<EscortBar view={view({ state: 'NAVIGATING', destination: 'left_2m', distanceRemaining: 1.2 })} places={places} stopping={false} offline={false} onStop={onStop}/>)
    expect(screen.getByText('On the way to Left, 2 m')).toBeInTheDocument()
    expect(screen.getByText('1.2 m to go')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Stop' }))
    expect(onStop).toHaveBeenCalledOnce()
  })
  it('says when the robot is paused or the connection is lost, without a Stop that cannot work', () => {
    const { rerender } = render(<EscortBar view={view({ state: 'HOLD', reason: 'the robot was stopped by staff' })} places={places} stopping={false} offline={false} onStop={() => undefined}/>)
    expect(screen.getByText('the robot was stopped by staff')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Stop' })).toBeNull()
    rerender(<EscortBar view={view()} places={places} stopping={false} offline onStop={() => undefined}/>)
    expect(screen.getByText('Connection lost')).toBeInTheDocument()
  })
})

describe('RobotRequestFlow', () => {
  beforeEach(() => { vi.useFakeTimers() })
  afterEach(() => { vi.useRealTimers() })
  const session = () => ({ id: 'robot-1', expiresAt: Date.now() + 600_000 })
  const props = (client: RobotTaskClient, over = {}) => ({ text: 'Take me to Left, 2 m', source: 'typed' as const, proposedAction: 'GO_TO_PLACE' as const, place: 'left_2m', placeLabel: 'Left, 2 m',
    session: session(), client, onExpired: vi.fn(), onCancel: vi.fn(), onDone: vi.fn(), ...over })
  const flush = async (ms: number) => { await act(async () => { await vi.advanceTimersByTimeAsync(ms) }) }

  it('asks first, submits exactly once, follows the request and shows the arrival', async () => {
    const { client, submit } = fakeClient({ status: 'processing' }, [
      { status: 'executing', revision: 2, progress: { goalStatus: 'moving', distanceRemaining: 1.2 } },
      { status: 'completed', revision: 3, reply: 'We have arrived at Left, 2 m.' }])
    const p = props(client)
    render(<RobotRequestFlow {...p}/>)
    expect(screen.getByRole('heading', { name: 'Go to Left, 2 m?' })).toBeInTheDocument()
    expect(submit).not.toHaveBeenCalled()                                   // nothing is sent before the guest confirms
    const yes = screen.getByRole('button', { name: 'Yes, take me there' })
    fireEvent.click(yes); fireEvent.click(yes); fireEvent.click(yes)
    await flush(0)
    expect(submit).toHaveBeenCalledOnce()
    expect(screen.getByRole('heading', { name: 'Getting ready' })).toBeInTheDocument()
    await flush(700)
    expect(screen.getByRole('heading', { name: 'On the way' })).toBeInTheDocument()
    expect(screen.getByText('1.2 m to go')).toBeInTheDocument()
    await flush(700)
    expect(screen.getByRole('heading', { name: 'We have arrived' })).toBeInTheDocument()
    expect(screen.getByText('We have arrived at Left, 2 m.')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Where next?' }))
    expect(p.onDone).toHaveBeenCalledOnce()
  })
  it('words a typed request that the backend turned into an escort as an escort, naming the place', async () => {
    const { client } = fakeClient({ status: 'processing', place: 'home' }, [{ status: 'completed', revision: 2, place: 'home', reply: 'We have arrived at Start point.' }])
    render(<RobotRequestFlow {...props(client, { text: 'take me home please', proposedAction: null, place: undefined, placeLabel: undefined, labelOf: (n: string) => (n === 'home' ? 'Start point' : n) })}/>)
    expect(screen.getByRole('heading', { name: 'Your request is ready' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Send' }))
    await flush(0)
    expect(screen.getByRole('heading', { name: 'Getting ready' })).toBeInTheDocument()
    expect(screen.getByText('Checking the way to Start point…')).toBeInTheDocument()
    await flush(700)
    expect(screen.getByRole('heading', { name: 'We have arrived' })).toBeInTheDocument()
  })
  it('shows the visitor-safe message when the robot cannot, and never invents a reply', async () => {
    const { client } = fakeClient({ status: 'failed', reason: 'robot_unavailable', message: 'I can’t start right now: the battery is too low for another escort.' })
    render(<RobotRequestFlow {...props(client)}/>)
    fireEvent.click(screen.getByRole('button', { name: 'Yes, take me there' }))
    await flush(0)
    expect(screen.getByRole('heading', { name: 'I couldn’t finish that' })).toBeInTheDocument()
    expect(screen.getByText(/battery is too low/)).toBeInTheDocument()
    expect(screen.queryByText(/arrived/i)).toBeNull()
  })
  it('cancel before confirming sends nothing', () => {
    const submit = vi.fn()
    const p = props({ submit, status: vi.fn() } as unknown as RobotTaskClient)
    render(<RobotRequestFlow {...p}/>)
    fireEvent.click(screen.getByRole('button', { name: 'Not now' }))
    expect(p.onCancel).toHaveBeenCalledOnce()
    expect(submit).not.toHaveBeenCalled()
  })
  it('tells the truth when the service is unreachable, answers wrongly, or takes too long', async () => {
    const down = vi.fn(async () => { throw Object.assign(new Error('x'), { code: 'service_unavailable' }) })
    const first = render(<RobotRequestFlow {...props({ submit: down, status: vi.fn() } as unknown as RobotTaskClient)}/>)
    fireEvent.click(screen.getByRole('button', { name: 'Yes, take me there' }))
    await flush(0)
    expect(screen.getByRole('heading', { name: 'I can’t reach the robot' })).toBeInTheDocument()
    first.unmount()
    const stranger = vi.fn(async () => task({ clientRequestId: 'someone-elses-request' }))               // an answer for another request is not trusted
    const second = render(<RobotRequestFlow {...props({ submit: stranger, status: stranger } as unknown as RobotTaskClient)}/>)
    fireEvent.click(screen.getByRole('button', { name: 'Yes, take me there' }))
    await flush(0)
    expect(screen.getByRole('heading', { name: 'I got an answer I could not trust' })).toBeInTheDocument()
    second.unmount()
    const { client } = fakeClient({ status: 'executing' }, [{ status: 'executing' }])
    render(<RobotRequestFlow {...props(client, { waitMs: 3000 })}/>)
    fireEvent.click(screen.getByRole('button', { name: 'Yes, take me there' }))
    await flush(3500)
    expect(screen.getByRole('heading', { name: 'This is taking longer than expected' })).toBeInTheDocument()
    expect(screen.getByText(/Nothing is claimed as done/)).toBeInTheDocument()
  })
  it('ends the flow when the session has expired', async () => {
    const p = props({ submit: vi.fn(), status: vi.fn() } as unknown as RobotTaskClient, { session: { id: 'robot-1', expiresAt: Date.now() - 1 } })
    render(<RobotRequestFlow {...p}/>)
    fireEvent.click(screen.getByRole('button', { name: 'Yes, take me there' }))
    await flush(0)
    expect(p.onExpired).toHaveBeenCalledOnce()
  })
})

describe('RobotSession (the whole guest screen against a fake backend)', () => {
  const info: RobotSessionInfo = { authorization: { accessToken: 'tok-123456789012345678', expiresAt: Date.now() + 600_000 }, robotName: 'D1', nickname: 'Ana', places }
  const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } })
  let calls: Array<{ path: string; method: string; body: unknown; auth: string | undefined }>
  let stateBody: Record<string, unknown>
  let taskBody: Record<string, unknown>
  beforeEach(() => {
    calls = []
    stateBody = { seq: 1, messages: [{ seq: 1, text: 'Hello Ana! Where would you like to go?' }], expiresAt: Date.now() + 600_000, state: 'AWAITING_INPUT', goal_status: null, destination: null, at: 'home',
      distance_remaining: null, reason: '', options: [], robot: { x: 0, y: 0, yaw: 0, available: true } }
    taskBody = { request_id: 'req-1', client_request_id: '', status: 'processing', action: 'GO_TO_PLACE', revision: 1, demo: false, place: 'left_2m' }
    vi.stubGlobal('fetch', vi.fn(async (path: string, init?: RequestInit & { headers?: Record<string, string> }) => {
      const method = init?.method ?? 'GET'
      const body = init?.body ? JSON.parse(init.body as string) : null
      calls.push({ path, method, body, auth: init?.headers?.Authorization })
      if (path.startsWith('/api/state')) return json(stateBody)
      if (path === '/api/requests' && method === 'POST') { taskBody = { ...taskBody, client_request_id: body.client_request_id }; return json(taskBody, 201) }
      if (path.startsWith('/api/requests/')) return json(taskBody)
      if (path === '/api/robot/stop') return json({ ok: true })
      return json({ error: 'not_found' }, 404)
    }))
  })
  const mount = () => render(<RobotSession info={info} session={{ id: 'robot-1', expiresAt: info.authorization.expiresAt }} nickname="Ana" onExpired={vi.fn()} onLeave={vi.fn()} onPrivacy={vi.fn()}/>)

  it('greets with what the robot said, shows the layout, and sends nothing until a tapped place is confirmed', async () => {
    mount()
    expect(await screen.findByText('Hello Ana! Where would you like to go?')).toBeInTheDocument()
    expect(screen.getByRole('heading', { name: 'Where to?' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /Left, 2 m/, hidden: false, pressed: false }))
    expect(await screen.findByRole('heading', { name: 'Go to Left, 2 m?' })).toBeInTheDocument()
    expect(calls.some(c => c.path === '/api/requests')).toBe(false)
    fireEvent.click(screen.getByRole('button', { name: 'Yes, take me there' }))
    await waitFor(() => expect(calls.some(c => c.path === '/api/requests' && c.method === 'POST')).toBe(true))
    const post = calls.find(c => c.path === '/api/requests' && c.method === 'POST')!
    expect(post.body).toMatchObject({ text: 'Take me to Left, 2 m', source: 'typed', proposed_action: 'GO_TO_PLACE', place: 'left_2m' })
    expect(post.auth).toBe(`Bearer ${info.authorization.accessToken}`)
  })
  it('keeps Stop on screen while it walks and stops without a confirmation', async () => {
    stateBody = { ...stateBody, state: 'NAVIGATING', goal_status: 'moving', destination: 'left_2m', distance_remaining: 1.4 }
    mount()
    const stop = await screen.findByRole('button', { name: 'Stop' })
    fireEvent.click(stop)
    await waitFor(() => expect(calls.some(c => c.path === '/api/robot/stop' && c.method === 'POST')).toBe(true))
    expect(screen.queryByRole('heading', { name: /Your request is ready/ })).toBeNull()
  })
  it('disables the places and says why when the robot is paused', async () => {
    stateBody = { ...stateBody, state: 'HOLD', reason: 'the robot was stopped by staff', robot: { x: 0, y: 0, yaw: 0, available: false } }
    mount()
    await waitFor(() => expect(screen.getAllByText('the robot was stopped by staff').length).toBeGreaterThan(0))
    for (const chip of within(screen.getByRole('group', { name: 'Places' })).getAllByRole('button')) expect(chip).toBeDisabled()
  })
  it('says the robot is not ready, with the reason, and disables the places, when it cannot start a walk', async () => {
    stateBody = { ...stateBody, ready: false, notice: 'The robot is not placed on the map yet' }
    mount()
    expect(await screen.findByText('Not ready')).toBeInTheDocument()
    expect(screen.getByText(/not placed on the map yet\. A staff member can help/)).toBeInTheDocument()
    for (const chip of within(screen.getByRole('group', { name: 'Places' })).getAllByRole('button')) expect(chip).toBeDisabled()
    expect(screen.queryByText('Robot ready')).toBeNull()
  })
  it('sends a typed question only after the guest confirms it, and lets the backend decide what it means', async () => {
    mount()
    await screen.findByText('Hello Ana! Where would you like to go?')
    fireEvent.change(screen.getByLabelText('Or ask me something'), { target: { value: 'what can you do?' } })
    fireEvent.click(screen.getByRole('button', { name: 'Send message' }))
    expect(await screen.findByRole('heading', { name: 'Your request is ready' })).toBeInTheDocument()
    expect(calls.some(c => c.path === '/api/requests')).toBe(false)
  })
})


describe('radar layout geometry', () => {
  const standard: RobotPlace[] = [
    { name: 'home', label: 'Start point', description: '', x: 0, y: 0, home: true },
    { name: 'front_2m', label: 'Front, 2 m ahead', description: '', x: 2, y: 0, home: false },
    { name: 'back_2m', label: 'Back, 2 m behind', description: '', x: -2, y: 0, home: false },
    { name: 'right_2m', label: 'Right, 2 m', description: '', x: 0, y: -2, home: false },
    { name: 'left_2m', label: 'Left, 2 m', description: '', x: 0, y: 2, home: false },
  ]
  const rects = (container: HTMLElement) => {
    const nodes = [...container.querySelectorAll('.place-ring')].map(c => ({ x: +c.getAttribute('cx')! - 16, y: +c.getAttribute('cy')! - 16, w: 32, h: 32 }))
    const pills = [...container.querySelectorAll('.place-pill')].map(r => ({ x: +r.getAttribute('x')!, y: +r.getAttribute('y')!, w: +r.getAttribute('width')!, h: +r.getAttribute('height')! }))
    return { nodes, pills }
  }
  const hit = (a: { x: number; y: number; w: number; h: number }, b: typeof a) => a.x < b.x + b.w && a.x + a.w > b.x && a.y < b.y + b.h && a.y + a.h > b.y
  it.each([['four in a cross plus the start', standard], ['two places', standard.slice(0, 2)], ['clustered', standard.map((p, i) => ({ ...p, x: p.x * 0.3 + i * 0.05, y: p.y * 0.3 }))]])('no label covers another label or a node: %s', (_name, list) => {
    const { container } = render(<PlacesMap places={list} view={null} selected={null} onSelect={() => undefined}/>)
    const { nodes, pills } = rects(container)
    pills.forEach((a, i) => {
      expect(a.x).toBeGreaterThanOrEqual(0); expect(a.x + a.w).toBeLessThanOrEqual(360); expect(a.y).toBeGreaterThanOrEqual(0); expect(a.y + a.h).toBeLessThanOrEqual(330)
      pills.forEach((b, j) => { if (i < j) expect(hit(a, b)).toBe(false) })
      if (list === standard) nodes.forEach(n => expect(hit(a, n)).toBe(false))
    })
  })
})

describe('PlaceCards', () => {
  it('shows where the robot is, where it is heading and how far each place is, and respects disabled', () => {
    const onSelect = vi.fn()
    const v = view({ state: 'NAVIGATING', destination: 'left_2m', at: 'home', robot: { x: 0, y: 0.5, yaw: 1.57, available: true } })
    const { rerender } = render(<PlaceCards places={places} view={v} disabled={false} selected={null} onSelect={onSelect}/>)
    expect(screen.getByRole('button', { name: /Left, 2 m/ })).toHaveTextContent('Heading there')
    expect(screen.getByRole('button', { name: /Front, 2 m ahead/ })).toHaveTextContent('2.1 m away')
    fireEvent.click(screen.getByRole('button', { name: /Front/ }))
    expect(onSelect).toHaveBeenCalledWith('front_2m')
    rerender(<PlaceCards places={places} view={view({ at: 'home' })} disabled selected={null} onSelect={onSelect}/>)
    expect(screen.getByRole('button', { name: /Start point/ })).toHaveTextContent('You are here')
    for (const b of screen.getAllByRole('button')) expect(b).toBeDisabled()
  })
})

describe('Sheet', () => {
  it('closes on Escape and on a tap outside, labels itself, and gives the page its scroll back', () => {
    const onClose = vi.fn()
    const { container, unmount } = render(<Sheet labelledBy="t" onClose={onClose}><h3 id="t">Title</h3></Sheet>)
    expect(screen.getByRole('dialog', { name: 'Title' })).toHaveAttribute('aria-modal', 'true')
    expect(document.body.style.overflow).toBe('hidden')
    fireEvent.keyDown(document, { key: 'Escape' })
    fireEvent.click(container.querySelector('.sheet-backdrop')!)
    expect(onClose).toHaveBeenCalledTimes(2)
    unmount()
    expect(document.body.style.overflow).not.toBe('hidden')
  })
})
