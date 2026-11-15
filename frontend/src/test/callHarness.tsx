import { act, fireEvent, render, screen } from '@testing-library/react'
import { vi } from 'vitest'
import { App } from '../App'

export function joinCall(nickname = 'Nova') {
  window.history.replaceState(null, '', '/join')
  const view = render(<App/>)
  fireEvent.change(screen.getByLabelText(/What should I call you/), { target: { value: nickname } })
  fireEvent.click(screen.getByRole('button', { name: /Let's Get Started/ }))
  return view
}
export async function startCall() { await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Start Conversation' })) }) }
export async function finishManualTurn() {
  await act(async () => { await vi.advanceTimersByTimeAsync(400) })
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Finish turn' })) })
}
export function writeFallback() {
  const summary = screen.queryByText(/Need another way/)
  if (summary && !(summary.closest('details') as HTMLDetailsElement).open) fireEvent.click(summary)
  fireEvent.click(screen.getByRole('button', { name: 'Write a request instead' }))
}
