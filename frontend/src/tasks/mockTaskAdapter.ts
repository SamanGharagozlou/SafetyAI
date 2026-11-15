import { isTerminal, randomRequestId, requireActiveSession, requireSubmission, TaskServiceError } from './contracts'
import type { DemoAction, RequestSubmission, TaskAdapter, TaskReason, TaskSession, TaskSnapshot, TaskStatus } from './contracts'
import { resolveDemoAction } from './vocabulary'

export const DEMO_SCENARIOS = ['success', 'denied', 'failed', 'robot_unavailable', 'timeout', 'service_error'] as const
export type DemoScenario = typeof DEMO_SCENARIOS[number]
export interface DemoTaskAdapter extends TaskAdapter { readonly mode: 'demo' }
export interface MockTaskOptions { scenario?: DemoScenario; stepMs?: number; latencyMs?: number }

export const demoReplies: Record<DemoAction, string> = {
  GREET: 'Hello, curious human! I’m ClawMetry. It’s lovely to meet you. This greeting is a demo text response; the physical robot hasn’t moved or spoken.',
  ROBOT_INFO: 'ClawMetry is a visitor interface for an AGIBOT D1 Ultra-W robot. Here you can try short requests and see a simulated task journey. No physical robot is connected to this demo.',
  HELP: 'Try “Hello”, “Robot info”, or “Help”. These demo requests produce text responses only. Movement and other robot commands are not supported.',
}
function pause(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal.aborted) { reject(new DOMException('Canceled', 'AbortError')); return }
    const finish = () => { signal.removeEventListener('abort', cancel); resolve() }
    const timer = setTimeout(finish, ms)
    const cancel = () => { clearTimeout(timer); signal.removeEventListener('abort', cancel); reject(new DOMException('Canceled', 'AbortError')) }
    signal.addEventListener('abort', cancel, { once: true })
  })
}
interface Job { request: RequestSubmission; sessionId: string; createdAt: number; snapshot: TaskSnapshot }

/** Entirely browser-local. No network, AI interpretation, safety approval, or SDK. */
export function createMockTaskAdapter({ scenario = 'success', stepMs = 900, latencyMs = 120 }: MockTaskOptions = {}): DemoTaskAdapter {
  if (!Number.isFinite(stepMs) || stepMs <= 0 || !Number.isFinite(latencyMs) || latencyMs < 0 || !DEMO_SCENARIOS.includes(scenario)) throw new TaskServiceError('invalid_request')
  const jobs = new Map<string, Job>()
  const keys = new Map<string, string>()
  let disposed = false
  const available = () => { if (disposed) throw new TaskServiceError('service_unavailable') }
  function advance(job: Job): TaskSnapshot {
    if (isTerminal(job.snapshot.status)) return job.snapshot
    const step = Math.max(0, Math.floor((Date.now() - job.createdAt) / stepMs))
    const action = job.snapshot.action
    let status: TaskStatus = step === 0 ? 'queued' : step === 1 ? 'processing' : step === 2 ? 'awaiting_approval' : step === 3 ? 'executing' : 'completed'
    let reason: TaskReason | undefined
    if (!action && step >= 2) { status = 'denied'; reason = 'unsupported_request' }
    else if (scenario === 'robot_unavailable' && step >= 2) { status = 'failed'; reason = 'robot_unavailable' }
    else if (scenario === 'timeout' && step >= 2) status = 'awaiting_approval'
    else if (scenario === 'denied' && step >= 3) { status = 'denied'; reason = 'approval_denied' }
    else if (scenario === 'failed' && step >= 4) { status = 'failed'; reason = 'execution_failed' }
    // Never regress on clock changes; a snapshot revision orders status observations.
    if (step < job.snapshot.revision) return job.snapshot
    job.snapshot = { ...job.snapshot, status, revision: step, reason,
      reply: status === 'completed' && action ? demoReplies[action] : undefined }
    return job.snapshot
  }
  return {
    mode: 'demo',
    async submit(request: RequestSubmission, session: TaskSession, signal: AbortSignal) {
      requireActiveSession(session); requireSubmission(request); available()
      await pause(latencyMs, signal)
      requireActiveSession(session); available()
      if (scenario === 'service_error') throw new TaskServiceError('service_unavailable')
      const existingId = keys.get(request.clientRequestId)
      if (existingId) {
        const existing = jobs.get(existingId)!
        if (existing.sessionId !== session.id || existing.request.text !== request.text || existing.request.source !== request.source || existing.request.proposedAction !== request.proposedAction) throw new TaskServiceError('conflict')
        return advance(existing)
      }
      const requestId = randomRequestId('demo-request')
      // The mock reclassifies text independently. A client-supplied action is not authority.
      const snapshot: TaskSnapshot = { requestId, clientRequestId: request.clientRequestId, status: 'queued', action: resolveDemoAction(request.text), revision: 0, demo: true }
      jobs.set(requestId, { request: { ...request }, sessionId: session.id, createdAt: Date.now(), snapshot })
      keys.set(request.clientRequestId, requestId)
      return snapshot
    },
    async status(requestId, session, signal) {
      requireActiveSession(session); available()
      await pause(latencyMs, signal)
      requireActiveSession(session); available()
      const job = jobs.get(requestId)
      if (!job || job.sessionId !== session.id) throw new TaskServiceError('not_found')
      return advance(job)
    },
    dispose() { disposed = true; jobs.clear(); keys.clear() },
  }
}
