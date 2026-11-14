import { useEffect, useRef, useState } from 'react'
import type { MouseEvent } from 'react'
import { Robot, Shield } from './Artwork'
import { demoSessionService, sessionDuration } from './services/demoSession'
import type { DemoSession } from './services/demoSession'
import { useRoute } from './useRoute'
import { VoiceInteraction } from './voice/VoiceInteraction'

type Notice = 'ended' | 'expired' | 'unavailable' | null
const notices = {
  ended: 'Your demo session has ended. Ready for another hello?',
  expired: 'Your demo session has expired. Start again whenever you’re ready.',
  unavailable: 'There’s no active demo session on this page. Let’s start with a hello.',
}

export function App() {
  const { route, navigate } = useRoute()
  const [session, setSession] = useState<DemoSession | null>(null)
  const [nickname, setNickname] = useState('')
  const [notice, setNotice] = useState<Notice>(null)
  const [error, setError] = useState('')
  const heading = useRef<HTMLHeadingElement>(null)

  useEffect(() => {
    heading.current?.focus({ preventScroll: true })
    window.scrollTo?.(0, 0)
  }, [route])

  // Refreshing /session cannot restore a session: no storage or server is involved.
  useEffect(() => {
    if (route === '/session' && !session) {
      setNotice(previous => previous ?? 'unavailable')
      navigate('/join', true)
    } else if ((route === '/join' || route === 'not-found') && session) {
      setSession(null)
      setNickname('')
      setNotice('ended')
    }
  }, [route, session, navigate])

  useEffect(() => {
    if (!session) return
    const checkExpiry = () => {
      if (!demoSessionService.isExpired(session)) return
      setSession(null)
      setNickname('')
      setNotice('expired')
      // Keep privacy readable; its Back destination will become /join.
      if (window.location.pathname !== '/privacy') navigate('/join', true)
    }
    const timer = window.setTimeout(checkExpiry, Math.max(0, session.expiresAt - Date.now()))
    // Timers may be suspended on a sleeping phone. Recheck the absolute deadline.
    window.addEventListener('focus', checkExpiry)
    document.addEventListener('visibilitychange', checkExpiry)
    return () => {
      window.clearTimeout(timer)
      window.removeEventListener('focus', checkExpiry)
      document.removeEventListener('visibilitychange', checkExpiry)
    }
  }, [session, navigate])

  function startSession(asGuest = false) {
    try {
      const next = demoSessionService.create(asGuest ? '' : nickname, sessionDuration(import.meta.env.VITE_DEMO_SESSION_SECONDS))
      setSession(next)
      setNickname('')
      setNotice(null)
      setError('')
      navigate('/session')
    } catch {
      setError('We couldn’t start this demo session. Please try again, or ask the event team for help.')
    }
  }

  function endSession() {
    setSession(null)
    setNickname('')
    setError('')
    setNotice('ended')
    navigate('/join', true)
  }

  function openPrivacy(event: MouseEvent<HTMLAnchorElement>) {
    // Preserve standard modified-click behavior; a new tab gets its own empty memory.
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return
    event.preventDefault()
    if (route !== '/privacy') navigate('/privacy')
  }

  const home = session ? '/session' : '/join'
  return <div className="app-shell">
    <header className="flex items-center justify-between gap-3">
      <a href={home} className="brand flex items-center gap-2.5" onClick={event => { if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return; event.preventDefault(); navigate(home) }} aria-label={session ? 'ClawMetry demo session' : 'ClawMetry welcome page'}>
        <span className="brand-icon" aria-hidden="true"><span/><span/></span>
        <span>Claw<span className="text-teal-300">Metry</span><span className="brand-dot">.</span></span>
      </a>
      <span className="prototype-badge">VISITOR PROTOTYPE</span>
    </header>

    <main id="main-content">
      {route === '/join' && <section className="welcome-card page-enter" aria-labelledby="welcome-heading">
        <div className="eyebrow"><span className="status-dot"/> A LITTLE CURIOSITY. A NEW CONNECTION.</div>
        <div className="avatar-scene"><div className="orbit orbit-one"/><div className="orbit orbit-two"/><span className="spark spark-one">✦</span><span className="spark spark-two">+</span><Robot/><span className="hello-bubble">Hello, human <span aria-hidden="true">✧</span></span></div>
        <h1 ref={heading} tabIndex={-1} id="welcome-heading">Hey there! I'm your<br className="desktop-break"/> robot assistant.</h1>
        <p className="intro">A small hello. The start of something interesting.<br/>Let’s get acquainted. No account required.</p>
        {notice && <p className="session-notice" role="status">{notices[notice]}</p>}
        <form className="welcome-form" onSubmit={event => { event.preventDefault(); startSession() }}>
          <label htmlFor="nickname" className="flex items-center justify-between gap-2">What should I call you? <span>Optional</span></label>
          <input id="nickname" name="nickname" value={nickname} onChange={event => { setNickname(event.target.value); setError('') }} maxLength={40} placeholder="A nickname is perfect" autoComplete="off" aria-describedby="nickname-help" />
          <p id="nickname-help" className="field-help">Just a nickname, or leave it blank. You’re welcome either way.</p>
          {error && <p className="form-error" role="alert">{error}</p>}
          <button type="submit" className="primary-button">Let's Get Started <span aria-hidden="true">→</span></button>
          <button type="button" className="guest-button" onClick={() => startSession(true)}>Continue as Guest</button>
        </form>
        <div className="privacy-note"><Shield/><p><strong>Your curiosity. Your privacy.</strong><br/>No real name, email, or account needed. Microphone access is your choice after joining.</p></div>
      </section>}

      {route === '/session' && session && <section className="welcome-card secondary-card session-card page-enter" aria-labelledby="session-heading">
        <span className="eyebrow"><span className="status-dot"/> ROBOT STATUS · DEMO MODE</span>
        <Robot small/>
        <h1 ref={heading} tabIndex={-1} id="session-heading">Nice to meet you, {session.nickname}!</h1>
        <p className="intro">Your temporary demo session is ready.<br/>This is a frontend simulation, with no robot connected.</p>
        <VoiceInteraction key={session.id}/>
        <p className="field-help mb-6">This demo expires at <time dateTime={new Date(session.expiresAt).toISOString()}>{new Date(session.expiresAt).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' })}</time>.<br/>It does not authenticate or authorize robot access.</p>
        <button type="button" className="secondary-button" onClick={endSession}>End Session</button>
      </section>}

      {route === '/privacy' && <section className="welcome-card secondary-card privacy-page page-enter" aria-labelledby="privacy-heading">
        <span className="privacy-emblem"><Shield/></span>
        <span className="eyebrow">A CLEARER PICTURE</span>
        <h1 ref={heading} tabIndex={-1} id="privacy-heading">Privacy, in plain words.</h1>
        <p className="intro">Here’s how this Phase 3 prototype works.</p>
        <dl className="privacy-details">
          <div><dt>A nickname is optional.</dt><dd>You can continue as Guest. No real name, email, or account is required.</dd></div>
          <div><dt>A temporary demo, in memory only.</dt><dd>Your nickname, simulated session ID, and expiry time stay in this page’s React memory. This app does not save them to cookies or browser storage, put them in the URL, or send them to a server. Ending the session, expiry, refreshing, or closing the page clears them. Navigating back to welcome also ends the session.</dd></div>
          <div><dt>Your microphone, on your terms.</dt><dd>Only Start Recording or Retry recording requests microphone access. Recording is limited to 20 seconds and requires HTTPS or localhost. Stopping, canceling, hiding the tab, leaving the session view, or ending the session stops microphone tracks. If permission arrives after cancellation, those tracks are stopped immediately.</dd></div>
          <div><dt>Review before sending.</dt><dd>Audio is held in page memory for review, without browser storage or persistent audio files. Typed requests also stay in page memory. Retry, Discard, leaving the session view, or ending the session clears the recording and its playback URL. Typed requests are a local preview and do not dispatch robot actions. They are cleared when leaving the session view.</dd></div>
          <div><dt>Transcription is optional and separate.</dt><dd>By default, transcription is not connected and Continue does not upload audio. If the event team configures a same-site transcription endpoint, only the button labeled “Continue — send for transcription” sends your recording and its format/duration metadata. A returned transcript can be edited before continuing. Canceling a request cannot guarantee that the server has not received it. Server retention and deletion behavior have not been verified.</dd></div>
          <div><dt>No tracking.</dt><dd>This frontend includes no analytics, third-party trackers, or third-party audio services. Fonts and artwork are local. No audio or transcripts are logged by application code. The robot remains disconnected.</dd></div>
          <div><dt>Your QR code is just a link.</dt><dd>The event team generates it locally. No third-party QR-processing service is used. Scanning opens the site’s join page, without a nickname or session ID in the link.</dd></div>
          <div><dt>This is an early prototype.</dt><dd>Sessions are simulated, not backend authentication or robot authorization. The server hosting this page may keep standard request logs, such as IP addresses. Hosting practices have not been verified. These notes describe the frontend, not a claim of certified GDPR compliance or backend data deletion.</dd></div>
        </dl>
        <button type="button" className="secondary-button" onClick={() => navigate(home, true)}>← Back {session ? 'to session' : 'to welcome'}</button>
      </section>}

      {route === 'not-found' && <section className="welcome-card secondary-card page-enter" aria-labelledby="missing-heading">
        <Robot small/><h1 ref={heading} tabIndex={-1} id="missing-heading">Let’s find your hello.</h1>
        <p className="intro mb-6">That page isn’t part of this prototype.</p>
        <button type="button" className="secondary-button" onClick={() => navigate('/join', true)}>Go to welcome</button>
      </section>}
      <div className="below-card"><Shield/><span>Designed with people in mind.</span><span className="separator">·</span><a href="/privacy" onClick={openPrivacy}>Privacy information</a></div>
    </main>
    <footer className="flex flex-wrap items-center justify-between gap-3"><span>Built for a more human hello.</span><span>ClawMetry <span className="footer-slash">/</span> SafetyAI Robotics Hackathon</span></footer>
  </div>
}
