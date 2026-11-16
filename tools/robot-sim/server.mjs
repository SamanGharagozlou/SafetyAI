#!/usr/bin/env node
/**
 * A fake D1 robot, so the voice AI can be tuned with no hardware.
 *
 *   node tools/robot-sim/server.mjs            # -> http://127.0.0.1:5175
 *   HOST=0.0.0.0 node ...                      # when a tunnel needs to reach it
 *
 * Speaks the same protocol as the real d1-vapi-server: POST /vapi with VAPI's
 * tool-call envelope, GET /status with the same fields. So the console, the
 * buttons and a live Vapi assistant all work against it unchanged.
 *
 * It is deliberately NOT a yes-machine. Every refusal, quirk and unit below
 * was observed on the real robot, because a simulator that always succeeds
 * would tune the assistant into something that breaks on contact with
 * hardware:
 *
 *   - gestures and walks are REFUSED unless ctrl_mode == 1, with the real
 *     error string, which is what the assistant's recovery rule keys on
 *   - the robot SETTLES out of standing when idle, so a plan that stands up
 *     once and assumes it stays up is wrong
 *   - walk_distance BLOCKS until the walk completes and UNDERSHOOTS slightly
 *   - speed is a FRACTION of the cap, not m/s
 *   - ctrl_mode reports values the SDK does not document (18, 21)
 *
 * No auth: it is a fake robot and holds nothing worth stealing. Do not add
 * the real bearer token here.
 */
import { createServer } from 'node:http'

const PORT = Number(process.env.PORT ?? 5175)
const HOST = process.env.HOST ?? '127.0.0.1'

/**
 * LEUKERBAD THERME (formerly Burgerbad), Valais, Switzerland.
 *
 * SOURCING, because a wrong price spoken to a real guest is the worst failure
 * this thing can have. The ticket prices below are the ones attributed to the
 * official site (3h and day, adult/child). Everything else came from tourism
 * listings and guides, which DISAGREED with each other - other sources quoted
 * 37/22 and 37/18 for the day ticket, and hours ranging from 08:00-20:00 to
 * 10:00-18:00.
 *
 * leukerbad.ch returns 403 to automated fetching, so none of this was read
 * from the operator directly. CONFIRM AGAINST THE REAL TARIFF SHEET BEFORE A
 * PAYING GUEST HEARS IT. Anything not in this list, the agent must refuse to
 * guess and send the guest to the desk - that rule is in the prompt.
 */
const OFFERINGS = [
  { id: 'bath_3h',     name: 'Thermal bath, three hours', price: 'thirty-three francs for adults, twenty for children', dur: '3 hours',  note: 'Ten thermal pools, indoor and outdoor, twenty-eight to forty-three degrees.' },
  { id: 'bath_day',    name: 'Thermal bath, full day',    price: 'thirty-nine francs for adults, twenty-three for children', dur: 'all day', note: 'The whole day in the pools. Best value if you are staying past lunch.' },
  { id: 'sauna_entry', name: 'Sauna world',               price: 'extra on top of the bath ticket - the desk has the current rate', dur: 'with your ticket', note: 'Finnish sauna, herbal sauna, steam baths, and a sauna garden with the mountain panorama. Sixteen and over.' },
  { id: 'family',      name: 'Family visit',              price: 'children under six come in free, and there are family reductions at the desk', dur: 'any ticket', note: 'The Badnerblitz slide runs a hundred and twenty-six metres with two full three-sixty curves.' },
  { id: 'guest_card',  name: 'Leukerbad guest card discount', price: 'around fifteen percent off, if your hotel gave you the card', dur: 'any ticket', note: 'Worth asking at the desk - most hotels in the village hand it out.' },
  // Read straight off their own in-house screen in the stairwell, so this one
  // is solid where the ticket prices are second-hand.
  { id: 'brunch_spa',  name: 'Brunch and Spa, Sundays', price: 'fifty-one francs', dur: 'Sundays, 08:30 to 10:30', note: 'Brunch and the spa together. The best thing to offer a couple or a family on a Sunday morning.' },
  { id: 'massage',     name: 'Massage and therapy',    price: 'ask at the desk - the rate is not on the door sign', dur: '10:00 to 18:00', note: 'Booked at reception. Hours are shorter than the pools.' },
  // Read off the handwritten board by the till, so these are exact.
  { id: 'bakery',      name: 'Bakery counter',         price: 'Gipfeli and croissant one fifty; Schoggiweggli, Nussschnecke, pain au chocolat and escargot two seventy; Schinkengipfel and croissant au jambon three ninety', dur: 'while the cafe is open', note: 'Cheap way in for someone who is hungry, cold, or waiting for somebody. A Gipfeli and a coffee is under five francs.' },
]

/**
 * The building, as walked through on video: entrance plaza with the steaming
 * fountain -> reception desk (curved blue counter, shop behind it selling
 * towels and swim things) -> restaurant and coffee bar off to one side ->
 * stairs down to the changing rooms and lockers -> turnstiles -> pools.
 */
/*
 * `aliases` exist because guests do not speak in ids. Someone asks for "the
 * spa", "the baths", "somewhere to eat" - and the first version matched only
 * on the id or a literal substring of `name`, so "take me to the thermal
 * baths" came back "unknown destination" while the list had just offered "the
 * thermal pools". The guest hears a robot contradicting itself.
 */
const DESTINATIONS = [
  { id: 'reception',  name: 'the reception desk, just inside the doors', walk_cm: 60,
    aliases: ['desk', 'front desk', 'check in', 'checkin', 'entrance', 'tickets', 'ticket'] },
  { id: 'shop',       name: 'the shop behind reception, for towels and swim things', walk_cm: 70,
    aliases: ['store', 'towel', 'towels', 'swimsuit', 'costume', 'goggles'] },
  { id: 'restaurant', name: 'the restaurant and coffee bar', walk_cm: 90,
    aliases: ['food', 'eat', 'lunch', 'dinner', 'coffee', 'cafe', 'bar', 'drink', 'hungry'] },
  { id: 'pizzeria',   name: 'the pizzeria with the sun terrace', walk_cm: 110,
    aliases: ['pizza', 'terrace', 'sun terrace', 'outside table'] },
  { id: 'changing',   name: 'the stairs down to the changing rooms', walk_cm: 100, stairs: true,
    aliases: ['changing room', 'changing rooms', 'locker', 'lockers', 'change', 'cabin'] },
  { id: 'pools',      name: 'the thermal pools, through the turnstiles', walk_cm: 150, stairs: true,
    aliases: ['pool', 'thermal', 'thermal bath', 'thermal baths', 'bath', 'baths',
              'swim', 'swimming', 'water', 'therme'] },
  { id: 'sauna',      name: 'the sauna world', walk_cm: 140, restricted: true,
    aliases: ['spa', 'wellness', 'steam', 'steam room', 'hammam', 'saunas'] },
  { id: 'elevator',   name: 'the lift', walk_cm: 80,
    aliases: ['elevator', 'lift', 'upstairs', 'downstairs'] },
  { id: 'staff_only', name: 'the staff area', walk_cm: 50, restricted: true,
    aliases: ['staff', 'office', 'back office', 'employees'] },
]

/*
 * Forgiving match: exact id, then alias, then substring either way, then a
 * single shared significant word. Deliberately generous - the cost of guiding
 * someone to the pools when they meant the sauna is that they say so and the
 * robot corrects; the cost of refusing is a dead end mid-conversation.
 */
const STOPWORDS = new Set(['the', 'a', 'an', 'to', 'me', 'please', 'go', 'take',
  'walk', 'show', 'where', 'is', 'are', 'of', 'in', 'at', 'and', 'room', 'area'])

function findDestination(query) {
  const q = String(query ?? '').toLowerCase().replace(/[^a-z0-9 ]/g, ' ').trim()
  if (!q) return null
  const words = q.split(/\s+/).filter(w => w && !STOPWORDS.has(w))

  let d = DESTINATIONS.find(x => x.id === q)
  if (d) return d
  d = DESTINATIONS.find(x => (x.aliases ?? []).some(a => a === q))
  if (d) return d
  d = DESTINATIONS.find(x => x.name.toLowerCase().includes(q) || q.includes(x.id))
  if (d) return d
  d = DESTINATIONS.find(x => (x.aliases ?? []).some(a => q.includes(a)))
  if (d) return d
  // last resort: any significant word shared with an alias or the name
  return DESTINATIONS.find(x => words.some(w => w.length > 2 &&
    ((x.aliases ?? []).some(a => a.includes(w)) || x.name.toLowerCase().includes(w)))) ?? null
}

/**
 * Promotions change by time of day, per the concept board's "controllable by
 * time/season". The greeter should pitch what is actually good value NOW -
 * a day ticket at seven in the evening is a bad recommendation and a guest
 * who notices that stops trusting the robot.
 */
function currentPromotion() {
  const now = new Date()
  const h = now.getHours()
  // Sunday morning has a real, verified offer, so it beats anything generic.
  if (now.getDay() === 0 && h < 11) {
    return 'It is Sunday morning - Brunch and Spa runs 08:30 to 10:30 for fifty-one francs. Lead with this.'
  }
  if (h < 10) return 'Mornings are the quietest time in the grotto, before the crowd arrives. Note the sauna and hammam only open at ten.'
  if (h < 14) return 'The full day ticket is the one that pays off if you are staying past lunch.'
  if (h < 17) return 'Afternoon: the outdoor pools with the mountains in view are the thing to do today.'
  if (h < 19) return 'This late, the three hour ticket is the better buy than a full day. Sauna and hammam close at seven.'
  return 'Close to closing - the pools shut at eight, so only sell a short ticket and be honest that it is nearly over.'
}

const bookings = []

const GESTURES = ['bow', 'greet', 'look_around', 'nod', 'shake', 'squat', 'sway_circle', 'tilt', 'welcome', 'wiggle']
const DIRECTIONS = ['forward', 'backward', 'turn_left', 'turn_right', 'strafe_left', 'strafe_right']
const COMMANDS = ['stand_up', 'lie_down', 'shake_hand', 'damp']
const GAITS = ['move', 'crawl', 'climb']

/** Seconds of stillness after which the real robot settles back down. */
const SETTLE_AFTER_S = 45

const robot = {
  connected: true,
  ctrl_mode: 51,            // starts lying, as it usually is
  moving: false,
  gesture_active: false,
  gesture_name: null,
  gait: 'move',
  last_action: 'none',
  battery_pct: 87,
  lastMotion: Date.now(),
}

const standing = () => robot.ctrl_mode === 1
const touch = () => { robot.lastMotion = Date.now() }

function settleCheck() {
  if (standing() && !robot.moving && !robot.gesture_active &&
      (Date.now() - robot.lastMotion) / 1000 > SETTLE_AFTER_S) {
    robot.ctrl_mode = 18          // the undocumented idle value the real robot reports
    robot.last_action = 'settled'
    log('(settled out of standing on its own)')
  }
}

const log = m => console.log(`${new Date().toLocaleTimeString()}  ${m}`)
const notStanding = () => `Sorry, that failed: robot is not standing (ctrl_mode=${robot.ctrl_mode}); send command stand_up first`
const sleep = ms => new Promise(r => setTimeout(r, ms))

async function runTool(name, args) {
  settleCheck()

  if (!robot.connected) return 'Sorry, that failed: no live link to the robot'

  switch (name) {
    case 'robot_status': {
      const posture = standing() ? 'standing' : robot.ctrl_mode === 51 ? 'lying down' : 'not standing'
      if (robot.arrived && !robot.moving) {
        const where = robot.arrived
        robot.arrived = null          // report arrival once, not forever
        return `ARRIVED at ${where}. Tell the guest you are both here now. Robot is ${posture}.`
      }
      if (robot.moving) return `Still walking, not there yet. Robot is ${posture}.`
      return `Robot is connected, control mode ${robot.ctrl_mode}, stationary and ${posture}.`
    }

    case 'robot_command': {
      const c = args.command
      if (!COMMANDS.includes(c)) return `Sorry, that failed: unknown command '${c}'; use one of ${JSON.stringify(COMMANDS)}`
      if (c === 'damp') return 'Sorry, that failed: damp is not available'
      await sleep(400)
      if (c === 'stand_up') robot.ctrl_mode = 1
      if (c === 'lie_down') robot.ctrl_mode = 51
      if (c === 'shake_hand' && !standing()) return notStanding()
      robot.last_action = c
      touch()
      return `Done: ${c.replace('_', ' ')}.`
    }

    case 'do_gesture': {
      const g = args.gesture
      if (!GESTURES.includes(g)) return `Sorry, that failed: unknown gesture '${g}'`
      if (!standing()) return notStanding()
      robot.gesture_active = true
      robot.gesture_name = g
      robot.last_action = `gesture ${g}`
      touch()
      // Fire-and-forget on the real robot: the call returns at once and the
      // gesture plays out afterwards.
      const dur = g === 'look_around' ? 7900 : 3000
      setTimeout(() => { robot.gesture_active = false; robot.gesture_name = null; touch() }, dur)
      return `Performing ${g.replace('_', ' ')}.`
    }

    case 'move_robot': {
      const d = args.direction
      if (!DIRECTIONS.includes(d)) return `Sorry, that failed: unknown direction '${d}'`
      if (!standing()) return notStanding()
      const duration = Math.min(5, Math.max(0.1, Number(args.duration ?? 1.5)))
      const speed = Math.min(1, Math.max(0.05, Number(args.speed ?? 0.4)))
      robot.moving = true
      robot.last_action = `move ${d}`
      touch()
      setTimeout(() => { robot.moving = false; touch() }, duration * 1000)
      return `Moving ${d.replace('_', ' ')} for ${duration} seconds at ${(speed * 0.6).toFixed(2)} metres per second.`
    }

    case 'walk_distance': {
      const cm = Number(args.distance_cm)
      if (!Number.isFinite(cm)) return 'Sorry, that failed: distance_cm is required; refusing to guess a distance and move the robot'
      if (cm < 2 || cm > 200) return `Sorry, that failed: distance_cm must be 2-200, got ${cm}`
      if (!standing()) return notStanding()
      const dir = args.direction ?? 'forward'
      robot.moving = true
      robot.last_action = `walk ${dir} ${cm}cm`
      touch()
      // BLOCKS, like the real one: roughly 7s for 30cm.
      await sleep(Math.min(25000, (cm / 30) * 7000))
      robot.moving = false
      touch()
      const actual = cm * (0.93 + Math.random() * 0.06)   // it undershoots
      return `Walked ${actual.toFixed(1)} centimetres ${dir}.`
    }

    case 'set_gait': {
      if (!GAITS.includes(args.gait)) return `Sorry, that failed: unknown gait '${args.gait}'`
      robot.gait = args.gait
      robot.last_action = `gait ${args.gait}`
      return `Gait set to ${args.gait}.`
    }

    /* ---------------- concierge / commercial ---------------- */

    case 'list_offerings': {
      const list = OFFERINGS.map(o => `${o.name}, ${o.price}, ${o.dur}. ${o.note}`).join(' ')
      return `What we offer: ${list} RIGHT NOW: ${currentPromotion()}`
    }

    case 'show_payment_qr': {
      // The robot has a screen. It shows a code; it never learns the outcome.
      const o = OFFERINGS.find(x => x.id === args.offering_id)
      if (!o) return `Sorry, that failed: unknown offering '${args.offering_id}'.`
      log(`QR SHOWN for ${o.name}`)
      return `Showing a payment code on my screen for ${o.name}. I cannot tell whether it goes through - the desk confirms that, so do not tell them it worked.`
    }

    case 'book_treatment': {
      const o = OFFERINGS.find(x => x.id === args.offering_id || x.name.toLowerCase() === String(args.offering_id ?? '').toLowerCase())
      if (!o) return `Sorry, that failed: unknown offering '${args.offering_id}'. Call list_offerings for the valid ids.`
      if (!args.guest_name) return 'Sorry, that failed: guest_name is required before a booking can be held.'
      const ref = 'TB' + String(1000 + bookings.length + 1)
      bookings.push({ ref, offering: o.id, name: args.guest_name, when: args.when ?? 'unspecified' })
      log(`BOOKING ${ref}: ${o.name} for ${args.guest_name} (${args.when ?? 'time unspecified'})`)
      return `Held: ${o.name} for ${args.guest_name}, ${args.when ?? 'time to be confirmed'}. Reference ${ref}. ${o.price}, payable at reception. Not yet paid.`
    }

    case 'call_staff': {
      const urgency = args.urgency ?? 'routine'
      log(`STAFF CALLED (${urgency}): ${args.reason ?? 'no reason given'}`)
      return urgency === 'emergency'
        ? 'Staff alerted immediately. Someone is on their way now.'
        : `A staff member has been asked to come over. Reason logged: ${args.reason ?? 'general'}.`
    }

    case 'list_destinations':
      // Carry the id alongside the prose. Without it the assistant can only
      // echo back the description it was given, and guide_to then has to
      // guess - which is exactly how "the thermal baths" became unknown.
      return `I can walk you to: ${DESTINATIONS.map(d => `${d.name} [${d.id}]`).join(', ')}.`
        + ' Pass the name in square brackets to guide_to.'

    case 'guide_to': {
      const d = findDestination(args.destination)
      if (!d) return `Sorry, that failed: unknown destination '${args.destination}'. Call list_destinations first.`
      if (!standing()) return notStanding()
      // Restricted zones are refused by the robot, not negotiated by the
      // assistant - and the refusal carries the alternative with it, so the
      // model always has something better to offer than an apology.
      if (d.restricted) {
        return d.id === 'staff_only'
          ? 'Refused by the robot\'s safety system: that area is staff only. I can fetch a member of the team instead.'
          : `Refused by the robot's safety system: ${d.name} needs a staff member to take you in. I can walk you to the reception desk and they will sort it.`
      }
      // Accessibility: stairs are routed via the lift on request, per the
      // concept board - elderly guests go via the lift BY DEFAULT.
      const viaLift = args.step_free === true && d.stairs
      robot.moving = true
      robot.last_action = `guiding to ${d.id}${viaLift ? ' via the lift' : ''}`
      touch()
      const seconds = Math.round((d.walk_cm / 30) * 7 * (viaLift ? 1.4 : 1))
      setTimeout(() => {
        robot.moving = false
        robot.last_action = `ARRIVED at ${d.id}`
        robot.arrived = d.name
        touch()
      }, seconds * 1000)
      return `Setting off to ${d.name}${viaLift ? ', taking the lift rather than the stairs' : ''}. About ${seconds} seconds. Ask the guest to follow and stay close. Call robot_status to see when we have ARRIVED, and tell them when we do.`
    }

    case 'stop_robot':
      robot.moving = false
      robot.gesture_active = false
      robot.gesture_name = null
      robot.last_action = 'stop'
      touch()
      return 'Stopped.'

    default:
      return `Unknown tool ${name}. Available: move_robot, walk_distance, stop_robot, robot_command, set_gait, do_gesture, robot_status`
  }
}

const readBody = req => new Promise(resolve => {
  let raw = ''
  req.on('data', c => { raw += c })
  req.on('end', () => resolve(raw))
})

createServer(async (req, res) => {
  const send = (code, body) => {
    res.writeHead(code, { 'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*' })
    res.end(JSON.stringify(body))
  }
  const url = new URL(req.url, `http://x`)

  if (url.pathname === '/health') return send(200, { ok: true, service: 'd1-robot-SIMULATOR' })

  if (url.pathname === '/status') {
    settleCheck()
    robot.battery_pct = Math.max(0, robot.battery_pct - 0.002)
    return send(200, {
      ok: true,
      connected: robot.connected,
      link_ok: robot.connected,
      posture_known: robot.connected,
      ctrl_mode: robot.connected ? robot.ctrl_mode : null,
      standing: robot.connected && standing(),
      moving: robot.moving,
      gesture_active: robot.gesture_active,
      gesture_name: robot.gesture_name,
      gait: robot.gait,
      last_action: robot.last_action,
      odom_available: true,
      battery_pct: Math.round(robot.battery_pct),
      simulated: true,
      gestures_available: GESTURES,
      directions_available: DIRECTIONS,
    })
  }

  // Toggle the link, to rehearse what the assistant says when the robot is
  // unreachable - the case that actually bit us in the field.
  if (url.pathname === '/sim/link') {
    robot.connected = url.searchParams.get('up') !== 'false'
    log(`link forced ${robot.connected ? 'UP' : 'DOWN'}`)
    return send(200, { connected: robot.connected })
  }

  if (req.method === 'POST' && (url.pathname === '/vapi' || url.pathname === '/')) {
    // A malformed body used to throw here, and because this handler is async
    // with nothing catching it, the rejection took the WHOLE SIMULATOR DOWN -
    // one stray request and the demo is over. Vapi is not the only thing that
    // can reach this port once it is tunnelled to the internet.
    let body
    try {
      body = JSON.parse(await readBody(req) || '{}')
    } catch (error) {
      log(`bad request body: ${error.message}`)
      return send(400, { error: `body is not JSON: ${error.message}` })
    }
    const calls = body?.message?.toolCallList ?? body?.message?.toolCalls ?? []
    const results = []
    for (const c of calls) {
      const name = c.name ?? c.function?.name
      let args = c.arguments ?? c.function?.arguments ?? {}
      if (typeof args === 'string') { try { args = JSON.parse(args) } catch { args = {} } }
      const started = Date.now()
      const result = await runTool(name, args)
      log(`${name} ${JSON.stringify(args)} -> ${result}  [${Date.now() - started}ms]`)
      results.push({ toolCallId: c.id ?? 'sim', result })
    }
    return send(200, { results })
  }

  send(404, { error: 'not found' })
}).listen(PORT, HOST, () => {
  console.log(`D1 robot SIMULATOR on http://${HOST}:${PORT}`)
  console.log('Not a yes-machine: it refuses gestures and walks unless standing,')
  console.log('settles out of standing when idle, and makes walks block and undershoot.')
  console.log(`Force the link down with: curl "http://${HOST}:${PORT}/sim/link?up=false"\n`)
})
