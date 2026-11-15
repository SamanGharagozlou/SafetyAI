import { useEffect, useState, useSyncExternalStore } from 'react'
import { VoiceRecorder } from './recorder'

export function useVoiceRecorder() {
  const [recorder] = useState(() => new VoiceRecorder())
  const snapshot = useSyncExternalStore(recorder.subscribe, recorder.getSnapshot)
  useEffect(() => {
    const onVisibility = () => {
      const status = recorder.getSnapshot().status
      if (document.visibilityState === 'hidden' && ['recording', 'requesting', 'stopping'].includes(status)) {
        recorder.cancel('Recording canceled when you left this tab. Nothing was sent. Start again when you’re ready.')
      }
    }
    const onPageHide = () => recorder.cancel()
    document.addEventListener('visibilitychange', onVisibility)
    window.addEventListener('pagehide', onPageHide)
    return () => {
      document.removeEventListener('visibilitychange', onVisibility)
      window.removeEventListener('pagehide', onPageHide)
      recorder.cancel()
    }
  }, [recorder])
  return { recorder, snapshot }
}
