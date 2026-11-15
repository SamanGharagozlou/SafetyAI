import { describe, expect, it } from 'vitest'
import { CALL_STATES, transitionCallState } from './state'

describe('typed call state transitions', () => {
  it('supports the complete conversation path', () => {
    const path = ['idle', 'requesting_permission', 'listening', 'speech_detected', 'finalizing_turn', 'awaiting_transcription', 'awaiting_review', 'awaiting_confirmation', 'processing', 'responding', 'requesting_permission', 'listening', 'muted', 'requesting_permission', 'listening', 'ended'] as const
    for (let i = 1; i < path.length; i++) expect(transitionCallState(path[i - 1], path[i])).toBe(path[i])
  })
  it.each(CALL_STATES)('allows cleanup from %s', state => expect(transitionCallState(state, 'ended')).toBe('ended'))
  it('rejects submitting directly from listening or responding with no task', () => {
    expect(() => transitionCallState('listening', 'processing')).toThrow()
    expect(() => transitionCallState('idle', 'responding')).toThrow()
  })
})
