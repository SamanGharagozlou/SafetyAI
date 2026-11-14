import { act, fireEvent, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { installMediaMocks, MockMediaRecorder } from '../test/media'
import { joinCall, startCall, finishManualTurn, writeFallback } from '../test/callHarness'

let media: ReturnType<typeof installMediaMocks>
beforeEach(() => {
  media = installMediaMocks(); vi.useFakeTimers()
  vi.stubEnv('VITE_DEMO_SESSION_SECONDS', '600'); vi.stubEnv('VITE_TRANSCRIBE_ENDPOINT', '')
  vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => {})
  vi.spyOn(HTMLMediaElement.prototype, 'load').mockImplementation(() => {})
})
afterEach(() => vi.unstubAllGlobals())

describe('voice lifecycle regressions in CallScreen', () => {
  it('never prompts on joining and has no native recorder interface', () => {
    joinCall()
    expect(media.getUserMedia).not.toHaveBeenCalled()
    expect(screen.queryByRole('button', { name: /Stop Recording|Continue|Use voice|Type a request/ })).not.toBeInTheDocument()
    expect(document.querySelector('audio')).toBeNull()
  })
  it('Mute discards capture and Unmute starts a fresh stream', async () => {
    joinCall(); await startCall(); fireEvent.click(screen.getByRole('button', { name: 'Mute' }))
    expect(media.streams[0].track.stop).toHaveBeenCalledTimes(1)
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Unmute' })) })
    expect(media.getUserMedia).toHaveBeenCalledTimes(2)
  })
  it.each(['privacy', 'end', 'unmount', 'back'])('stops active capture on %s', async navigation => {
    const view = joinCall(); await startCall()
    if (navigation === 'privacy') fireEvent.click(screen.getByRole('link', { name: 'Privacy information' }))
    if (navigation === 'end') fireEvent.click(screen.getByRole('button', { name: 'Leave session' }))
    if (navigation === 'unmount') view.unmount()
    if (navigation === 'back') act(() => { window.history.replaceState(null, '', '/join'); window.dispatchEvent(new PopStateEvent('popstate')) })
    expect(media.streams[0].track.stop).toHaveBeenCalledTimes(1)
    expect(media.createObjectURL).not.toHaveBeenCalled()
  })
  it('releases audio URLs when leaving the view with a pending upload review', async () => {
    vi.stubEnv('VITE_TRANSCRIBE_ENDPOINT', '/api/transcribe')
    joinCall(); await startCall(); await finishManualTurn()
    fireEvent.click(screen.getByRole('link', { name: 'Privacy information' }))
    expect(media.revokeObjectURL).toHaveBeenCalledTimes(1)
    fireEvent.click(screen.getByRole('button', { name: /Back to session/ }))
    expect(screen.getByText('Nice to meet you, Nova!')).toBeInTheDocument()
    expect(document.querySelector('audio')).toBeNull()
  })
  it('stops capture and clears the screen on session expiry', async () => {
    vi.stubEnv('VITE_DEMO_SESSION_SECONDS', '2')
    joinCall(); await startCall(); act(() => vi.advanceTimersByTime(2000))
    expect(media.streams[0].track.stop).toHaveBeenCalledTimes(1)
    expect(screen.getByRole('status')).toHaveTextContent('has expired')
  })
  it('stops on tab hiding and does not resume automatically', async () => {
    joinCall(); await startCall()
    vi.spyOn(document, 'visibilityState', 'get').mockReturnValue('hidden')
    fireEvent(document, new Event('visibilitychange'))
    expect(media.streams[0].track.stop).toHaveBeenCalledTimes(1)
    expect(screen.getByRole('button', { name: 'Unmute' })).toBeInTheDocument()
    expect(media.getUserMedia).toHaveBeenCalledTimes(1)
  })
  it('handles permission resolving after navigation', async () => {
    let grant!: (stream: MediaStream) => void
    media.getUserMedia.mockImplementationOnce(() => new Promise(resolve => { grant = resolve }))
    joinCall(); fireEvent.click(screen.getByRole('button', { name: 'Start Conversation' }))
    fireEvent.click(screen.getByRole('button', { name: 'Leave session' }))
    await act(async () => { grant(media.createStream()) })
    expect(media.streams[0].track.stop).toHaveBeenCalledTimes(1)
    expect(MockMediaRecorder.instances).toHaveLength(0)
  })
})

describe('in-call fallback and explicit upload consent', () => {
  it('works without recording support, validates text, and supports editing', () => {
    vi.stubGlobal('MediaRecorder', undefined)
    const fetch = vi.fn(); vi.stubGlobal('fetch', fetch)
    joinCall(); writeFallback()
    expect(screen.getByLabelText('Your request')).toHaveFocus()
    fireEvent.click(screen.getByRole('button', { name: 'Review request' }))
    expect(screen.getByText('Please enter a request to review.')).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('Your request'), { target: { value: ' Tell me about your sensors. ' } })
    fireEvent.click(screen.getByRole('button', { name: 'Review request' }))
    expect(screen.getByRole('heading', { name: 'Confirm your request' })).toHaveFocus()
    fireEvent.click(screen.getByRole('button', { name: 'Edit request' }))
    expect(screen.getByLabelText('Your request')).toHaveValue('Tell me about your sensors.')
    expect(fetch).not.toHaveBeenCalled(); expect(media.getUserMedia).not.toHaveBeenCalled()
  })
  it('disconnected turns offer manual entry without an upload, transcript, or audio card', async () => {
    const fetch = vi.fn(); vi.stubGlobal('fetch', fetch)
    joinCall(); await startCall(); await finishManualTurn()
    expect(screen.getByLabelText('Your request')).toHaveValue('')
    expect(screen.getByText(/Transcription not connected;/)).toBeInTheDocument()
    expect(media.revokeObjectURL).toHaveBeenCalledTimes(1)
    expect(document.querySelector('audio')).toBeNull(); expect(fetch).not.toHaveBeenCalled()
  })
  it('uploads only after authorization and shows only returned transcript text', async () => {
    vi.stubEnv('VITE_TRANSCRIBE_ENDPOINT', '/api/transcribe')
    const fetch = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ text: 'Actual test-server response' }) }); vi.stubGlobal('fetch', fetch)
    joinCall(); await startCall(); await finishManualTurn()
    expect(fetch).not.toHaveBeenCalled()
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Send audio for transcription' })) })
    expect(fetch).toHaveBeenCalledTimes(1)
    expect(screen.getByLabelText('Your request')).toHaveValue('Actual test-server response')
  })
  it('aborts an upload on session end and ignores a late response', async () => {
    vi.stubEnv('VITE_TRANSCRIBE_ENDPOINT', '/api/transcribe')
    let resolve!: (value: unknown) => void
    const fetch = vi.fn().mockImplementation(() => new Promise(done => { resolve = done })); vi.stubGlobal('fetch', fetch)
    joinCall(); await startCall(); await finishManualTurn()
    fireEvent.click(screen.getByRole('button', { name: 'Send audio for transcription' }))
    const signal = fetch.mock.calls[0][1].signal as AbortSignal
    fireEvent.click(screen.getByRole('button', { name: 'Leave session' }))
    expect(signal.aborted).toBe(true)
    await act(async () => { resolve({ ok: true, json: async () => ({ text: 'Late test response' }) }) })
    expect(screen.queryByText('Late test response')).not.toBeInTheDocument()
  })
  it('does not claim that an unsuccessful upload was never received', async () => {
    vi.stubEnv('VITE_TRANSCRIBE_ENDPOINT', '/api/transcribe')
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('Test network failure')))
    joinCall(); await startCall(); await finishManualTurn()
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Send audio for transcription' })) })
    expect(screen.getByText(/server may already have received audio/)).toBeInTheDocument()
  })
  it('keeps optional playback collapsed and releases it on End Call', async () => {
    vi.stubEnv('VITE_TRANSCRIBE_ENDPOINT', '/api/transcribe')
    joinCall(); await startCall(); await finishManualTurn()
    const player = screen.getByLabelText('Play captured turn')
    expect(player).not.toBeVisible()
    fireEvent.click(screen.getByText('Review audio before sending'))
    expect(player).toBeVisible()
    fireEvent.click(screen.getByRole('button', { name: 'End Call' }))
    expect(media.revokeObjectURL).toHaveBeenCalledTimes(1)
    expect(player).not.toHaveAttribute('src')
  })
  it('keeps optional playback errors inside the call with a manual fallback', async () => {
    // The configured consent view is the only place that retains a playback URL.
    vi.stubEnv('VITE_TRANSCRIBE_ENDPOINT', '/api/transcribe')
    await joinCall(); await startCall(); await finishManualTurn()
    fireEvent.click(screen.getByText('Review audio before sending'))
    fireEvent.error(screen.getByLabelText('Play captured turn'))
    expect(screen.getByRole('alert')).toHaveTextContent('Playback unavailable')
    expect(screen.getByRole('button', { name: 'Write instead and clear audio' })).toBeVisible()
  })

})
