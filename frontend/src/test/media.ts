import { vi } from 'vitest'

export class MockMediaRecorder {
  static instances: MockMediaRecorder[] = []
  static isTypeSupported = vi.fn((type: string) => type === 'audio/webm;codecs=opus')
  state: RecordingState = 'inactive'
  mimeType: string
  empty = false
  ondataavailable: ((event: BlobEvent) => void) | null = null
  onstop: (() => void) | null = null
  onerror: (() => void) | null = null
  start = vi.fn(() => { this.state = 'recording' })
  stop = vi.fn(() => {
    this.state = 'inactive'
    void Promise.resolve().then(() => {
      this.ondataavailable?.({ data: new Blob(this.empty ? [] : ['test audio'], { type: this.mimeType }) } as BlobEvent)
      this.onstop?.()
    })
  })
  constructor(_stream: MediaStream, options?: MediaRecorderOptions) {
    this.mimeType = options?.mimeType || 'audio/webm'
    MockMediaRecorder.instances.push(this)
  }
}

export function installMediaMocks() {
  MockMediaRecorder.instances = []
  MockMediaRecorder.isTypeSupported.mockReset().mockImplementation(type => type === 'audio/webm;codecs=opus')
  vi.stubGlobal('isSecureContext', true)
  vi.stubGlobal('MediaRecorder', MockMediaRecorder)
  const createObjectURL = vi.fn(() => `blob:test-${Math.random()}`)
  const revokeObjectURL = vi.fn()
  vi.stubGlobal('URL', Object.assign(class extends URL {}, { createObjectURL, revokeObjectURL }))
  const streams: { stream: MediaStream; track: { readyState: string; stop: ReturnType<typeof vi.fn>; onended: (() => void) | null } }[] = []
  const createStream = () => {
    const track = { readyState: 'live', stop: vi.fn(() => { track.readyState = 'ended' }), onended: null as (() => void) | null }
    const stream = { getTracks: () => [track], getAudioTracks: () => [track] } as unknown as MediaStream
    streams.push({ stream, track })
    return stream
  }
  const getUserMedia = vi.fn(async () => createStream())
  vi.stubGlobal('navigator', Object.assign(Object.create(navigator), { mediaDevices: { getUserMedia } }))
  return { streams, createStream, getUserMedia, createObjectURL, revokeObjectURL }
}
