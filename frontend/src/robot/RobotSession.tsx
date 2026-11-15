import { useEffect, useMemo, useRef, useState } from 'react'
import type { FormEvent, MouseEvent, ReactNode } from 'react'
import type { TaskSession } from '../tasks/contracts'
import { EscortBar } from './EscortBar'
import { KeyboardIcon, MicIcon, SendIcon, VolumeIcon } from './icons'
import { PlaceCards } from './PlaceCards'
import { PlacesMap } from './PlacesMap'
import { RobotRequestFlow } from './RobotRequestFlow'
import { stopRobot } from './robotApi'
import type { RobotSessionInfo } from './robotApi'
import { createRobotTaskClient, MAX_ROBOT_TEXT } from './robotTasks'
import type { RobotAction } from './robotTasks'
import { useConversation } from './useConversation'
import { useRobotView } from './useRobotView'

interface Pending { key: number; text: string; proposedAction: RobotAction | null; place?: string }
interface Props {
  info: RobotSessionInfo
  session: TaskSession
  nickname: string
  /** The live voice call, when configured. It stays mounted while the guest looks at the places, so the call is not dropped. */
  talk?: ReactNode
  onExpired(): void
  onLeave(): void
  onPrivacy(event: MouseEvent<HTMLAnchorElement>): void
}

export function RobotSession({ info, session, nickname, talk, onExpired, onLeave, onPrivacy }: Props) {
  const { view, lines, offline } = useRobotView(info.authorization, onExpired)
  const client = useMemo(() => createRobotTaskClient(() => info.authorization), [info.authorization])
  const [tab, setTab] = useState<'places' | 'talk'>('places')
  const [pending, setPending] = useState<Pending | null>(null)
  const [draft, setDraft] = useState('')
  const [typing, setTyping] = useState(false)       // typing is optional: the conversation is by voice or taps
  const [showPlaces, setShowPlaces] = useState(false)
  const [yes, setYes] = useState(0)
  const [stopping, setStopping] = useState(false)
  const [stopFailed, setStopFailed] = useState(false)
  const counter = useRef(0)
  const heading = useRef<HTMLHeadingElement>(null)
  const label = (name: string) => info.places.find(p => p.name === name)?.label ?? name

  const offair = !view || view.state === 'HOLD' || !view.robot.available || offline      // cannot be reached or is paused
  const notReady = !offair && !view.ready                                              // reachable, but cannot start a walk right now
  const blocked = offair || notReady
  const last = lines.at(-1)?.text
  const from = view?.robot.x != null && view.robot.y != null ? { x: view.robot.x, y: view.robot.y } : null
  const away = (name: string) => {
    const p = info.places.find(q => q.name === name)
    return p && from ? `${Math.hypot(p.x - from.x, p.y - from.y).toFixed(1)} m from here` : undefined
  }

  function pick(name: string) {
    if (blocked) return
    setPending({ key: ++counter.current, text: `Take me to ${label(name)}`, proposedAction: 'GO_TO_PLACE', place: name })
  }
  const talk_ = useConversation({ auth: info.authorization, voice: info.consent.voice, awaitingConfirm: !!pending?.place, lang: navigator.language || 'en',
    onPropose: name => { if (!blocked) setPending({ key: ++counter.current, text: `Take me to ${label(name)}`, proposedAction: 'GO_TO_PLACE', place: name }) },
    onYes: () => setYes(n => n + 1), onNo: () => setPending(null), onStop: () => { void stop() }, onFinish: onLeave, onExpired })
  // The guest agreed to voice by tapping the consent button, so greet aloud and then listen.
  const greeted = useRef(false)
  useEffect(() => {
    if (greeted.current || !info.consent.voice) return
    greeted.current = true
    talk_.enable()
    talk_.robotSays(`Hi ${nickname || 'there'}! Where would you like to go?`, true)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
  function send(event: FormEvent) {
    event.preventDefault()
    const text = draft.trim()
    if (!text) return
    talk_.submitText(text)
    setDraft('')
  }
  async function stop() {
    setStopping(true)
    setStopFailed(false)
    try { await stopRobot(info.authorization) } catch { setStopFailed(true) } finally { setStopping(false) }
  }

  const places = <>
    <div className="voice-card">
      <button type="button" className={`voice-orb is-${talk_.phase}`} disabled={talk_.state !== 'ready'} aria-label={talk_.phase === 'listening' ? 'Listening. Tap to finish' : 'Tap to talk'}
        onClick={() => talk_.phase === 'listening' ? talk_.stopListening() : talk_.begin()}><MicIcon/></button>
      <p className="voice-status" role="status">{talk_.phase === 'listening' ? 'Listening…' : talk_.phase === 'thinking' ? 'One moment…' : talk_.phase === 'speaking' ? 'Speaking…' : talk_.state === 'ready' ? 'Tap the microphone and talk to me' : 'Tap a place below, or type'}</p>
      {talk_.heard && <p className="voice-heard">“{talk_.heard}”</p>}
      <p className="robot-says" aria-live="polite">{talk_.said || last || `Hi ${nickname}! Where would you like to go?`}</p>
      {talk_.notice && <p className="stage-note" role="status">{talk_.notice}</p>}
      {talk_.state === 'unsupported' && <p className="stage-note" role="status">Voice needs a secure (HTTPS) page and a supported browser. You can tap a place or type.</p>}
      <div className="voice-tools">
        {talk_.state === 'ready' && <button type="button" className="quick-chip" onClick={() => talk_.setMuted(!talk_.muted)} aria-pressed={talk_.muted}><VolumeIcon muted={talk_.muted}/>{talk_.muted ? 'Voice off' : 'Voice on'}</button>}
        <button type="button" className="quick-chip" onClick={() => setTyping(t => !t)} aria-expanded={typing}><KeyboardIcon/>Type instead</button>
        <button type="button" className="quick-chip" onClick={() => setShowPlaces(v => !v)} aria-expanded={showPlaces}>{showPlaces ? 'Hide places' : 'Show places'}</button>
      </div>
      {typing && <form className="voice-type" onSubmit={send}>
        <label htmlFor="robot-text" className="sr-only">Type a message</label>
        <input id="robot-text" value={draft} onChange={event => setDraft(event.target.value)} maxLength={MAX_ROBOT_TEXT} placeholder="Say where you want to go…" autoComplete="off"/>
        <button type="submit" className="dock-send" aria-label="Send message" disabled={!draft.trim()}><SendIcon/></button>
      </form>}
    </div>

    {(showPlaces || talk_.state !== 'ready') && <>
    <div className="stage-card">
      <div className="stage-head">
        <div><span className="eyebrow-label">Destinations</span><h2 id="places-heading" ref={heading} tabIndex={-1}>Where to?</h2></div>
        <span className={`stage-pill${blocked ? ' is-off' : ''}`}><i/>{offair ? 'Unavailable' : notReady ? 'Not ready' : 'Robot ready'}</span>
      </div>
      {blocked && view && <p className="stage-note" role="status">{view.state === 'HOLD' ? (view.reason || 'The robot is paused.') : notReady ? `${view.notice || 'The robot cannot start a walk right now'}. A staff member can help.` : 'The robot is not available right now.'}</p>}
      <PlacesMap places={info.places} view={view} selected={pending?.place ?? null} disabled={blocked} onSelect={pick}/>
      <div className="places-legend" aria-hidden="true"><span><i className="dot-place"/>place</span><span><i className="dot-target"/>heading there</span><span><i className="dot-here"/>robot is here</span></div>
      {stopFailed && <p className="form-error" role="alert">I could not confirm the stop. Please tell a staff member.</p>}
    </div>

    <PlaceCards places={info.places} view={view} disabled={blocked} selected={pending?.place ?? null} onSelect={pick}/>

    </>}

    {pending && <RobotRequestFlow key={pending.key} text={pending.text} source="typed" proposedAction={pending.proposedAction} place={pending.place}
      placeLabel={pending.place ? label(pending.place) : undefined} meta={pending.place ? away(pending.place) : undefined} labelOf={label}
      confirmSignal={yes} session={session} client={client} onExpired={onExpired} onCancel={() => setPending(null)} onDone={() => setPending(null)}/>}
  </>

  return <section className="robot-screen page-enter" aria-label={`${info.robotName} companion`}>
    <div className="call-top"><span className="demo-pill">Live robot</span>
      {talk && <div className="robot-tabs" role="tablist" aria-label="View">
        <button type="button" role="tab" id="tab-places" aria-selected={tab === 'places'} aria-controls="panel-places" onClick={() => setTab('places')}>Places</button>
        <button type="button" role="tab" id="tab-talk" aria-selected={tab === 'talk'} aria-controls="panel-talk" onClick={() => setTab('talk')}>Talk</button>
      </div>}
    </div>
    <EscortBar view={view} places={info.places} stopping={stopping} offline={offline} onStop={() => void stop()}/>
    <div id="panel-places" role="tabpanel" aria-labelledby="tab-places" className="robot-panel" hidden={tab !== 'places'}>{places}</div>
    {talk && <div id="panel-talk" role="tabpanel" aria-labelledby="tab-talk" className="robot-panel" hidden={tab !== 'talk'}>{talk}</div>}
    <div className="call-session-links"><a href="/privacy" onClick={onPrivacy}>Privacy information</a><button type="button" onClick={onLeave}>Leave session</button></div>
  </section>
}
