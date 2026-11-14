import React, { useEffect, useRef, useState } from 'react'
import { createRoot } from 'react-dom/client'
import './styles.css'

type Page = 'welcome' | 'interaction' | 'privacy'

function Shield({ className = '' }: { className?: string }) {
  return <svg className={className} width="20" height="20" viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="m12 3 8 3v5c0 5-4.5 8.5-8 10-3.5-1.5-8-5-8-10V6l8-3Z" stroke="currentColor" strokeWidth="1.6" /><path d="m8.5 11.5 2.5 2.5 4.5-5" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" /></svg>
}

function Robot({ small = false }: { small?: boolean }) {
  return <svg className={small ? 'robot robot-small' : 'robot'} viewBox="0 0 240 210" fill="none" role="img" aria-label="A smiling teal robot waving hello">
    <defs><linearGradient id="shell" x1="80" y1="40" x2="165" y2="160" gradientUnits="userSpaceOnUse"><stop stopColor="#c8fff2"/><stop offset="1" stopColor="#55c9c3"/></linearGradient><linearGradient id="body" x1="90" y1="145" x2="145" y2="200" gradientUnits="userSpaceOnUse"><stop stopColor="#8ce9d8"/><stop offset="1" stopColor="#36a8aa"/></linearGradient></defs>
    <ellipse cx="120" cy="199" rx="61" ry="7" fill="#07151e" opacity=".5"/>
    <path d="M158 164c26 6 36-10 39-31" stroke="#64d5ca" strokeWidth="16" strokeLinecap="round"/>
    <path d="m191 135-3-13m9 11 7-17m-2 20 13-9" stroke="#9aeee0" strokeWidth="7" strokeLinecap="round"/>
    <path d="M78 162c-14 6-20 14-22 26" stroke="#67d8ce" strokeWidth="15" strokeLinecap="round"/>
    <rect x="84" y="145" width="72" height="49" rx="22" fill="url(#body)"/>
    <rect x="100" y="185" width="13" height="13" rx="5" fill="#6bd9ce"/><rect x="129" y="185" width="13" height="13" rx="5" fill="#6bd9ce"/>
    <circle cx="120" cy="168" r="6" fill="#133944"/><circle cx="120" cy="168" r="2.5" fill="#91ffde"/>
    <path d="M120 52V36" stroke="#8de8d7" strokeWidth="5"/><circle cx="120" cy="30" r="7" fill="#9afce2"/>
    <rect x="49" y="87" width="17" height="33" rx="8" fill="#49b4b4"/><rect x="174" y="87" width="17" height="33" rx="8" fill="#49b4b4"/>
    <rect x="60" y="53" width="120" height="99" rx="34" fill="url(#shell)"/>
    <rect x="72" y="70" width="96" height="65" rx="24" fill="#102d39"/>
    <path d="M89 99c0-8 12-8 12 0m38 0c0-8 12-8 12 0" stroke="#91ffe4" strokeWidth="6" strokeLinecap="round"/>
    <path d="M109 113q11 11 22 0" stroke="#91ffe4" strokeWidth="4" strokeLinecap="round"/>
    <ellipse cx="86" cy="110" rx="5" ry="2.5" fill="#4caeb4"/><ellipse cx="154" cy="110" rx="5" ry="2.5" fill="#4caeb4"/>
  </svg>
}

function App() {
  const [page, setPage] = useState<Page>('welcome')
  const [privacyReturn, setPrivacyReturn] = useState<Page>('welcome')
  const [nickname, setNickname] = useState('')
  const heading = useRef<HTMLHeadingElement>(null)
  const firstRender = useRef(true)

  useEffect(() => {
    if (firstRender.current) { firstRender.current = false; return }
    heading.current?.focus()
  }, [page])

  function openPrivacy(event: React.MouseEvent<HTMLAnchorElement>) {
    event.preventDefault()
    setPrivacyReturn(page)
    setPage('privacy')
  }

  return <div className="app-shell">
    <header className="flex items-center justify-between gap-3">
      <a href="#welcome" className="brand flex items-center gap-2.5" onClick={event => { event.preventDefault(); setPage('welcome') }} aria-label="ClawMetry welcome page">
        <span className="brand-icon" aria-hidden="true"><span/><span/></span>
        <span>Claw<span className="text-teal-300">Metry</span><span className="brand-dot">.</span></span>
      </a>
      <span className="prototype-badge">VISITOR PROTOTYPE</span>
    </header>

    <main id="main-content">
      {page === 'welcome' && <section className="welcome-card page-enter" aria-labelledby="welcome-heading">
        <div className="eyebrow"><span className="status-dot"/> A LITTLE CURIOSITY. A NEW CONNECTION.</div>
        <div className="avatar-scene"><div className="orbit orbit-one"/><div className="orbit orbit-two"/><span className="spark spark-one">✦</span><span className="spark spark-two">+</span><Robot/><span className="hello-bubble">Hello, human <span aria-hidden="true">✧</span></span></div>
        <h1 ref={heading} tabIndex={-1} id="welcome-heading">Hey there! I'm your<br className="desktop-break"/> robot assistant.</h1>
        <p className="intro">A small hello. The start of something interesting.<br/>Let’s get acquainted.</p>
        <form className="welcome-form" onSubmit={event => { event.preventDefault(); setNickname(nickname.trim()); setPage('interaction') }}>
          <label htmlFor="nickname" className="flex items-center justify-between gap-2">What should I call you? <span>Optional</span></label>
          <input id="nickname" name="nickname" value={nickname} onChange={event => setNickname(event.target.value)} maxLength={40} placeholder="A nickname is perfect" autoComplete="off" aria-describedby="nickname-help" />
          <p id="nickname-help" className="field-help">Just a nickname, or leave it blank. You’re welcome either way.</p>
          <button type="submit" className="primary-button">Let's Get Started <span aria-hidden="true">→</span></button>
        </form>
        <div className="privacy-note"><Shield/><p><strong>Your curiosity. Your privacy.</strong><br/>No real name, email, or account needed. Your microphone stays off.</p></div>
      </section>}

      {page === 'interaction' && <section className="welcome-card secondary-card page-enter" aria-labelledby="interaction-heading">
        <span className="eyebrow"><span className="status-dot"/> NICE TO MEET YOU</span>
        <Robot small/>
        <h1 ref={heading} tabIndex={-1} id="interaction-heading">{nickname ? `Welcome, ${nickname}!` : 'Welcome, curious human!'}</h1>
        <p className="intro">You’re in the right place.</p>
        <div className="coming-soon"><span className="feature-label">A PREVIEW OF WHAT’S NEXT</span><h2>A conversation with ClawMetry</h2><p>In a future phase, you’ll be able to speak to the robot and see its responses here.</p><span className="phase-tag">Phase 1 · Interface preview only</span></div>
        <p className="field-help mb-6">Voice and robot connections are not active.<br/>No microphone access is requested.</p>
        <button type="button" className="secondary-button" onClick={() => setPage('welcome')}>← Back to welcome</button>
      </section>}

      {page === 'privacy' && <section className="welcome-card secondary-card privacy-page page-enter" aria-labelledby="privacy-heading">
        <span className="privacy-emblem"><Shield/></span>
        <span className="eyebrow">A CLEARER PICTURE</span>
        <h1 ref={heading} tabIndex={-1} id="privacy-heading">Privacy, in plain words.</h1>
        <p className="intro">Here’s how this Phase 1 prototype works.</p>
        <dl className="privacy-details">
          <div><dt>A nickname is optional.</dt><dd>You can continue without one. No real name, email, or account is required.</dd></div>
          <div><dt>It stays in this page’s memory.</dt><dd>Your nickname is held in React memory only. This app does not save it to cookies or browser storage, or send it to a server. Refreshing or closing the page clears it.</dd></div>
          <div><dt>No listening. No tracking.</dt><dd>This frontend does not access your microphone, connect to a robot, or include analytics or third-party trackers. Fonts and artwork are local.</dd></div>
          <div><dt>This is an early prototype.</dt><dd>The server hosting this page may keep standard request logs, such as IP addresses. Hosting practices have not been verified. These notes describe the current frontend, not a claim of GDPR compliance.</dd></div>
        </dl>
        <button type="button" className="secondary-button" onClick={() => setPage(privacyReturn)}>← Back</button>
      </section>}
      <div className="below-card"><Shield/><span>Designed with people in mind.</span><span className="separator">·</span><a href="#privacy" onClick={openPrivacy}>Privacy information</a></div>
    </main>
    <footer className="flex flex-wrap items-center justify-between gap-3"><span>Built for a more human hello.</span><span>ClawMetry <span className="footer-slash">/</span> SafetyAI Robotics Hackathon</span></footer>
  </div>
}

createRoot(document.getElementById('root')!).render(<React.StrictMode><App/></React.StrictMode>)
