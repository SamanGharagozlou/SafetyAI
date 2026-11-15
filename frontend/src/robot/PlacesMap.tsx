import { HomeIcon } from './icons'
import type { RobotPlace, RobotView } from './robotApi'

// "Radar" layout of the destinations: distance rings around the start point, glowing nodes with name pills, an animated route and the robot.
// There is deliberately no floor plan or map image. Everything dynamic is an SVG attribute (the app's CSP forbids inline styles);
// colours, glow and motion live in styles.css (and stop under prefers-reduced-motion).
const W = 360
const H = 330
const PAD_X = 58
const PAD_Y = 46
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
  const scale = Math.min((W - 2 * PAD_X) / spanX, (H - 2 * PAD_Y) / spanY)
  const ox = (W - (maxX - minX) * scale) / 2 - minX * scale
  const oy = (H + (maxY - minY) * scale) / 2 + minY * scale
  const sx = (x: number) => ox + x * scale
  const sy = (y: number) => oy - y * scale

  const target = view?.destination ?? null
  const here = view?.at ?? null
  const targetPlace = target ? places.find(p => p.name === target) : undefined
  const origin = places.find(p => p.home) ?? places[0]
  const cx = sx(origin.x), cy = sy(origin.y)
  const reach = Math.max(...places.map(p => Math.hypot(p.x - origin.x, p.y - origin.y)), 1)
  const rings = Array.from({ length: Math.min(5, Math.ceil(reach) + 1) }, (_, i) => i + 1)

  // Name pills avoid the nodes and each other; first free spot of six wins.
  const boxes: Box[] = places.map(p => ({ x: sx(p.x) - 22, y: sy(p.y) - 22, w: 44, h: 44 }))
  const pills = places.map(p => {
    const x = sx(p.x), y = sy(p.y), w = Math.round(p.label.length * 6.5 + 20), h = 24
    const cl = (v: number) => Math.min(Math.max(v, 6), W - 6 - w)
    const spots: Array<[number, number]> = [[x + 22, y - 12], [x - 22 - w, y - 12], [cl(x - w / 2), y - 28 - h], [cl(x - w / 2), y + 28], [cl(x + 14), y - 28 - h], [cl(x - w + 14), y + 28]]
    const fits = (s: [number, number]) => s[0] >= 6 && s[0] + w <= W - 6 && s[1] >= 6 && s[1] + h <= H - 6
    const free = (s: [number, number]) => !boxes.some(o => overlaps({ x: s[0] - 2, y: s[1] - 2, w: w + 4, h: h + 4 }, o))
    const spot = spots.find(s => fits(s) && free(s)) ?? spots.find(fits) ?? spots[0]
    boxes.push({ x: spot[0], y: spot[1], w, h })
    return { x: spot[0], y: spot[1], w, h }
  })

  return <svg className="places-map" viewBox={`0 0 ${W} ${H}`} role="group" aria-label="Layout of the places the robot can take you to">
    <defs>
      <radialGradient id="map-bg" cx="50%" cy="48%" r="75%"><stop offset="0" stopColor="#164752"/><stop offset=".55" stopColor="#0d2a35"/><stop offset="1" stopColor="#081a23"/></radialGradient>
      <radialGradient id="node-glow"><stop offset="0" stopColor="#71e5cf" stopOpacity=".55"/><stop offset="1" stopColor="#71e5cf" stopOpacity="0"/></radialGradient>
      <radialGradient id="node-glow-hot"><stop offset="0" stopColor="#ffc46b" stopOpacity=".7"/><stop offset="1" stopColor="#ffc46b" stopOpacity="0"/></radialGradient>
      <linearGradient id="sweep" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stopColor="#71e5cf" stopOpacity=".26"/><stop offset="1" stopColor="#71e5cf" stopOpacity="0"/></linearGradient>
      <clipPath id="map-clip"><rect x="0" y="0" width={W} height={H} rx="26"/></clipPath>
    </defs>
    <g clipPath="url(#map-clip)">
      <rect className="map-bg" width={W} height={H} fill="url(#map-bg)"/>
      <g transform={`translate(${cx} ${cy})`} aria-hidden="true">
        <g className="radar-sweep"><path d={`M0 0 L${W} 0 A${W} ${W} 0 0 0 ${W * Math.cos(Math.PI / 4)} ${-W * Math.sin(Math.PI / 4)} Z`} fill="url(#sweep)"/></g>
        {rings.map(k => <g key={k}><circle className="map-ring" r={k * scale}/><text className="ring-label" x={k * scale * 0.7071 + 4} y={-k * scale * 0.7071 - 3}>{k} m</text></g>)}
        <line className="map-axis" x1={-W} y1="0" x2={W} y2="0"/><line className="map-axis" x1="0" y1={-H} x2="0" y2={H}/>
      </g>
      {robot && targetPlace && <>
        <line className="map-route-glow" x1={sx(robot.x)} y1={sy(robot.y)} x2={sx(targetPlace.x)} y2={sy(targetPlace.y)}/>
        <line className="map-route" x1={sx(robot.x)} y1={sy(robot.y)} x2={sx(targetPlace.x)} y2={sy(targetPlace.y)}/>
      </>}
      {places.map((p, i) => {
        const isTarget = target === p.name
        const isHere = here === p.name && !isTarget
        const cls = `place${isTarget ? ' is-target' : ''}${isHere ? ' is-here' : ''}${selected === p.name ? ' is-selected' : ''}${disabled ? ' is-disabled' : ''}`
        const choose = () => { if (!disabled) onSelect(p.name) }
        const x = sx(p.x), y = sy(p.y), pill = pills[i]
        return <g key={p.name} className={cls} role="button" tabIndex={disabled ? -1 : 0} aria-disabled={disabled} aria-pressed={selected === p.name}
                  aria-label={`${p.label}${isHere ? ', the robot is here' : ''}${isTarget ? ', heading there' : ''}`}
                  onClick={choose} onKeyDown={event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); choose() } }}>
          <circle className="place-hit" cx={x} cy={y} r="26"/>
          <circle className="place-glow" cx={x} cy={y} r="34" fill={isTarget ? 'url(#node-glow-hot)' : 'url(#node-glow)'}/>
          <circle className="place-select" cx={x} cy={y} r="23"/>
          <circle className="place-ring" cx={x} cy={y} r="16"/>
          <circle className="place-dot" cx={x} cy={y} r="6.5"/>
          {p.home && <g className="place-home" transform={`translate(${x - 8} ${y - 8}) scale(.667)`}><HomeIcon/></g>}
          <rect className="place-pill" x={pill.x} y={pill.y} width={pill.w} height={pill.h} rx="12"/>
          <text className="place-label" x={pill.x + pill.w / 2} y={pill.y + pill.h / 2 + 4} textAnchor="middle">{p.label}</text>
        </g>
      })}
      {robot && <g className="map-robot" transform={`translate(${sx(robot.x)} ${sy(robot.y)})`} aria-hidden="true">
        <circle className="robot-halo" r="20"/>
        <g transform={`rotate(${(-robot.yaw * 180) / Math.PI}) scale(1.15)`}><path d="M13 0 L-9 8.5 L-4.5 0 L-9 -8.5 Z"/></g>
      </g>}
    </g>
    <rect className="map-frame" x="0.5" y="0.5" width={W - 1} height={H - 1} rx="26"/>
  </svg>
}
