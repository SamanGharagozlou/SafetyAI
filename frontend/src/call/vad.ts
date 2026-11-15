/** Amplitude-based turn detection, not speech recognition or speaker identification. */
export interface VadConfig {
  calibrationMs: number
  minimumSpeechMs: number
  silenceMs: number
  onsetMultiplier: number
  releaseMultiplier: number
  minimumOnset: number
  minimumRelease: number
}
export const DEFAULT_VAD: VadConfig = {
  calibrationMs: 500, minimumSpeechMs: 250, silenceMs: 1200,
  onsetMultiplier: 3, releaseMultiplier: 1.8, minimumOnset: 0.012, minimumRelease: 0.008,
}
export type VadEvent = 'calibrating' | 'quiet' | 'candidate' | 'speech' | 'rejected' | 'finished'
export class TurnDetector {
  private config: VadConfig
  private startedAt: number | null = null
  private samples: number[] = []
  private floor = 0.004
  private candidateAt: number | null = null
  private lastVoiceAt = 0
  private confirmed = false
  private finished = false
  private previousAt = -Infinity
  activitySeen = false
  constructor(options: Partial<VadConfig> = {}) {
    this.config = { ...DEFAULT_VAD, ...options }
    const c = this.config
    if (Object.values(c).some(value => !Number.isFinite(value) || value <= 0) || c.minimumSpeechMs > 2000 || c.silenceMs < 500 || c.silenceMs > 5000 || c.calibrationMs > 2000 || c.releaseMultiplier >= c.onsetMultiplier || c.minimumRelease >= c.minimumOnset) throw new Error('Invalid local speech detector configuration.')
  }
  get hasSpeech() { return this.confirmed }
  get noiseFloor() { return this.floor }
  sample(rms: number, at: number): VadEvent {
    if (!Number.isFinite(rms) || !Number.isFinite(at) || at < this.previousAt) return 'quiet'
    this.previousAt = at
    rms = Math.max(0, Math.min(1, rms))
    if (this.finished) return 'finished'
    this.startedAt ??= at
    if (at - this.startedAt < this.config.calibrationMs) {
      // Bounded calibration storage. A low percentile tolerates a brief early voice/bump.
      if (this.samples.length < 100) this.samples.push(rms)
      return 'calibrating'
    }
    if (this.samples.length) {
      const sorted = this.samples.sort((a, b) => a - b)
      this.floor = Math.max(0.002, sorted[Math.floor(sorted.length * 0.3)])
      this.samples = []
    }
    const onset = Math.max(this.config.minimumOnset, this.floor * this.config.onsetMultiplier)
    const release = Math.min(onset * 0.85, Math.max(this.config.minimumRelease, this.floor * this.config.releaseMultiplier))
    if (rms >= onset) this.activitySeen = true
    if (this.candidateAt === null) {
      if (rms >= onset) { this.candidateAt = at; this.lastVoiceAt = at; return 'candidate' }
      // Slow adaptation only while quiet; do not let speech raise its own threshold.
      this.floor = this.floor * 0.995 + Math.min(rms, onset * 0.6) * 0.005
      return 'quiet'
    }
    if (rms >= release) {
      this.lastVoiceAt = at
      if (at - this.candidateAt >= this.config.minimumSpeechMs) this.confirmed = true
      return this.confirmed ? 'speech' : 'candidate'
    }
    if (!this.confirmed) { this.candidateAt = null; return 'rejected' }
    if (at - this.lastVoiceAt >= this.config.silenceMs) { this.finished = true; return 'finished' }
    return 'speech'
  }
}
