import { useCallback, useEffect, useRef, useState } from 'react'
import { interpretText, RobotApiError } from './robotApi'
import type { RobotAuthorization } from './robotApi'
import { listen, recognitionSupported, speak, speechDiagnostics, SpeechError, stopSpeaking, yesNo } from './speech'
import type { ListenHandle } from './speech'

export type Phase = 'idle' | 'listening' | 'thinking' | 'speaking'
export type VoiceState = 'ready' | 'off' | 'unsupported'

interface Options {
  auth: RobotAuthorization
  /** The guest agreed to use their voice. Without it there is no microphone at all. */
  voice: boolean
  /** A "Shall I take you there?" question is open: a spoken yes / no answers it. */
  awaitingConfirm: boolean
  lang: string
  onPropose(place: string): void
  onYes(): void
  onNo(): void
  onStop(): void
  onFinish(): void
  onExpired(): void
}

const errorText = (error: unknown) => {
  if (error instanceof SpeechError) {
    if (error.code === 'not-allowed') return 'The microphone is blocked. You can still tap a place or type.'
    if (error.code === 'no-speech') return 'I didn’t hear anything. Tap the microphone to try again.'
    if (error.code === 'network') return 'The speech service could not be reached. You can tap a place or type.'
    if (error.code === 'aborted') return ''
  }
  return 'I couldn’t listen just now. You can tap a place or type.'
}

/** The conversation: tap to talk, the robot answers aloud and in a caption, and it asks before it moves. Voice is optional; typing and taps always work. */
export function useConversation(o: Options) {
  const [phase, setPhase] = useState<Phase>('idle')
  const [heard, setHeard] = useState('')
  const [said, setSaid] = useState('')
  const [notice, setNotice] = useState('')
  const [muted, setMuted] = useState(false)
  const state: VoiceState = !o.voice ? 'off' : recognitionSupported() ? 'ready' : 'unsupported'
  const opts = useRef(o)
  opts.current = o
  const mutedRef = useRef(false)
  mutedRef.current = muted
  const handle = useRef<ListenHandle | null>(null)
  const speaking = useRef<AbortController | null>(null)
  const started = useRef(false)             // the guest has used the microphone: only then may the robot speak or listen on its own
  const alive = useRef(true)
  const turn = useRef(0)

  useEffect(() => { alive.current = true; return () => { alive.current = false; handle.current?.abort(); speaking.current?.abort(); stopSpeaking() } }, [])

  const say = useCallback(async (text: string, listenAfter = false) => {
    setSaid(text)
    if (!started.current || mutedRef.current) { setPhase('idle'); return }
    speaking.current?.abort()
    const controller = new AbortController()
    speaking.current = controller
    setPhase('speaking')
    const spoke = await speak(text, opts.current.lang, controller.signal)
    if (!alive.current || controller.signal.aborted) return
    setPhase('idle')
    if (!spoke) setNotice('I could not speak out loud on this phone (check the media volume and that a text-to-speech voice is installed; ' + speechDiagnostics() + '). You can read me here and tap the microphone to answer.')
    if (listenAfter && started.current && opts.current.voice) begin()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  async function understand(text: string) {
    setHeard(text)
    setNotice('')
    setPhase('thinking')
    const mine = ++turn.current
    const answer = opts.current.awaitingConfirm ? yesNo(text) : null
    if (answer === 'yes') { opts.current.onYes(); setPhase('idle'); return }
    if (answer === 'no') { opts.current.onNo(); await say('Okay, no problem. Where would you like to go?'); return }
    try {
      const r = await interpretText(opts.current.auth, text)
      if (!alive.current || mine !== turn.current) return
      if (r.action === 'GO_TO_PLACE' && r.place) {
        if (r.ready === false) { await say(`I can’t start right now: ${(r.notice || 'the robot is not ready').toLowerCase()}. A staff member can help.`); return }
        opts.current.onPropose(r.place)
        await say(`Shall I take you to ${r.label ?? 'there'}? Say yes, or tap the button.`, true)
      } else if (r.action === 'STOP_ROBOT') {
        opts.current.onStop()          // the robot's own "I have stopped" line is spoken by the screen
      } else if (r.action === 'END_SESSION') {
        await say('Goodbye! It was nice to walk with you.')
        if (alive.current) opts.current.onFinish()
      } else {
        await say(r.reply || 'I didn’t catch that. Where would you like to go?')
      }
    } catch (error) {
      if (!alive.current) return
      if (error instanceof RobotApiError && error.code === 'session_expired') { opts.current.onExpired(); return }
      setPhase('idle')
      setSaid('Sorry, I couldn’t reach the robot just now. Please try again, or tap a place.')
    }
  }

  function begin() {
    if (!alive.current || !opts.current.voice || !recognitionSupported()) return
    started.current = true
    speaking.current?.abort()
    stopSpeaking()
    handle.current?.abort()
    setNotice('')
    setPhase('listening')
    const mine = handle.current = listen(opts.current.lang)
    mine.result.then(text => { if (handle.current === mine && alive.current) void understand(text) })
      .catch(error => { if (handle.current !== mine || !alive.current) return; setPhase('idle'); setNotice(errorText(error)) })
  }
  const stopListening = () => { handle.current?.stop() }
  /** Typed text goes through exactly the same path as speech. */
  const submitText = (text: string) => { const t = text.trim(); if (t) { handle.current?.abort(); void understand(t) } }
  /** Lines the robot says on its own (greeting, "We have arrived"): shown as the caption, spoken once the guest has used voice. */
  const robotSays = useCallback((text: string, listenAfter = false) => { void say(text, listenAfter) }, [say])
  /** The guest already tapped something (the consent button), so the browser lets the robot speak and listen. */
  const enable = useCallback(() => { started.current = true }, [])

  return { phase, heard, said, notice, state, muted, setMuted, begin, stopListening, submitText, robotSays, enable }
}
