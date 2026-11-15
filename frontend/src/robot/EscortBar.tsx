import type { RobotPlace, RobotView } from './robotApi'

interface Props { view: RobotView | null; places: readonly RobotPlace[]; stopping: boolean; offline: boolean; onStop(): void }

/** Always visible while the robot moves or is paused: what is happening, how far, and Stop. Stop never waits for a confirmation. */
export function EscortBar({ view, places, stopping, offline, onStop }: Props) {
  if (offline) return <div className="escort-bar escort-warn" role="status"><span className="escort-text"><strong>Connection lost</strong><span>Trying to reach the robot again…</span></span></div>
  if (!view) return null
  const label = (name: string | null) => places.find(p => p.name === name)?.label ?? 'your destination'
  const moving = view.state === 'NAVIGATING' || view.state === 'PREPARING'
  if (!moving && view.state !== 'HOLD') return null
  const d = view.distanceRemaining
  const pct = d == null || view.state !== 'NAVIGATING' ? 6 : Math.max(6, Math.min(100, 100 - d * 25))
  return <div className={`escort-bar${view.state === 'HOLD' ? ' escort-warn' : ''}`} role="status" aria-live="polite">
    <span className="escort-text">
      {view.state === 'HOLD'
        ? <><strong>Paused</strong><span>{view.reason || 'The robot cannot move right now.'}</span></>
        : view.state === 'PREPARING'
          ? <><strong>Getting ready</strong><span>One moment, the robot is getting up.</span></>
          : <><strong>On the way to {label(view.destination)}</strong><span>{d == null ? 'Starting…' : `${d.toFixed(1)} m to go`}</span></>}
    </span>
    {moving && <>
      <button type="button" className="escort-stop" onClick={onStop} disabled={stopping}>{stopping ? 'Stopping…' : 'Stop'}</button>
      <svg className="escort-progress" viewBox="0 0 100 4" preserveAspectRatio="none" aria-hidden="true"><rect className="track" width="100" height="4" rx="2"/><rect className="fill" width={pct} height="4" rx="2"/></svg>
    </>}
  </div>
}
