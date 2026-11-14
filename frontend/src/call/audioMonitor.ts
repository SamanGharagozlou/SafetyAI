export interface AudioSample { rms: number; at: number }
interface Options { onSample(sample: AudioSample): void; onInterrupted(): void }

/** Activated in the Start/Unmute gesture. Never connects microphone audio to speakers. */
export class AudioMonitor {
  private context: AudioContext | null = null
  private analyser: AnalyserNode | null = null
  private source: MediaStreamAudioSourceNode | null = null
  private data: Float32Array<ArrayBuffer> | null = null
  private frame = 0
  private lastSample = -Infinity
  private attachedAt = 0
  private closed = false
  private options: Options
  readonly available: boolean
  constructor(options: Options) {
    this.options = options
    const Constructor = window.AudioContext || (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext
    this.available = !!Constructor
    if (!Constructor) return
    try {
      this.context = new Constructor()
      // Invoke resume synchronously in the initiating gesture for Safari.
      void this.context.resume().catch(() => { if (!this.closed) options.onInterrupted() })
    } catch { this.context = null }
  }
  attach(stream: MediaStream): boolean {
    if (!this.context || this.closed) return false
    try {
      const context = this.context
      this.analyser = context.createAnalyser()
      this.analyser.fftSize = 1024
      this.data = new Float32Array(this.analyser.fftSize)
      this.source = context.createMediaStreamSource(stream)
      this.source.connect(this.analyser)
      this.attachedAt = performance.now()
      context.onstatechange = () => {
        if (!this.closed && String(context.state) !== 'running') this.options.onInterrupted()
      }
      this.frame = requestAnimationFrame(this.sample)
      return true
    } catch { this.close(); return false }
  }
  private sample = (at: number) => {
    if (this.closed || !this.context || !this.analyser || !this.data) return
    if (String(this.context.state) !== 'running') {
      // A browser may need a short period to finish its user-gesture resume.
      if (at - this.attachedAt > 1500) { this.options.onInterrupted(); return }
    } else if (at - this.lastSample >= 50) {
      this.lastSample = at
      try { this.analyser.getFloatTimeDomainData(this.data) }
      catch { this.options.onInterrupted(); return }
      let sum = 0
      for (const value of this.data) sum += value * value
      this.options.onSample({ rms: Math.sqrt(sum / this.data.length), at })
    }
    if (!this.closed) this.frame = requestAnimationFrame(this.sample)
  }
  close() {
    if (this.closed) return
    this.closed = true
    cancelAnimationFrame(this.frame)
    if (this.context) this.context.onstatechange = null
    try { this.source?.disconnect() } catch { /* Continue releasing other resources. */ }
    try { this.analyser?.disconnect() } catch { /* Continue releasing the context. */ }
    this.data?.fill(0)
    this.data = null
    const context = this.context
    this.context = null
    this.source = null
    this.analyser = null
    if (context && String(context.state) !== 'closed') void context.close().catch(() => {})
  }
}
