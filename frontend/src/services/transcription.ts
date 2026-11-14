export const PROPOSED_TRANSCRIPTION_PATH = '/api/transcribe'
export const MAX_REQUEST_LENGTH = 2000

export interface TranscriptionAudio {
  readonly blob: Blob
  readonly mimeType: string
  readonly durationMs: number
}
export type TranscriptionResult = { status: 'not-connected' } | { status: 'transcribed'; text: string }
export interface TranscriptionClient {
  readonly connected: boolean
  readonly endpoint: string
  transcribe(audio: TranscriptionAudio, signal: AbortSignal): Promise<TranscriptionResult>
}

/** Proposed wire contract only; leave unset until the backend teammate confirms it. */
export function createTranscriptionClient(configuredEndpoint = ''): TranscriptionClient {
  const endpoint = configuredEndpoint.trim()
  // Same-origin paths only. Do not send visitor audio to third-party services.
  const valid = /^\/(?!\/)[^\\\s?#]+$/.test(endpoint)
  return {
    connected: valid,
    endpoint: valid ? endpoint : '',
    async transcribe(audio, signal) {
      if (!valid) return { status: 'not-connected' }
      if (!audio.blob.size || !audio.mimeType.startsWith('audio/') || audio.blob.type !== audio.mimeType || !Number.isFinite(audio.durationMs) || audio.durationMs < 0 || audio.durationMs > 20_000) {
        throw new Error('Invalid recording metadata.')
      }
      const format = audio.mimeType.split(';')[0]
      const extension = ({ 'audio/mp4': 'm4a', 'audio/ogg': 'ogg', 'audio/webm': 'webm', 'audio/wav': 'wav', 'audio/aac': 'aac' } as Record<string, string>)[format] || 'audio'
      const form = new FormData()
      form.append('audio', audio.blob, `recording.${extension}`)
      form.append('mime_type', audio.mimeType)
      form.append('duration_ms', String(Math.round(audio.durationMs)))
      const response = await fetch(endpoint, {
        method: 'POST', body: form, signal,
        credentials: 'omit', cache: 'no-store', redirect: 'error', referrerPolicy: 'no-referrer',
      })
      if (!response.ok) throw new Error('Transcription request failed.')
      const data: unknown = await response.json()
      if (!data || typeof data !== 'object' || !('text' in data) || typeof data.text !== 'string' || !data.text.trim() || data.text.length > MAX_REQUEST_LENGTH) {
        throw new Error('The transcription response did not match the agreed frontend format.')
      }
      return { status: 'transcribed', text: data.text.trim() }
    },
  }
}
