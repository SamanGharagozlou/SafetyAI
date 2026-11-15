import { VoiceRecorder } from '../voice/recorder'
import type { AudioClip } from '../voice/recorder'
import { createTranscriptionClient, MAX_REQUEST_LENGTH } from '../services/transcription'
import type { TranscriptionClient } from '../services/transcription'
import { createMockTaskAdapter } from '../tasks/mockTaskAdapter'
import type { DemoScenario, DemoTaskAdapter } from '../tasks/mockTaskAdapter'
import { isTerminal, randomRequestId, requireActiveSession, TaskServiceError } from '../tasks/contracts'
import type { RequestSource, TaskSession, TaskSnapshot } from '../tasks/contracts'
import { resolveDemoAction } from '../tasks/vocabulary'
import { AudioMonitor } from './audioMonitor'
import { TurnDetector } from './vad'
import type { VadConfig } from './vad'
import { transitionCallState } from './state'
import type { CallState } from './state'

export interface HistoryTurn { id: string; text: string; status: string; reply?: string }
export interface CallSnapshot {
  state: CallState; contentState: CallState; active: boolean; muted: boolean; microphoneActive: boolean
  level: number; levels: readonly number[]; calibration: boolean; automaticDetection: boolean
  seconds: number; pendingPermission: boolean; message: string; draft: string; source: RequestSource
  clip: AudioClip | null; history: readonly HistoryTurn[]; task: TaskSnapshot | null; taskIssue: string
}
interface Monitor { attach(stream: MediaStream): boolean; close(): void }
export interface CallOptions {
  session: TaskSession; onExpired(): void; transcription?: TranscriptionClient; vad?: Partial<VadConfig>
  monitorFactory?: (callbacks: ConstructorParameters<typeof AudioMonitor>[0]) => Monitor
  taskFactory?: (scenario: DemoScenario) => DemoTaskAdapter
  responsePauseMs?: number; idlePauseMs?: number
}
const empty = (): CallSnapshot => ({ state: 'idle', contentState: 'idle', active: false, muted: false, microphoneActive: false,
  level: 0, levels: Array(16).fill(0), calibration: false, automaticDetection: true, seconds: 0,
  pendingPermission: false, message: '', draft: '', source: 'typed', clip: null, history: [], task: null, taskIssue: '' })
const captureStates: CallState[] = ['requesting_permission', 'listening', 'speech_detected', 'finalizing_turn']
const taskMessages = { queued: 'Demo request queued', processing: 'Processing demo request…', awaiting_approval: 'Demo awaiting approval — no real operator approval', executing: 'Preparing a demo text response — no robot movement', completed: 'Demo response ready', denied: 'Demo request declined', failed: 'Demo could not finish' }

/** One call, one capture, one asynchronous operation at a time. No robot API is used. */
export class CallController {
  private value = empty()
  private listeners = new Set<() => void>()
  private recorder = new VoiceRecorder()
  private monitor: Monitor | null = null
  private detector: TurnDetector | null = null
  private analyserWorking = false
  private options: CallOptions
  readonly transcription: TranscriptionClient
  private callGeneration = 0
  private captureGeneration = 0
  private uploadGeneration = 0
  private taskGeneration = 0
  private upload: AbortController | null = null
  private uploadTimer: ReturnType<typeof setTimeout> | undefined
  private taskAbort: AbortController | null = null
  private adapter: DemoTaskAdapter | null = null
  private taskTimer: ReturnType<typeof setTimeout> | undefined
  private taskDeadline: ReturnType<typeof setTimeout> | undefined
  private nextTimer: ReturnType<typeof setTimeout> | undefined
  private clock: ReturnType<typeof setInterval> | undefined
  private startedAt = 0
  private captureAt = 0
  private speechCandidate = false
  private uploadAttempted = false
  private submissionLocked = false
  constructor(options: CallOptions) {
    this.options = options
    this.transcription = options.transcription ?? createTranscriptionClient()
    this.recorder.subscribe(this.recorderChanged)
  }
  getSnapshot = () => this.value
  subscribe = (listener: () => void) => { this.listeners.add(listener); return () => { this.listeners.delete(listener) } }
  private patch(patch: Partial<CallSnapshot>) { this.value = { ...this.value, ...patch }; this.listeners.forEach(listener => listener()) }
  private advance(next: CallState, patch: Partial<CallSnapshot> = {}) {
    const current = this.value.muted && this.value.state === 'muted' ? this.value.contentState : this.value.state
    const state = transitionCallState(current, next)
    this.patch({ ...patch, contentState: state, state: this.value.muted && next !== 'ended' ? 'muted' : state })
  }
  private valid() {
    try { requireActiveSession(this.options.session); return true } catch { this.end(); this.options.onExpired(); return false }
  }
  private releaseAudio() {
    this.captureGeneration++
    this.monitor?.close(); this.monitor = null
    this.detector = null; this.analyserWorking = false
    this.recorder.cancel()
    this.patch({ microphoneActive: false, clip: null, level: 0, levels: Array(16).fill(0), calibration: false })
  }
  private stopUpload() { this.uploadGeneration++; this.upload?.abort(); this.upload = null; clearTimeout(this.uploadTimer) }
  private stopTask() {
    this.taskGeneration++; this.taskAbort?.abort(); this.taskAbort = null
    clearTimeout(this.taskTimer); clearTimeout(this.taskDeadline)
    this.adapter?.dispose(); this.adapter = null
  }
  private startClock() {
    this.startedAt = Date.now()
    this.clock = setInterval(() => {
      if (!this.valid()) return
      this.patch({ seconds: Math.max(0, Math.floor((Date.now() - this.startedAt) / 1000)) })
    }, 1000)
  }
  start = () => {
    if (this.value.active || this.recorder.getSnapshot().requestPending || !this.valid()) return
    this.callGeneration++
    this.value = empty()
    this.patch({ active: true })
    this.startClock()
    this.beginCapture()
  }
  private beginCapture = () => {
    if (!this.value.active || this.value.muted || this.upload || this.taskAbort || !this.valid()) return
    const recording = this.recorder.getSnapshot()
    if (recording.requestPending || recording.status === 'recording') return
    clearTimeout(this.nextTimer)
    this.submissionLocked = false
    this.patch({ draft: '', source: 'typed', task: null, taskIssue: '', message: '', muted: false })
    this.advance('requesting_permission')
    const generation = ++this.captureGeneration
    this.detector = new TurnDetector(this.options.vad)
    this.speechCandidate = false
    this.captureAt = performance.now()
    const monitor = (this.options.monitorFactory ?? (callbacks => new AudioMonitor(callbacks)))({
      onSample: sample => {
        if (generation !== this.captureGeneration || !this.value.active || this.value.muted || !this.value.microphoneActive) return
        const detector = this.detector
        if (!detector) return
        const event = detector.sample(sample.rms, sample.at)
        const level = Math.min(1, Math.max(0, sample.rms * 8))
        this.patch({ level, levels: [...this.value.levels.slice(1), level], calibration: event === 'calibrating' })
        if (event === 'candidate' && !this.speechCandidate) {
          this.speechCandidate = true
          // Reset the silent prelude; the 20-second utterance bound starts here.
          this.recorder.restartTurn()
        } else if (event === 'rejected') this.speechCandidate = false
        if (event === 'speech' && this.value.contentState === 'listening') this.advance('speech_detected')
        if (event === 'finished') this.finishTurn(false)
        if (!detector.hasSpeech && sample.at - this.captureAt >= (this.options.idlePauseMs ?? 60_000)) this.mute('No speech yet. Your microphone is paused; Unmute when you’re ready.')
      },
      onInterrupted: () => { if (generation === this.captureGeneration && this.value.active) this.mute('Audio processing paused. Tap Unmute to resume, or write your request.') },
    })
    this.monitor = monitor
    void this.recorder.start({
      onStream: stream => {
        if (generation !== this.captureGeneration) return
        this.analyserWorking = monitor.attach(stream)
        this.patch({ automaticDetection: this.analyserWorking, calibration: this.analyserWorking })
      },
      onLimit: () => {
        if (generation !== this.captureGeneration) return
        if (this.detector?.hasSpeech || !this.analyserWorking) this.finishTurn(false)
        else this.recorder.restartTurn() // No retained silent recordings, URLs, or transcripts.
      },
    })
  }
  private recorderChanged = () => {
    const capture = this.recorder.getSnapshot()
    this.patch({ pendingPermission: capture.requestPending })
    if (!this.value.active) return
    if (capture.status === 'error' && captureStates.includes(this.value.contentState)) {
      this.monitor?.close(); this.monitor = null; this.detector = null
      this.advance('error', { microphoneActive: false, level: 0, levels: Array(16).fill(0), message: capture.message, calibration: false })
    } else if (capture.status === 'recording' && this.value.contentState === 'requesting_permission') {
      this.advance('listening', { microphoneActive: true })
    } else if (capture.status === 'reviewing' && capture.clip && this.value.contentState === 'finalizing_turn') {
      const clip = capture.clip
      if (this.transcription.connected) this.advance('awaiting_transcription', { clip, message: 'Your speech turn is captured locally. Sending it requires your permission.' })
      else {
        this.recorder.cancel()
        this.advance('awaiting_review', { clip: null, message: 'Speech recognition unavailable. Transcription not connected; no audio was uploaded. Please write your request.' })
      }
    }
  }
  finishTurn = (manual = true) => {
    if (!this.value.active || this.value.muted || !['listening', 'speech_detected'].includes(this.value.contentState) || !this.valid()) return
    if (this.analyserWorking && !this.detector?.hasSpeech && (!manual || !this.detector?.activitySeen)) {
      this.releaseAudio()
      this.advance('awaiting_review', { message: 'No speech detected. No audio was sent. You can write your request or try another turn.' })
      return
    }
    if (this.recorder.getSnapshot().elapsedMs < 250 && !this.detector?.hasSpeech) {
      this.releaseAudio()
      this.advance('awaiting_review', { message: 'That turn was very short. Nothing was sent. Write your request or try again.' })
      return
    }
    this.advance('finalizing_turn', { microphoneActive: false, level: 0, levels: Array(16).fill(0), calibration: false })
    this.captureGeneration++
    this.monitor?.close(); this.monitor = null; this.detector = null
    this.recorder.stop()
  }
  mute = (message = 'Microphone muted. The unfinished turn was discarded.') => {
    if (!this.value.active || this.value.muted) return
    clearTimeout(this.nextTimer)
    const previous = this.value.contentState
    if (captureStates.includes(previous)) {
      this.releaseAudio()
      this.patch({ contentState: 'listening' })
    }
    this.patch({ state: transitionCallState(this.value.state, 'muted'), muted: true, microphoneActive: false, message, level: 0, levels: Array(16).fill(0), calibration: false })
  }
  unmute = () => {
    if (!this.value.active || !this.value.muted || this.value.pendingPermission || !this.valid()) return
    const resume = this.value.contentState
    this.patch({ muted: false })
    if (['listening', 'speech_detected', 'requesting_permission', 'error', 'responding'].includes(resume)) this.beginCapture()
    else this.patch({ state: transitionCallState('muted', resume), message: 'Microphone off during review and processing.' })
  }
  nextTurn = () => {
    if (!this.value.active || this.value.pendingPermission || !this.valid()) return
    this.stopUpload(); this.stopTask(); this.releaseAudio()
    this.uploadAttempted = false
    this.patch({ muted: false })
    this.beginCapture()
  }
  writeInstead = () => {
    if (!this.valid()) return
    if (!this.value.active) { this.callGeneration++; this.patch({ active: true }); this.startClock() }
    clearTimeout(this.nextTimer)
    this.stopUpload(); this.stopTask(); this.releaseAudio()
    this.submissionLocked = false
    this.advance('awaiting_review', { message: 'Write your request. It stays local until you explicitly confirm a demo task.' })
  }
  editDraft = (draft: string) => { if (this.value.active) this.patch({ draft: draft.slice(0, MAX_REQUEST_LENGTH) }) }
  review = () => {
    if (!this.value.active || !this.valid() || this.value.contentState !== 'awaiting_review') return
    if (!this.value.draft.trim()) { this.patch({ message: 'Please enter a request to review.' }); return }
    this.advance('awaiting_confirmation', { draft: this.value.draft.trim(), message: '' })
  }
  edit = () => { if (this.value.contentState === 'awaiting_confirmation') this.advance('awaiting_review') }
  cancelRequest = () => {
    if (this.value.contentState !== 'awaiting_confirmation') return
    this.advance('awaiting_review', { draft: '', source: 'typed', message: 'Request canceled. No demo task was submitted.' })
  }
  authorizeUpload = async () => {
    const clip = this.value.clip
    if (!clip || !this.transcription.connected || this.upload || !this.value.active || !this.valid()) return
    const generation = ++this.uploadGeneration
    const controller = new AbortController()
    this.upload = controller; this.uploadAttempted = true
    this.patch({ message: 'Sending this turn for transcription with your permission…' })
    this.uploadTimer = setTimeout(() => controller.abort(), 30_000)
    try {
      const result = await this.transcription.transcribe(clip, controller.signal)
      if (generation !== this.uploadGeneration || !this.value.active || !this.valid()) return
      this.recorder.cancel()
      this.advance('awaiting_review', { clip: null, draft: result.status === 'transcribed' ? result.text : '', source: result.status === 'transcribed' ? 'transcript' : 'typed',
        message: result.status === 'transcribed' ? 'Please check and edit the returned transcript. Nothing has been submitted as a task.' : 'Transcription not connected. Please write your request.' })
    } catch {
      if (generation === this.uploadGeneration && this.value.active) this.patch({ message: 'Transcription unavailable or timed out. You can retry or write instead. The server may already have received audio.' })
    } finally {
      if (generation === this.uploadGeneration) { this.upload = null; clearTimeout(this.uploadTimer); this.patch({}) }
    }
  }
  get uploading() { return !!this.upload }
  confirm = (scenario: DemoScenario = 'success') => {
    if (this.submissionLocked || this.value.contentState !== 'awaiting_confirmation' || !this.value.active || !this.valid()) return
    this.submissionLocked = true
    this.advance('processing', { message: 'Processing demo request…', taskIssue: '' })
    const generation = ++this.taskGeneration
    const abort = new AbortController(); this.taskAbort = abort
    const request = { text: this.value.draft, source: this.value.source, proposedAction: resolveDemoAction(this.value.draft), clientRequestId: randomRequestId('client-request') }
    const historyId = request.clientRequestId
    this.patch({ history: [...this.value.history.slice(-5), { id: historyId, text: request.text, status: 'queued' }] })
    const until = Date.now() + 12_000
    const current = () => generation === this.taskGeneration && this.value.active && !abort.signal.aborted && this.valid()
    const fail = (message: string) => {
      if (!current()) return
      this.stopTask()
      this.updateHistory(historyId, { status: 'unavailable', reply: message })
      this.advance('error', { taskIssue: message, message })
    }
    const timeout = () => fail('Demo status timed out. No completed robot action is claimed.')
    this.taskDeadline = setTimeout(timeout, 12_000)
    const accept = (task: TaskSnapshot, expectedId?: string) => {
      if (!current()) return
      if (Date.now() >= until) { timeout(); return }
      if (!task.demo || task.clientRequestId !== request.clientRequestId || (expectedId && task.requestId !== expectedId)) { fail('Demo status could not be verified.'); return }
      if (!this.value.task || task.revision >= this.value.task.revision) {
        this.patch({ task, message: taskMessages[task.status] })
        this.updateHistory(historyId, { status: task.status, reply: task.reply })
      }
      if (isTerminal(task.status) && task.revision >= (this.value.task?.revision ?? 0)) {
        const reply = task.reply ?? (task.reason === 'unsupported_request' ? 'This demo supports Hello, Robot info, and Help only. Movement is not supported.' : task.reason === 'robot_unavailable' ? 'Simulated robot unavailable. No robot connection was attempted.' : 'This demo request did not complete. No physical action was performed.')
        this.stopTask()
        this.updateHistory(historyId, { status: task.status, reply })
        this.advance('responding', { message: reply })
        const call = this.callGeneration
        this.nextTimer = setTimeout(() => {
          if (call === this.callGeneration && this.value.active && !this.value.muted && this.valid() && document.visibilityState !== 'hidden') this.nextTurn()
        }, this.options.responsePauseMs ?? 2500)
      } else this.taskTimer = setTimeout(() => { if (current()) void this.adapter!.status(task.requestId, this.options.session, abort.signal).then(next => accept(next, task.requestId)).catch(() => fail('The demo service is unavailable. No robot action is claimed.')) }, 500)
    }
    try {
      this.adapter = (this.options.taskFactory ?? (value => createMockTaskAdapter({ scenario: value })))(scenario)
      if (this.adapter.mode !== 'demo') throw new TaskServiceError('invalid_response')
      void this.adapter.submit(request, this.options.session, abort.signal).then(value => accept(value)).catch(() => fail('The demo service is unavailable. No robot action is claimed.'))
    } catch { fail('The demo service is unavailable. No robot action is claimed.') }
  }
  private updateHistory(id: string, change: Partial<HistoryTurn>) { this.patch({ history: this.value.history.map(turn => turn.id === id ? { ...turn, ...change } : turn) }) }
  hide = () => {
    if (!this.value.active) return
    const hadTask = !!this.taskAbort
    this.stopUpload(); this.stopTask()
    this.releaseAudio()
    const resume: CallState = hadTask ? 'responding' : ['awaiting_transcription', 'finalizing_turn'].includes(this.value.contentState) ? 'awaiting_review' : this.value.contentState
    if (!this.value.muted) this.mute('Microphone paused while this page is hidden. Unmute deliberately to resume.')
    this.patch({ contentState: captureStates.includes(resume) ? 'listening' : resume, message: hadTask ? 'Local demo tracking paused. Ending a view does not imply server cancellation.' : 'Microphone paused while this page is hidden. Unmute deliberately to resume.' })
  }
  end = () => {
    this.callGeneration++
    this.patch({ active: false })
    clearInterval(this.clock); clearTimeout(this.nextTimer)
    this.stopUpload(); this.stopTask(); this.releaseAudio()
    const state = transitionCallState(this.value.state, 'ended')
    this.patch({ ...empty(), state, contentState: state, pendingPermission: this.recorder.getSnapshot().requestPending,
      message: this.uploadAttempted ? 'Conversation ended. Audio may already have reached the transcription server; no server deletion is claimed.' : 'Conversation ended. Your microphone is off.' })
    this.uploadAttempted = false; this.submissionLocked = false
  }
  dispose = () => { if (this.value.active || this.value.pendingPermission) this.end() }
}
