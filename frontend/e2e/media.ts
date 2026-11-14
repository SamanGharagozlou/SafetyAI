import type { Page } from '@playwright/test'

declare global {
  interface Window {
    __voiceTest: {
      requests: number
      stops: number
      revoked: number
      resolve: (() => void) | null
    }
  }
}

/** Synthetic audio and permissions: browser tests never open the real microphone. */
export async function mockMedia(page: Page, permission: 'grant' | 'pending' | 'NotAllowedError' | 'NotFoundError' | 'NotReadableError' = 'grant') {
  await page.addInitScript(({ permission }) => {
    const state = { requests: 0, stops: 0, revoked: 0, resolve: null as (() => void) | null }
    window.__voiceTest = state
    const originalRevoke = URL.revokeObjectURL.bind(URL)
    URL.revokeObjectURL = url => { state.revoked++; originalRevoke(url) }
    function stream() {
      const track = { readyState: 'live', onended: null, stop() { if (this.readyState === 'live') state.stops++; this.readyState = 'ended' } }
      return { getTracks: () => [track], getAudioTracks: () => [track] } as unknown as MediaStream
    }
    Object.defineProperty(navigator, 'mediaDevices', { configurable: true, value: {
      getUserMedia: async () => {
        state.requests++
        if (permission === 'pending') return new Promise<MediaStream>(resolve => { state.resolve = () => resolve(stream()) })
        if (permission !== 'grant') throw new DOMException('Synthetic permission fixture', permission)
        return stream()
      },
    } })
    // A short silent WAV permits actual playback of the synthetic review clip.
    function wav() {
      const data = new ArrayBuffer(44 + 8000)
      const view = new DataView(data)
      const chars = (offset: number, value: string) => { for (let i = 0; i < value.length; i++) view.setUint8(offset + i, value.charCodeAt(i)) }
      chars(0, 'RIFF'); view.setUint32(4, data.byteLength - 8, true); chars(8, 'WAVE')
      chars(12, 'fmt '); view.setUint32(16, 16, true); view.setUint16(20, 1, true); view.setUint16(22, 1, true)
      view.setUint32(24, 8000, true); view.setUint32(28, 16000, true); view.setUint16(32, 2, true); view.setUint16(34, 16, true)
      chars(36, 'data'); view.setUint32(40, 8000, true)
      return new Blob([data], { type: 'audio/wav' })
    }
    class SyntheticRecorder {
      static isTypeSupported = (type: string) => type === 'audio/wav'
      state: RecordingState = 'inactive'
      mimeType = 'audio/wav'
      ondataavailable: ((event: BlobEvent) => void) | null = null
      onstop: (() => void) | null = null
      onerror: (() => void) | null = null
      start() { this.state = 'recording' }
      stop() {
        this.state = 'inactive'
        queueMicrotask(() => { this.ondataavailable?.({ data: wav() } as BlobEvent); this.onstop?.() })
      }
    }
    Object.defineProperty(window, 'MediaRecorder', { configurable: true, value: SyntheticRecorder })
  }, { permission })
}
