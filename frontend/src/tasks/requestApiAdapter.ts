import { DEMO_ACTIONS, requireActiveSession, requireSubmission, TASK_STATUSES, TaskServiceError } from './contracts'
import type { TaskAdapter, TaskReason, TaskSnapshot } from './contracts'

export interface RequestApiConfig { submitPath: string; statusPathTemplate: string }
/** Only a future backend-issued credential belongs here; never use a nickname. */
export interface SessionAuthorization { accessToken: string; expiresAt: number }
export const requestApiConfig: RequestApiConfig = {
  submitPath: import.meta.env.VITE_REQUESTS_ENDPOINT || '/api/requests',
  statusPathTemplate: import.meta.env.VITE_REQUEST_STATUS_ENDPOINT || '/api/requests/{request_id}',
}
const reasons: TaskReason[] = ['unsupported_request', 'approval_denied', 'execution_failed', 'robot_unavailable']
function validPath(path: string) { return /^\/(?!\/)[^\\\s?#{}]+$/.test(path) }
function decodeTask(data: unknown): TaskSnapshot {
  if (!data || typeof data !== 'object') throw new TaskServiceError('invalid_response')
  const value = data as Record<string, unknown>
  if (typeof value.request_id !== 'string' || !/^[a-zA-Z0-9_-]{1,100}$/.test(value.request_id) ||
      typeof value.client_request_id !== 'string' || !/^[a-zA-Z0-9-]{1,100}$/.test(value.client_request_id) ||
      !TASK_STATUSES.includes(value.status as never) || (value.action !== null && !DEMO_ACTIONS.includes(value.action as never)) ||
      !Number.isSafeInteger(value.revision) || Number(value.revision) < 0 || typeof value.demo !== 'boolean' ||
      (value.reason !== undefined && !reasons.includes(value.reason as TaskReason)) ||
      (value.status === 'completed' && (value.action === null || typeof value.reply !== 'string' || !value.reply.trim() || value.reply.length > 2000)) ||
      (value.status !== 'completed' && value.reply !== undefined)) throw new TaskServiceError('invalid_response')
  return { requestId: value.request_id, clientRequestId: value.client_request_id, status: value.status as TaskSnapshot['status'],
    action: value.action as TaskSnapshot['action'], revision: Number(value.revision), demo: value.demo,
    reply: value.reply as string | undefined, reason: value.reason as TaskReason | undefined }
}

/** Prepared only. Phase 4 UI never instantiates or calls this adapter. */
export function createRequestApiAdapter(config: RequestApiConfig, getAuthorization: () => SessionAuthorization | null): TaskAdapter {
  const template = config.statusPathTemplate
  if (!validPath(config.submitPath) || !template.includes('{request_id}') || template.split('{request_id}').length !== 2 || !validPath(template.replace('{request_id}', 'id'))) throw new TaskServiceError('invalid_request')
  async function call(path: string, signal: AbortSignal, init: RequestInit = {}) {
    const authorization = getAuthorization()
    if (!authorization?.accessToken.trim() || authorization.accessToken.startsWith('demo-') || !Number.isFinite(authorization.expiresAt) || Date.now() >= authorization.expiresAt) throw new TaskServiceError('session_expired')
    const response = await fetch(path, { ...init, signal, cache: 'no-store', credentials: 'omit', redirect: 'error', referrerPolicy: 'no-referrer',
      headers: { ...init.headers, Authorization: `Bearer ${authorization.accessToken}` } })
    if (!response.ok) throw new TaskServiceError(response.status === 401 || response.status === 403 ? 'session_expired' : 'service_unavailable')
    try { return decodeTask(await response.json()) } catch { throw new TaskServiceError('invalid_response') }
  }
  return {
    mode: 'api',
    async submit(request, session, signal) {
      requireActiveSession(session); requireSubmission(request)
      const result = await call(config.submitPath, signal, { method: 'POST', headers: { 'Content-Type': 'application/json', 'Idempotency-Key': request.clientRequestId },
        body: JSON.stringify({ text: request.text, source: request.source, proposed_action: request.proposedAction, client_request_id: request.clientRequestId }) })
      requireActiveSession(session)
      if (result.clientRequestId !== request.clientRequestId) throw new TaskServiceError('invalid_response')
      return result
    },
    async status(requestId, session, signal) {
      requireActiveSession(session)
      if (!/^[a-zA-Z0-9_-]{1,100}$/.test(requestId)) throw new TaskServiceError('invalid_request')
      const result = await call(template.replace('{request_id}', encodeURIComponent(requestId)), signal, { method: 'GET' })
      requireActiveSession(session)
      if (result.requestId !== requestId) throw new TaskServiceError('invalid_response')
      return result
    },
    dispose() { /* Caller owns AbortSignals. No server cancellation/deletion is implied. */ },
  }
}
