import { describe, expect, it } from 'vitest'
import { DEFAULT_VAD, TurnDetector } from './vad'

function calibrated(noise = 0.003) {
  const vad = new TurnDetector()
  for (let t = 0; t < 500; t += 50) expect(vad.sample(noise, t)).toBe('calibrating')
  return vad
}
describe('local amplitude VAD', () => {
  it('uses conservative configurable defaults', () => expect(DEFAULT_VAD).toMatchObject({ calibrationMs: 500, minimumSpeechMs: 250, silenceMs: 1200 }))
  it('calibrates, requires minimum speech, and finishes only after a sustained pause', () => {
    const vad = calibrated()
    expect(vad.sample(0.003, 500)).toBe('quiet')
    expect(vad.sample(0.08, 600)).toBe('candidate')
    expect(vad.sample(0.08, 800)).toBe('candidate')
    expect(vad.sample(0.08, 850)).toBe('speech')
    expect(vad.sample(0.002, 1800)).toBe('speech')
    expect(vad.sample(0.002, 2049)).toBe('speech')
    expect(vad.sample(0.002, 2050)).toBe('finished')
    expect(vad.hasSpeech).toBe(true)
  })
  it('rejects clicks and short bursts without producing a turn', () => {
    const vad = calibrated()
    expect(vad.sample(0.2, 600)).toBe('candidate')
    expect(vad.sample(0.002, 700)).toBe('rejected')
    expect(vad.sample(0.002, 3000)).toBe('quiet')
    expect(vad.hasSpeech).toBe(false)
  })
  it('does not mistake constant calibrated hackathon noise for speech', () => {
    const vad = calibrated(0.04)
    for (let t = 500; t < 5000; t += 50) expect(vad.sample(0.04 + (t % 100 ? 0.005 : -0.005), t)).toBe('quiet')
    expect(vad.sample(0.2, 5000)).toBe('candidate')
    expect(vad.sample(0.18, 5250)).toBe('speech')
  })
  it('uses a lower release threshold rather than chopping normal pauses', () => {
    const vad = calibrated(0.01)
    vad.sample(0.05, 600)
    expect(vad.sample(0.024, 850)).toBe('speech')
    expect(vad.sample(0.001, 1700)).toBe('speech')
    expect(vad.sample(0.05, 1800)).toBe('speech')
    expect(vad.sample(0.001, 2999)).toBe('speech')
    expect(vad.sample(0.001, 3000)).toBe('finished')
  })
  it('ignores invalid and stale measurements', () => {
    const vad = calibrated()
    expect(vad.sample(NaN, 600)).toBe('quiet')
    expect(vad.sample(0.2, 100)).toBe('quiet')
    expect(vad.hasSpeech).toBe(false)
  })
  it.each([{ silenceMs: 100 }, { minimumSpeechMs: 0 }, { calibrationMs: 5000 }, { onsetMultiplier: 1 }, { minimumRelease: 1 }])('rejects unsafe tuning', options => expect(() => new TurnDetector(options)).toThrow())
})
