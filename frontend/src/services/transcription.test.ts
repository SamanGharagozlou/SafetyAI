import { afterEach, describe, expect, it, vi } from 'vitest'
import { createTranscriptionClient } from './transcription'

const audio = { blob: new Blob(['test audio'], { type: 'audio/webm;codecs=opus' }), mimeType: 'audio/webm;codecs=opus', durationMs: 1234 }
afterEach(() => vi.unstubAllGlobals())

describe('opt-in transcription boundary', () => {
  it.each(['', 'https://example.test/api/transcribe', '//example.test/transcribe', '/\\example.test', '/api/transcribe?token=test'])('does not fetch with disconnected/invalid configuration (%s)', async endpoint => {
    const fetch = vi.fn()
    vi.stubGlobal('fetch', fetch)
    const client = createTranscriptionClient(endpoint)
    expect(client.connected).toBe(false)
    expect(await client.transcribe(audio, new AbortController().signal)).toEqual({ status: 'not-connected' })
    expect(fetch).not.toHaveBeenCalled()
  })
  it('sends Blob and metadata only when explicitly called, with no cookies or redirect following', async () => {
    const fetch = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ text: ' A response provided only by this test server fixture. ' }) })
    vi.stubGlobal('fetch', fetch)
    const client = createTranscriptionClient('/custom/transcribe')
    expect(fetch).not.toHaveBeenCalled()
    const signal = new AbortController().signal
    const result = await client.transcribe(audio, signal)
    expect(result).toEqual({ status: 'transcribed', text: 'A response provided only by this test server fixture.' })
    const [url, options] = fetch.mock.calls[0]
    expect(url).toBe('/custom/transcribe')
    expect(options).toMatchObject({ method: 'POST', credentials: 'omit', redirect: 'error', cache: 'no-store', signal })
    expect(options.body.get('audio').size).toBe(audio.blob.size)
    expect(options.body.get('audio').type).toBe(audio.mimeType)
    expect(options.body.get('mime_type')).toBe(audio.mimeType)
    expect(options.body.get('duration_ms')).toBe('1234')
  })
  it.each([{ ok: false }, { ok: true, json: async () => ({}) }, { ok: true, json: async () => ({ text: '' }) }, { ok: true, json: async () => ({ text: 'x'.repeat(2001) }) }])('rejects failed or malformed responses', async response => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(response))
    await expect(createTranscriptionClient('/api/transcribe').transcribe(audio, new AbortController().signal)).rejects.toThrow()
  })
  it('passes aborts through without inventing a transcript', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new DOMException('Canceled', 'AbortError')))
    await expect(createTranscriptionClient('/api/transcribe').transcribe(audio, new AbortController().signal)).rejects.toThrow()
  })
})
