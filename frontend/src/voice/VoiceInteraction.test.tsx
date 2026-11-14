import { act, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { App } from '../App'
import { installMediaMocks, MockMediaRecorder } from '../test/media'

let media: ReturnType<typeof installMediaMocks>
beforeEach(() => {
  media = installMediaMocks()
  window.history.replaceState(null, '', '/join')
  vi.stubEnv('VITE_DEMO_SESSION_SECONDS', '600')
  vi.stubEnv('VITE_TRANSCRIBE_ENDPOINT', '')
  vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => {})
  vi.spyOn(HTMLMediaElement.prototype, 'load').mockImplementation(() => {})
})
afterEach(() => vi.unstubAllGlobals())

function join() {
  const view = render(<App />)
  fireEvent.change(screen.getByLabelText(/What should I call you/), { target: { value: 'Nova' } })
  fireEvent.click(screen.getByRole('button', { name: /Let's Get Started/ }))
  return view
}
async function record() {
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Start Recording' })) })
}
async function review() {
  await record()
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Stop Recording' })) })
}

describe('voice interaction lifecycle within the visitor session', () => {
  it('never prompts on joining and clears audio on discard', async () => {
    join()
    expect(media.getUserMedia).not.toHaveBeenCalled()
    await review()
    expect(screen.getByLabelText('Play your recording')).toHaveAttribute('src', expect.stringContaining('blob:'))
    const player = screen.getByLabelText('Play your recording')
    fireEvent.click(screen.getByRole('button', { name: 'Discard' }))
    expect(player).not.toHaveAttribute('src')
    expect(HTMLMediaElement.prototype.pause).toHaveBeenCalled()
    expect(HTMLMediaElement.prototype.load).toHaveBeenCalled()
    expect(media.revokeObjectURL).toHaveBeenCalledTimes(1)
    expect(screen.queryByLabelText('Play your recording')).not.toBeInTheDocument()
  })
  it('supports Cancel and Retry without retaining previous audio', async () => {
    join()
    await record()
    fireEvent.click(screen.getByRole('button', { name: 'Cancel' }))
    expect(media.streams[0].track.stop).toHaveBeenCalledTimes(1)
    await review()
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Retry recording' })) })
    expect(media.revokeObjectURL).toHaveBeenCalledTimes(1)
    expect(media.getUserMedia).toHaveBeenCalledTimes(3)
  })
  it.each(['privacy', 'end', 'unmount', 'back'])('stops active capture on %s', async navigation => {
    const view = join()
    await record()
    if (navigation === 'privacy') fireEvent.click(screen.getByRole('link', { name: 'Privacy information' }))
    if (navigation === 'end') fireEvent.click(screen.getByRole('button', { name: 'End Session' }))
    if (navigation === 'unmount') view.unmount()
    if (navigation === 'back') act(() => {
      window.history.replaceState(null, '', '/join')
      window.dispatchEvent(new PopStateEvent('popstate'))
    })
    await act(async () => {})
    expect(media.streams[0].track.stop).toHaveBeenCalledTimes(1)
    expect(media.createObjectURL).not.toHaveBeenCalled()
  })
  it('releases playback URLs when leaving the session view', async () => {
    join()
    await review()
    fireEvent.click(screen.getByRole('link', { name: 'Privacy information' }))
    expect(media.revokeObjectURL).toHaveBeenCalledTimes(1)
    fireEvent.click(screen.getByRole('button', { name: /Back to session/ }))
    expect(screen.getByRole('heading', { name: 'Nice to meet you, Nova!' })).toBeInTheDocument()
    expect(screen.queryByLabelText('Play your recording')).not.toBeInTheDocument()
  })
  it('stops recording and clears state on session expiry', async () => {
    vi.useFakeTimers()
    vi.stubEnv('VITE_DEMO_SESSION_SECONDS', '2')
    join()
    await record()
    act(() => vi.advanceTimersByTime(2000))
    expect(media.streams[0].track.stop).toHaveBeenCalledTimes(1)
    expect(screen.getByRole('status')).toHaveTextContent('has expired')
  })
  it('stops recording when the document is hidden and does not resume automatically', async () => {
    join()
    await record()
    vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('hidden')
    fireEvent(document, new Event('visibilitychange'))
    expect(media.streams[0].track.stop).toHaveBeenCalledTimes(1)
    expect(screen.getByRole('button', { name: 'Start Recording' })).toBeInTheDocument()
    expect(media.getUserMedia).toHaveBeenCalledTimes(1)
  })
  it('handles permission resolving after navigation', async () => {
    let grant!: (stream: MediaStream) => void
    media.getUserMedia.mockImplementationOnce(() => new Promise(resolve => { grant = resolve }))
    join()
    fireEvent.click(screen.getByRole('button', { name: 'Start Recording' }))
    fireEvent.click(screen.getByRole('button', { name: 'End Session' }))
    await act(async () => { grant(media.createStream()) })
    expect(media.streams[0].track.stop).toHaveBeenCalledTimes(1)
    expect(MockMediaRecorder.instances).toHaveLength(0)
  })
})

describe('typed alternative and transcription consent', () => {
  it('works without recording support, validates empty text, and supports editing', () => {
    vi.stubGlobal('MediaRecorder', undefined)
    const fetch = vi.fn()
    vi.stubGlobal('fetch', fetch)
    join()
    fireEvent.click(screen.getByRole('button', { name: 'Type a request' }))
    expect(screen.getByLabelText('Your request')).toHaveFocus()
    fireEvent.click(screen.getByRole('button', { name: 'Continue' }))
    expect(screen.getByRole('alert')).toHaveTextContent('Please enter a request')
    fireEvent.change(screen.getByLabelText('Your request'), { target: { value: '  Tell me about your sensors.  ' } })
    fireEvent.click(screen.getByRole('button', { name: 'Continue' }))
    expect(screen.getByRole('heading', { name: 'Your request is ready' })).toHaveFocus()
    expect(screen.getByText('Tell me about your sensors.')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Edit request' }))
    expect(screen.getByLabelText('Your request')).toHaveValue('Tell me about your sensors.')
    expect(screen.getByRole('heading', { name: 'Nice to meet you, Nova!' })).toBeInTheDocument()
    expect(media.getUserMedia).not.toHaveBeenCalled()
    expect(fetch).not.toHaveBeenCalled()
  })
  it('does not upload on recording, review, or Continue when disconnected', async () => {
    const fetch = vi.fn()
    vi.stubGlobal('fetch', fetch)
    join()
    await review()
    fireEvent.click(screen.getByRole('button', { name: 'Continue' }))
    expect(screen.getByText(/Your recording has not been uploaded/)).toBeInTheDocument()
    expect(fetch).not.toHaveBeenCalled()
    expect(media.revokeObjectURL).toHaveBeenCalledTimes(1)
    expect(screen.getByLabelText('Your request')).toHaveFocus()
  })
  it('uploads only after the labeled send action and allows editing the real response', async () => {
    vi.stubEnv('VITE_TRANSCRIBE_ENDPOINT', '/api/transcribe')
    const fetch = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ text: 'Test server response, not simulated recognition.' }) })
    vi.stubGlobal('fetch', fetch)
    join()
    await review()
    expect(fetch).not.toHaveBeenCalled()
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Continue — send for transcription' })) })
    expect(fetch).toHaveBeenCalledTimes(1)
    expect(screen.getByLabelText('Your request')).toHaveValue('Test server response, not simulated recognition.')
    expect(media.revokeObjectURL).toHaveBeenCalledTimes(1)
  })
  it('aborts an in-flight upload on session end and ignores a late response', async () => {
    vi.stubEnv('VITE_TRANSCRIBE_ENDPOINT', '/api/transcribe')
    let resolve!: (response: unknown) => void
    const fetch = vi.fn().mockImplementation(() => new Promise(done => { resolve = done }))
    vi.stubGlobal('fetch', fetch)
    join()
    await review()
    fireEvent.click(screen.getByRole('button', { name: 'Continue — send for transcription' }))
    const signal = fetch.mock.calls[0][1].signal as AbortSignal
    fireEvent.click(screen.getByRole('button', { name: 'End Session' }))
    expect(signal.aborted).toBe(true)
    await act(async () => { resolve({ ok: true, json: async () => ({ text: 'Late test response.' }) }) })
    expect(screen.queryByText('Late test response.')).not.toBeInTheDocument()
    expect(media.revokeObjectURL).toHaveBeenCalledTimes(1)
  })
  it('does not claim nothing was sent after an unsuccessful upload attempt', async () => {
    vi.stubEnv('VITE_TRANSCRIBE_ENDPOINT', '/api/transcribe')
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('test network failure')))
    join()
    await review()
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Continue — send for transcription' })) })
    fireEvent.click(screen.getByRole('button', { name: 'Discard' }))
    expect(screen.getByText(/transcription server may already have received/)).toBeInTheDocument()
    expect(screen.queryByText('Recording discarded. Nothing was sent.')).not.toBeInTheDocument()
  })
  it('reports playback failure truthfully', async () => {
    join()
    await review()
    fireEvent.error(screen.getByLabelText('Play your recording'))
    expect(screen.getByRole('alert')).toHaveTextContent('couldn’t play')
  })
})
