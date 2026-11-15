// The frontend's task model (tasks/contracts.ts) extended with what the robot backend adds (docs/companion_api.md):
// actions GO_TO_PLACE / STOP_ROBOT / END_SESSION, an optional tapped `place`, and the optional response fields `place`, `message`, `progress`.
// Kept separate from the demo adapter on purpose: the demo build, its mock adapter and their tests are untouched.
import { TASK_STATUSES, TaskServiceError, randomRequestId } from '../tasks/contracts'
import type { RequestSource, TaskStatus } from '../tasks/contracts'
import { ROBOT_API } from './robotApi'
import type { RobotAuthorization } from './robotApi'

export const ROBOT_ACTIONS = ['GREET', 'ROBOT_INFO', 'HELP', 'GO_TO_PLACE', 'STOP_ROBOT', 'END_SESSION'] as const
export type RobotAction = typeof ROBOT_ACTIONS[number]
export type RobotTaskReason = 'unsupported_request' | 'approval_denied' | 'execution_failed' | 'robot_unavailable'
const reasons: RobotTaskReason[] = ['unsupported_request', 'approval_denied', 'execution_failed', 'robot_unavailable']
export const MAX_ROBOT_TEXT = 500

export interface RobotSubmission { readonly text: string; readonly source: RequestSource; readonly proposedAction: RobotAction | null; readonly place?: string; readonly clientRequestId: string }
export interface RobotTask {
  readonly requestId: string
  readonly clientRequestId: string
  readonly status: TaskStatus
  readonly action: RobotAction | null
  readonly revision: number
  readonly reply?: string
  readonly reason?: RobotTaskReason
  readonly message?: string
  readonly place?: string
  readonly progress?: { readonly goalStatus: string | null; readonly distanceRemaining: number | null }
}
export interface RobotTaskClient {
  submit(request: RobotSubmission, signal: AbortSignal): Promise<RobotTask>
  status(requestId: string, signal: AbortSignal): Promise<RobotTask>
}
export const newRobotRequestId = () => randomRequestId('robot-request')
export const isRobotTerminal = (s: TaskStatus) => s === 'completed' || s === 'denied' || s === 'failed'

/** Same rules as the frontend's decodeTask: `reply` only when completed (and then non-empty); never `demo: true` here. */
export function decodeRobotTask(data: unknown): RobotTask {
  if (!data || typeof data !== 'object') throw new TaskServiceError('invalid_response')
  const v = data as Record<string, unknown>
  if (typeof v.request_id !== 'string' || !/^[a-zA-Z0-9_-]{1,100}$/.test(v.request_id) || typeof v.client_request_id !== 'string' || !/^[a-zA-Z0-9-]{1,100}$/.test(v.client_request_id) ||
      !TASK_STATUSES.includes(v.status as never) || (v.action !== null && !ROBOT_ACTIONS.includes(v.action as never)) || !Number.isSafeInteger(v.revision) || Number(v.revision) < 0 ||
      v.demo !== false || (v.reason !== undefined && !reasons.includes(v.reason as RobotTaskReason)) ||
      (v.status === 'completed' && (v.action === null || typeof v.reply !== 'string' || !v.reply.trim() || v.reply.length > 2000)) ||
      (v.status !== 'completed' && v.reply !== undefined) ||
      (v.message !== undefined && (typeof v.message !== 'string' || v.message.length > 2000)) || (v.place !== undefined && (typeof v.place !== 'string' || !/^[a-zA-Z0-9_-]{1,64}$/.test(v.place)))) {
    throw new TaskServiceError('invalid_response')
  }
  let progress: RobotTask['progress']
  if (v.progress !== undefined) {
    const p = v.progress as Record<string, unknown> | null
    if (!p || typeof p !== 'object' || (p.distance_remaining != null && (typeof p.distance_remaining !== 'number' || !Number.isFinite(p.distance_remaining))) ||
        (p.goal_status != null && typeof p.goal_status !== 'string')) throw new TaskServiceError('invalid_response')
    progress = { goalStatus: (p.goal_status as string | null | undefined) ?? null, distanceRemaining: (p.distance_remaining as number | null | undefined) ?? null }
  }
  return { requestId: v.request_id, clientRequestId: v.client_request_id, status: v.status as TaskStatus, action: v.action as RobotAction | null, revision: Number(v.revision),
    reply: v.reply as string | undefined, reason: v.reason as RobotTaskReason | undefined, message: v.message as string | undefined, place: v.place as string | undefined, progress }
}

export function createRobotTaskClient(getAuthorization: () => RobotAuthorization | null): RobotTaskClient {
  async function call(path: string, signal: AbortSignal, init: RequestInit): Promise<RobotTask> {
    const auth = getAuthorization()
    if (!auth?.accessToken.trim() || !Number.isFinite(auth.expiresAt) || Date.now() >= auth.expiresAt) throw new TaskServiceError('session_expired')
    const response = await fetch(path, { ...init, signal, cache: 'no-store', credentials: 'omit', redirect: 'error', referrerPolicy: 'no-referrer',
      headers: { ...(init.headers as Record<string, string> | undefined), Accept: 'application/json', Authorization: `Bearer ${auth.accessToken}` } })
    if (!response.ok) throw new TaskServiceError(response.status === 401 || response.status === 403 ? 'session_expired' : response.status === 409 ? 'conflict' : 'service_unavailable')
    try { return decodeRobotTask(await response.json()) } catch { throw new TaskServiceError('invalid_response') }
  }
  return {
    async submit(request, signal) {
      const text = request.text.trim()
      if (!text || text.length > MAX_ROBOT_TEXT || !/^[a-zA-Z0-9-]{1,100}$/.test(request.clientRequestId) || !['typed', 'transcript'].includes(request.source) ||
          (request.proposedAction !== null && !ROBOT_ACTIONS.includes(request.proposedAction))) throw new TaskServiceError('invalid_request')
      const body: Record<string, unknown> = { text, source: request.source, proposed_action: request.proposedAction, client_request_id: request.clientRequestId }
      if (request.place) body.place = request.place
      const task = await call(`${ROBOT_API}/requests`, signal, { method: 'POST', body: JSON.stringify(body), headers: { 'Content-Type': 'application/json', 'Idempotency-Key': request.clientRequestId } })
      if (task.clientRequestId !== request.clientRequestId) throw new TaskServiceError('invalid_response')
      return task
    },
    async status(requestId, signal) {
      if (!/^[a-zA-Z0-9_-]{1,100}$/.test(requestId)) throw new TaskServiceError('invalid_request')
      const task = await call(`${ROBOT_API}/requests/${encodeURIComponent(requestId)}`, signal, { method: 'GET' })
      if (task.requestId !== requestId) throw new TaskServiceError('invalid_response')
      return task
    },
  }
}
