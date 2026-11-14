import { useEffect, useMemo, useRef, useState } from 'react'
import { createTranscriptionClient, MAX_REQUEST_LENGTH } from '../services/transcription'
import { recordingUnavailable } from './recorder'
import { useVoiceRecorder } from './useVoiceRecorder'

function Microphone() {
  return <svg width="22" height="25" viewBox="0 0 24 24" fill="none" aria-hidden="true"><rect x="9" y="2" width="6" height="12" rx="3" stroke="currentColor" strokeWidth="1.7"/><path d="M5 10v2a7 7 0 0 0 14 0v-2M12 19v3m-4 0h8" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round"/></svg>
}

export function VoiceInteraction() {
  const { recorder, snapshot } = useVoiceRecorder()
  const [mode, setMode] = useState<'voice' | 'text'>('voice')
  const [text, setText] = useState('')
  const [ready, setReady] = useState(false)
  const [notice, setNotice] = useState('')
  const [textError, setTextError] = useState('')
  const [sending, setSending] = useState(false)
  const [playbackError, setPlaybackError] = useState('')
  const input = useRef<HTMLTextAreaElement>(null)
  const preview = useRef<HTMLHeadingElement>(null)
  const audio = useRef<HTMLAudioElement>(null)
  const reviewHeading = useRef<HTMLHeadingElement>(null)
  const stopButton = useRef<HTMLButtonElement>(null)
  const startButton = useRef<HTMLButtonElement>(null)
  const previousStatus = useRef<string>('idle')
  const request = useRef<AbortController | null>(null)
  const requestVersion = useRef(0)
  const uploadAttempted = useRef(false)
  const requestTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined)
  const client = useMemo(() => createTranscriptionClient(import.meta.env.VITE_TRANSCRIBE_ENDPOINT), [])
  const unavailable = recordingUnavailable()
  const { status, clip } = snapshot
  const active = status === 'requesting' || status === 'recording' || status === 'stopping'

  function abortRequest() {
    requestVersion.current++
    request.current?.abort()
    request.current = null
    clearTimeout(requestTimer.current)
  }

  useEffect(() => () => { abortRequest() }, [])
  useEffect(() => {
    if (status === 'recording') stopButton.current?.focus()
    if (status === 'reviewing') reviewHeading.current?.focus()
    if (status === 'idle' && previousStatus.current !== 'idle') startButton.current?.focus()
    previousStatus.current = status
  }, [status])
  useEffect(() => {
    if (mode === 'text' && !ready) input.current?.focus()
    if (ready) preview.current?.focus()
  }, [mode, ready])
  useEffect(() => {
    const element = audio.current
    return () => {
      // A detached media element can retain buffered playback: explicitly release it.
      element?.pause()
      element?.removeAttribute('src')
      element?.load()
    }
  }, [clip?.url])
  useEffect(() => {
    const pause = () => { if (document.visibilityState === 'hidden') audio.current?.pause() }
    const onPageHide = () => audio.current?.pause()
    document.addEventListener('visibilitychange', pause)
    window.addEventListener('pagehide', onPageHide)
    return () => {
      document.removeEventListener('visibilitychange', pause)
      window.removeEventListener('pagehide', onPageHide)
    }
  }, [])

  function discard(message?: string) {
    const result = message ?? (uploadAttempted.current
      ? 'Recording cleared from this page. The transcription server may already have received the audio.'
      : 'Recording discarded. Nothing was sent.')
    abortRequest()
    uploadAttempted.current = false
    setSending(false)
    audio.current?.pause()
    recorder.cancel()
    setPlaybackError('')
    setNotice(result)
  }
  function chooseMode(next: 'voice' | 'text') {
    if (next === mode) return
    discard('')
    setReady(false)
    setTextError('')
    setMode(next)
  }
  function startRecording() {
    discard('')
    void recorder.start()
  }

  async function continueAudio() {
    if (!clip || sending) return
    if (!client.connected) {
      audio.current?.pause()
      recorder.cancel()
      setMode('text')
      setNotice('Transcription not connected. Your recording has not been uploaded and has been cleared from this page. Please type your request instead.')
      return
    }
    audio.current?.pause()
    abortRequest()
    const version = requestVersion.current
    const controller = new AbortController()
    request.current = controller
    setSending(true)
    uploadAttempted.current = true
    setNotice('Sending this recording to this site’s transcription endpoint…')
    let timedOut = false
    requestTimer.current = setTimeout(() => { timedOut = true; controller.abort() }, 30_000)
    try {
      const result = await client.transcribe(clip, controller.signal)
      if (version !== requestVersion.current) return
      if (result.status === 'not-connected') {
        setNotice('Transcription not connected. Please type your request instead.')
        return
      }
      setText(result.text)
      recorder.cancel()
      setMode('text')
      setReady(false)
      setNotice('Transcription received. Please check and edit it before continuing. No robot action has been sent.')
    } catch {
      if (version !== requestVersion.current) return
      setNotice(timedOut ? 'Transcription took too long. You can retry or type instead. The server may already have received the audio.' : 'We couldn’t transcribe this recording. You can retry or type instead. No robot action has been sent.')
    } finally {
      if (version === requestVersion.current) {
        clearTimeout(requestTimer.current)
        request.current = null
        setSending(false)
      }
    }
  }

  const stateMessage = status === 'requesting' ? 'Waiting for microphone permission. Choose Allow in your browser, or cancel and type instead.'
    : status === 'recording' ? 'I’m ready for your short request. Tap Stop Recording when you’re done.'
    : status === 'stopping' ? 'Microphone off. Preparing your recording for review…'
    : status === 'reviewing' ? 'Recording ready. Have a listen before you decide what to do next.'
    : status === 'error' ? snapshot.message
    : snapshot.message || 'A little hello goes a long way. Record a short request, or type it instead.'
  const timer = `00:${Math.floor(snapshot.elapsedMs / 1000).toString().padStart(2, '0')}`

  return <section className="interaction-panel" aria-labelledby="interaction-title">
    <span className="feature-label">YOUR VOICE. YOUR CHOICE.</span>
    <h2 id="interaction-title">What’s on your mind?</h2>
    <div className="input-modes" aria-label="Choose an input method">
      <button type="button" aria-pressed={mode === 'voice'} onClick={() => chooseMode('voice')}>Use voice</button>
      <button type="button" aria-pressed={mode === 'text'} onClick={() => chooseMode('text')}>Type a request</button>
    </div>

    {mode === 'voice' && <div className="voice-panel">
      <p className="robot-message" role={status === 'error' ? 'alert' : 'status'}>{stateMessage}</p>
      <div className="mic-state"><span className={status === 'recording' ? 'recording-dot' : 'mic-off-dot'} aria-hidden="true"/>{status === 'recording' ? 'Microphone on · Recording' : status === 'requesting' ? 'Permission requested' : snapshot.permission === 'denied' ? 'Permission denied · Microphone off' : 'Microphone off'}</div>
      {status === 'recording' && <p className="recording-timer" role="timer" aria-label="Recording time">{timer}<span> / 00:20</span></p>}
      {unavailable && <p className="availability-note">{unavailable}</p>}
      {!active && !clip && <button type="button" ref={startButton} className="primary-button record-button" disabled={!!unavailable || snapshot.requestPending} onClick={startRecording}><Microphone/>Start Recording</button>}
      {status === 'idle' && snapshot.requestPending && <p className="field-help">The browser’s earlier permission prompt is still pending. Dismiss it or choose a permission before trying again. You can type now.</p>}
      {status === 'requesting' && <button type="button" className="secondary-button" onClick={() => discard('Permission request canceled. Dismiss any remaining browser prompt. You can type instead.')}>Cancel</button>}
      {status === 'recording' && <div className="recording-actions"><button type="button" ref={stopButton} className="primary-button stop-button" onClick={recorder.stop}><span aria-hidden="true">■</span>Stop Recording</button><button type="button" className="secondary-button" onClick={() => discard('Recording canceled. Nothing was sent.')}>Cancel</button></div>}
      {status === 'stopping' && <button type="button" className="secondary-button" onClick={() => discard('Recording canceled. Nothing was sent.')}>Cancel</button>}
      {clip && <div className="audio-review">
        <h3 ref={reviewHeading} tabIndex={-1}>Recording ready</h3>
        <label htmlFor="recording-playback">Your recording · {clip.durationMs < 1000 ? 'under 1 second' : `${Math.ceil(clip.durationMs / 1000)} seconds`}</label>
        <audio ref={audio} id="recording-playback" aria-label="Play your recording" controls controlsList="nodownload" preload="metadata" src={clip.url} onError={() => setPlaybackError('This browser couldn’t play this audio format. You can retry recording or type instead.')} />
        {playbackError && <p role="alert" className="form-error">{playbackError}</p>}
        <p className="audio-consent">{client.connected ? <>Only <strong>Continue — send for transcription</strong> uploads this audio to <code>{client.endpoint}</code> on this site. No robot action will be sent.</> : 'This recording stays in page memory. Continue moves to typed input and clears the audio, without uploading it.'}</p>
        <button type="button" className="primary-button" disabled={sending} onClick={() => void continueAudio()}>{sending ? 'Transcribing…' : client.connected ? 'Continue — send for transcription' : 'Continue'}</button>
        {sending && <button type="button" className="guest-button" onClick={() => { abortRequest(); setSending(false); setNotice('Transcription request canceled. The server may already have received the audio. No server deletion is claimed.') }}>Cancel transcription</button>}
        <div className="review-actions"><button type="button" className="secondary-button" disabled={sending} onClick={startRecording}>Retry recording</button><button type="button" className="secondary-button" onClick={() => discard()}>Discard</button></div>
      </div>}
      {!clip && <p className="field-help">Up to 20 seconds. Recording starts only when you choose it, and stops if you leave this view or hide the tab.</p>}
    </div>}

    {mode === 'text' && !ready && <form className="typed-request" onSubmit={event => {
      event.preventDefault()
      if (!text.trim()) { setTextError('Please enter a request before continuing.'); input.current?.focus(); return }
      setText(text.trim()); setReady(true); setTextError(''); setNotice('')
    }}>
      <label htmlFor="typed-request">Your request</label>
      <textarea ref={input} id="typed-request" rows={4} value={text} maxLength={MAX_REQUEST_LENGTH} placeholder="For example, ask a question about the robot…" autoComplete="off" onChange={event => { setText(event.target.value); setTextError('') }} aria-describedby={`typed-help${textError ? ' typed-error' : ''}`} aria-invalid={!!textError}/>
      <p id="typed-help" className="field-help">Type freely. Your request stays in this page’s memory and is not sent to the robot. {text.length}/{MAX_REQUEST_LENGTH}</p>
      {textError && <p id="typed-error" role="alert" className="form-error">{textError}</p>}
      <button type="submit" className="primary-button">Continue</button>
    </form>}
    {mode === 'text' && ready && <div className="request-preview">
      <h3 ref={preview} tabIndex={-1}>Your request is ready</h3>
      <blockquote>{text}</blockquote>
      <p>This is a local request preview. Continuing here does not send a robot command or submit this text.</p>
      <button type="button" className="secondary-button" onClick={() => setReady(false)}>Edit request</button>
    </div>}
    {notice && <p className="session-notice" role="status">{notice}</p>}
    <div className="transcription-status"><span className="mic-off-dot" aria-hidden="true"/>{client.connected ? 'Transcription endpoint configured' : 'Transcription not connected'}</div>
    <p className="field-help interaction-privacy">{client.connected ? 'Audio is uploaded only with the labeled send button. Typed requests stay local. Server retention must be confirmed by the event team.' : 'No audio uploads. No speech recognition is connected. Typed requests stay local.'}</p>
  </section>
}
