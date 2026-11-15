import { CheckIcon, HomeIcon, PinIcon, WalkIcon } from './icons'
import type { RobotPlace, RobotView } from './robotApi'

interface Props { places: readonly RobotPlace[]; view: RobotView | null; disabled: boolean; selected: string | null; onSelect(name: string): void }

const metres = (n: number) => `${n.toFixed(1)} m`
/** Swipeable destination cards: the same choices as the dots, easier to read and to tap on a phone. */
export function PlaceCards({ places, view, disabled, selected, onSelect }: Props) {
  const from = view?.robot.x != null && view.robot.y != null ? { x: view.robot.x, y: view.robot.y } : null
  return <div className="place-rail" role="group" aria-label="Places">
    {places.map(p => {
      const isHere = view?.at === p.name && view.state !== 'NAVIGATING'
      const isTarget = view?.destination === p.name
      const meta = isTarget ? 'Heading there' : isHere ? 'You are here' : from ? `${metres(Math.hypot(p.x - from.x, p.y - from.y))} away` : p.description || 'Tap to go'
      return <button key={p.name} type="button" className={`place-card${isHere ? ' is-here' : ''}${isTarget ? ' is-target' : ''}${selected === p.name ? ' is-selected' : ''}`}
        disabled={disabled} onClick={() => onSelect(p.name)}>
        <span className="place-card-icon">{isHere ? <CheckIcon/> : isTarget ? <WalkIcon/> : p.home ? <HomeIcon/> : <PinIcon/>}</span>
        <span className="place-card-label">{p.label}</span>
        <span className="place-card-meta">{meta}</span>
      </button>
    })}
  </div>
}
