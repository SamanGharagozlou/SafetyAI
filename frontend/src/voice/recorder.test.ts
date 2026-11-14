import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { installMediaMocks, MockMediaRecorder } from '../test/media'
import { MAX_RECORDING_MS, VoiceRecorder } from './recorder'

let media: ReturnType<typeof installMediaMocks>
let recorder: VoiceRecorder
beforeEach(() => { media = installMediaMocks(); recorder = new VoiceRecorder() })
afterEach(() => { recorder.cancel(); vi.unstubAllGlobals() })

describe('deliberate capture and transitions', () => {
  it('starts idle and never requests permission on construction', async () => {
    expect(recorder.getSnapshot().status).toBe('idle')
    expect(media.getUserMedia).not.toHaveBeenCalled()
    await recorder.start()
    expect(media.getUserMedia).toHaveBeenCalledExactlyOnceWith({ audio: true, video: false })
    expect(recorder.getSnapshot()).toMatchObject({ status: 'recording', permission: 'granted' })
  })
  it('prevents overlapping captures and stops tracks before preparing review', async () => {
    await recorder.start()
    await recorder.start()
    expect(media.getUserMedia).toHaveBeenCalledTimes(1)
    recorder.stop()
    expect(media.streams[0].track.stop).toHaveBeenCalledTimes(1)
    expect(recorder.getSnapshot().status).toBe('stopping')
    await Promise.resolve()
    expect(recorder.getSnapshot().status).toBe('reviewing')
    expect(recorder.getSnapshot().clip?.mimeType).toBe('audio/webm;codecs=opus')
    expect(recorder.getSnapshot().clip?.blob.size).toBeGreaterThan(0)
  })
  it('automatically stops at 20 seconds', async () => {
    vi.useFakeTimers()
    await recorder.start()
    vi.advanceTimersByTime(MAX_RECORDING_MS)
    await Promise.resolve()
    expect(media.streams[0].track.stop).toHaveBeenCalledTimes(1)
    expect(recorder.getSnapshot()).toMatchObject({ status: 'reviewing', elapsedMs: MAX_RECORDING_MS })
    expect(recorder.getSnapshot().clip?.durationMs).toBe(MAX_RECORDING_MS)
  })
  it('cancels without retaining late data and allows another deliberate recording', async () => {
    await recorder.start()
    recorder.cancel()
    await Promise.resolve()
    expect(media.streams[0].track.stop).toHaveBeenCalledTimes(1)
    expect(recorder.getSnapshot()).toMatchObject({ status: 'idle', clip: null })
    expect(media.createObjectURL).not.toHaveBeenCalled()
    await recorder.start()
    expect(media.getUserMedia).toHaveBeenCalledTimes(2)
  })
  it('revokes the previous playback URL on retry and discard', async () => {
    await recorder.start()
    recorder.stop()
    await Promise.resolve()
    const first = recorder.getSnapshot().clip!.url
    await recorder.start()
    expect(media.revokeObjectURL).toHaveBeenCalledWith(first)
    recorder.stop()
    await Promise.resolve()
    const second = recorder.getSnapshot().clip!.url
    recorder.cancel()
    expect(media.revokeObjectURL).toHaveBeenCalledWith(second)
    expect(recorder.getSnapshot().clip).toBeNull()
  })
  it('closes a late permission grant after Cancel and does not open a second prompt', async () => {
    let grant!: (stream: MediaStream) => void
    media.getUserMedia.mockImplementationOnce(() => new Promise(resolve => { grant = resolve }))
    const pending = recorder.start()
    expect(recorder.getSnapshot().status).toBe('requesting')
    recorder.cancel()
    await recorder.start()
    expect(media.getUserMedia).toHaveBeenCalledTimes(1)
    grant(media.createStream())
    await pending
    expect(media.streams[0].track.stop).toHaveBeenCalledTimes(1)
    expect(MockMediaRecorder.instances).toHaveLength(0)
    expect(recorder.getSnapshot()).toMatchObject({ status: 'idle', requestPending: false })
  })
  it('blocks another view from requesting the microphone while an old prompt is pending', async () => {
    let grant!: (stream: MediaStream) => void
    media.getUserMedia.mockImplementationOnce(() => new Promise(resolve => { grant = resolve }))
    const pending = recorder.start()
    recorder.cancel()
    const nextView = new VoiceRecorder()
    await nextView.start()
    expect(media.getUserMedia).toHaveBeenCalledTimes(1)
    expect(nextView.getSnapshot().message).toContain('earlier microphone permission prompt')
    grant(media.createStream())
    await pending
    await nextView.start()
    expect(nextView.getSnapshot().status).toBe('recording')
    expect(media.streams[0].track.stop).toHaveBeenCalledTimes(1)
    nextView.cancel()
  })
  it('stops tracks on recording failure and disconnected devices', async () => {
    await recorder.start()
    MockMediaRecorder.instances[0].onerror?.()
    expect(media.streams[0].track.stop).toHaveBeenCalledTimes(1)
    expect(recorder.getSnapshot().status).toBe('error')
    await recorder.start()
    media.streams[1].track.onended?.()
    expect(media.streams[1].track.stop).toHaveBeenCalledTimes(1)
    expect(recorder.getSnapshot().message).toContain('disconnected')
  })
  it('cleans up if MediaRecorder.start throws', async () => {
    class BrokenRecorder extends MockMediaRecorder {
      constructor(stream: MediaStream) {
        super(stream)
        this.start.mockImplementation(() => { throw new Error('test failure') })
      }
    }
    vi.stubGlobal('MediaRecorder', BrokenRecorder)
    await recorder.start()
    expect(media.streams[0].track.stop).toHaveBeenCalledTimes(1)
    expect(recorder.getSnapshot().status).toBe('error')
  })
})

describe('permissions, compatibility, and unusable recordings', () => {
  it.each([
    ['NotAllowedError', 'wasn’t allowed', 'denied'],
    ['SecurityError', 'wasn’t allowed', 'denied'],
    ['NotFoundError', 'No microphone', 'unavailable'],
    ['NotReadableError', 'busy', 'unavailable'],
    ['AbortError', 'didn’t work', 'unavailable'],
  ])('handles %s', async (name, message, permission) => {
    media.getUserMedia.mockRejectedValueOnce(new DOMException('test', name))
    await recorder.start()
    expect(recorder.getSnapshot()).toMatchObject({ status: 'error', permission })
    expect(recorder.getSnapshot().message).toContain(message)
  })
  it('requires a secure context without prompting', async () => {
    vi.stubGlobal('isSecureContext', false)
    await recorder.start()
    expect(recorder.getSnapshot().message).toContain('HTTPS or localhost')
    expect(media.getUserMedia).not.toHaveBeenCalled()
  })
  it.each(['recorder', 'mediaDevices'])('handles missing %s', async missing => {
    if (missing === 'recorder') vi.stubGlobal('MediaRecorder', undefined)
    else vi.stubGlobal('navigator', {})
    await recorder.start()
    expect(recorder.getSnapshot().message).toContain('cannot record')
    expect(media.getUserMedia).not.toHaveBeenCalled()
  })
  it('detects an MP4 format when WebM is unsupported', async () => {
    MockMediaRecorder.isTypeSupported.mockImplementation(type => type === 'audio/mp4')
    await recorder.start()
    expect(MockMediaRecorder.instances[0].mimeType).toBe('audio/mp4')
  })
  it('uses the recorder default when no listed format matches', async () => {
    MockMediaRecorder.isTypeSupported.mockReturnValue(false)
    await recorder.start()
    recorder.stop()
    await Promise.resolve()
    expect(recorder.getSnapshot().clip?.mimeType).toBe('audio/webm')
  })
  it('rejects an empty recording without creating an object URL', async () => {
    await recorder.start()
    MockMediaRecorder.instances[0].empty = true
    recorder.stop()
    await Promise.resolve()
    expect(recorder.getSnapshot().status).toBe('error')
    expect(media.createObjectURL).not.toHaveBeenCalled()
    expect(media.streams[0].track.stop).toHaveBeenCalledTimes(1)
  })
  it('releases tracks and reports an error when stopping throws', async () => {
    await recorder.start()
    MockMediaRecorder.instances[0].stop.mockImplementation(() => { throw new Error('test failure') })
    recorder.stop()
    expect(media.streams[0].track.stop).toHaveBeenCalledTimes(1)
    expect(recorder.getSnapshot().status).toBe('error')
  })
})
