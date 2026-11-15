import { useEffect, useMemo, useRef, useState } from 'react'
import type { FormEvent, MouseEvent, ReactNode } from 'react'
import { Robot } from '../Artwork'
import type { TaskSession } from '../tasks/contracts'
import { EscortBar } from './EscortBar'
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
  const flowCard = useRef<HTMLDivElement>(null)
  const label = (name: string) => info.places.find(p => p.name === name)?.label ?? name

  // A new request opens its confirm card below the layout: bring it into view on a phone.
  useEffect(() => { if (pending) flowCard.current?.scrollIntoView?.({ behavior: 'smooth', block: 'center' }) }, [pending])

  const blocked = !view || view.state === 'HOLD' || !view.robot.available || offline
  const last = lines.at(-1)?.text

  function pick(name: string) {
    if (blocked) return
    setPending({ key: ++counter.current, text: `Take me to ${label(name)}`, proposedAction: 'GO_TO_PLACE', place: name })
  }
  function send(event: FormEvent) {
    event.preventDefault()
    const text = draft.trim()
    if (!text) return
    setPending({ key: ++counter.current, text, proposedAction: null })
    setDraft('')
  }
  async function stop() {
    setStopping(true)
    setStopFailed(false)
    try { await stopRobot(info.authorization) } catch { setStopFailed(true) } finally { setStopping(false) }
  }

  const places = <>
    <div className="robot-hello">
      <Robot small/>
      <p className="robot-says" aria-live="polite">{last ?? `Hi ${nickname}! Where would you like to go?`}</p>
    </div>

    <div className="places-card">
      <h2 id="places-heading" ref={heading} tabIndex={-1}>Where to?</h2>
      <p className="field-help">Tap a place. The dots show where the robot can take you.</p>
      <PlacesMap places={info.places} view={view} selected={pending?.place ?? null} disabled={blocked} onSelect={pick}/>
      <div className="places-legend" aria-hidden="true"><span><i className="dot-place"/>place</span><span><i className="dot-target"/>heading there</span><span><i className="dot-here"/>robot is here</span></div>
      {stopFailed && <p className="form-error" role="alert">I could not confirm the stop. Please tell a staff member.</p>}
      {blocked && view && <p className="field-help" role="status">{view.state === 'HOLD' ? (view.reason || 'The robot is paused.') : 'The robot is not available right now.'}</p>}
    </div>

    {pending && <div ref={flowCard} className="places-card robot-flow-card">
      <RobotRequestFlow key={pending.key} text={pending.text} source="typed" proposedAction={pending.proposedAction} place={pending.place}
        placeLabel={pending.place ? label(pending.place) : undefined} labelOf={label} session={session} client={client} onExpired={onExpired}
        onCancel={() => setPending(null)} onDone={() => setPending(null)}/>
    </div>}

    <div className="place-chips" role="group" aria-label="Places">
      {info.places.map(p => <button key={p.name} type="button" className={`place-chip${view?.at === p.name ? ' is-here' : ''}${view?.destination === p.name ? ' is-target' : ''}`}
        disabled={blocked} onClick={() => pick(p.name)}>{p.label}</button>)}
    </div>

    <form className="robot-composer" onSubmit={send}>
      <label htmlFor="robot-text">Or ask me something</label>
      <div className="composer-row">
        <input id="robot-text" value={draft} onChange={event => setDraft(event.target.value)} maxLength={MAX_ROBOT_TEXT} placeholder="Where is the sauna? Take me to the left…" autoComplete="off"/>
        <button type="submit" className="composer-send" disabled={!draft.trim()}>Send</button>
      </div>
    </form>

    {lines.length > 1 && <div className="call-history robot-history" aria-label="What the robot said" aria-live="off">
      {lines.slice(-4).map(line => <div key={line.seq} className="history-assistant"><span>ClawMetry</span><p>{line.text}</p></div>)}
    </div>}
  </>

  return <section className="call-screen robot-screen page-enter" aria-label={`${info.robotName} companion`}>
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
