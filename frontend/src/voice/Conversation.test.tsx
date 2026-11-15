import { act, fireEvent, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { installMediaMocks } from '../test/media'
import { joinCall, startCall, finishManualTurn, writeFallback } from '../test/callHarness'

let media: ReturnType<typeof installMediaMocks>
beforeEach(() => {
  media = installMediaMocks(); vi.useFakeTimers()
  vi.stubEnv('VITE_DEMO_SESSION_SECONDS', '600'); vi.stubEnv('VITE_TRANSCRIBE_ENDPOINT', '')
  vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => {})
  vi.spyOn(HTMLMediaElement.prototype, 'load').mockImplementation(() => {})
})
afterEach(() => vi.unstubAllGlobals())
describe('dedicated conversational screen regressions', () => {
  it('pre-call shows the original robot and one main Start action without capture controls', () => {
    joinCall()
    expect(screen.getByRole('heading', { name: 'Talk to ClawMetry' })).toHaveFocus()
    expect(screen.getByRole('img', { name: /smiling teal robot/ })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Start Conversation' })).toBeEnabled()
    expect(screen.queryByRole('button', { name: /Mute|End Call|Finish turn|Stop Recording|Continue/ })).not.toBeInTheDocument()
    expect(media.getUserMedia).not.toHaveBeenCalled()
  })
  it('starts deliberately and displays only two persistent primary call controls', async () => {
    joinCall(); await startCall()
    expect(screen.getByRole('group', { name: 'Call controls' }).querySelectorAll('button')).toHaveLength(2)
    expect(screen.getByRole('button', { name: 'Finish turn' })).toBeInTheDocument()
    expect(screen.getByRole('timer', { name: 'Conversation duration' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Start Conversation' })).not.toBeInTheDocument()
  })
  it('Mute releases tracks; Unmute resumes a fresh stream without submitting partial audio', async () => {
    joinCall(); await startCall(); fireEvent.click(screen.getByRole('button', { name: 'Mute' }))
    expect(media.streams[0].track.stop).toHaveBeenCalledTimes(1)
    expect(media.createObjectURL).not.toHaveBeenCalled()
    await act(async () => { fireEvent.click(screen.getByRole('button', { name: 'Unmute' })) })
    expect(media.getUserMedia).toHaveBeenCalledTimes(2)
  })
  it('does not reopen capture while reviewing text when unmuted', async () => {
    joinCall(); await startCall(); await finishManualTurn()
    fireEvent.click(screen.getByRole('button', { name: 'Mute' })); fireEvent.click(screen.getByRole('button', { name: 'Unmute' }))
    expect(media.getUserMedia).toHaveBeenCalledTimes(1)
    expect(screen.getByLabelText('Your request')).toBeInTheDocument()
  })
  it('End Call returns focus to Start and preserves only the visitor session', async () => {
    joinCall(); await startCall(); fireEvent.click(screen.getByRole('button', { name: 'End Call' }))
    expect(screen.getByRole('button', { name: 'Start Conversation' })).toHaveFocus()
    expect(screen.getByText('Nice to meet you, Nova!')).toBeInTheDocument()
    expect(media.streams[0].track.stop).toHaveBeenCalledTimes(1)
    await startCall(); expect(media.getUserMedia).toHaveBeenCalledTimes(2)
  })
  it('End clears draft and optional audio review data', async () => {
    joinCall(); await startCall(); await finishManualTurn()
    fireEvent.change(screen.getByLabelText('Your request'), { target: { value: 'Private draft' } })
    fireEvent.click(screen.getByRole('button', { name: 'End Call' })); writeFallback()
    expect(screen.getByLabelText('Your request')).toHaveValue('')
  })
  it('caps a manual utterance at 20 seconds without ending the conversation', async () => {
    joinCall(); await startCall()
    await act(async () => { await vi.advanceTimersByTimeAsync(20_000) })
    expect(screen.getByLabelText('Your request')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'End Call' })).toBeInTheDocument()
    expect(media.streams[0].track.stop).toHaveBeenCalledTimes(1)
  })
  it('closes late permission grants after End without restarting', async () => {
    let grant!: (stream: MediaStream) => void
    media.getUserMedia.mockImplementationOnce(() => new Promise(resolve => { grant = resolve }))
    joinCall(); fireEvent.click(screen.getByRole('button', { name: 'Start Conversation' })); fireEvent.click(screen.getByRole('button', { name: 'End Call' }))
    await act(async () => { grant(media.createStream()) })
    expect(media.streams[0].track.stop).toHaveBeenCalledTimes(1)
    expect(screen.getByRole('button', { name: 'Start Conversation' })).toBeEnabled()
  })
  it('Mute during permission waits for that prompt to settle before Unmute', async () => {
    let grant!: (stream: MediaStream) => void
    media.getUserMedia.mockImplementationOnce(() => new Promise(resolve => { grant = resolve }))
    joinCall(); fireEvent.click(screen.getByRole('button', { name: 'Start Conversation' })); fireEvent.click(screen.getByRole('button', { name: 'Mute' }))
    expect(screen.getByRole('button', { name: 'Unmute' })).toBeDisabled()
    await act(async () => { grant(media.createStream()) })
    expect(media.streams[0].track.stop).toHaveBeenCalledTimes(1)
  })
  it.each(['NotAllowedError', 'NotFoundError', 'NotReadableError'])('offers text within the same CallScreen for %s', async name => {
    media.getUserMedia.mockRejectedValueOnce(new DOMException('Test error', name))
    joinCall(); await startCall()
    expect(screen.getByLabelText('Your request')).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('Your request'), { target: { value: 'Hello' } })
    fireEvent.click(screen.getByRole('button', { name: 'Review request' }))
    expect(screen.getByRole('button', { name: 'Confirm demo request' })).toBeInTheDocument()
  })
  it('End aborts a pending explicit upload and ignores its late text', async () => {
    vi.stubEnv('VITE_TRANSCRIBE_ENDPOINT', '/api/transcribe')
    let resolve!: (value: unknown) => void
    const fetch = vi.fn().mockImplementation(() => new Promise(done => { resolve = done })); vi.stubGlobal('fetch', fetch)
    joinCall(); await startCall(); await finishManualTurn()
    fireEvent.click(screen.getByRole('button', { name: 'Send audio for transcription' }))
    const signal = fetch.mock.calls[0][1].signal as AbortSignal
    fireEvent.click(screen.getByRole('button', { name: 'End Call' })); expect(signal.aborted).toBe(true)
    await act(async () => { resolve({ ok: true, json: async () => ({ text: 'Late test fixture' }) }) })
    writeFallback(); expect(screen.getByLabelText('Your request')).toHaveValue('')
  })
  it('End disposes the local simulated task and history', async () => {
    joinCall(); writeFallback(); fireEvent.change(screen.getByLabelText('Your request'), { target: { value: 'Hello' } })
    fireEvent.click(screen.getByRole('button', { name: 'Review request' })); fireEvent.click(screen.getByRole('button', { name: 'Confirm demo request' }))
    await act(async () => { await vi.advanceTimersByTimeAsync(200) })
    fireEvent.click(screen.getByRole('button', { name: 'End Call' }))
    await act(async () => { await vi.advanceTimersByTimeAsync(5000) })
    expect(screen.queryByLabelText('Conversation history')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Start Conversation' })).toBeInTheDocument()
  })
})
