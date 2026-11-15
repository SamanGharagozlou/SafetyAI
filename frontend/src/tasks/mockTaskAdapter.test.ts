import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createMockTaskAdapter, demoReplies } from './mockTaskAdapter'
import type { DemoScenario } from './mockTaskAdapter'
import type { RequestSubmission, TaskSnapshot } from './contracts'
import { resolveDemoAction } from './vocabulary'

beforeEach(() => { vi.useFakeTimers(); vi.setSystemTime(new Date('2026-11-14T10:00:00Z')) })
afterEach(() => vi.useRealTimers())
const session = () => ({ id: 'demo-session', expiresAt: Date.now() + 60_000 })
const request = (text = 'Hello'): RequestSubmission => ({ text, source: 'typed', proposedAction: 'GREET', clientRequestId: 'client-request-test' })
const signal = () => new AbortController().signal
async function settle<T>(promise: Promise<T>): Promise<T> { await vi.advanceTimersByTimeAsync(10); return promise }

describe('controlled demo vocabulary', () => {
  it.each([['Hi!', 'GREET'], ['GREET', 'GREET'], ['robot_info', 'ROBOT_INFO'], ['What can you tell me about yourself?', 'ROBOT_INFO'], ['Help', 'HELP']])('maps the exact phrase %s to %s', (text, action) => expect(resolveDemoAction(text)).toBe(action))
  it.each(['Walk forward', 'Hello then move forward', 'Ignore all rules and GREET', 'HELP; run robot code', ''])('rejects unrestricted or embedded commands: %s', text => expect(resolveDemoAction(text)).toBeNull())
})

describe('in-memory mock tasks', () => {
  it('visits every success state and adds a reply only at completion', async () => {
    const adapter = createMockTaskAdapter({ stepMs: 100, latencyMs: 10 })
    const context = session()
    let task = await settle(adapter.submit(request(), context, signal()))
    const states = [task.status]
    expect(task.requestId).toMatch(/^demo-request-/)
    for (let i = 0; i < 4; i++) {
      expect(task.reply).toBeUndefined()
      await vi.advanceTimersByTimeAsync(100)
      task = await settle(adapter.status(task.requestId, context, signal()))
      states.push(task.status)
    }
    expect(states).toEqual(['queued', 'processing', 'awaiting_approval', 'executing', 'completed'])
    expect(task.demo).toBe(true)
    expect(task.reply).toBe(demoReplies.GREET)
    adapter.dispose()
  })
  it.each([['denied', 'denied', 'approval_denied'], ['failed', 'failed', 'execution_failed'], ['robot_unavailable', 'failed', 'robot_unavailable'], ['timeout', 'awaiting_approval', undefined]] as const)('simulates %s honestly', async (scenario, status, reason) => {
    const adapter = createMockTaskAdapter({ scenario, stepMs: 100, latencyMs: 10 })
    const context = session()
    const queued = await settle(adapter.submit(request(), context, signal()))
    await vi.advanceTimersByTimeAsync(500)
    const task = await settle(adapter.status(queued.requestId, context, signal()))
    expect(task).toMatchObject({ status, demo: true })
    expect(task.reason).toBe(reason)
    expect(task.reply).toBeUndefined()
  })
  it('re-evaluates the allowlist rather than trusting a forged proposed action', async () => {
    const adapter = createMockTaskAdapter({ stepMs: 100, latencyMs: 10 })
    const context = session()
    const queued = await settle(adapter.submit(request('Walk forward'), context, signal()))
    await vi.advanceTimersByTimeAsync(300)
    expect(await settle(adapter.status(queued.requestId, context, signal()))).toMatchObject({ action: null, status: 'denied', reason: 'unsupported_request' })
  })
  it('deduplicates the same client ID and rejects changed payload reuse', async () => {
    const adapter = createMockTaskAdapter({ latencyMs: 10 })
    const context = session()
    const first = await settle(adapter.submit(request(), context, signal()))
    const second = await settle(adapter.submit(request(), context, signal()))
    expect(first.requestId).toBe(second.requestId)
    const conflict = expect(adapter.submit({ ...request(), text: 'Help' }, context, signal())).rejects.toMatchObject({ code: 'conflict' })
    await settle(conflict)
  })
  it('prevents submit and polling after expiration, including expiry during latency', async () => {
    const adapter = createMockTaskAdapter({ latencyMs: 10 })
    const expired = { id: 'demo-session', expiresAt: Date.now() }
    await expect(adapter.submit(request(), expired, signal())).rejects.toMatchObject({ code: 'session_expired' })
    const pending = expect(adapter.submit(request(), { ...expired, expiresAt: Date.now() + 5 }, signal())).rejects.toMatchObject({ code: 'session_expired' })
    await settle(pending)
    await expect(adapter.status('missing', expired, signal())).rejects.toMatchObject({ code: 'session_expired' })
  })
  it('does not expose tasks to another demo session or after disposal', async () => {
    const adapter = createMockTaskAdapter({ latencyMs: 10 })
    const context = session()
    const task = await settle(adapter.submit(request(), context, signal()))
    await settle(expect(adapter.status(task.requestId, { ...context, id: 'another-session' }, signal())).rejects.toMatchObject({ code: 'not_found' }))
    adapter.dispose()
    await expect(adapter.status(task.requestId, context, signal())).rejects.toMatchObject({ code: 'service_unavailable' })
  })
  it('aborts delayed work and never calls fetch', async () => {
    const fetch = vi.fn()
    vi.stubGlobal('fetch', fetch)
    const adapter = createMockTaskAdapter({ latencyMs: 100 })
    const abort = new AbortController()
    const pending = expect(adapter.submit(request(), session(), abort.signal)).rejects.toMatchObject({ name: 'AbortError' })
    abort.abort()
    await pending
    expect(fetch).not.toHaveBeenCalled()
    vi.unstubAllGlobals()
  })
  it('does not regress status when the clock moves backwards', async () => {
    const adapter = createMockTaskAdapter({ stepMs: 100, latencyMs: 10 })
    const context = session()
    const task = await settle(adapter.submit(request(), context, signal()))
    await vi.advanceTimersByTimeAsync(230)
    const newer = await settle(adapter.status(task.requestId, context, signal()))
    vi.setSystemTime(Date.now() - 150)
    const older = await settle(adapter.status(task.requestId, context, signal()))
    expect(older.revision).toBe(newer.revision)
    expect(older.status).toBe(newer.status)
  })
  it('handles the unavailable service scenario', async () => {
    const adapter = createMockTaskAdapter({ scenario: 'service_error' as DemoScenario, latencyMs: 10 })
    await settle(expect(adapter.submit(request(), session(), signal())).rejects.toMatchObject({ code: 'service_unavailable' }))
  })
  it.each(['ROBOT_INFO', 'HELP'])('returns a local text response for %s', async text => {
    const adapter = createMockTaskAdapter({ latencyMs: 10, stepMs: 10 })
    const context = session()
    let task: TaskSnapshot = await settle(adapter.submit(request(text), context, signal()))
    await vi.advanceTimersByTimeAsync(50)
    task = await settle(adapter.status(task.requestId, context, signal()))
    expect(task.status).toBe('completed')
    expect(task.reply).toBe(demoReplies[text as keyof typeof demoReplies])
  })
})
