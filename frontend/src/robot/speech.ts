// Browser speech for the conversation: listening (Web Speech API) and spoken replies (speechSynthesis). Nothing is recorded or stored by this app.
// Recognition needs a secure context (HTTPS or localhost) and a browser that offers it (Chrome, Edge, Safari); the speech service behind it belongs to the
// browser vendor (e.g. Google or Apple). Where it is missing the app falls back to taps and typing: voice is never required.

type Ctor = new () => RecognitionLike
interface RecognitionLike {
  lang: string; interimResults: boolean; continuous: boolean; maxAlternatives: number
  onresult: ((event: { results: ArrayLike<ArrayLike<{ transcript: string }> & { isFinal?: boolean }> }) => void) | null
  onerror: ((event: { error: string }) => void) | null
  onend: (() => void) | null
  start(): void; stop(): void; abort(): void
}
const ctor = (): Ctor | undefined => {
  const w = window as unknown as { SpeechRecognition?: Ctor; webkitSpeechRecognition?: Ctor }
  return w.SpeechRecognition ?? w.webkitSpeechRecognition
}

export class SpeechError extends Error {
  constructor(readonly code: 'not-allowed' | 'no-speech' | 'network' | 'unsupported' | 'aborted' | 'failed') { super(code); this.name = 'SpeechError' }
}

export const recognitionSupported = () => typeof window !== 'undefined' && window.isSecureContext !== false && !!ctor()
export const speechOutputSupported = () => typeof window !== 'undefined' && 'speechSynthesis' in window && typeof SpeechSynthesisUtterance !== 'undefined'

export interface ListenHandle { readonly result: Promise<string>; stop(): void; abort(): void }

/** Listen for one sentence. Resolves with the final transcript; rejects with a SpeechError (silence, blocked microphone, ...). */
export function listen(lang: string): ListenHandle {
  const Recognition = ctor()
  if (!Recognition || !recognitionSupported()) return { result: Promise.reject(new SpeechError('unsupported')), stop() { /* nothing */ }, abort() { /* nothing */ } }
  const rec = new Recognition()
  rec.lang = lang
  rec.interimResults = false
  rec.continuous = false
  rec.maxAlternatives = 1
  let text = ''
  let failure: SpeechError | null = null
  const result = new Promise<string>((resolve, reject) => {
    rec.onresult = event => {
      for (let i = 0; i < event.results.length; i++) { const alt = event.results[i][0]; if (alt?.transcript) text = alt.transcript }
    }
    rec.onerror = event => {
      const code = event.error
      failure = new SpeechError(code === 'not-allowed' || code === 'service-not-allowed' ? 'not-allowed' : code === 'no-speech' ? 'no-speech' : code === 'network' ? 'network' : code === 'aborted' ? 'aborted' : 'failed')
    }
    rec.onend = () => { if (text.trim()) resolve(text.trim()); else reject(failure ?? new SpeechError('no-speech')) }
  })
  try { rec.start() } catch { return { result: Promise.reject(new SpeechError('failed')), stop() { /* nothing */ }, abort() { /* nothing */ } } }
  return { result, stop: () => { try { rec.stop() } catch { /* already stopped */ } }, abort: () => { failure = new SpeechError('aborted'); try { rec.abort() } catch { /* already stopped */ } } }
}

/** Speak a sentence; resolves when it ends or is cancelled. Resolves at once where speech output does not exist. */
export function speak(text: string, lang: string, signal?: AbortSignal): Promise<void> {
  if (!speechOutputSupported() || !text.trim()) return Promise.resolve()
  return new Promise<void>(resolve => {
    const done = () => { signal?.removeEventListener('abort', cancel); resolve() }
    const utterance = new SpeechSynthesisUtterance(text)
    utterance.lang = lang
    utterance.onend = done
    utterance.onerror = done
    const cancel = () => { try { window.speechSynthesis.cancel() } catch { /* ignore */ } done() }
    if (signal?.aborted) { done(); return }
    signal?.addEventListener('abort', cancel)
    try { window.speechSynthesis.cancel(); window.speechSynthesis.speak(utterance) } catch { done() }
  })
}
export const stopSpeaking = () => { try { if (speechOutputSupported()) window.speechSynthesis.cancel() } catch { /* ignore */ } }

const YES = /^(yes|yeah|yep|yup|sure|ok|okay|go|go ahead|please|please do|do it|take me|let'?s go|sounds good)\b/i
const NO = /^(no|nope|nah|not now|cancel|never ?mind|stop|don'?t)\b/i
/** A spoken answer to "Shall I take you there?" */
export function yesNo(text: string): 'yes' | 'no' | null {
  const t = text.trim().replace(/[.!?,]+$/g, '')
  return YES.test(t) ? 'yes' : NO.test(t) ? 'no' : null
}
