import { useCallback, useEffect, useRef, useState } from 'react'
import type { MouseEvent } from 'react'
import { Robot } from '../Artwork'
import type { TaskSession } from '../tasks/contracts'
import { requireActiveSession } from '../tasks/contracts'
import { VapiSession } from '../voice/vapiSession'
import type { VapiConfig, VapiEvents, VapiRole, VapiStatus } from '../voice/vapiSession'
import { CallVisualizer } from './CallVisualizer'
import type { CallState } from './state'

const LEVELS = 16
const MAX_LINES = 12

interface Line { id: number; role: VapiRole; text: string }

/** Status words that stay true: nothing here claims a robot moved. */
const statusLabel: Record<VapiStatus, string> = {
  idle: 'Ready to start',
  connecting: 'Connecting…',
  live: 'Listening — just talk',
  ended: 'Conversation ended',
  error: 'Let’s try another way',
}

/** Reuses the existing visual states so the orb and waveform behave as before. */
const visualState: Record<VapiStatus, CallState> = {
  idle: 'idle', connecting: 'requesting_permission', live: 'listening',
  ended: 'ended', error: 'error',
}

export interface LiveCallScreenProps {
  config: VapiConfig
  session: TaskSession
  nickname: string
  onExpired(): void
  onLeave(): void
  onPrivacy(event: MouseEvent<HTMLAnchorElement>): void
}

export function LiveCallScreen({ config, session, nickname, onExpired, onLeave, onPrivacy }: LiveCallScreenProps) {
  const [status, setStatus] = useState<VapiStatus>('idle')
  const [detail, setDetail] = useState('')
  const [botSpeaking, setBotSpeaking] = useState(false)
  const [level, setLevel] = useState(0)
  const [levels, setLevels] = useState<readonly number[]>(() => Array(LEVELS).fill(0))
  const [lines, setLines] = useState<readonly Line[]>([])
  const [muted, setMuted] = useState(false)
  const [seconds, setSeconds] = useState(0)
  // ONE VapiSession per call, created on the button press - never one per
  // component. StrictMode mounts, cleans up and remounts, so an instance held
  // in a ref initializer is already stopped by the time the user clicks.
  const sessionRef = useRef<VapiSession | null>(null)
  const heading = useRef<HTMLHeadingElement>(null)
  const start = useRef<HTMLButtonElement>(null)
  const active = status === 'connecting' || status === 'live'

  useEffect(() => { heading.current?.focus({ preventScroll: true }) }, [])

  const end = useCallback(() => {
    sessionRef.current?.stop()
    sessionRef.current = null
    setStatus(previous => (previous === 'error' ? previous : 'ended'))
    setLevel(0)
    setLevels(Array(LEVELS).fill(0))
    setBotSpeaking(false)
    setMuted(false)
  }, [])

  // The visitor session is still the authority on how long this may run.
  useEffect(() => {
    try { requireActiveSession(session) } catch { end(); onExpired() }
  }, [session.id, session.expiresAt, session, end, onExpired])

  useEffect(() => {
    if (!active) return
    const timer = setInterval(() => {
      try { requireActiveSession(session) } catch { end(); onExpired(); return }
      setSeconds(value => value + 1)
    }, 1000)
    return () => clearInterval(timer)
  }, [active, session.id, session.expiresAt, session, end, onExpired])

  // A hidden tab, a navigation away or an unmount must drop the call and the mic.
  useEffect(() => {
    const hide = () => { if (document.visibilityState === 'hidden') end() }
    const leave = () => sessionRef.current?.stop()
    document.addEventListener('visibilitychange', hide)
    window.addEventListener('pagehide', leave)
    return () => {
      document.removeEventListener('visibilitychange', hide)
      window.removeEventListener('pagehide', leave)
      // Read .current at cleanup time: a call started after this effect ran
      // must still be torn down.
      sessionRef.current?.stop()
      sessionRef.current = null
    }
  }, [end])

  useEffect(() => { if (status === 'ended' || status === 'error') start.current?.focus({ preventScroll: true }) }, [status])

  function begin() {
    try { requireActiveSession(session) } catch { end(); onExpired(); return }
    sessionRef.current?.stop()
    const events: VapiEvents = {
      onStatus: (next, why) => { setStatus(next); setDetail(why ?? '') },
      onLevel: value => { setLevel(value); setLevels(previous => [...previous.slice(1), value]) },
      onTranscript: (role, text) => setLines(previous => [...previous, { id: previous.length, role, text }].slice(-MAX_LINES)),
      onSpeaking: setBotSpeaking,
    }
    const live = new VapiSession(config, events)
    sessionRef.current = live
    setLines([])
    setDetail('')
    setSeconds(0)
    void live.start()
  }

  function toggleMute() {
    const next = !muted
    setMuted(next)
    sessionRef.current?.setMuted(next)
  }

  const minutes = Math.floor(seconds / 60).toString().padStart(2, '0')
  const clock = `${minutes}:${(seconds % 60).toString().padStart(2, '0')}`
  const spoken = status === 'live' && botSpeaking ? 'ClawMetry is speaking' : statusLabel[status]

  return <section className="call-screen page-enter" aria-labelledby="call-heading">
    {!active ? <div className="call-precall">
      <span className="demo-pill">Live voice</span>
      <div className="avatar-scene precall-scene"><div className="orbit orbit-one"/><Robot/></div>
      <h1 ref={heading} tabIndex={-1} id="call-heading">Talk to ClawMetry</h1>
      <p className="intro">
        {status === 'ended' ? 'That’s a wrap. Start another whenever you like.' : 'Your robot assistant is ready.'}
        {nickname && status === 'idle' && <> Nice to meet you, <strong>{nickname}</strong>!</>}
      </p>
      {detail && <p className="call-notice" role="alert">{detail}</p>}
      <button ref={start} type="button" className="primary-button" onClick={begin}>Start Conversation</button>
      <p className="call-fineprint">
        Speech is processed by Vapi, an external voice service, while the call is open.
        Audio is not recorded or stored by this page. The robot does not move in this build.
      </p>
      <div className="call-session-links"><a href="/privacy" onClick={onPrivacy}>Privacy information</a><button type="button" onClick={onLeave}>Leave session</button></div>
    </div> : <>
      <div className="call-stage">
        <div className="call-stage-top"><span className="demo-pill">Live voice</span><span className="call-timer">{clock}</span></div>
        <CallVisualizer state={visualState[status]} active={status === 'live' && !muted} level={muted ? 0 : level} levels={muted ? Array(LEVELS).fill(0) : levels}/>
        <h1 ref={heading} tabIndex={-1} id="call-heading">{spoken}</h1>
        <p className="call-substatus">{detail || (muted ? 'Microphone off' : status === 'live' ? 'No buttons. Interrupt whenever you like.' : 'Setting up the call…')}</p>
        {lines.length > 0 && <div className="call-history" aria-label="Conversation transcript" aria-live="polite">
          {lines.slice(-4).map(line => <div key={line.id} className={line.role === 'user' ? 'history-user' : 'history-assistant'}>
            <span>{line.role === 'user' ? 'You' : 'ClawMetry'}</span><p>{line.text}</p>
          </div>)}
        </div>}
      </div>
      <div className="persistent-call-controls">
        <div className="call-control-pair" role="group" aria-label="Call controls">
          <button type="button" className="circle-control mute-control" aria-pressed={muted} onClick={toggleMute}>
            <span><svg width="27" height="27" viewBox="0 0 24 24" fill="none" aria-hidden="true"><rect x="9" y="2" width="6" height="12" rx="3" stroke="currentColor" strokeWidth="1.7"/><path d="M5 10v2a7 7 0 0 0 14 0v-2M12 19v3m-4 0h8" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round"/>{muted && <path d="m3 3 18 18" stroke="currentColor" strokeWidth="2"/>}</svg></span>
            {muted ? 'Unmute' : 'Mute'}
          </button>
          <button type="button" className="circle-control end-control" onClick={end}>
            <span><svg width="29" height="29" viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M3 14c4-6 14-6 18 0l-3 4-4-2v-3h-4v3l-4 2-3-4Z" stroke="currentColor" strokeWidth="1.7" strokeLinejoin="round"/></svg></span>
            End Call
          </button>
        </div>
        <div className="call-session-links"><a href="/privacy" onClick={onPrivacy}>Privacy information</a><button type="button" onClick={onLeave}>Leave session</button></div>
      </div>
    </>}
  </section>
}
