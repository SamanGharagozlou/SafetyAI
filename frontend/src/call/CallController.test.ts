import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { installMediaMocks, MockMediaRecorder } from '../test/media'
import { CallController } from './CallController'
import type { CallOptions } from './CallController'
import { createMockTaskAdapter } from '../tasks/mockTaskAdapter'

let media: ReturnType<typeof installMediaMocks>
let controller: CallController
let callbacks: Parameters<NonNullable<CallOptions['monitorFactory']>>[0]
let close: ReturnType<typeof vi.fn<() => void>>
let expired: ReturnType<typeof vi.fn<() => void>>
beforeEach(() => { vi.useFakeTimers(); media = installMediaMocks(); close = vi.fn(); expired = vi.fn() })
afterEach(() => { controller?.dispose(); vi.unstubAllGlobals() })
function setup(options: Partial<CallOptions> = {}) {
  controller = new CallController({ session: { id: 'demo-test', expiresAt: Date.now() + 600_000 }, onExpired: expired,
    monitorFactory: value => { callbacks = value; return { attach: () => true, close } }, ...options })
  return controller
}
async function time(ms: number) { await vi.advanceTimersByTimeAsync(ms) }
async function start() { controller.start(); await Promise.resolve() }
async function sample(rms: number, ms = 50) { await time(ms); callbacks.onSample({ rms, at: performance.now() }) }
async function speech() {
  for (let i = 0; i < 12; i++) await sample(0.003)
  for (let i = 0; i < 7; i++) await sample(0.1)
}
async function finish() {
  for (let i = 0; i < 25; i++) await sample(0.002)
  await Promise.resolve()
}

describe('call capture, VAD, and truthful activity', () => {
  it('has no microphone/analyser activity before Start and prevents duplicate Start', async () => {
    setup(); expect(media.getUserMedia).not.toHaveBeenCalled()
    controller.start(); controller.start(); await Promise.resolve()
    expect(media.getUserMedia).toHaveBeenCalledTimes(1)
    expect(controller.getSnapshot()).toMatchObject({ state: 'listening', microphoneActive: true, active: true })
  })
  it('detects and finalizes a natural speech turn, with no uploads/transcript when disconnected', async () => {
    const fetch = vi.fn(); vi.stubGlobal('fetch', fetch)
    setup(); await start(); await speech()
    expect(controller.getSnapshot().state).toBe('speech_detected')
    expect(controller.getSnapshot().level).toBeGreaterThan(0)
    await finish()
    expect(controller.getSnapshot()).toMatchObject({ state: 'awaiting_review', microphoneActive: false, draft: '', clip: null })
    expect(controller.getSnapshot().message).toContain('Transcription not connected')
    expect(media.streams[0].track.stop).toHaveBeenCalledTimes(1)
    expect(media.revokeObjectURL).toHaveBeenCalledTimes(1)
    expect(fetch).not.toHaveBeenCalled()
    expect(close).toHaveBeenCalled()
  })
  it('rolls silent buffers without creating empty clips or a second stream', async () => {
    setup(); await start()
    await time(40_000)
    expect(media.getUserMedia).toHaveBeenCalledTimes(1)
    expect(MockMediaRecorder.instances).toHaveLength(3)
    expect(MockMediaRecorder.instances.filter(instance => instance.state === 'recording')).toHaveLength(1)
    expect(media.createObjectURL).not.toHaveBeenCalled()
    expect(controller.getSnapshot().state).toBe('listening')
  })
  it('does not preserve a silence-only manual turn', async () => {
    setup(); await start(); await sample(0.002, 600)
    controller.finishTurn()
    expect(controller.getSnapshot().state).toBe('awaiting_review')
    expect(controller.getSnapshot().message).toContain('No speech detected')
    expect(media.createObjectURL).not.toHaveBeenCalled()
  })
  it('caps meaningful utterances at 20 seconds without ending the call', async () => {
    setup(); await start(); await speech(); await time(20_000)
    expect(controller.getSnapshot()).toMatchObject({ active: true, state: 'awaiting_review', microphoneActive: false })
    expect(media.streams[0].track.stop).toHaveBeenCalledTimes(1)
  })
  it('supports Finish turn without Web Audio, with explicit manual detection status', async () => {
    setup({ monitorFactory: () => ({ attach: () => false, close }) }); await start(); await time(500)
    expect(controller.getSnapshot().automaticDetection).toBe(false)
    controller.finishTurn(); await Promise.resolve()
    expect(controller.getSnapshot().state).toBe('awaiting_review')
  })
  it.each(['listening', 'speech'])('Mute stops tracks during %s; Unmute makes a fresh capture', async phase => {
    setup(); await start(); if (phase === 'speech') await speech()
    controller.mute()
    expect(controller.getSnapshot()).toMatchObject({ state: 'muted', microphoneActive: false, level: 0 })
    expect(media.streams[0].track.stop).toHaveBeenCalledTimes(1)
    await time(2000); expect(media.getUserMedia).toHaveBeenCalledTimes(1)
    controller.unmute(); await Promise.resolve()
    expect(media.getUserMedia).toHaveBeenCalledTimes(2)
    expect(controller.getSnapshot().state).toBe('listening')
  })
  it('mutes on AudioContext interruption rather than showing false Listening', async () => {
    setup(); await start(); callbacks.onInterrupted()
    expect(controller.getSnapshot()).toMatchObject({ state: 'muted', microphoneActive: false })
    expect(close).toHaveBeenCalled()
    expect(media.streams[0].track.stop).toHaveBeenCalledTimes(1)
  })
  it('ignores old analyser callbacks after End', async () => {
    setup(); await start(); const old = callbacks
    controller.end(); old.onSample({ rms: 0.8, at: 9000 }); old.onInterrupted()
    expect(controller.getSnapshot()).toMatchObject({ state: 'ended', active: false, level: 0, history: [] })
  })
  it('cleans up tab hiding without automatically resuming on return', async () => {
    setup(); await start(); await speech(); controller.hide(); await time(1000)
    expect(controller.getSnapshot()).toMatchObject({ state: 'muted', microphoneActive: false })
    expect(media.getUserMedia).toHaveBeenCalledTimes(1)
    expect(media.streams[0].track.stop).toHaveBeenCalledTimes(1)
  })
  it('handles microphone removal and MediaRecorder failures', async () => {
    setup(); await start(); media.streams[0].track.onended?.()
    expect(controller.getSnapshot()).toMatchObject({ state: 'error', microphoneActive: false })
    expect(close).toHaveBeenCalled()
    controller.nextTurn(); await Promise.resolve()
    MockMediaRecorder.instances.at(-1)?.onerror?.()
    expect(controller.getSnapshot().state).toBe('error')
    expect(media.streams[1].track.stop).toHaveBeenCalledTimes(1)
  })
  it('stops late permission grants after ending and blocks overlapping prompts', async () => {
    let grant!: (stream: MediaStream) => void
    media.getUserMedia.mockImplementationOnce(() => new Promise(resolve => { grant = resolve }))
    setup(); controller.start(); controller.end(); controller.start()
    grant(media.createStream()); await Promise.resolve(); await Promise.resolve()
    expect(media.getUserMedia).toHaveBeenCalledTimes(1)
    expect(media.streams[0].track.stop).toHaveBeenCalledTimes(1)
    expect(controller.getSnapshot().state).toBe('ended')
  })
})

describe('in-call review, confirmation, tasks and async guards', () => {
  function draft(text = 'Hello') { controller.writeInstead(); controller.editDraft(text); controller.review() }
  it('validates, edits, cancels, and never auto-confirms', () => {
    const factory = vi.fn(() => createMockTaskAdapter())
    setup({ taskFactory: factory }); controller.writeInstead(); controller.review()
    expect(controller.getSnapshot().message).toContain('Please enter')
    controller.editDraft(' Hello '); controller.review()
    expect(controller.getSnapshot()).toMatchObject({ state: 'awaiting_confirmation', draft: 'Hello' })
    controller.edit(); controller.editDraft('Help'); controller.review(); controller.cancelRequest()
    expect(controller.getSnapshot()).toMatchObject({ state: 'awaiting_review', draft: '' })
    expect(factory).not.toHaveBeenCalled()
  })
  it('runs all task stages, deduplicates Confirm, and resumes another turn in the same call', async () => {
    const factory = vi.fn(() => createMockTaskAdapter({ stepMs: 800, latencyMs: 0 }))
    setup({ taskFactory: factory, responsePauseMs: 1000 }); draft(); controller.confirm(); controller.confirm()
    await time(1)
    expect(controller.getSnapshot().task?.status).toBe('queued')
    await time(1100); expect(controller.getSnapshot().task?.status).toBe('processing')
    await time(1100); expect(controller.getSnapshot().task?.status).toBe('awaiting_approval')
    await time(1600); expect(controller.getSnapshot().task?.status).toBe('completed')
    expect(controller.getSnapshot().state).toBe('responding')
    expect(factory).toHaveBeenCalledTimes(1)
    await time(1200)
    expect(controller.getSnapshot()).toMatchObject({ state: 'listening', microphoneActive: true })
    expect(controller.getSnapshot().history).toHaveLength(1)
  })
  it.each(['denied', 'failed', 'robot_unavailable', 'service_error', 'timeout'] as const)('honestly handles %s', async scenario => {
    setup({ taskFactory: value => createMockTaskAdapter({ scenario: value, stepMs: 100, latencyMs: 0 }), responsePauseMs: 30_000 })
    draft(); controller.confirm(scenario); await time(scenario === 'timeout' ? 12_001 : 1000)
    expect(controller.getSnapshot().history[0].reply).toBeTruthy()
    expect(controller.getSnapshot().task?.status).not.toBe('completed')
    if (scenario === 'timeout') expect(controller.getSnapshot().taskIssue).toContain('timed out')
  })
  it('declines movement requests independently of the UI', async () => {
    setup({ responsePauseMs: 30_000 }); draft('Hello then walk forward'); controller.confirm(); await time(3000)
    expect(controller.getSnapshot().task).toMatchObject({ status: 'denied', action: null })
  })
  it('blocks expired-session confirmation and clears memory', () => {
    setup({ session: { id: 'demo', expiresAt: Date.now() + 1000 } }); draft(); vi.setSystemTime(Date.now() + 1000); controller.confirm()
    expect(expired).toHaveBeenCalled()
    expect(controller.getSnapshot()).toMatchObject({ state: 'ended', history: [], draft: '' })
  })
  it('never uploads automatically; uses only an explicitly returned transcript', async () => {
    const transcribe = vi.fn().mockResolvedValue({ status: 'transcribed', text: 'Hello' })
    setup({ transcription: { connected: true, endpoint: '/api/transcribe', transcribe } })
    await start(); await speech(); await finish()
    expect(controller.getSnapshot().state).toBe('awaiting_transcription')
    expect(transcribe).not.toHaveBeenCalled()
    await controller.authorizeUpload()
    expect(transcribe).toHaveBeenCalledTimes(1)
    expect(controller.getSnapshot()).toMatchObject({ state: 'awaiting_review', draft: 'Hello', source: 'transcript', clip: null })
  })
  it('aborts and ignores a stale upload after End', async () => {
    let resolve!: (value: { status: 'transcribed'; text: string }) => void
    let signal!: AbortSignal
    const transcribe = vi.fn((_clip, input: AbortSignal) => { signal = input; return new Promise<{ status: 'transcribed'; text: string }>(done => { resolve = done }) })
    setup({ transcription: { connected: true, endpoint: '/api/transcribe', transcribe } }); await start(); await speech(); await finish()
    const pending = controller.authorizeUpload(); controller.end(); resolve({ status: 'transcribed', text: 'Late fixture' }); await pending
    expect(signal.aborted).toBe(true)
    expect(controller.getSnapshot()).toMatchObject({ state: 'ended', draft: '', clip: null, history: [] })
  })
  it('End during a queued task clears history and ignores later completion', async () => {
    setup(); draft(); controller.confirm(); controller.end(); await time(5000)
    expect(controller.getSnapshot()).toMatchObject({ state: 'ended', history: [], task: null })
  })
  it('Mute while processing preserves task updates without restarting the microphone', async () => {
    setup({ responsePauseMs: 100 }); draft(); controller.confirm(); controller.mute(); await time(6000)
    expect(controller.getSnapshot()).toMatchObject({ state: 'muted', contentState: 'responding', microphoneActive: false })
    expect(media.getUserMedia).not.toHaveBeenCalled()
  })
})


describe('conversation boundary regression guards', () => {
  it('permits a text fallback while native permission is pending and stops its late grant', async () => {
    let grant!: (stream: MediaStream) => void
    media.getUserMedia.mockImplementationOnce(() => new Promise(resolve => { grant = resolve }))
    setup(); controller.start(); controller.writeInstead(); controller.editDraft('Help'); controller.review()
    expect(controller.getSnapshot().state).toBe('awaiting_confirmation')
    grant(media.createStream()); await Promise.resolve(); await Promise.resolve()
    expect(media.streams[0].track.stop).toHaveBeenCalledTimes(1)
    expect(controller.getSnapshot().draft).toBe('Help')
  })
  it('bounds memory history across repeated text turns', async () => {
    setup({ taskFactory: () => createMockTaskAdapter({ stepMs: 1, latencyMs: 0 }), responsePauseMs: 30_000 })
    for (let i = 0; i < 9; i++) {
      controller.writeInstead(); controller.editDraft(i % 2 ? 'Help' : 'Hello'); controller.review(); controller.confirm()
      await time(550)
    }
    expect(controller.getSnapshot().history).toHaveLength(6)
    controller.end(); expect(controller.getSnapshot().history).toEqual([])
  })
  it('pauses after sustained silence with no retained clip', async () => {
    setup({ idlePauseMs: 1000 }); await start()
    for (let i = 0; i < 22; i++) await sample(.002)
    expect(controller.getSnapshot()).toMatchObject({ state: 'muted', microphoneActive: false, clip: null })
    expect(media.createObjectURL).not.toHaveBeenCalled()
  })
})
