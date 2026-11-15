import { useEffect, useRef, useState } from 'react'
import { requireActiveSession, TaskServiceError } from '../tasks/contracts'
import type { RequestSource, TaskSession, TaskStatus } from '../tasks/contracts'
import { ChatIcon, PinIcon, WarnIcon } from './icons'
import { Sheet } from './Sheet'
import { isRobotTerminal, newRobotRequestId } from './robotTasks'
import type { RobotAction, RobotTask, RobotTaskClient } from './robotTasks'

/** An escort can take minutes (Nav2 gives up after 180 s): wait much longer than the simulated demo's 12 s. */
export const ROBOT_TASK_WAIT_MS = 240_000
const POLL_MS = 600

const copy = (status: TaskStatus, escort: boolean, place: string): { title: string; line: string } => {
  switch (status) {
    case 'queued': return { title: 'Request received', line: 'The robot has your request.' }
    case 'processing': return escort ? { title: 'Getting ready', line: `Checking the way to ${place}…` } : { title: 'One moment', line: 'Thinking about your question…' }
    case 'awaiting_approval': return { title: 'Waiting for approval', line: 'A staff member needs to approve this first.' }
    case 'executing': return { title: 'On the way', line: `Follow me to ${place}. I will tell you when we arrive.` }
    case 'completed': return escort ? { title: 'We have arrived', line: '' } : { title: 'Here you go', line: '' }
    case 'denied': return { title: 'I can’t help with that', line: '' }
    case 'failed': return { title: 'I couldn’t finish that', line: '' }
  }
}
const reasonLine = (task: RobotTask) =>
  task.message || (task.reason === 'robot_unavailable' ? 'The robot is not available right now. A staff member can help.'
    : task.reason === 'unsupported_request' ? 'Try asking where something is, or tap a place on the layout.'
      : task.reason === 'execution_failed' ? 'Something went wrong on the way. You can try again.' : '')

interface Props {
  text: string
  source: RequestSource
  proposedAction: RobotAction | null
  place?: string
  placeLabel?: string
  /** Shown under the question, e.g. "2.0 m from here". */
  meta?: string
  /** Label of a place by name, so a typed request the backend turned into an escort still names its destination. */
  labelOf?: (name: string) => string
  session: TaskSession
  client: RobotTaskClient
  onExpired(): void
  onCancel(): void
  onDone(): void
  waitMs?: number
}

function CheckBadge() {
  return <svg className="check-draw" viewBox="0 0 52 52" aria-hidden="true"><circle className="check-circle" cx="26" cy="26" r="23"/><path className="check-mark" d="m15.500 27 7 7 14-15.500"/></svg>
}

/** Confirm (a bottom sheet), follow the request (a quiet strip while the journey card shows progress), then the result (a sheet). Same logic as tasks/RequestFlow.tsx. */
export function RobotRequestFlow({ text, source, proposedAction, place, placeLabel, meta, labelOf, session, client, onExpired, onCancel, onDone, waitMs = ROBOT_TASK_WAIT_MS }: Props) {
  const [stage, setStage] = useState<'confirmation' | 'task'>('confirmation')
  const [task, setTask] = useState<RobotTask | null>(null)
  const [issue, setIssue] = useState<'timeout' | 'unavailable' | 'invalid' | 'busy' | null>(null)
  const title = useRef<HTMLHeadingElement>(null)
  const controller = useRef<AbortController | null>(null)
  const pollTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  const deadline = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  const attempt = useRef(0)
  const submitted = useRef(false)
  const latest = useRef(-1)
  // The backend decides what a request means: a typed "take me home" becomes an escort even though nothing was proposed.
  const escort = proposedAction === 'GO_TO_PLACE' || task?.action === 'GO_TO_PLACE'
  const target = (task?.place && labelOf ? labelOf(task.place) : placeLabel) ?? 'your destination'

  function halt() {
    attempt.current++
    controller.current?.abort()
    clearTimeout(pollTimer.current)
    clearTimeout(deadline.current)
  }
  useEffect(() => () => halt(), [])
  useEffect(() => { title.current?.focus({ preventScroll: true }) }, [stage, task?.status, issue])

  const alive = () => { try { requireActiveSession(session); return true } catch { halt(); onExpired(); return false } }

  function confirm() {
    if (submitted.current || !alive()) return   // the ref lock stops a second POST even before React disables the button
    submitted.current = true
    setStage('task')
    const version = ++attempt.current
    const abort = new AbortController()
    controller.current = abort
    const clientRequestId = newRobotRequestId()
    const until = Date.now() + waitMs
    deadline.current = setTimeout(() => { if (version === attempt.current) { halt(); setIssue('timeout') } }, waitMs)
    const current = () => {
      if (version !== attempt.current || abort.signal.aborted || !alive()) return false
      if (Date.now() >= until) { halt(); setIssue('timeout'); return false }
      return true
    }
    const failed = (error: unknown) => {
      if (!current()) return
      if (error instanceof TaskServiceError && error.code === 'session_expired') { halt(); onExpired(); return }
      halt()
      setIssue(error instanceof TaskServiceError && error.code === 'invalid_response' ? 'invalid' : error instanceof TaskServiceError && error.code === 'conflict' ? 'busy' : 'unavailable')
    }
    const accept = (snapshot: RobotTask, id?: string) => {
      if (!current()) return
      if (snapshot.clientRequestId !== clientRequestId || (id && snapshot.requestId !== id)) { failed(new TaskServiceError('invalid_response')); return }
      if (snapshot.revision >= latest.current) { latest.current = snapshot.revision; setTask(snapshot) }
      if (isRobotTerminal(snapshot.status)) { clearTimeout(deadline.current); return }
      pollTimer.current = setTimeout(() => {
        if (!current()) return
        void client.status(id ?? snapshot.requestId, abort.signal).then(next => accept(next, id ?? snapshot.requestId)).catch(failed)
      }, POLL_MS)
    }
    void Promise.resolve().then(() => current() ? client.submit({ text, source, proposedAction, place, clientRequestId }, abort.signal) : null)
      .then(snapshot => { if (snapshot) accept(snapshot) }).catch(failed)
  }

  if (stage === 'confirmation') {
    return <Sheet labelledBy="flow-title" onClose={onCancel}>
      <span className="sheet-icon" aria-hidden="true">{escort ? <PinIcon/> : <ChatIcon/>}</span>
      <h3 id="flow-title" ref={title} tabIndex={-1}>{escort ? `Go to ${target}?` : 'Your request is ready'}</h3>
      {escort && meta && <p className="sheet-sub">{meta}</p>}
      {!escort && <blockquote className="sheet-quote">{text}</blockquote>}
      <p className="sheet-note">{escort ? 'I will lead you there at walking pace. You can stop me at any time with the Stop button.' : 'I will answer you. Nothing moves unless you ask to be taken somewhere.'}</p>
      <button type="button" className="sheet-primary" onClick={confirm}>{escort ? 'Yes, take me there' : 'Send'}</button>
      <button type="button" className="sheet-secondary" onClick={onCancel}>Not now</button>
    </Sheet>
  }

  const status = task ? copy(task.status, escort, target) : { title: 'Sending…', line: 'Telling the robot.' }
  const terminal = task ? isRobotTerminal(task.status) : false
  const failure = !issue && task && (task.status === 'denied' || task.status === 'failed') ? reasonLine(task) : ''
  const ok = !issue && task?.status === 'completed'
  const distance = task?.status === 'executing' ? task.progress?.distanceRemaining : null

  if (!terminal && !issue) {
    return <div className="flow-strip" role="status" aria-live="polite" aria-atomic="true">
      <span className="strip-spinner" aria-hidden="true"/>
      <div><h3 ref={title} tabIndex={-1}>{status.title}</h3><p>{distance != null ? `${distance.toFixed(1)} m to go` : status.line}</p></div>
    </div>
  }
  const heading = issue === 'timeout' ? 'This is taking longer than expected' : issue === 'invalid' ? 'I got an answer I could not trust' : issue === 'busy' ? 'That request clashed with another' : issue === 'unavailable' ? 'I can’t reach the robot' : status.title
  const body = issue === 'timeout' ? 'I stopped waiting. Check where the robot is, or press Stop. Nothing is claimed as done.' : issue === 'invalid' ? 'Please try again, or ask a staff member.'
    : issue === 'unavailable' ? 'Please try again in a moment, or ask a staff member.' : issue === 'busy' ? 'Please try again.' : failure
  return <Sheet labelledBy="flow-title" onClose={() => { halt(); onDone() }}>
    <span className={`sheet-badge ${ok ? 'is-ok' : 'is-warn'}`} aria-hidden="true">{ok ? <CheckBadge/> : <WarnIcon/>}</span>
    <div role={issue ? 'alert' : 'status'} aria-atomic="true">
      <h3 id="flow-title" ref={title} tabIndex={-1}>{heading}</h3>
      {ok && escort && <p className="sheet-sub">{target}</p>}
      {ok && task?.reply && <p className="sheet-reply">{task.reply}</p>}
      {body && <p className="sheet-note">{body}</p>}
    </div>
    <button type="button" className="sheet-primary" onClick={() => { halt(); onDone() }}>{ok && escort ? 'Where next?' : 'Close'}</button>
  </Sheet>
}
