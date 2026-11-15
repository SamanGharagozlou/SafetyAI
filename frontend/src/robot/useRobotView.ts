import { useEffect, useRef, useState } from 'react'
import { fetchRobotView, RobotApiError } from './robotApi'
import type { RobotAuthorization, RobotLine, RobotView } from './robotApi'

const MOVING = ['NAVIGATING', 'PREPARING', 'RETURNING_HOME']

/** Polls GET /api/state: where the robot is, progress, and what the robot says. 0.6 s while moving, 1 s otherwise. Memory only. */
export function useRobotView(auth: RobotAuthorization, onExpired: () => void) {
  const [view, setView] = useState<RobotView | null>(null)
  const [lines, setLines] = useState<readonly RobotLine[]>([])
  const [offline, setOffline] = useState(false)
  const expired = useRef(onExpired)
  expired.current = onExpired
  useEffect(() => {
    const abort = new AbortController()
    let timer: ReturnType<typeof setTimeout> | undefined
    let live = true
    let seq = 0
    let failures = 0
    let moving = false
    const tick = async () => {
      try {
        const next = await fetchRobotView(auth, seq, abort.signal)
        if (!live) return
        seq = next.seq
        failures = 0
        moving = MOVING.includes(next.state)
        setOffline(false)
        setView(next)
        if (next.lines.length) setLines(previous => [...previous, ...next.lines].slice(-30))
      } catch (error) {
        if (!live || abort.signal.aborted) return
        if (error instanceof RobotApiError && error.code === 'session_expired') { expired.current(); return }
        if (++failures >= 3) setOffline(true)
      }
      if (live) timer = setTimeout(tick, moving ? 600 : 1000)
    }
    void tick()
    return () => { live = false; abort.abort(); clearTimeout(timer) }
  }, [auth])
  return { view, lines, offline }
}
