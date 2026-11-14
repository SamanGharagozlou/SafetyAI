import { MAX_REQUEST_LENGTH } from '../services/transcription'

export const TASK_STATUSES = ['queued', 'processing', 'awaiting_approval', 'executing', 'completed', 'denied', 'failed'] as const
export type TaskStatus = typeof TASK_STATUSES[number]
export const DEMO_ACTIONS = ['GREET', 'ROBOT_INFO', 'HELP'] as const
export type DemoAction = typeof DEMO_ACTIONS[number]
export type RequestSource = 'typed' | 'transcript'
export type TaskReason = 'unsupported_request' | 'approval_denied' | 'execution_failed' | 'robot_unavailable'

/** This context never includes a nickname or an authorization token. */
export interface TaskSession { readonly id: string; readonly expiresAt: number }
export interface RequestSubmission {
  readonly text: string
  readonly source: RequestSource
  readonly proposedAction: DemoAction | null
  readonly clientRequestId: string
}
export interface TaskSnapshot {
  readonly requestId: string
  readonly clientRequestId: string
  readonly status: TaskStatus
  readonly action: DemoAction | null
  readonly revision: number
  readonly demo: boolean
  readonly reply?: string
  readonly reason?: TaskReason
}
export interface TaskAdapter {
  readonly mode: 'demo' | 'api'
  submit(request: RequestSubmission, session: TaskSession, signal: AbortSignal): Promise<TaskSnapshot>
  status(requestId: string, session: TaskSession, signal: AbortSignal): Promise<TaskSnapshot>
  /** Releases frontend resources; does not imply server-side cancellation/deletion. */
  dispose(): void
}
export type TaskErrorCode = 'session_expired' | 'service_unavailable' | 'invalid_response' | 'not_found' | 'conflict' | 'invalid_request'
export class TaskServiceError extends Error {
  constructor(readonly code: TaskErrorCode) { super(code); this.name = 'TaskServiceError' }
}
export function requireActiveSession(session: TaskSession) {
  if (!session.id || !Number.isFinite(session.expiresAt) || Date.now() >= session.expiresAt) throw new TaskServiceError('session_expired')
}
export function requireSubmission(request: RequestSubmission) {
  if (!request.text.trim() || request.text.length > MAX_REQUEST_LENGTH || !/^[a-zA-Z0-9-]{1,100}$/.test(request.clientRequestId) || !['typed', 'transcript'].includes(request.source) || (request.proposedAction !== null && !DEMO_ACTIONS.includes(request.proposedAction))) {
    throw new TaskServiceError('invalid_request')
  }
}
export function isTerminal(status: TaskStatus) { return status === 'completed' || status === 'denied' || status === 'failed' }
export function randomRequestId(prefix: string) {
  return `${prefix}-${Array.from(crypto.getRandomValues(new Uint8Array(16)), byte => byte.toString(16).padStart(2, '0')).join('')}`
}
