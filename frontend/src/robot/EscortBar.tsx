import { StopIcon } from './icons'
import type { RobotPlace, RobotView } from './robotApi'

interface Props { view: RobotView | null; places: readonly RobotPlace[]; stopping: boolean; offline: boolean; onStop(): void }
const X0 = 16
const X1 = 224

/** The live journey card: always visible while the robot moves or is paused. Stop never waits for a confirmation. */
export function EscortBar({ view, places, stopping, offline, onStop }: Props) {
  if (offline) return <div className="journey journey-warn" role="status"><div className="journey-text"><span className="journey-eyebrow">Offline</span><strong>Connection lost</strong><span className="journey-meta">Trying to reach the robot again…</span></div></div>
  if (!view) return null
  const label = (name: string | null) => places.find(p => p.name === name)?.label ?? 'your destination'
  const moving = view.state === 'NAVIGATING' || view.state === 'PREPARING'
  if (!moving && view.state !== 'HOLD') return null
  const d = view.distanceRemaining
  const pct = d == null || view.state !== 'NAVIGATING' ? 4 : Math.max(4, Math.min(96, 100 - d * 22))
  const x = X0 + ((X1 - X0) * pct) / 100
  return <div className={`journey${view.state === 'HOLD' ? ' journey-warn' : ''}`} role="status" aria-live="polite">
    <div className="journey-text">
      <span className="journey-eyebrow">{view.state === 'HOLD' ? 'Paused' : 'Live'}</span>
      {view.state === 'HOLD'
        ? <><strong>The robot is paused</strong><span className="journey-meta">{view.reason || 'It cannot move right now.'}</span></>
        : view.state === 'PREPARING'
          ? <><strong>Getting ready</strong><span className="journey-meta">One moment, the robot is getting up.</span></>
          : <><strong>On the way to {label(view.destination)}</strong><span className="journey-meta">{d == null ? 'Starting…' : `${d.toFixed(1)} m to go`}</span></>}
    </div>
    {moving && <button type="button" className="journey-stop" onClick={onStop} disabled={stopping}><StopIcon/>{stopping ? 'Stopping…' : 'Stop'}</button>}
    {moving && <svg className="journey-track" viewBox="0 0 240 30" aria-hidden="true">
      <line className="track-base" x1={X0} y1="15" x2={X1} y2="15"/>
      <line className="track-done" x1={X0} y1="15" x2={x} y2="15"/>
      <circle className="track-start" cx={X0} cy="15" r="4"/>
      <path className="track-pin" d={`M${X1} 22c-5-4-7-7.5-7-10.5a7 7 0 0 1 14 0c0 3-2 6.500-7 10.500Z`}/>
      <circle className="track-pin-dot" cx={X1} cy="11.5" r="2.4"/>
      <g className="track-robot" transform={`translate(${x} 15)`}><circle r="9"/><rect x="-4.6" y="-3.6" width="9.2" height="6.4" rx="2.2"/><circle className="eye" cx="-1.7" cy="-.5" r=".9"/><circle className="eye" cx="1.7" cy="-.5" r=".9"/></g>
    </svg>}
  </div>
}
