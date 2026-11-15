export const CALL_STATES = ['idle', 'requesting_permission', 'listening', 'speech_detected', 'finalizing_turn', 'awaiting_transcription', 'awaiting_review', 'awaiting_confirmation', 'processing', 'responding', 'muted', 'error', 'ended'] as const
export type CallState = typeof CALL_STATES[number]
const transitions: Record<CallState, readonly CallState[]> = {
  idle: ['requesting_permission', 'awaiting_review', 'error', 'ended'],
  requesting_permission: ['listening', 'awaiting_review', 'muted', 'error', 'ended'],
  listening: ['speech_detected', 'finalizing_turn', 'muted', 'awaiting_review', 'error', 'ended'],
  speech_detected: ['finalizing_turn', 'muted', 'awaiting_review', 'error', 'ended'],
  finalizing_turn: ['awaiting_transcription', 'awaiting_review', 'muted', 'error', 'ended'],
  awaiting_transcription: ['awaiting_review', 'requesting_permission', 'muted', 'error', 'ended'],
  awaiting_review: ['awaiting_confirmation', 'requesting_permission', 'muted', 'error', 'ended'],
  awaiting_confirmation: ['awaiting_review', 'processing', 'requesting_permission', 'muted', 'error', 'ended'],
  processing: ['responding', 'muted', 'error', 'ended'],
  responding: ['requesting_permission', 'awaiting_review', 'muted', 'error', 'ended'],
  muted: ['requesting_permission', 'awaiting_transcription', 'awaiting_review', 'awaiting_confirmation', 'processing', 'responding', 'error', 'ended'],
  error: ['requesting_permission', 'awaiting_review', 'muted', 'ended'],
  ended: ['requesting_permission', 'awaiting_review', 'error', 'idle'],
}
export function transitionCallState(current: CallState, next: CallState): CallState {
  if (current === next) return current
  if (!transitions[current].includes(next)) throw new Error(`Invalid call transition: ${current} → ${next}`)
  return next
}
export const callLabels: Record<CallState, string> = {
  idle: 'Ready to start', requesting_permission: 'Starting microphone…', listening: 'Listening…',
  speech_detected: 'I can hear you', finalizing_turn: 'Finishing your turn…',
  awaiting_transcription: 'Your turn is ready', awaiting_review: 'Review your request',
  awaiting_confirmation: 'Request awaiting confirmation', processing: 'Processing demo request…',
  responding: 'Demo response ready', muted: 'Microphone muted', error: 'Let’s try another way', ended: 'Conversation ended',
}
