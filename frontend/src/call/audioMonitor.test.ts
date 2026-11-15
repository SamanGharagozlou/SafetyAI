import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { AudioMonitor } from './audioMonitor'

let frame: FrameRequestCallback
let context: FakeContext
const disconnectSource = vi.fn(), disconnectAnalyser = vi.fn(), closed = vi.fn(), resume = vi.fn()
class FakeContext {
  state = 'running'
  onstatechange: (() => void) | null = null
  constructor() { context = this }
  resume() { resume(); return Promise.resolve() }
  close() { this.state = 'closed'; closed(); return Promise.resolve() }
  createMediaStreamSource() { return { connect: vi.fn(), disconnect: disconnectSource } }
  createAnalyser() { return { fftSize: 1024, getFloatTimeDomainData: (data: Float32Array) => data.fill(0.05), disconnect: disconnectAnalyser } }
}
beforeEach(() => {
  vi.stubGlobal('AudioContext', FakeContext)
  vi.stubGlobal('requestAnimationFrame', vi.fn((callback: FrameRequestCallback) => { frame = callback; return 1 }))
  vi.stubGlobal('cancelAnimationFrame', vi.fn())
  vi.clearAllMocks()
})
afterEach(() => vi.unstubAllGlobals())
describe('Web Audio analyser ownership', () => {
  it('resumes in the initiating gesture, measures actual PCM RMS, and never connects speakers', () => {
    const onSample = vi.fn(), onInterrupted = vi.fn()
    const monitor = new AudioMonitor({ onSample, onInterrupted })
    expect(resume).toHaveBeenCalledTimes(1)
    expect(monitor.attach({} as MediaStream)).toBe(true)
    frame(50)
    expect(onSample.mock.calls[0][0].rms).toBeCloseTo(0.05)
    monitor.close()
    expect(disconnectSource).toHaveBeenCalledTimes(1)
    expect(disconnectAnalyser).toHaveBeenCalledTimes(1)
    expect(closed).toHaveBeenCalledTimes(1)
    expect(cancelAnimationFrame).toHaveBeenCalled()
  })
  it('reports suspension/interruption and closes idempotently', () => {
    const onInterrupted = vi.fn()
    const monitor = new AudioMonitor({ onSample: vi.fn(), onInterrupted })
    monitor.attach({} as MediaStream)
    context.state = 'suspended'; context.onstatechange?.()
    expect(onInterrupted).toHaveBeenCalledTimes(1)
    monitor.close(); monitor.close()
    expect(closed).toHaveBeenCalledTimes(1)
    expect(context.onstatechange).toBeNull()
  })
  it('does not sample stale animation frames after close', () => {
    const onSample = vi.fn()
    const monitor = new AudioMonitor({ onSample, onInterrupted: vi.fn() }); monitor.attach({} as MediaStream)
    const stale = frame; monitor.close(); stale(1000)
    expect(onSample).not.toHaveBeenCalled()
  })
  it('supports Safari’s prefixed constructor and manual fallback when absent', () => {
    vi.stubGlobal('AudioContext', undefined)
    Object.defineProperty(window, 'webkitAudioContext', { configurable: true, value: FakeContext })
    const monitor = new AudioMonitor({ onSample: vi.fn(), onInterrupted: vi.fn() })
    expect(monitor.attach({} as MediaStream)).toBe(true); monitor.close()
    Reflect.deleteProperty(window, 'webkitAudioContext')
    const absent = new AudioMonitor({ onSample: vi.fn(), onInterrupted: vi.fn() })
    expect(absent.attach({} as MediaStream)).toBe(false); absent.close()
  })
})
