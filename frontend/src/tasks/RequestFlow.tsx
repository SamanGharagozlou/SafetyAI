import { useEffect, useRef, useState } from 'react'
import { isTerminal, randomRequestId, requireActiveSession, TaskServiceError } from './contracts'
import type { RequestSource, TaskSession, TaskSnapshot, TaskStatus } from './contracts'
import { createMockTaskAdapter } from './mockTaskAdapter'
import type { DemoScenario, DemoTaskAdapter } from './mockTaskAdapter'
import { actionDescriptions, resolveDemoAction } from './vocabulary'

export const TASK_WAIT_MS = 12_000
export const statusCopy: Record<TaskStatus, { title: string; explanation: string }> = {
  queued: { title: 'Demo request queued', explanation: 'Your confirmed request is in the local demo queue. No robot has received it.' },
  processing: { title: 'Demo request processing', explanation: 'The demo is checking its small list of supported requests. This is not AI interpretation or robot authorization.' },
  awaiting_approval: { title: 'Demo awaiting approval', explanation: 'This is where a safety approval step would appear. This check is simulated; no real operator has approved anything.' },
  executing: { title: 'Demo response in progress', explanation: 'A text response is being simulated. No physical robot action is executing.' },
  completed: { title: 'Demo complete', explanation: 'The simulated text response is ready. No physical robot action was performed.' },
  denied: { title: 'Demo request declined', explanation: 'The demo did not allow this request. Nothing was sent to a robot.' },
  failed: { title: 'Demo could not finish', explanation: 'The simulation ended without a completed response. Nothing was sent to a robot.' },
}
const scenarioLabels: Record<DemoScenario, string> = {
  success: 'Friendly response', denied: 'Approval denied', failed: 'Task failure', robot_unavailable: 'Robot unavailable', timeout: 'Status timeout', service_error: 'Service unavailable',
}
interface Props {
  text: string
  source: RequestSource
  session: TaskSession
  onExpired(): void
  onEdit(): void
  onCancel(): void
  onRestart(): void
  adapterFactory?: (scenario: DemoScenario) => DemoTaskAdapter
}
const defaultFactory = (scenario: DemoScenario) => createMockTaskAdapter({ scenario })

export function RequestFlow({ text, source, session, onExpired, onEdit, onCancel, onRestart, adapterFactory = defaultFactory }: Props) {
  const [stage, setStage] = useState<'confirmation' | 'task'>('confirmation')
  const [scenario, setScenario] = useState<DemoScenario>('success')
  const [task, setTask] = useState<TaskSnapshot | null>(null)
  const [issue, setIssue] = useState<'timeout' | 'unavailable' | 'invalid' | null>(null)
  const title = useRef<HTMLHeadingElement>(null)
  const adapter = useRef<DemoTaskAdapter | null>(null)
  const controller = useRef<AbortController | null>(null)
  const pollTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  const deadline = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  const attempt = useRef(0)
  const submitted = useRef(false)
  const latestRevision = useRef(-1)
  const action = resolveDemoAction(text)

  function stop() {
    attempt.current++
    controller.current?.abort()
    clearTimeout(pollTimer.current)
    clearTimeout(deadline.current)
    adapter.current?.dispose()
    adapter.current = null
  }
  useEffect(() => () => stop(), [])
  useEffect(() => { title.current?.focus() }, [stage])

  function checkSession() {
    try { requireActiveSession(session); return true } catch { stop(); onExpired(); return false }
  }
  function confirm() {
    // Ref lock prevents two posts even before React renders the disabled button.
    if (submitted.current || !checkSession()) return
    submitted.current = true
    setStage('task')
    const version = ++attempt.current
    const abort = new AbortController()
    controller.current = abort
    let clientRequestId: string
    let client: DemoTaskAdapter
    try {
      clientRequestId = randomRequestId('client-request')
      client = adapterFactory(scenario)
      if (client.mode !== 'demo') throw new TaskServiceError('invalid_response')
      adapter.current = client
    } catch {
      stop()
      setIssue('unavailable')
      return
    }
    deadline.current = setTimeout(() => {
      if (version !== attempt.current || !checkSession()) return
      stop()
      setIssue('timeout')
    }, TASK_WAIT_MS)
    const deadlineAt = Date.now() + TASK_WAIT_MS
    const current = () => {
      if (version !== attempt.current || abort.signal.aborted || !checkSession()) return false
      if (Date.now() >= deadlineAt) { stop(); setIssue('timeout'); return false }
      return true
    }
    function failed(error: unknown) {
      if (!current()) return
      if (error instanceof TaskServiceError && error.code === 'session_expired') { stop(); onExpired(); return }
      stop()
      setIssue(error instanceof TaskServiceError && error.code === 'invalid_response' ? 'invalid' : 'unavailable')
    }
    async function accept(snapshot: TaskSnapshot, expectedId?: string) {
      if (!current()) return
      if (!snapshot.demo || snapshot.clientRequestId !== clientRequestId || (expectedId && snapshot.requestId !== expectedId)) {
        failed(new TaskServiceError('invalid_response')); return
      }
      if (snapshot.revision >= latestRevision.current) {
        latestRevision.current = snapshot.revision
        setTask(snapshot)
      }
      if (snapshot.revision >= latestRevision.current && isTerminal(snapshot.status)) { clearTimeout(deadline.current); return }
      pollTimer.current = setTimeout(() => {
        if (!current()) return
        void client.status(expectedId ?? snapshot.requestId, session, abort.signal).then(next => accept(next, expectedId ?? snapshot.requestId)).catch(failed)
      }, 500)
    }
    // Catch both synchronous adapter errors and rejected asynchronous work.
    void Promise.resolve().then(() => {
      if (!current()) return null
      return client.submit({ text, source, proposedAction: action, clientRequestId }, session, abort.signal)
    }).then(snapshot => { if (snapshot) return accept(snapshot) }).catch(failed)
  }

  const reason = task?.reason === 'unsupported_request' ? 'This demo supports only GREET, ROBOT_INFO, and HELP. Try “Hello”, “Robot info”, or “Help”. Other requests, including movement, are declined.'
    : task?.reason === 'approval_denied' ? 'In this demo outcome, approval was declined. No real approval decision was requested.'
    : task?.reason === 'robot_unavailable' ? 'Simulated robot unavailable. No connection to a physical robot was attempted.'
    : task?.reason === 'execution_failed' ? 'The demo response failed to complete. No physical execution took place.' : ''

  return <div className="request-flow">
    <span className="demo-label">DEMO MODE · LOCAL SIMULATION</span>
    {stage === 'confirmation' ? <div className="request-preview">
      <h3 ref={title} tabIndex={-1}>Your request is ready</h3>
      <p>Please check your words before confirming.</p>
      <div className="conversation-turn"><span className="speaker">You · {source === 'transcript' ? 'Transcription reviewed' : 'Typed request'}</span><blockquote>{text}</blockquote></div>
      <div className="planned-action"><span>Demo response</span><strong>{action ? actionDescriptions[action] : 'Not supported in this demo'}</strong>{action && <code>{action}</code>}</div>
      <p>{action ? 'Confirm submits only to the in-memory task simulation. This does not authorize or control the real robot.' : 'You can edit this request. If confirmed, the demo will decline it. Movement and unrestricted commands are not supported.'}</p>
      <details className="demo-outcomes"><summary>Try a demo outcome</summary><label htmlFor="demo-scenario">Simulated outcome</label><select id="demo-scenario" value={scenario} onChange={event => setScenario(event.target.value as DemoScenario)}>{Object.entries(scenarioLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select><p className="field-help">This changes the local simulation only. It is not a real safety approval control.</p></details>
      <button type="button" className="primary-button" onClick={confirm}>Confirm demo request</button>
      <div className="review-actions"><button type="button" className="secondary-button" onClick={onEdit}>Edit request</button><button type="button" className="secondary-button" onClick={onCancel}>Cancel request</button></div>
    </div> : <div className="task-status-view">
      <div className="conversation-turn"><span className="speaker">You · Confirmed request</span><blockquote>{text}</blockquote></div>
      <div className="conversation-turn robot-turn"><span className="speaker">ClawMetry · Demo mode</span>
        <div role={issue ? 'alert' : 'status'} aria-atomic="true">
          <h3 ref={title} tabIndex={-1}>{issue === 'timeout' ? 'Demo status timed out' : issue === 'invalid' ? 'Demo status could not be verified' : issue === 'unavailable' ? 'Demo service unavailable' : task ? statusCopy[task.status].title : 'Starting your demo request…'}</h3>
          <p>{issue === 'timeout' ? 'The demo did not reach a final state in time. No completion is claimed. You can start another interaction.' : issue === 'invalid' ? 'The demo returned an unexpected status. We won’t present it as a completed action.' : issue === 'unavailable' ? 'The local simulation could not start or continue. Please start another interaction.' : task ? statusCopy[task.status].explanation : 'One moment while the local simulation creates a request ID. No physical robot is involved.'}</p>
          {!issue && reason && <p>{reason}</p>}
          {!issue && task?.status === 'completed' && task.reply && <p className="friendly-response">{task.reply}</p>}
        </div>
      </div>
      {task && <dl className="task-metadata"><div><dt>{issue ? 'Last demo status' : 'Current demo status'}</dt><dd className="task-badge">{task.status.replaceAll('_', ' ')}</dd></div><div><dt>Demo request ID</dt><dd className="task-id">{task.requestId}</dd></div></dl>}
      <p className="field-help">Every state here is simulated, including approval and execution. The real backend must make its own safety and authorization decisions.</p>
      <button type="button" className="secondary-button" onClick={() => { stop(); if (checkSession()) onRestart() }}>Start another interaction</button>
      {!issue && task && !isTerminal(task.status) && <p className="field-help">Starting again clears this local simulation.</p>}
    </div>}
  </div>
}
