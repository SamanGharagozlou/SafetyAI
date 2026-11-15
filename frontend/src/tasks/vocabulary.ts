import type { DemoAction } from './contracts'

// Exact phrases only: a greeting inside a movement request must never authorize it.
const phrases: Record<DemoAction, readonly string[]> = {
  GREET: ['greet', 'hello', 'hi', 'say hello', 'greet me'],
  ROBOT_INFO: ['robot_info', 'robot info', 'tell me about yourself', 'what can you tell me about yourself', 'tell me about the robot', 'what is this robot'],
  HELP: ['help', 'help me', 'what can i ask', 'show help'],
}
export function resolveDemoAction(text: string): DemoAction | null {
  const normalized = text.trim().toLowerCase().replace(/[.!?]+$/, '').replace(/\s+/g, ' ')
  return (Object.keys(phrases) as DemoAction[]).find(action => phrases[action].includes(normalized)) ?? null
}
export const actionDescriptions: Record<DemoAction, string> = {
  GREET: 'A friendly text greeting',
  ROBOT_INFO: 'An introduction to this robot demo',
  HELP: 'Suggestions for what to ask',
}
