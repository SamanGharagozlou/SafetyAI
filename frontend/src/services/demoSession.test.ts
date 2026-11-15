import { describe, expect, it } from 'vitest'
import { DEFAULT_SESSION_DURATION_MS, demoSessionService, sessionDuration } from './demoSession'

describe('demo session service', () => {
  it('creates distinct explicitly demo IDs and normalizes the optional nickname', () => {
    const first = demoSessionService.create('  Nova  ', 1000)
    const second = demoSessionService.create('   ', 1000)
    expect(first.id).toMatch(/^demo-[0-9a-f]{32}$/)
    expect(first.id).not.toBe(second.id)
    expect(first.nickname).toBe('Nova')
    expect(second.nickname).toBe('Guest')
  })
  it('expires at the exact deadline', () => {
    const session = demoSessionService.create('', 1000)
    expect(demoSessionService.isExpired(session, session.expiresAt - 1)).toBe(false)
    expect(demoSessionService.isExpired(session, session.expiresAt)).toBe(true)
  })
  it('uses a default and validates configured expiry', () => {
    expect(sessionDuration()).toBe(DEFAULT_SESSION_DURATION_MS)
    expect(sessionDuration('1')).toBe(1000)
    expect(sessionDuration('86400')).toBe(86400000)
    for (const invalid of ['0', '-1', '1.5', 'nope', 'Infinity', '86401', ' ']) {
      expect(() => sessionDuration(invalid)).toThrow()
    }
    expect(() => demoSessionService.create('a'.repeat(41), 1000)).toThrow()
    expect(() => demoSessionService.create('', NaN)).toThrow()
  })
})
