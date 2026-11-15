// Client for the robot companion backend (docs/companion_api.md). Same conventions as tasks/requestApiAdapter.ts:
// same-origin root-relative paths, Bearer credential, no cookies, no caching, strict response validation.
// The credential is backend-issued and lives in memory only. The map is never sent: only the layout of the destinations.

export const ROBOT_API = '/api'

export interface RobotAuthorization { readonly accessToken: string; readonly expiresAt: number }
export interface RobotPlace { readonly name: string; readonly label: string; readonly description: string; readonly x: number; readonly y: number; readonly home: boolean }
export const ROBOT_STATES = ['IDLE', 'AWAITING_INPUT', 'PREPARING', 'NAVIGATING', 'ARRIVED', 'BLOCKED', 'STOPPED', 'RETURNING_HOME', 'HOLD'] as const
export type RobotStateName = typeof ROBOT_STATES[number]
export interface RobotLine { readonly seq: number; readonly text: string }
export interface RobotView {
  readonly seq: number
  readonly lines: readonly RobotLine[]
  readonly expiresAt: number
  readonly state: RobotStateName
  readonly goalStatus: 'sending' | 'received' | 'moving' | null
  readonly destination: string | null
  readonly at: string | null
  readonly distanceRemaining: number | null
  readonly reason: string
  readonly robot: { readonly x: number | null; readonly y: number | null; readonly yaw: number | null; readonly available: boolean }
}
export interface RobotSessionInfo { readonly authorization: RobotAuthorization; readonly robotName: string; readonly nickname: string; readonly places: readonly RobotPlace[] }

export type RobotErrorCode = 'invalid_qr' | 'robot_busy' | 'robot_unavailable' | 'session_expired' | 'service_unavailable' | 'invalid_response'
export class RobotApiError extends Error {
  constructor(readonly code: RobotErrorCode, message = '') { super(message || code); this.name = 'RobotApiError' }
}

type Json = Record<string, unknown>
const isRecord = (v: unknown): v is Json => typeof v === 'object' && v !== null && !Array.isArray(v)
const isNum = (v: unknown): v is number => typeof v === 'number' && Number.isFinite(v)
const orNull = <T>(v: unknown, ok: (x: unknown) => x is T): T | null => (v === null || v === undefined ? null : ok(v) ? v : (() => { throw new RobotApiError('invalid_response') })())

async function call(path: string, init: RequestInit, signal: AbortSignal | undefined, auth?: RobotAuthorization): Promise<Json> {
  if (auth && (!auth.accessToken || !Number.isFinite(auth.expiresAt) || Date.now() >= auth.expiresAt)) throw new RobotApiError('session_expired')
  const headers: Record<string, string> = { Accept: 'application/json' }
  if (init.body !== undefined) headers['Content-Type'] = 'application/json'
  if (auth) headers.Authorization = `Bearer ${auth.accessToken}`
  let response: Response
  try {
    response = await fetch(path, { ...init, headers: { ...headers, ...(init.headers as Record<string, string> | undefined) }, signal, cache: 'no-store', credentials: 'omit', redirect: 'error', referrerPolicy: 'no-referrer' })
  } catch (error) {
    if (signal?.aborted) throw error
    throw new RobotApiError('service_unavailable')
  }
  let body: unknown = null
  try { body = await response.json() } catch { /* empty or not JSON */ }
  if (!response.ok) {
    const code = isRecord(body) && typeof body.error === 'string' ? body.error : ''
    const message = isRecord(body) && typeof body.message === 'string' ? body.message : ''
    if (response.status === 401) throw new RobotApiError('session_expired')
    if (code === 'invalid_qr') throw new RobotApiError('invalid_qr', message)
    if (code === 'robot_busy') throw new RobotApiError('robot_busy', message)
    if (code === 'robot_unavailable') throw new RobotApiError('robot_unavailable', message)
    throw new RobotApiError('service_unavailable')
  }
  if (!isRecord(body)) throw new RobotApiError('invalid_response')
  return body
}

function decodePlace(v: unknown): RobotPlace {
  if (!isRecord(v) || typeof v.name !== 'string' || !/^[a-zA-Z0-9_-]{1,64}$/.test(v.name) || typeof v.label !== 'string' || !v.label.trim() ||
      typeof v.description !== 'string' || !isNum(v.x) || !isNum(v.y) || typeof v.home !== 'boolean') throw new RobotApiError('invalid_response')
  return { name: v.name, label: v.label, description: v.description, x: v.x, y: v.y, home: v.home }
}

export async function createRobotSession(qrToken: string, nickname: string, signal?: AbortSignal): Promise<RobotSessionInfo> {
  if (!qrToken) throw new RobotApiError('invalid_qr')
  const body = await call(`${ROBOT_API}/session`, { method: 'POST', body: JSON.stringify({ token: qrToken, nickname: nickname.trim().slice(0, 24), lang: (navigator.language || 'en').slice(0, 2) }) }, signal)
  if (typeof body.accessToken !== 'string' || body.accessToken.length < 16 || !isNum(body.expiresAt) || body.expiresAt <= Date.now() ||
      !isRecord(body.robot) || typeof body.robot.name !== 'string' || typeof body.nickname !== 'string' || !Array.isArray(body.places) || body.places.length > 100) throw new RobotApiError('invalid_response')
  return { authorization: { accessToken: body.accessToken, expiresAt: body.expiresAt }, robotName: body.robot.name, nickname: body.nickname, places: body.places.map(decodePlace) }
}

export async function endRobotSession(auth: RobotAuthorization, signal?: AbortSignal): Promise<string> {
  const body = await call(`${ROBOT_API}/session`, { method: 'DELETE' }, signal, auth)
  return typeof body.reply === 'string' ? body.reply : ''
}

export async function stopRobot(auth: RobotAuthorization, signal?: AbortSignal): Promise<void> {
  await call(`${ROBOT_API}/robot/stop`, { method: 'POST', body: '{}' }, signal, auth)
}

export async function fetchRobotView(auth: RobotAuthorization, after: number, signal?: AbortSignal): Promise<RobotView> {
  const b = await call(`${ROBOT_API}/state?after=${Math.max(0, Math.floor(after))}`, { method: 'GET' }, signal, auth)
  const robot = b.robot
  if (!isNum(b.seq) || !Array.isArray(b.messages) || !isNum(b.expiresAt) || !ROBOT_STATES.includes(b.state as RobotStateName) || !isRecord(robot) || typeof robot.available !== 'boolean' ||
      typeof b.reason !== 'string' || !['sending', 'received', 'moving', null, undefined].includes(b.goal_status as never)) throw new RobotApiError('invalid_response')
  const lines = b.messages.map(m => {
    if (!isRecord(m) || !isNum(m.seq) || typeof m.text !== 'string') throw new RobotApiError('invalid_response')
    return { seq: m.seq, text: m.text.slice(0, 2000) }
  })
  const text = (v: unknown): v is string => typeof v === 'string'
  return {
    seq: b.seq, lines, expiresAt: b.expiresAt, state: b.state as RobotStateName, goalStatus: (b.goal_status ?? null) as RobotView['goalStatus'],
    destination: orNull(b.destination, text), at: orNull(b.at, text), distanceRemaining: orNull(b.distance_remaining, isNum), reason: b.reason,
    robot: { x: orNull(robot.x, isNum), y: orNull(robot.y, isNum), yaw: orNull(robot.yaw, isNum), available: robot.available },
  }
}
