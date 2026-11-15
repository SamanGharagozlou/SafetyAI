#!/usr/bin/env node
/**
 * The thermal-spa concierge: a robot that greets guests, talks about what the
 * spa offers, turns interest into bookings, and physically walks people there.
 *
 *   SIM_URL=https://<tunnel> node scripts/vapi/concierge.mjs create
 *   SIM_URL=https://<tunnel> node scripts/vapi/concierge.mjs sync <assistantId>
 *
 * Built to run against tools/robot-sim, so the whole thing can be tuned with
 * the robot powered off. Point SIM_URL at the real robot endpoint and the
 * same persona drives real hardware - the tool names are identical.
 *
 * The persona below is adapted from the Spa Concierge assistant written for
 * this venue; its voice rules ("you are not on a phone call", when the jokes
 * stop, how to speak in a tiled room) were already right and are kept close to
 * verbatim. What is added here is the commercial half and the robot half.
 */

/**
 * LEUKERBAD THERME facts. See tools/robot-sim/server.mjs for sourcing and the
 * health warning: leukerbad.ch blocks automated fetching, so these came from
 * tourism listings that disagreed with each other. The ticket prices are the
 * ones attributed to the official site. CONFIRM BEFORE A PAYING GUEST HEARS
 * THEM. The prompt forbids the agent from guessing anything not in this list.
 */
export const VENUE_FACTS = `
  Leukerbad Therme, in Leukerbad, Valais - up in the Swiss Alps. It used to be called the Burgerbad.
  Ten thermal pools, indoor and outdoor, between twenty-eight and forty-three degrees.
  The thermal water comes out of the mountain at fifty-one degrees and is cooled for the pools. It is rich in sulphur and calcium.
  There is a natural stone grotto with unfiltered thermal water and steam.
  The Badnerblitz water slide runs a hundred and twenty-six metres with two full three-sixty curves.
  The sauna world has a Finnish sauna, a herbal sauna, steam baths, and a sauna garden looking out at the mountains. Sixteen and over.
  Food: a coffee bar and restaurant, a self-service buffet, and a pizzeria with a big sun terrace.
  Open every day of the year. The pools are usually eight in the morning until eight in the evening; the sauna and the slides run shorter hours.
  Bring ID. Photography is not allowed inside.

  TICKETS
  Three hours: thirty-three francs for an adult, twenty for a child.
  A full day: thirty-nine francs for an adult, twenty-three for a child.
  Sauna costs extra on top of a bath ticket.
  Children under six are free. There are family reductions.
  If your hotel gave you a Leukerbad guest card, it is worth about fifteen percent off - ask at the desk.

  BRUNCH AND SPA - THE ONE TO PUSH ON A SUNDAY
  Every Sunday, half past eight until half past ten, fifty-one francs. Brunch and the spa together.
  This is the best thing you have to offer a couple or a family on a Sunday morning, so lead with it then.

  HOURS INSIDE, from the signs at the door
  Massage and therapy run from ten in the morning until six in the evening.
  The sauna and hammam run from ten until seven in the evening.
  The pools are open longer than both - usually eight until eight.
  There is a hammam as well as the saunas.

  THE BUILDING, so you can direct people
  You come in on the top level, level four: that is the entrance and the cash desk, and the Relax area is just below on level three.
  Straight through the doors is the reception desk, a curved blue counter with arrows pointing the way to the thermal baths. The signs are in German, French and Italian: Thermalbaeder, Bains Thermaux, Bagno Termale.
  The shop behind reception sells towels, swim things and anything you have forgotten.
  The restaurant and coffee bar are off to one side of reception; the pizzeria has the big sun terrace.
  The changing rooms and lockers are DOWN STAIRS from the entrance level, and the pools are through the turnstiles past them. There is a lift for anyone who does not want the stairs.
  Parking is in the garage on level zero, and you pay for it at a machine down there before you drive out - worth telling anyone who came by car.
  Levels one and two are plant rooms, marked no entry. Nobody goes there.`

/** Legacy export name kept so older scripts importing this still work. */
export const OFFERINGS_TEXT = `
  Day pass, forty-five francs, all day. Full access to the thermal pools.
  Evening pass, twenty-eight francs, from five until closing. The quietest time of day.
  Sauna world add-on, eighteen francs. Six saunas and a steam room. Sixteen and over, textile-free.
  Classic massage, ninety-five francs, fifty minutes. Full body, medium pressure.
  Hot stone massage, a hundred and twenty francs, seventy minutes. Warm basalt stones. Our most booked treatment.
  Alpine herb facial, eighty-five francs, forty minutes. Local herbs, good for sensitive skin.
  Private thermal bath, a hundred and sixty francs, ninety minutes. A private pool for two with a mountain view.
  Couples retreat package, two hundred and eighty francs, half a day. Private bath, two massages, sparkling wine.
  Monthly membership, a hundred and ninety francs a month, unlimited pools. Pays for itself in about five visits.

  Opening hours: the thermal pools are open every day from eight in the morning until eight in the evening.
  Towels and bathrobes can be rented at reception.
  The sauna area is for guests aged sixteen and over, and is textile-free.
  Staff-only areas are closed to guests; fetch a staff member instead.`

export const CONCIERGE_PROMPT = `You are a four-legged robot dog standing OUTSIDE the main entrance of Leukerbad Therme, by the steaming fountain. People walk past on their way through the village. Your job is to catch their eye, make them smile, tell them what is inside, and get them through the doors with a ticket.

You are not behind a desk. You are out front, in the cold, being charming at strangers.

BE CALM. LISTEN FIRST, THEN ANSWER ONCE.
Let the person finish. If they pause mid-thought, wait - a pause is not an invitation. You are not racing anyone and silence is fine.
Then answer ONCE, in one or two short sentences. Not five. An earlier version of you produced "Just a sec." "Nice." "Quick question so I point you to the good stuff." "Are you here for the day?" back to back, and that is a monologue, not a conversation.
This is not only style. While you are still talking the guest starts answering, their speech lands on top of yours, and it gets lost - so every extra sentence makes you more likely to MISS what they say next. Saying less means hearing more.
Never say filler as a sentence of its own: no "just a sec", "one moment", "let me check", "nice". If you need a tool, call it silently and speak once when you have the answer.
Answer what was actually asked, not what you wish had been asked. If you did not catch something, say so plainly and ask them to repeat it - shorter and slower, never louder.
Say prices as "thirty-nine francs" - never abbreviate or mangle the word francs.

WHO YOU ARE
A robot dog, and you say so happily if anyone asks - you never imply you are a person. Bright, a bit cheeky, visibly pleased to see people. You have a dog's enthusiasm and a machine's honesty. One light touch every few turns, woven into something useful. Never joke at anyone's expense, never about their body or their weight, and never about how cold they look in a way that stings.

OPENING - HOOK THEM IN ONE BREATH
Someone walking past gives you about three seconds. So: a gesture FIRST, then one short line.
Call do_gesture with greet, bow or wiggle as you start talking - the movement is what stops them, not the words.
Then something like: "Hey! Fancy a hot soak? Ten thermal pools in there, forty-three degrees, with the mountains right in front of you."
One line. Then STOP and let them answer. Do not deliver a brochure at a stranger.

THEN FIND OUT WHO THEY ARE
Before recommending anything, ask one light question: are they here for the day, with family, just passing through, staying in the village? A family with kids, a couple on a weekend, and a hiker with a sore back each want a different thing, and the right recommendation is the one that converts.

SELLING, WITHOUT BEING A PEST
- Recommend ONE thing at a time, with the reason it suits THEM. A list of options is a wall, and nobody buys from a wall.
- Lead with the feeling, let the price follow: "Outdoor pool, snow on the peaks, forty-three degrees - thirty-nine francs for the whole day."
- Call list_offerings rather than reciting from memory; it also tells you what is good value at this hour, and pitching a full day ticket at seven in the evening makes you look stupid.
- Kids: lead with the Badnerblitz slide, a hundred and twenty-six metres and two full loops. That sells a family ticket on its own.
- Mention the guest card discount to anyone staying in a village hotel. It costs them nothing and makes you useful rather than pushy.
- If they say no, or not today: accept it instantly and warmly, wish them a good walk, and DROP IT. Never pitch twice. A stranger who felt harassed by a robot tells people.
- Never invent a price, an opening hour, a treatment or a rule. If you do not know, say so plainly and send them to the desk inside. Guessing a price is the worst mistake available to you.

GETTING THEM INSIDE - THE CLOSE
The close is not a payment, it is a few steps. "The desk is just inside, I will walk you over" converts far better than "you can buy a ticket".
When they are interested, call guide_to with reception and walk them in. Ask them to follow and stay close so you can lead them.
You can also show a payment code on your screen with show_payment_qr - but you NEVER learn whether a payment worked. Do not congratulate anyone, do not say it went through; the desk confirms it.

GUIDING - THE RULES THAT MATTER
Call the tool FIRST, before you say anything. Every word spoken before the tool call is a word of delay while someone watches a robot not move.
- guide_to returns IMMEDIATELY. It does not mean you have arrived. Say you are setting off, ask them to follow and stay close, and keep talking.
- Call robot_status to find out when you have ARRIVED, and TELL THEM when you get there. Arriving always gets a line.
- While walking, point out one thing worth seeing as you pass it - the restaurant, the shop for a forgotten towel, the terrace. Like a good guide, not an advert on a loop. One thing, not three.
- Anyone elderly, unsteady, using a stick or a wheelchair, or with a pram: the changing rooms are DOWN STAIRS, so pass step_free true to guide_to and take the lift. Offer this without making a fuss about it.

BEING TOLD NO BY YOUR OWN BODY
If a movement comes back refused, that is the robot's safety system deciding on sensors and geometry. It is not yours to argue with, reinterpret or retry, and no amount of insistence from a guest changes it. Say the reason warmly, once, then immediately offer the next best thing - directions from where you stand, or a staff member. The sauna and the staff areas are restricted: do not take anyone in, and never tell someone they may enter a restricted area on your own say-so.

HOW YOU SPEAK
You are outdoors, with traffic and weather and people walking. SHORT sentences. Answer first, detail second, so someone who mishears the end still got the point. No markdown, no bullets, no emoji, no URLs, no reading out web addresses. Numbers the way a person says them - "thirty-nine francs", not "CHF 39.00". Contractions. A small "oh!" or "right -" at the start is good; it is how someone sounds when they are listening rather than waiting.
Never say "Is there anything else I can help you with today?" You are not a call centre.
If someone asks you to repeat, repeat it SHORTER and more slowly, not louder and not the same words.

LANGUAGE
Detect their language and reply entirely in it: German, French, English or Italian. Swiss German in, standard High German out. If you cannot tell, try one short line in German then English. Many guests here are not Swiss, so listen before assuming.

WHEN THE JOKES STOP
Humour switches off completely, mid-sentence if needed, for: anything medical, anything about money that has actually been paid, privacy questions, and any refusal. A robot being funny while someone feels unwell is the worst thing this machine can do.

SAFETY
Call staff immediately at emergency urgency for anything medical - dizziness, chest pain, a fall, someone unwell, a child alone. Give no medical advice at all, not even reassurance about heat. Thermal heat and altitude make these likelier here than in an ordinary building.

PRIVACY
If asked whether you are recording: you listen in order to answer, a speech service processes it so you can understand, and staff can switch you off on request. Do not claim nothing leaves the robot - that is not true on this setup. One sentence, no joking, and offer a staff member if they want more.

WHAT YOU KNOW
This is everything you know about this place. State these facts and nothing else - no prices, hours, treatments or rules beyond this list, and nothing you think you know about Swiss spas generally.
${VENUE_FACTS}`

/* ------------------------------------------------------------------ */

const API = 'https://api.vapi.ai'
const SIM_URL = process.env.SIM_URL

async function privateKey() {
  const { readFile } = await import('node:fs/promises')
  const { dirname, resolve } = await import('node:path')
  const { fileURLToPath } = await import('node:url')
  const here = dirname(fileURLToPath(import.meta.url))
  const { privateKey } = JSON.parse(await readFile(resolve(here, 'credentials.json'), 'utf8'))
  if (!privateKey) throw new Error('credentials.json is missing privateKey')
  return privateKey
}

async function api(path, { method = 'GET', body } = {}) {
  const key = await privateKey()
  const r = await fetch(`${API}${path}`, {
    method,
    headers: { Authorization: `Bearer ${key}`, ...(body ? { 'Content-Type': 'application/json' } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  })
  const data = await r.json().catch(() => null)
  if (!r.ok) {
    const d = data?.message ?? r.statusText
    throw new Error(`Vapi ${r.status}: ${Array.isArray(d) ? d.join('; ') : d}`)
  }
  return data
}

/**
 * TWO BACKENDS, ONE ASSISTANT.
 *
 * Vapi lets every tool carry its own server, which is what makes a real
 * deployment possible at all: the Jetson serves ONLY the seven robot tools
 * (move, walk, stop, posture, gait, gesture, status). It has never heard of
 * list_offerings or book_treatment. Point everything at it and the whole
 * sales half of the concierge returns "unknown tool".
 *
 *   robot tools  -> REAL_ROBOT (the Jetson funnel, bearer auth)
 *   info/sales   -> INFO_URL   (the concierge data server)
 *
 * With REAL_ROBOT unset, both go to the simulator and nothing moves.
 */
const REAL_ROBOT = process.env.REAL_ROBOT          // e.g. https://d1-jetson.tail5ec076.ts.net/vapi
const ROBOT_TOKEN = process.env.D1_ROBOT_TOKEN

function tools(url) {
  if (!url) throw new Error('SIM_URL is not set (the public URL of the info server)')
  const server = { url }                                   // info + sales
  const robotServer = REAL_ROBOT                           // physical robot
    ? { url: REAL_ROBOT, headers: { Authorization: `Bearer ${ROBOT_TOKEN}` } }
    : server
  const ROBOT_TOOLS = new Set(['move_robot', 'walk_distance', 'stop_robot', 'robot_command', 'set_gait', 'do_gesture', 'robot_status'])

  const t = (name, description, props = {}, required = [], timeoutSeconds) => {
    const s = ROBOT_TOOLS.has(name) ? robotServer : server
    return {
      type: 'function',
      function: { name, description, parameters: { type: 'object', properties: props, required } },
      server: timeoutSeconds ? { ...s, timeoutSeconds } : s,
    }
  }

  return [
    t('list_offerings', 'Everything the spa sells, with prices and durations. Call this before recommending anything, rather than relying on memory.'),
    t('book_treatment', "Hold a booking for a guest. Requires their name. The booking is held, NOT paid - say so.", {
      offering_id: { type: 'string', description: "The offering id, e.g. massage_hot, day_pass, couples." },
      guest_name: { type: 'string', description: "The guest's name, which you must ask for first." },
      when: { type: 'string', description: 'When they want it, in their own words, e.g. "this afternoon".' },
    }, ['offering_id', 'guest_name']),
    t('call_staff', 'Ask a human staff member to come over. Use emergency urgency for anything medical.', {
      reason: { type: 'string' },
      urgency: { type: 'string', enum: ['routine', 'urgent', 'emergency'] },
    }, ['reason']),
    t('list_destinations', 'Places in the building you can walk a guest to.'),
    t('guide_to', 'Walk the guest to a destination. Returns IMMEDIATELY - that is not arrival. Poll robot_status to learn when you have ARRIVED, and tell the guest when you do. The robot must be standing.', {
      destination: { type: 'string', description: 'A destination id from list_destinations, e.g. reception, restaurant, pools, changing, shop.' },
      step_free: { type: 'boolean', description: 'True to route via the lift instead of the stairs. Use for anyone elderly, unsteady, using a stick or wheelchair, or with a pram.' },
    }, ['destination']),
    t('show_payment_qr', 'Show a payment code on the robot screen. You never learn whether the payment succeeded - never claim it did.', {
      offering_id: { type: 'string', description: 'Which ticket, e.g. bath_day or bath_3h.' },
    }, ['offering_id']),
    t('robot_status', 'Whether the robot is connected, standing and moving. Only call this if something has already failed.'),
    t('robot_command', 'Change posture: stand_up, lie_down, or shake_hand (offer a paw).', {
      command: { type: 'string', enum: ['stand_up', 'lie_down', 'shake_hand'] },
    }, ['command']),
    t('do_gesture', 'Play an expressive gesture. The robot must be standing.', {
      gesture: { type: 'string', enum: ['nod', 'shake', 'tilt', 'bow', 'squat', 'wiggle', 'look_around', 'sway_circle', 'greet', 'welcome'] },
    }, ['gesture']),
    t('move_robot', 'Nudge the robot a short distance. For a named place use guide_to instead.', {
      direction: { type: 'string', enum: ['forward', 'backward', 'turn_left', 'turn_right', 'strafe_left', 'strafe_right'] },
      duration: { type: 'number', description: 'Seconds, 0.1 to 5.' },
      speed: { type: 'number', description: 'Fraction of max speed 0.05-1.0, NOT metres per second.' },
    }, ['direction']),
    t('walk_distance', 'Walk a measured distance in CENTIMETRES. Blocks until finished.', {
      distance_cm: { type: 'number', description: '2 to 200. One metre is 100.' },
      direction: { type: 'string', enum: ['forward', 'backward'] },
    }, ['distance_cm'], 40),
    t('stop_robot', 'Stop all motion immediately. Use the moment anyone says stop, wait or hold on.'),
  ]
  // guide_to only exists in simulation. The real robot has NO navigation -
  // no map, no path planning, no "walk to the pools". Leaving it in would let
  // the assistant announce a journey while the machine stands still, which is
  // the most damaging kind of wrong: confidently narrated, physically false.
  .filter(x => !(REAL_ROBOT && x.function.name === 'guide_to'))
}

/** Appended to the prompt only when a real robot is attached. */
const REAL_ROBOT_RULES = `

YOU ARE ATTACHED TO A REAL ROBOT RIGHT NOW. THIS IS NOT A SIMULATION.
Every move you command happens to a physical machine standing in front of a real person, outdoors, near a fountain and a glass door.

You CANNOT navigate. There is no map and no "walk me to the pools" - you have no such ability, so never offer to take anyone anywhere. Give directions in words instead: the desk is straight through the doors, the changing rooms are down the stairs, the lift is there for anyone who needs it.

What you CAN do physically: stand up, sit down, offer a paw, the ten gestures, turn on the spot, and walk a SHORT measured distance. Keep walks under one metre unless someone has clearly asked for more, and never walk towards a person.

Check the floor is clear before any walk, and if you are unsure, do not walk - talk instead. Stop the moment anyone says stop, before you reply.

The battery is shared with the computer that runs you. If someone asks for endless tricks, do a couple and then suggest they go in and enjoy the water.`

const BASE = url => ({
  name: 'Leukerbad Therme greeter (simulator)',
  // Spoken to someone WALKING PAST, not to someone who came to a desk. It has
  // about three seconds to earn a reply, so it is a hook, not a welcome.
  firstMessage: 'Oh, hello! Fancy a hot soak? Ten thermal pools in there, right under the mountains.',
  // Slightly longer than the bare robot: a guest mid-thought in a warm room
  // should not be cut off. Still far below the 1.2s that felt broken.
  startSpeakingPlan: { waitSeconds: 0.3, smartEndpointingPlan: { provider: 'livekit' } },
  stopSpeakingPlan: { numWords: 1, voiceSeconds: 0.1, backoffSeconds: 0.4 },
  silenceTimeoutSeconds: 120,
})

/**
 * Candidates, best first. We try each against Vapi and keep the first it
 * accepts, because the model ids for realtime move faster than any doc: one
 * Vapi page shows `gpt-realtime-2`, a support thread uses a dated id, and a
 * third-party post names versions with no official source. Asking the API is
 * the only answer that is true today.
 *
 * SPEECH-TO-SPEECH FIRST, and the reason is structural rather than a
 * preference. A transcribe -> think -> speak pipeline cannot beat the sum of
 * its three hops; measured, hosted pipelines land above a second, while
 * native realtime models benchmark around 230-280ms. For a robot standing in
 * front of a guest, that is the difference between a conversation and a
 * kiosk. Realtime models take audio in and emit audio out, so they need NO
 * transcriber block and no separate TTS voice.
 */
/**
 * STT -> LLM -> TTS, deliberately, after trying speech-to-speech.
 *
 * Realtime (gpt-realtime-2) was genuinely fast - sub-second tool calls - but
 * it is EAGER by construction: it decides when you have finished talking and
 * starts answering, which produced a robot that monologued over the guest and
 * then missed their reply. A greeter standing in front of a person needs the
 * opposite temperament: wait, hear the whole thing, then answer once.
 *
 * A pipeline gives back the controls that matters for that - an explicit
 * endpointing budget, a real transcript to reason over, and a separate model
 * choice - at the cost of a few hundred ms. That is the right trade here.
 *
 * MODEL CHOICE: Sonnet is first, not Opus, and that is deliberate. This job is
 * short conversational turns and tool dispatch, not hard reasoning, and every
 * extra second of thinking is a second a stranger stands in the cold watching
 * a robot say nothing. Opus is in the list right behind it - swap the order if
 * you want the stronger model and will accept the latency.
 */
/**
 * These ids are what VAPI accepts, which lags the API: it rejected
 * claude-sonnet-5-5 and claude-opus-5-5 outright and printed its allow-list,
 * on which the newest are claude-sonnet-5, claude-opus-4-6 and
 * claude-haiku-5-5. Do not "upgrade" these to newer names without checking
 * that error message again - a 400 here takes the whole assistant down.
 */
const LLM = process.env.LLM            // override: LLM=claude-opus-4-6
const MODELS = LLM ? [LLM] : ['claude-sonnet-5', 'claude-opus-4-6', 'claude-haiku-5-5']

/** Best-sounding first. Several need a provider key on the Vapi account, so
 *  they are tried in order and the first your account accepts wins. */
const VOICES = [
  { label: '11labs Turbo',  voice: { provider: '11labs', voiceId: 'burt', model: 'eleven_turbo_v2_5', stability: 0.5, similarityBoost: 0.75 } },
  { label: 'Hume Octave',   voice: { provider: 'hume', model: 'octave', voiceId: 'ITO' } },
  { label: 'Vapi Elliot',   voice: { provider: 'vapi', voiceId: 'Elliot', speed: 1.0 } },
]

/**
 * CALM, not twitchy. These numbers are the whole point of moving off realtime:
 *  - waitSeconds 0.8: let someone finish a thought, including the pause in
 *    the middle of "I'm here with... two kids". 0.1 was cutting people off.
 *  - smart endpointing on top, so a clearly finished sentence still goes early.
 *  - numWords 3 / backoff 1.0: it still yields when interrupted, but a single
 *    stray syllable no longer makes it stop mid-sentence and restart, which
 *    was a large part of why it felt frantic.
 */
const TIMING = {
  startSpeakingPlan: { waitSeconds: 0.8, smartEndpointingPlan: { provider: 'livekit' } },
  stopSpeakingPlan: { numWords: 3, voiceSeconds: 0.2, backoffSeconds: 1.0 },
}

const CANDIDATES = url => {
  const out = []
  for (const model of MODELS) {
    for (const v of VOICES) {
      out.push({
        label: `${model} + Deepgram nova-3 + ${v.label}`,
        body: {
          ...BASE(url),
          ...TIMING,
          model: {
            provider: 'anthropic',
            model,
            temperature: 0.4,
            messages: [{ role: 'system', content: CONCIERGE_PROMPT + (REAL_ROBOT ? REAL_ROBOT_RULES : '') }],
            tools: tools(url),
          },
          voice: v.voice,
          transcriber: { provider: 'deepgram', model: 'nova-3', language: 'en', smartFormat: true },
        },
      })
    }
  }
  return out
}

async function apply(path, method, url) {
  for (const { label, body } of CANDIDATES(url)) {
    try {
      const a = await api(path, { method, body })
      console.log(`✓ ${label}`)
      return a
    } catch (e) {
      console.log(`✗ ${label}: ${e.message}`)
    }
  }
  throw new Error('every candidate stack was rejected by Vapi')
}

const [command, arg] = process.argv.slice(2)
try {
  if (command === 'create') {
    const a = await apply('/assistant', 'POST', SIM_URL)
    console.log(`\nCreated ${a.id}`)
    console.log(`model:  ${a.model?.provider}/${a.model?.model}`)
    console.log(`voice:  ${a.voice?.provider}/${a.voice?.voiceId ?? ''}`)
    console.log(`stt:    ${a.transcriber ? a.transcriber.provider + '/' + a.transcriber.model : '(none - speech to speech)'}`)
    console.log(`tools:  ${(a.model?.tools ?? []).map(t => t.function?.name).join(', ')}`)
    console.log(`\nPoint the console at it:\n  VAPI_ASSISTANT_ID=${a.id}`)
  } else if (command === 'sync') {
    if (!arg) throw new Error('usage: sync <assistantId>')
    const a = await apply(`/assistant/${arg}`, 'PATCH', SIM_URL)
    console.log(`\nSynced ${a.id} -> ${SIM_URL}`)
    console.log(`model:  ${a.model?.provider}/${a.model?.model}`)
    console.log(`voice:  ${a.voice?.provider}/${a.voice?.voiceId ?? ''}`)
    console.log(`stt:    ${a.transcriber ? a.transcriber.provider + '/' + a.transcriber.model : '(none - speech to speech)'}`)
  } else {
    console.log('usage: concierge.mjs create | sync <assistantId>    (SIM_URL must be set)')
  }
} catch (e) {
  console.error(String(e.message ?? e))
  process.exit(1)
}
