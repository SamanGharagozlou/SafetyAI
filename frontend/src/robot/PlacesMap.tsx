import type { RobotPlace, RobotView } from './robotApi'

// Layout of the destinations only: dots with rings and the robot arrow. There is deliberately no floor plan or map image.
// Everything dynamic is an SVG attribute (the app's CSP forbids inline styles); colours and the pulse live in styles.css.
const W = 320
const H = 270
const PAD = 38
const MIN_EXTENT_M = 3

interface Props {
  places: readonly RobotPlace[]
  view: RobotView | null
  selected: string | null
  disabled?: boolean
  onSelect(name: string): void
}

interface Box { x: number; y: number; w: number; h: number }
const overlaps = (a: Box, b: Box) => a.x < b.x + b.w && a.x + a.w > b.x && a.y < b.y + b.h && a.y + a.h > b.y

export function PlacesMap({ places, view, selected, disabled = false, onSelect }: Props) {
  if (!places.length) return null
  const robot = view?.robot.x != null && view.robot.y != null ? { x: view.robot.x, y: view.robot.y, yaw: view.robot.yaw ?? 0 } : null
  const xs = [...places.map(p => p.x), ...(robot ? [robot.x] : [])]
  const ys = [...places.map(p => p.y), ...(robot ? [robot.y] : [])]
  const minX = Math.min(...xs), maxX = Math.max(...xs), minY = Math.min(...ys), maxY = Math.max(...ys)
  const spanX = Math.max(maxX - minX, MIN_EXTENT_M), spanY = Math.max(maxY - minY, MIN_EXTENT_M)
  const scale = Math.min((W - 2 * PAD) / spanX, (H - 2 * PAD) / spanY)
  const ox = (W - (maxX - minX) * scale) / 2 - minX * scale
  const oy = (H + (maxY - minY) * scale) / 2 + minY * scale
  const sx = (x: number) => ox + x * scale
  const sy = (y: number) => oy - y * scale

  const target = view?.destination ?? null
  const here = view?.at ?? null
  const targetPlace = target ? places.find(p => p.name === target) : undefined

  // Labels avoid the dots and each other; first free spot of six wins.
  const boxes: Box[] = places.map(p => ({ x: sx(p.x) - 19, y: sy(p.y) - 19, w: 38, h: 38 }))
  const labels = places.map(p => {
    const x = sx(p.x), y = sy(p.y), tw = p.label.length * 6.3, th = 13
    const spots: Array<[number, number]> = [[x + 15, y - 14], [x - 15 - tw, y - 14], [x + 15, y + 24], [x - 15 - tw, y + 24], [x - tw / 2, y - 26], [x - tw / 2, y + 36]]
    const fits = (s: [number, number]) => s[0] >= 4 && s[0] + tw <= W - 4 && s[1] - th >= 2 && s[1] <= H - 3
    const free = (s: [number, number]) => { const b = { x: s[0] - 2, y: s[1] - th, w: tw + 4, h: th + 3 }; return !boxes.some(o => overlaps(b, o)) }
    const spot = spots.find(s => fits(s) && free(s)) ?? spots.find(fits) ?? spots[0]
    boxes.push({ x: spot[0] - 2, y: spot[1] - th, w: tw + 4, h: th + 3 })
    return spot
  })

  const gridX: number[] = []
  const gridY: number[] = []
  for (let g = Math.ceil((0 - ox) / scale); sx(g) <= W; g++) if (sx(g) >= 0) gridX.push(sx(g))
  for (let g = Math.ceil((oy - H) / scale); sy(g) >= 0; g++) if (sy(g) <= H) gridY.push(sy(g))

  return <svg className="places-map" viewBox={`0 0 ${W} ${H}`} role="group" aria-label="Layout of the places the robot can take you to">
    <rect className="map-bg" x="0.5" y="0.5" width={W - 1} height={H - 1} rx="14"/>
    {gridX.map(x => <line key={`x${x}`} className="map-grid" x1={x} y1="6" x2={x} y2={H - 6}/>)}
    {gridY.map(y => <line key={`y${y}`} className="map-grid" x1="6" y1={y} x2={W - 6} y2={y}/>)}
    {robot && targetPlace && <line className="map-route" x1={sx(robot.x)} y1={sy(robot.y)} x2={sx(targetPlace.x)} y2={sy(targetPlace.y)}/>}
    {places.map((p, i) => {
      const isTarget = target === p.name
      const isHere = here === p.name && !isTarget
      const cls = `place${isTarget ? ' is-target' : ''}${isHere ? ' is-here' : ''}${selected === p.name ? ' is-selected' : ''}${disabled ? ' is-disabled' : ''}`
      const choose = () => { if (!disabled) onSelect(p.name) }
      return <g key={p.name} className={cls} role="button" tabIndex={disabled ? -1 : 0} aria-disabled={disabled} aria-pressed={selected === p.name}
                aria-label={`${p.label}${isHere ? ', the robot is here' : ''}${isTarget ? ', heading there' : ''}`}
                onClick={choose} onKeyDown={event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); choose() } }}>
        <circle className="place-hit" cx={sx(p.x)} cy={sy(p.y)} r="24"/>
        <circle className="place-select" cx={sx(p.x)} cy={sy(p.y)} r="22"/>
        <circle className="place-ring" cx={sx(p.x)} cy={sy(p.y)} r="15"/>
        <circle className="place-dot" cx={sx(p.x)} cy={sy(p.y)} r="6.5"/>
        <text className="place-label place-label-halo" x={labels[i][0]} y={labels[i][1]}>{p.label}</text>
        <text className="place-label" x={labels[i][0]} y={labels[i][1]}>{p.label}</text>
      </g>
    })}
    {robot && <g className="map-robot" transform={`translate(${sx(robot.x)} ${sy(robot.y)}) rotate(${(-robot.yaw * 180) / Math.PI})`} aria-hidden="true">
      <path d="M13 0 L-9 8 L-5 0 L-9 -8 Z"/>
    </g>}
  </svg>
}
