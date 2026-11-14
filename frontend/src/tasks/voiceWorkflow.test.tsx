import { act, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { App } from '../App'
import { installMediaMocks } from '../test/media'

beforeEach(() => {
  installMediaMocks()
  vi.useFakeTimers()
  window.history.replaceState(null, '', '/join')
  vi.stubEnv('VITE_DEMO_SESSION_SECONDS', '600')
  vi.stubEnv('VITE_TRANSCRIBE_ENDPOINT', '/api/transcribe')
  vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => {})
  vi.spyOn(HTMLMediaElement.prototype, 'load').mockImplementation(() => {})
})
afterEach(() => vi.unstubAllGlobals())
async function review() {
  render(<App/>)
  fireEvent.click(screen.getByRole('button', { name: 'Continue as Guest' }))
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Start Recording' })) })
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Stop Recording' })) })
}

describe('voice to explicit request confirmation', () => {
  it('reviews only the actual configured-service response and still requires task confirmation', async () => {
    const fetch = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ text: 'Hello' }) })
    vi.stubGlobal('fetch', fetch)
    await review()
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Continue — send for transcription' })) })
    expect(screen.getByLabelText('Your request')).toHaveValue('Hello')
    fireEvent.click(screen.getByRole('button', { name: 'Continue' }))
    expect(screen.getByText('You · Transcription reviewed')).toBeInTheDocument()
    expect(screen.getByText('Hello', { selector: 'blockquote' })).toBeInTheDocument()
    expect(screen.queryByText('Demo request ID')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Confirm demo request' }))
    await act(async () => { await vi.advanceTimersByTimeAsync(150) })
    expect(screen.getByRole('heading', { name: 'Demo request queued' })).toBeInTheDocument()
    expect(fetch).toHaveBeenCalledTimes(1) // The explicit transcription upload only.
    fireEvent.click(screen.getByRole('button', { name: 'End Session' }))
    await act(async () => { await vi.advanceTimersByTimeAsync(5000) })
    expect(screen.getByRole('status')).toHaveTextContent('has ended')
    expect(screen.queryByText('Demo request ID')).not.toBeInTheDocument()
  })
  it('blocks audio submission after expiry even before the session timer runs', async () => {
    const fetch = vi.fn()
    vi.stubGlobal('fetch', fetch)
    await review()
    vi.setSystemTime(Date.now() + 600_000)
    fireEvent.click(screen.getByRole('button', { name: 'Continue — send for transcription' }))
    expect(fetch).not.toHaveBeenCalled()
    expect(screen.getByRole('status')).toHaveTextContent('has expired')
  })
})
