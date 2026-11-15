import { useEffect, useState } from 'react'
import { Robot } from '../Artwork'
import type { CallState } from './state'

export function CallVisualizer({ state, active, level, levels }: { state: CallState; active: boolean; level: number; levels: readonly number[] }) {
  const [reduced, setReduced] = useState(() => window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false)
  useEffect(() => {
    const media = window.matchMedia?.('(prefers-reduced-motion: reduce)')
    if (!media) return
    const change = () => setReduced(media.matches)
    media.addEventListener?.('change', change)
    return () => media.removeEventListener?.('change', change)
  }, [])
  const measured = active && !reduced ? level : 0
  return <div className="call-visual" data-state={state} data-microphone={active ? 'on' : 'off'}>
    <div className="orb-scene">
      <svg className="audio-orb" viewBox="0 0 300 300" fill="none" aria-hidden="true">
        <circle className="orb-ambient" cx="150" cy="150" r="132"/>
        <circle className="orb-ring" cx="150" cy="150" r={112 + measured * 15} strokeWidth={1.5 + measured * 2}/>
        <circle className="orb-inner" cx="150" cy="150" r={101 + measured * 5}/>
      </svg>
      <Robot/>
    </div>
    <svg className="call-waveform" viewBox="0 0 256 40" fill="none" aria-hidden="true" data-reactive={active && !reduced ? 'true' : 'false'}>
      {levels.map((value, index) => {
        const amplitude = active && !reduced ? value * 17 : 0
        return <line key={index} x1={8 + index * 16} x2={8 + index * 16} y1={19 - amplitude} y2={21 + amplitude} stroke="currentColor" strokeWidth="3" strokeLinecap="round"/>
      })}
    </svg>
  </div>
}
