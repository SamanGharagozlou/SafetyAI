/** Frontend simulation only. IDs are not credentials or backend authorization. */
export interface DemoSession {
  readonly id: string
  readonly nickname: string
  readonly expiresAt: number
}

export const DEFAULT_SESSION_DURATION_MS = 10 * 60 * 1000

export function sessionDuration(value?: string): number {
  if (value === undefined || value === '') return DEFAULT_SESSION_DURATION_MS
  const seconds = Number(value)
  if (!Number.isInteger(seconds) || seconds < 1 || seconds > 86400) {
    throw new Error('VITE_DEMO_SESSION_SECONDS must be an integer from 1 to 86400.')
  }
  return seconds * 1000
}

export const demoSessionService = {
  create(nickname: string, durationMs: number): DemoSession {
    if (!Number.isFinite(durationMs) || durationMs < 1000 || durationMs > 86400000) {
      throw new Error('Invalid demo session duration.')
    }
    const cleaned = nickname.trim()
    if (cleaned.length > 40) throw new Error('Use a nickname of 40 characters or fewer.')
    // getRandomValues also works in local-network HTTP demos where randomUUID may not.
    const bytes = crypto.getRandomValues(new Uint8Array(16))
    const id = `demo-${Array.from(bytes, byte => byte.toString(16).padStart(2, '0')).join('')}`
    return { id, nickname: cleaned || 'Guest', expiresAt: Date.now() + durationMs }
  },
  isExpired(session: DemoSession, now = Date.now()): boolean {
    return now >= session.expiresAt
  },
}
