import { useEffect, useRef, useState, useSyncExternalStore } from 'react'
import type { MouseEvent } from 'react'
import { createTranscriptionClient, MAX_REQUEST_LENGTH } from '../services/transcription'
import { recordingUnavailable } from '../voice/recorder'
import { resolveDemoAction, actionDescriptions } from '../tasks/vocabulary'
import type { TaskSession } from '../tasks/contracts'
import { DEMO_SCENARIOS } from '../tasks/mockTaskAdapter'
import type { DemoScenario } from '../tasks/mockTaskAdapter'
import { CallController } from './CallController'
import { CallVisualizer } from './CallVisualizer'
import { callLabels } from './state'

function Phone({ end = false }: { end?: boolean }) {
  return <svg width="29" height="29" viewBox="0 0 24 24" fill="none" aria-hidden="true">{end
    ? <path d="M3 14c4-6 14-6 18 0l-3 4-4-2v-3h-4v3l-4 2-3-4Z" stroke="currentColor" strokeWidth="1.7" strokeLinejoin="round"/>
    : <path d="m6 3 4 4-2 3c1 3 3 5 6 6l3-2 4 4-2 3C10 22 2 14 3 6l3-3Z" stroke="currentColor" strokeWidth="1.7" strokeLinejoin="round"/>}</svg>
}
function Mic({ muted }: { muted: boolean }) {
  return <svg width="27" height="27" viewBox="0 0 24 24" fill="none" aria-hidden="true"><rect x="9" y="2" width="6" height="12" rx="3" stroke="currentColor" strokeWidth="1.7"/><path d="M5 10v2a7 7 0 0 0 14 0v-2M12 19v3m-4 0h8" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round"/>{muted && <path d="m3 3 18 18" stroke="currentColor" strokeWidth="2"/>}</svg>
}
export interface CallScreenProps {
  session: TaskSession; nickname: string; onExpired(): void; onLeave(): void
  onPrivacy(event: MouseEvent<HTMLAnchorElement>): void
}
export function CallScreen({ session, nickname, onExpired, onLeave, onPrivacy }: CallScreenProps) {
  const [controller] = useState(() => new CallController({ session, onExpired, transcription: createTranscriptionClient(import.meta.env.VITE_TRANSCRIBE_ENDPOINT) }))
  const call = useSyncExternalStore(controller.subscribe, controller.getSnapshot)
  const [scenario, setScenario] = useState<DemoScenario>('success')
  const [playbackFailed, setPlaybackFailed] = useState(false)
  const text = useRef<HTMLTextAreaElement>(null)
  const heading = useRef<HTMLHeadingElement>(null)
  const start = useRef<HTMLButtonElement>(null)
  const audio = useRef<HTMLAudioElement>(null)
  const wasActive = useRef(false)
  const stage = call.contentState
  useEffect(() => { heading.current?.focus({ preventScroll: true }) }, [])
  useEffect(() => {
    const visibility = () => { if (document.visibilityState === 'hidden') { audio.current?.pause(); controller.hide() } }
    const pageHide = () => controller.end()
    document.addEventListener('visibilitychange', visibility)
    window.addEventListener('pagehide', pageHide)
    return () => { document.removeEventListener('visibilitychange', visibility); window.removeEventListener('pagehide', pageHide); controller.dispose() }
  }, [controller])
  useEffect(() => {
    if (stage === 'awaiting_review') text.current?.focus({ preventScroll: true })
    if (stage === 'awaiting_confirmation') heading.current?.focus({ preventScroll: true })
    if (wasActive.current && !call.active) start.current?.focus({ preventScroll: true })
    wasActive.current = call.active
  }, [stage, call.active])
  useEffect(() => {
    setPlaybackFailed(false)
    const element = audio.current
    return () => { element?.pause(); element?.removeAttribute('src'); element?.load() }
  }, [call.clip?.url])
  const unavailable = recordingUnavailable()
  const status = call.microphoneActive && call.calibration ? 'Calibrating room noise…' : callLabels[call.state]
  const action = resolveDemoAction(call.draft)
  const minutes = Math.floor(call.seconds / 60).toString().padStart(2, '0')
  const seconds = (call.seconds % 60).toString().padStart(2, '0')
  const canFinish = call.microphoneActive && !call.muted && ['listening', 'speech_detected'].includes(stage)
  const needsText = stage === 'awaiting_review' || stage === 'error'
  const latest = call.history.at(-1)
  return <section className={`call-screen ${call.active ? 'is-active' : 'is-precall'}`} aria-label="ClawMetry conversation" data-call-state={call.state} data-content-state={stage}>
    <div className="call-top"><span className="demo-pill">Demo mode</span>{call.active && <span className="conversation-time" role="timer" aria-live="off" aria-label="Conversation duration">{minutes}:{seconds}</span>}</div>
    {!call.active ? <>
      <div className="precall-hero"><CallVisualizer state={call.state} active={false} level={0} levels={call.levels}/>
        <h1 ref={heading} tabIndex={-1}>Talk to ClawMetry</h1><p className="assistant-ready">Your robot assistant is ready.</p><p className="visitor-greeting">Nice to meet you, {nickname}!</p>
        <button ref={start} type="button" className="start-call" disabled={!!unavailable || call.pendingPermission} onClick={controller.start}><span className="start-call-disc"><Phone/></span><span>Start Conversation</span></button>
        <p className="call-truth">Local demo · no real robot connection</p>
        {call.state === 'ended' && <p role="status" className="call-notice">{call.message}</p>}
        {unavailable && <p className="call-notice">{unavailable}</p>}
        {call.pendingPermission && <p className="call-notice">An earlier browser permission prompt is still pending. Dismiss it or choose a permission before starting again.</p>}
        <details className="call-alternative"><summary>Need another way to communicate?</summary><button type="button" className="call-text-link" onClick={controller.writeInstead}>Write a request instead</button></details>
      </div>
      <div className="call-session-links"><a href="/privacy" onClick={onPrivacy}>Privacy information</a><button type="button" onClick={onLeave}>Leave session</button></div>
    </> : <>
      <div className="call-content">
        <span className="sr-only">Nice to meet you, {nickname}!</span>
        <h1 className="sr-only">Talk to ClawMetry</h1>
        <CallVisualizer state={call.state} active={call.microphoneActive} level={call.level} levels={call.levels}/>
        <div className="call-status" role="status" aria-live="polite" aria-atomic="true"><h2>{status}</h2><p>{call.microphoneActive ? call.automaticDetection ? 'Microphone on · local speech-turn detection' : 'Microphone on · use Finish turn; automatic detection unavailable' : 'Microphone off'}</p></div>
        <p className="call-truth">Local demo · no real robot connection</p>
        {canFinish && <button type="button" className="finish-turn" onClick={() => controller.finishTurn(true)}>Finish turn</button>}
        {stage === 'awaiting_transcription' && call.clip && <div className="call-context" aria-label="Transcription authorization">
          <h3>Your speech turn is captured</h3><p>Audio stays here until you authorize this turn’s upload to <code>{controller.transcription.endpoint}</code>. No robot task is sent.</p>
          <details className="call-playback"><summary>Review audio before sending</summary><audio ref={audio} aria-label="Play captured turn" controls controlsList="nodownload" preload="metadata" src={call.clip.url} onError={() => setPlaybackFailed(true)}/>{playbackFailed && <p role="alert">Playback unavailable. You can write instead or try another voice turn.</p>}</details>
          <button type="button" className="call-action" disabled={controller.uploading} onClick={() => void controller.authorizeUpload()}>{controller.uploading ? 'Transcribing…' : 'Send audio for transcription'}</button>
          <button type="button" className="call-text-link" onClick={controller.writeInstead}>Write instead and clear audio</button>
        </div>}
        {needsText && <form className="call-context call-editor" onSubmit={event => { event.preventDefault(); if (stage === 'error') controller.writeInstead(); controller.review() }}>
          <h3>{stage === 'error' ? 'We can still talk this way' : call.source === 'transcript' ? 'Check your transcript' : 'Speech recognition unavailable'}</h3>
          <p>{call.source === 'transcript' ? 'Edit the actual returned transcript before review.' : 'Transcription not connected or unavailable. Write your request; nothing is submitted automatically.'}</p>
          <label htmlFor="call-request">Your request</label><textarea ref={text} id="call-request" rows={2} maxLength={MAX_REQUEST_LENGTH} autoComplete="off" value={call.draft} onChange={event => controller.editDraft(event.target.value)} aria-describedby="call-request-help"/>
          <p id="call-request-help" className="call-fineprint">Up to 2000 characters. Review and confirmation come next.</p>
          <button type="submit" className="call-action">Review request</button>
        </form>}
        {stage === 'awaiting_confirmation' && <div className="call-context call-confirmation">
          <h3 ref={heading} tabIndex={-1}>Confirm your request</h3><blockquote>{call.draft}</blockquote>
          <p>{action && <code>{action} · </code>}{action ? actionDescriptions[action] : 'Unsupported in this demo. Only Hello, Robot info, and Help are allowed.'}</p>
          <p className="call-fineprint">Confirmation submits to a local simulation only. It is not real robot authorization.</p>
          <button type="button" className="call-action" onClick={() => controller.confirm(scenario)}>Confirm demo request</button>
          <div className="call-context-actions"><button type="button" onClick={controller.edit}>Edit request</button><button type="button" onClick={controller.cancelRequest}>Cancel request</button></div>
          <details className="call-demo-options"><summary>Try a demo outcome</summary><label htmlFor="call-scenario">Simulated outcome</label><select id="call-scenario" value={scenario} onChange={event => setScenario(event.target.value as DemoScenario)}>{DEMO_SCENARIOS.map(value => <option key={value} value={value}>{value.replaceAll('_', ' ')}</option>)}</select></details>
        </div>}
        {stage === 'processing' && <div className="call-context"><span className="demo-pill">Simulated task</span><p>{call.message}</p>{call.task && <p className="task-current">{call.task.status.replaceAll('_', ' ')}</p>}<p className="call-fineprint">Approval and execution are simulated. No physical action is being sent.</p></div>}
        {call.message && stage !== 'processing' && stage !== 'responding' && <p className="call-notice" role={stage === 'error' ? 'alert' : undefined}>{call.message}</p>}
        {latest && <div className="call-history" aria-label="Conversation history">
          <div className="history-user"><span>You · {latest.status.replaceAll('_', ' ')}</span><p>{latest.text}</p></div>
          {latest.reply && <div className="history-assistant"><span>ClawMetry · Demo text response</span><p>{latest.reply}</p></div>}
          {call.history.length > 1 && <details><summary>Earlier turns ({call.history.length - 1})</summary>{call.history.slice(0, -1).map(turn => <div key={turn.id} className="earlier-turn"><p>You: {turn.text}</p><p>Demo: {turn.reply || turn.status}</p></div>)}</details>}
        </div>}
        {['responding', 'awaiting_review', 'awaiting_transcription', 'error'].includes(stage) && <button type="button" className="call-text-link" disabled={call.pendingPermission} onClick={controller.nextTurn}>Another voice turn</button>}
        {['listening', 'speech_detected', 'requesting_permission'].includes(stage) && <details className="call-alternative"><summary>Need another way?</summary><button type="button" className="call-text-link" onClick={controller.writeInstead}>Write a request instead</button></details>}
      </div>
      <div className="persistent-call-controls">
        <div className="call-control-pair" role="group" aria-label="Call controls"><button type="button" className="circle-control mute-control" aria-pressed={call.muted} disabled={call.muted && call.pendingPermission} onClick={() => call.muted ? controller.unmute() : controller.mute()}><span><Mic muted={call.muted}/></span>{call.muted ? 'Unmute' : 'Mute'}</button><button type="button" className="circle-control end-control" onClick={controller.end}><span><Phone end/></span>End Call</button></div>
        <div className="call-session-links"><a href="/privacy" onClick={onPrivacy}>Privacy information</a><button type="button" onClick={onLeave}>Leave session</button></div>
      </div>
    </>}
  </section>
}
