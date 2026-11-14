export const MAX_RECORDING_MS = 20_000
// A native permission dialog can outlive its React view. Coordinate outstanding
// prompts across remounts; this flag contains no visitor or audio data.
let microphoneRequestPending = false
const MIME_TYPES = ['audio/webm;codecs=opus', 'audio/mp4', 'audio/ogg;codecs=opus', 'audio/webm']

export interface AudioClip {
  readonly blob: Blob
  readonly mimeType: string
  readonly durationMs: number
  readonly url: string
}
export type RecordingStatus = 'idle' | 'requesting' | 'recording' | 'stopping' | 'reviewing' | 'error'
export interface RecordingSnapshot {
  readonly status: RecordingStatus
  readonly elapsedMs: number
  readonly permission: 'not-requested' | 'requesting' | 'granted' | 'denied' | 'unavailable'
  readonly requestPending: boolean
  readonly clip: AudioClip | null
  readonly message: string
}

export function recordingUnavailable(): string | null {
  if (!window.isSecureContext) return 'Microphone access needs HTTPS or localhost. You can still type your request.'
  if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === 'undefined') {
    return 'This browser cannot record audio here. Try a supported browser, or type your request.'
  }
  return null
}

export function supportedMimeType(): string | undefined {
  if (typeof MediaRecorder.isTypeSupported !== 'function') return undefined
  return MIME_TYPES.find(type => MediaRecorder.isTypeSupported(type))
}

function recordingError(error: unknown): { message: string; denied: boolean } {
  const name = error instanceof Error || error instanceof DOMException ? error.name : ''
  if (name === 'NotAllowedError' || name === 'SecurityError') {
    return { message: 'Microphone access wasn’t allowed. You can change this site’s microphone permission in your browser, or type instead.', denied: true }
  }
  if (name === 'NotFoundError' || name === 'DevicesNotFoundError') {
    return { message: 'No microphone was found. Connect one and try again, or type your request.', denied: false }
  }
  if (name === 'NotReadableError' || name === 'TrackStartError') {
    return { message: 'Your microphone may be busy or unavailable. Close other apps using it, then try again, or type instead.', denied: false }
  }
  return { message: 'Recording didn’t work this time. Your microphone is off. Try again, or type your request.', denied: false }
}

/** Owns a single capture attempt. Audio and object URLs never leave page memory. */
export class VoiceRecorder {
  private snapshot: RecordingSnapshot = {
    status: 'idle', elapsedMs: 0, permission: 'not-requested', requestPending: false, clip: null, message: '',
  }
  private listeners = new Set<() => void>()
  private generation = 0
  private pending = false
  private stream: MediaStream | null = null
  private recorder: MediaRecorder | null = null
  private chunks: Blob[] = []
  private startedAt = 0
  private limitTimer: ReturnType<typeof setTimeout> | undefined
  private tickTimer: ReturnType<typeof setInterval> | undefined
  private stopTimer: ReturnType<typeof setTimeout> | undefined

  getSnapshot = () => this.snapshot
  subscribe = (listener: () => void) => {
    this.listeners.add(listener)
    return () => { this.listeners.delete(listener) }
  }
  private update(patch: Partial<RecordingSnapshot>) {
    this.snapshot = { ...this.snapshot, ...patch }
    this.listeners.forEach(listener => listener())
  }
  private clearTimers() {
    clearTimeout(this.limitTimer)
    clearInterval(this.tickTimer)
    clearTimeout(this.stopTimer)
  }
  private releaseTracks() {
    this.stream?.getTracks().forEach(track => {
      track.onended = null
      track.stop()
    })
    this.stream = null
  }
  private releaseRecorder() {
    this.clearTimers()
    if (this.recorder) {
      this.recorder.ondataavailable = null
      this.recorder.onstop = null
      this.recorder.onerror = null
      try { if (this.recorder.state !== 'inactive') this.recorder.stop() } catch { /* Tracks still close below. */ }
      this.recorder = null
    }
    this.releaseTracks()
    this.chunks = []
  }
  private releaseClip() {
    if (this.snapshot.clip) URL.revokeObjectURL(this.snapshot.clip.url)
  }
  private fail(message: string, denied = false) {
    this.generation++
    this.releaseRecorder()
    this.releaseClip()
    this.update({ status: 'error', clip: null, elapsedMs: 0, requestPending: this.pending,
      permission: denied ? 'denied' : this.snapshot.permission === 'requesting' ? 'unavailable' : this.snapshot.permission,
      message })
  }

  start = async () => {
    // Cancel cannot dismiss the native permission prompt. Do not open another one.
    if (this.pending || ['requesting', 'recording', 'stopping'].includes(this.snapshot.status)) return
    if (microphoneRequestPending) {
      this.fail('An earlier microphone permission prompt is still waiting. Dismiss it or choose a permission, then try again. You can type instead.')
      return
    }
    const unavailable = recordingUnavailable()
    if (unavailable) { this.fail(unavailable); return }
    this.cancel()
    const generation = ++this.generation
    this.pending = true
    microphoneRequestPending = true
    this.update({ status: 'requesting', permission: 'requesting', requestPending: true, message: '' })
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true, video: false })
      if (generation !== this.generation || document.visibilityState === 'hidden') {
        stream.getTracks().forEach(track => track.stop())
        if (generation === this.generation) this.cancel('Recording canceled while this tab was hidden. Nothing was sent.')
        return
      }
      this.stream = stream
      if (!stream.getAudioTracks().some(track => track.readyState === 'live')) {
        throw new DOMException('No live audio track', 'NotFoundError')
      }
      const mimeType = supportedMimeType()
      const recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined)
      this.recorder = recorder
      this.chunks = []
      stream.getTracks().forEach(track => {
        track.onended = () => {
          if (generation === this.generation && this.snapshot.status === 'recording') {
            this.fail('The microphone disconnected. Try again, or type your request.')
          }
        }
      })
      recorder.ondataavailable = event => {
        if (generation === this.generation && event.data.size > 0) this.chunks.push(event.data)
      }
      recorder.onerror = () => {
        if (generation === this.generation) this.fail('Recording failed. Your microphone is off. Try again, or type instead.')
      }
      recorder.onstop = () => {
        if (generation !== this.generation) return
        this.clearTimers()
        this.releaseTracks()
        this.recorder = null
        const type = recorder.mimeType || this.chunks.find(chunk => chunk.type)?.type || ''
        const blob = new Blob(this.chunks, { type })
        this.chunks = []
        if (!blob.size || !type.startsWith('audio/')) {
          this.fail('No usable audio was captured. Please try recording again, or type your request.')
          return
        }
        try {
          const url = URL.createObjectURL(blob)
          this.update({ status: 'reviewing', clip: { blob, mimeType: type, durationMs: this.snapshot.elapsedMs, url }, message: '' })
        } catch { this.fail('Your browser couldn’t prepare audio playback. Try again, or type instead.') }
      }
      this.startedAt = performance.now()
      recorder.start(250)
      this.update({ status: 'recording', permission: 'granted', elapsedMs: 0 })
      this.limitTimer = setTimeout(() => this.stop(), MAX_RECORDING_MS)
      this.tickTimer = setInterval(() => {
        const elapsedMs = Math.min(MAX_RECORDING_MS, performance.now() - this.startedAt)
        this.update({ elapsedMs })
        if (elapsedMs >= MAX_RECORDING_MS) this.stop()
      }, 100)
    } catch (error) {
      if (generation === this.generation) {
        const failure = recordingError(error)
        this.fail(failure.message, failure.denied)
      }
    } finally {
      microphoneRequestPending = false
      this.pending = false
      // This also unlocks Start after a canceled permission prompt eventually settles.
      this.update({ requestPending: false })
    }
  }

  stop = () => {
    if (this.snapshot.status !== 'recording' || !this.recorder) return
    this.clearTimers()
    this.update({ status: 'stopping', elapsedMs: Math.min(MAX_RECORDING_MS, Math.max(0, performance.now() - this.startedAt)) })
    try {
      const recorder = this.recorder
      recorder.stop()
      // Do not wait for the async stop/data events to release the physical microphone.
      this.releaseTracks()
      this.stopTimer = setTimeout(() => {
        if (this.snapshot.status === 'stopping') this.fail('Audio review couldn’t be prepared. Please try again, or type instead.')
      }, 2000)
    } catch { this.fail('Recording could not finish. Your microphone is off. Please try again or type instead.') }
  }

  cancel = (message = '') => {
    this.generation++
    this.releaseRecorder()
    this.releaseClip()
    this.update({ status: 'idle', clip: null, elapsedMs: 0, requestPending: this.pending,
      permission: this.snapshot.permission === 'requesting' ? 'not-requested' : this.snapshot.permission, message })
  }
}
