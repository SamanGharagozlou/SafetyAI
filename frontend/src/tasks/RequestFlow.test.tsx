import { openTextFallback } from '../test/textFallback'
import { act, fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { App } from '../App'
import { TaskServiceError } from './contracts'
import type { TaskSession, TaskSnapshot, TaskStatus } from './contracts'
import { createMockTaskAdapter } from './mockTaskAdapter'
import type { DemoTaskAdapter } from './mockTaskAdapter'
import { RequestFlow, TASK_WAIT_MS, statusCopy } from './RequestFlow'

const session = (): TaskSession => ({ id: 'demo-session', expiresAt: Date.now() + 60_000 })
function setup(text = 'Hello', overrides: Partial<Parameters<typeof RequestFlow>[0]> = {}) {
  const onEdit = vi.fn(), onCancel = vi.fn(), onRestart = vi.fn(), onExpired = vi.fn()
  const adapterFactory = vi.fn(() => createMockTaskAdapter({ stepMs: 800, latencyMs: 0 }))
  const props = { text, source: 'typed' as const, session: session(), onEdit, onCancel, onRestart, onExpired, adapterFactory, ...overrides }
  const view = render(<RequestFlow {...props}/> )
  return { ...view, ...props }
}
async function advance(ms = 0) { await act(async () => { await vi.advanceTimersByTimeAsync(ms) }) }
async function confirm() {
  fireEvent.click(screen.getByRole('button', { name: 'Confirm demo request' }))
  await advance()
}
beforeEach(() => { vi.useFakeTimers(); vi.stubEnv('VITE_DEMO_SESSION_SECONDS', '600') })

describe('explicit confirmation', () => {
  it('does not submit until Confirm and provides Edit/Cancel controls', () => {
    const props = setup()
    expect(screen.getByRole('heading', { name: 'Your request is ready' })).toHaveFocus()
    expect(props.adapterFactory).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: 'Edit request' }))
    expect(props.onEdit).toHaveBeenCalledTimes(1)
    fireEvent.click(screen.getByRole('button', { name: 'Cancel request' }))
    expect(props.onCancel).toHaveBeenCalledTimes(1)
    expect(props.adapterFactory).not.toHaveBeenCalled()
  })
  it('prevents duplicate submissions before React renders a new stage', async () => {
    const adapter = createMockTaskAdapter({ latencyMs: 100 })
    const submit = vi.spyOn(adapter, 'submit')
    setup('Hello', { adapterFactory: () => adapter })
    const button = screen.getByRole('button', { name: 'Confirm demo request' })
    act(() => { button.click(); button.click() })
    await advance(100)
    expect(submit).toHaveBeenCalledTimes(1)
    expect(screen.getByText('You · Confirmed request')).toBeInTheDocument()
  })
  it('edits the actual request, cancels without submitting, and keeps the session', () => {
    window.history.replaceState(null, '', '/join')
    render(<App/>)
    fireEvent.change(screen.getByLabelText(/What should I call you/), { target: { value: 'Nova' } })
    fireEvent.click(screen.getByRole('button', { name: /Let's Get Started/ }))
    openTextFallback()
    fireEvent.change(screen.getByLabelText('Your request'), { target: { value: 'Hello' } })
    fireEvent.click(screen.getByRole('button', { name: 'Review request' }))
    fireEvent.click(screen.getByRole('button', { name: 'Edit request' }))
    fireEvent.change(screen.getByLabelText('Your request'), { target: { value: 'Robot info' } })
    fireEvent.click(screen.getByRole('button', { name: 'Review request' }))
    expect(screen.getByText('Robot info')).toBeInTheDocument()
    expect(screen.getByText(/ROBOT_INFO/, { selector: 'code' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Cancel request' }))
    expect(screen.getByLabelText('Your request')).toHaveValue('')
    expect(screen.getByText('Nice to meet you, Nova!')).toBeInTheDocument()
    expect(screen.queryByText('Demo request ID')).not.toBeInTheDocument()
  })
  it('blocks Confirm at the absolute session deadline even before a timer fires', () => {
    const context = session()
    const props = setup('Hello', { session: context })
    vi.setSystemTime(context.expiresAt)
    fireEvent.click(screen.getByRole('button', { name: 'Confirm demo request' }))
    expect(props.onExpired).toHaveBeenCalled()
    expect(props.adapterFactory).not.toHaveBeenCalled()
  })
})

describe('all task states and honest failure handling', () => {
  it('progresses through the success states, announcing approval and completion accurately', async () => {
    setup()
    await confirm()
    expect(screen.getByRole('heading', { name: statusCopy.queued.title })).toBeInTheDocument()
    for (const state of ['processing', 'awaiting_approval', 'executing', 'completed'] as const) {
      await advance(1100)
      expect(screen.getByRole('heading', { name: statusCopy[state].title })).toBeInTheDocument()
    }
    expect(screen.getByText(/This greeting is a demo text response/)).toBeInTheDocument()
    expect(screen.getByText('DEMO MODE · LOCAL SIMULATION')).toBeInTheDocument()
  })
  it.each(['denied', 'failed', 'robot_unavailable', 'timeout', 'service_error'] as const)('handles demo outcome %s', async scenario => {
    setup('Hello', { adapterFactory: () => createMockTaskAdapter({ scenario, stepMs: 500, latencyMs: 0 }) })
    await confirm()
    await advance(scenario === 'timeout' ? TASK_WAIT_MS : 3000)
    const title = scenario === 'denied' ? 'Demo request declined' : scenario === 'timeout' ? 'Demo status timed out' : scenario === 'service_error' ? 'Demo service unavailable' : 'Demo could not finish'
    expect(screen.getByRole('heading', { name: title })).toBeInTheDocument()
    expect(screen.queryByText(/This greeting is a demo text response/)).not.toBeInTheDocument()
    if (scenario === 'robot_unavailable') expect(screen.getByText(/Simulated robot unavailable/)).toBeInTheDocument()
  })
  it('declines unsupported text instead of simulating movement', async () => {
    setup('Walk forward')
    expect(screen.getByText('Not supported in this demo')).toBeInTheDocument()
    await confirm()
    await advance(2500)
    expect(screen.getByRole('heading', { name: 'Demo request declined' })).toBeInTheDocument()
    expect(screen.getByText(/including movement, are declined/)).toBeInTheDocument()
  })
  it('handles a synchronous adapter initialization error', async () => {
    setup('Hello', { adapterFactory: () => { throw new Error('test initialization failure') } })
    await confirm()
    expect(screen.getByRole('heading', { name: 'Demo service unavailable' })).toBeInTheDocument()
  })
  it('starts another interaction by releasing the old adapter', async () => {
    const adapter = createMockTaskAdapter({ latencyMs: 0 })
    const dispose = vi.spyOn(adapter, 'dispose')
    const props = setup('Hello', { adapterFactory: () => adapter })
    await confirm()
    fireEvent.click(screen.getByRole('button', { name: 'Start another interaction' }))
    expect(dispose).toHaveBeenCalled()
    expect(props.onRestart).toHaveBeenCalledTimes(1)
  })
})

function deferredAdapter() {
  let resolve!: (snapshot: TaskSnapshot) => void
  let capturedSignal!: AbortSignal
  let clientRequestId = ''
  const submit = vi.fn((_request: Parameters<DemoTaskAdapter['submit']>[0], _session: TaskSession, signal: AbortSignal) => {
    clientRequestId = _request.clientRequestId
    capturedSignal = signal
    return new Promise<TaskSnapshot>(done => { resolve = done })
  })
  const status = vi.fn()
  const dispose = vi.fn()
  const adapter: DemoTaskAdapter = { mode: 'demo', submit, status, dispose }
  return { adapter, resolve: (snapshot: Partial<TaskSnapshot> = {}) => resolve({ requestId: 'demo-task-test', clientRequestId, demo: true, revision: 0, status: 'queued', action: 'GREET', ...snapshot }), getSignal: () => capturedSignal }
}

describe('stale responses, cancellation, and session lifetime', () => {
  it('aborts and ignores results after unmount', async () => {
    const pending = deferredAdapter()
    const props = setup('Hello', { adapterFactory: () => pending.adapter })
    await confirm()
    props.unmount()
    expect(pending.getSignal().aborted).toBe(true)
    await act(async () => pending.resolve())
    expect(pending.adapter.status).not.toHaveBeenCalled()
    expect(pending.adapter.dispose).toHaveBeenCalled()
  })
  it('times out a stuck submission and ignores its eventual result', async () => {
    const pending = deferredAdapter()
    setup('Hello', { adapterFactory: () => pending.adapter })
    await confirm()
    await advance(TASK_WAIT_MS)
    expect(pending.getSignal().aborted).toBe(true)
    await act(async () => pending.resolve({ status: 'completed', reply: 'Late test response', revision: 4 }))
    expect(screen.getByRole('heading', { name: 'Demo status timed out' })).toBeInTheDocument()
    expect(screen.queryByText('Late test response')).not.toBeInTheDocument()
  })
  it('checks the absolute task deadline when a sleeping browser returns a late response', async () => {
    const pending = deferredAdapter()
    setup('Hello', { adapterFactory: () => pending.adapter })
    await confirm()
    vi.setSystemTime(Date.now() + TASK_WAIT_MS)
    await act(async () => pending.resolve({ status: 'completed', reply: 'Late test response', revision: 4 }))
    expect(screen.getByRole('heading', { name: 'Demo status timed out' })).toBeInTheDocument()
    expect(screen.queryByText('Late test response')).not.toBeInTheDocument()
  })
  it('rejects a response arriving after session expiration', async () => {
    const pending = deferredAdapter()
    const context = session()
    const props = setup('Hello', { session: context, adapterFactory: () => pending.adapter })
    await confirm()
    vi.setSystemTime(context.expiresAt)
    await act(async () => pending.resolve())
    expect(props.onExpired).toHaveBeenCalled()
    expect(pending.getSignal().aborted).toBe(true)
    expect(screen.queryByText('demo-task-test')).not.toBeInTheDocument()
  })
  it('rejects mismatched request identity and real-mode snapshots in the demo UI', async () => {
    const pending = deferredAdapter()
    setup('Hello', { adapterFactory: () => pending.adapter })
    await confirm()
    await act(async () => pending.resolve({ clientRequestId: 'another-request', demo: false }))
    expect(screen.getByRole('heading', { name: 'Demo status could not be verified' })).toBeInTheDocument()
  })
  it('ignores an older revision without presenting a stale completion', async () => {
    const adapter: DemoTaskAdapter = {
      mode: 'demo',
      submit: vi.fn<DemoTaskAdapter['submit']>(async request => ({ requestId: 'demo-task', clientRequestId: request.clientRequestId, action: 'GREET', status: 'processing', revision: 2, demo: true })),
      status: vi.fn(), dispose: vi.fn(),
    }
    adapter.status = vi.fn(async () => ({ ...(await vi.mocked(adapter.submit).mock.results[0].value), status: 'completed' as TaskStatus, revision: 1, reply: 'Stale success' }))
    setup('Hello', { adapterFactory: () => adapter })
    await confirm()
    await advance(500)
    expect(screen.getByRole('heading', { name: 'Demo request processing' })).toBeInTheDocument()
    expect(screen.queryByText('Stale success')).not.toBeInTheDocument()
  })
  it('handles a polling failure without claiming completion', async () => {
    const adapter = createMockTaskAdapter({ latencyMs: 0 })
    vi.spyOn(adapter, 'status').mockRejectedValue(new TaskServiceError('service_unavailable'))
    setup('Hello', { adapterFactory: () => adapter })
    await confirm()
    await advance(500)
    expect(screen.getByRole('heading', { name: 'Demo service unavailable' })).toBeInTheDocument()
  })
})
