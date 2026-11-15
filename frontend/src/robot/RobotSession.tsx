import { useMemo, useRef, useState } from 'react'
import type { FormEvent, MouseEvent, ReactNode } from 'react'
import { Robot } from '../Artwork'
import type { TaskSession } from '../tasks/contracts'
import { EscortBar } from './EscortBar'
import { SendIcon, SparkIcon } from './icons'
import { PlaceCards } from './PlaceCards'
import { PlacesMap } from './PlacesMap'
import { RobotRequestFlow } from './RobotRequestFlow'
import { stopRobot } from './robotApi'
import type { RobotSessionInfo } from './robotApi'
import { createRobotTaskClient, MAX_ROBOT_TEXT } from './robotTasks'
import type { RobotAction } from './robotTasks'
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
const QUICK = ['What can you do?', 'Take me home']

export function RobotSession({ info, session, nickname, talk, onExpired, onLeave, onPrivacy }: Props) {
  const { view, lines, offline } = useRobotView(info.authorization, onExpired)
  const client = useMemo(() => createRobotTaskClient(() => info.authorization), [info.authorization])
  const [tab, setTab] = useState<'places' | 'talk'>('places')
  const [pending, setPending] = useState<Pending | null>(null)
  const [draft, setDraft] = useState('')
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
  function ask(text: string) {
    setPending({ key: ++counter.current, text, proposedAction: null })
  }
  function send(event: FormEvent) {
    event.preventDefault()
    const text = draft.trim()
    if (!text) return
    ask(text)
    setDraft('')
  }
  async function stop() {
    setStopping(true)
    setStopFailed(false)
    try { await stopRobot(info.authorization) } catch { setStopFailed(true) } finally { setStopping(false) }
  }

  const places = <>
    <div className="robot-hello">
      <div className="hello-avatar"><Robot small/><span className={`hello-status${blocked ? ' is-off' : ''}`} aria-hidden="true"/></div>
      <p className="robot-says" aria-live="polite">{last ?? `Hi ${nickname}! Where would you like to go?`}</p>
    </div>

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

    <div className="quick-row" role="group" aria-label="Quick questions">
      {QUICK.map(q => <button key={q} type="button" className="quick-chip" onClick={() => ask(q)}><SparkIcon/>{q}</button>)}
    </div>

    {lines.length > 1 && <div className="call-history robot-history" aria-label="What the robot said" aria-live="off">
      {lines.slice(-4, -1).map(line => <div key={line.seq} className="history-assistant"><span>ClawMetry</span><p>{line.text}</p></div>)}
    </div>}

    {pending && <RobotRequestFlow key={pending.key} text={pending.text} source="typed" proposedAction={pending.proposedAction} place={pending.place}
      placeLabel={pending.place ? label(pending.place) : undefined} meta={pending.place ? away(pending.place) : undefined} labelOf={label}
      session={session} client={client} onExpired={onExpired} onCancel={() => setPending(null)} onDone={() => setPending(null)}/>}
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
    {tab === 'places' && <form className="dock" onSubmit={send}>
      <label htmlFor="robot-text" className="sr-only">Or ask me something</label>
      <input id="robot-text" value={draft} onChange={event => setDraft(event.target.value)} maxLength={MAX_ROBOT_TEXT} placeholder="Ask me anything, or say where to go…" autoComplete="off"/>
      <button type="submit" className="dock-send" aria-label="Send message" disabled={!draft.trim()}><SendIcon/></button>
    </form>}
  </section>
}
