import { useState } from 'react'
import { FaceIcon, MicIcon, ShieldIcon } from './icons'
import { CONSENT_VERSION } from './robotApi'
import type { RobotConsent } from './robotApi'

interface Props { onAgree(consent: RobotConsent): void; onDecline(): void; onPrivacy(): void }

/** Asked once at the start. Nothing is pre-ticked; the guest can continue without voice or face and still tap and type. */
export function Consent({ onAgree, onDecline, onPrivacy }: Props) {
  const [voice, setVoice] = useState(false)
  const [face, setFace] = useState(false)
  return <section className="consent-card page-enter" aria-labelledby="consent-title">
    <ShieldIcon className="consent-shield"/>
    <h1 id="consent-title">Before we start</h1>
    <p>I can talk with you and recognise you while we walk together. Both are optional, and you can say no to either.</p>
    <label className="consent-item"><input type="checkbox" checked={voice} onChange={e => setVoice(e.target.checked)}/>
      <MicIcon/><span><strong>Use my voice</strong><small>My phone turns what you say into text so I can understand you. The browser’s speech service (for example Google or Apple) does this. I keep no recording.</small></span></label>
    <label className="consent-item"><input type="checkbox" checked={face} onChange={e => setFace(e.target.checked)}/>
      <FaceIcon/><span><strong>Use my face to recognise me</strong><small>Only so I can tell which guest to walk with. Nothing is saved as a picture.</small></span></label>
    <p className="consent-delete"><strong>When we are done, everything is deleted</strong>: your choices, your conversation and anything used to recognise you. You can leave at any moment.</p>
    <button type="button" className="sheet-primary" onClick={() => onAgree({ voice, face, version: CONSENT_VERSION })}>{voice || face ? 'I agree, continue' : 'Continue without voice or face'}</button>
    <button type="button" className="sheet-secondary" onClick={onDecline}>No thanks</button>
    <button type="button" className="link-button" onClick={onPrivacy}>Privacy information</button>
  </section>
}
