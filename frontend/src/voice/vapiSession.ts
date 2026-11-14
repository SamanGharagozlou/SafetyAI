/**
 * Live duplex voice through Vapi.
 *
 * This is the ONLY part of the app that sends visitor audio off the device, so
 * it is opt-in, off by default, and unreachable from the static demo build
 * (config/deployment.ts rejects a non-empty key for the `static-demo` profile,
 * exactly as it already does for VITE_TRANSCRIBE_ENDPOINT).
 *
 * The existing record -> upload -> review -> confirm path is untouched. With no
 * Vapi keys configured, nothing here is imported at runtime.
 */

export interface VapiConfig {
  readonly publicKey: string
  readonly assistantId: string
}

export type VapiStatus = 'idle' | 'connecting' | 'live' | 'ended' | 'error'
export type VapiRole = 'user' | 'assistant'

export interface VapiEvents {
  onStatus(status: VapiStatus, detail?: string): void
  onLevel(level: number): void
  onTranscript(role: VapiRole, text: string): void
  onSpeaking(speaking: boolean): void
}

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i

/** Both keys must be present and well-formed, or live voice stays off entirely. */
export function resolveVapiConfig(env: Record<string, string | undefined>): VapiConfig | null {
  const publicKey = (env.VITE_VAPI_PUBLIC_KEY ?? '').trim()
  const assistantId = (env.VITE_VAPI_ASSISTANT_ID ?? '').trim()
  if (!UUID.test(publicKey) || !UUID.test(assistantId)) return null
  return { publicKey, assistantId }
}

/**
 * Vapi's web SDK reports a failed WebRTC join only through `call-start-failed`,
 * and on some networks not at all - the room is simply deleted after ~15s and
 * the UI sits on "connecting" forever. Measured cause on one office network:
 * Chrome gathers ICE candidates on VPN interfaces that never answer STUN, so
 * gathering takes ~40s against Vapi's 15s budget. We cannot fix the network
 * from here, but we must never present that as a working call.
 */
export const CONNECT_TIMEOUT_MS = 20_000

export const CONNECT_FAILED_MESSAGE =
  'Could not reach the voice service. A VPN or a restricted network can block this. You can write your request instead.'

type VapiLike = {
  on(event: string, handler: (payload?: unknown) => void): void
  start(assistantId: string): Promise<unknown>
  stop(): void
  setMuted(muted: boolean): void
  removeAllListeners?(): void
}

export class VapiSession {
  private client: VapiLike | null = null
  private timer: ReturnType<typeof setTimeout> | undefined
  private generation = 0
  private settled = false
  private stopped = false

  constructor(private readonly config: VapiConfig, private readonly events: VapiEvents) {}

  async start(): Promise<void> {
    if (this.client || this.stopped) return
    const generation = ++this.generation
    this.settled = false
    this.events.onStatus('connecting')

    // Permission probe. RELEASE the tracks at once: a held stream leaves the
    // SDK publishing a dead track and the assistant hears silence.
    try {
      const probe = await navigator.mediaDevices.getUserMedia({ audio: true })
      probe.getTracks().forEach(track => track.stop())
    } catch {
      this.fail('Microphone access is needed for a voice call. You can write your request instead.')
      return
    }
    if (generation !== this.generation || this.stopped) return

    let client: VapiLike
    try {
      const module = await import('@vapi-ai/web')
      const Vapi = (module as { default?: new (key: string) => VapiLike }).default
      if (!Vapi) throw new Error('missing export')
      client = new Vapi(this.config.publicKey)
    } catch {
      this.fail('The voice service could not be loaded.')
      return
    }
    if (generation !== this.generation || this.stopped) { try { client.stop() } catch { /* nothing started */ } return }

    this.client = client
    client.on('call-start', () => { if (generation === this.generation) { this.settled = true; clearTimeout(this.timer); this.events.onStatus('live') } })
    client.on('call-end', () => { if (generation === this.generation) { this.settled = true; clearTimeout(this.timer); this.events.onStatus('ended') } })
    client.on('call-start-failed', () => this.fail(CONNECT_FAILED_MESSAGE, generation))
    client.on('error', () => { if (!this.settled) this.fail(CONNECT_FAILED_MESSAGE, generation) })
    client.on('speech-start', () => { if (generation === this.generation) this.events.onSpeaking(true) })
    client.on('speech-end', () => { if (generation === this.generation) this.events.onSpeaking(false) })
    client.on('volume-level', payload => {
      if (generation !== this.generation) return
      const level = typeof payload === 'number' ? payload : 0
      this.events.onLevel(Math.min(1, Math.max(0, level)))
    })
    client.on('message', payload => {
      if (generation !== this.generation) return
      const message = payload as { type?: string; transcriptType?: string; role?: string; transcript?: string } | undefined
      if (!message || message.type !== 'transcript' || message.transcriptType !== 'final') return
      const role: VapiRole = message.role === 'assistant' ? 'assistant' : 'user'
      const text = (message.transcript ?? '').trim()
      if (text) this.events.onTranscript(role, text)
    })

    this.timer = setTimeout(() => this.fail(CONNECT_FAILED_MESSAGE, generation), CONNECT_TIMEOUT_MS)
    try {
      await client.start(this.config.assistantId)
    } catch {
      this.fail(CONNECT_FAILED_MESSAGE, generation)
    }
  }

  private fail(detail: string, generation = this.generation) {
    if (generation !== this.generation || this.settled) return
    this.settled = true
    clearTimeout(this.timer)
    this.events.onStatus('error', detail)
    this.release()
  }

  setMuted(muted: boolean) {
    try { this.client?.setMuted(muted) } catch { /* the call may already be gone */ }
  }

  /** Ends the call and drops every listener. Safe to call more than once. */
  stop() {
    this.stopped = true
    this.generation++
    clearTimeout(this.timer)
    this.release()
  }

  private release() {
    const client = this.client
    this.client = null
    if (!client) return
    try { client.stop() } catch { /* already ended */ }
    try { client.removeAllListeners?.() } catch { /* optional in some versions */ }
  }
}
