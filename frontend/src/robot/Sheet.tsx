import { useEffect } from 'react'
import type { ReactNode } from 'react'

/** A bottom sheet for one decision at a time. Escape and a tap on the dimmed area close it; the page behind does not scroll. */
export function Sheet({ labelledBy, onClose, children }: { labelledBy: string; onClose(): void; children: ReactNode }) {
  useEffect(() => {
    const key = (event: KeyboardEvent) => { if (event.key === 'Escape') onClose() }
    document.addEventListener('keydown', key)
    const previous = document.body.style.overflow
    document.body.style.overflow = 'hidden'
    return () => { document.removeEventListener('keydown', key); document.body.style.overflow = previous }
  }, [onClose])
  return <div className="sheet-layer">
    <div className="sheet-backdrop" onClick={onClose} aria-hidden="true"/>
    <div className="sheet" role="dialog" aria-modal="true" aria-labelledby={labelledBy}>
      <span className="sheet-handle" aria-hidden="true"/>
      {children}
    </div>
  </div>
}
