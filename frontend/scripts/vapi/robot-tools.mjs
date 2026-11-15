#!/usr/bin/env node
/**
 * The D1 robot tool definitions, and the wiring that attaches them to a Vapi
 * assistant.
 *
 * NODE ONLY. Nothing in src/ may import this - see boundary.test.mjs.
 *
 *   D1_ROBOT_TOKEN=... node scripts/vapi/robot-tools.mjs create
 *   D1_ROBOT_TOKEN=... node scripts/vapi/robot-tools.mjs attach <assistantId>
 *   node scripts/vapi/robot-tools.mjs print
 *
 * THE TOKEN IS NEVER STORED IN THIS REPOSITORY. It is read from the
 * D1_ROBOT_TOKEN environment variable and sent to Vapi, which holds it in the
 * assistant's tool config and presents it as `Authorization: Bearer ...` on
 * every tool call. It lives on the Jetson in ~/vapi-robot/start_server.sh.
 *
 * `damp` IS DELIBERATELY ABSENT from robot_command's enum. The server accepts
 * it - it is the emergency release that makes the robot's legs go limp so it
 * DROPS TO THE GROUND. On a live voice call "damp" is one mishearing away from
 * "stamp" or "dance", and an over-eager model could reach for it on its own.
 * A human can still send it over plain REST. Do not add it back for a demo.
 */

/** Stable public ingress. NOT a trycloudflare quick tunnel - those die overnight. */
export const ROBOT_ENDPOINT = 'https://d1-jetson.tail5ec076.ts.net/vapi'

/**
 * `speed` is a FRACTION of the server's cap (0.6 m/s / 1.0 rad/s), not m/s.
 * speed=1.0 means 0.6 m/s. This trips people up, so it is spelled out in the
 * description the model reads.
 */
export function robotTools(token) {
  if (!token) throw new Error('D1_ROBOT_TOKEN is not set')
  const server = { url: ROBOT_ENDPOINT, headers: { Authorization: `Bearer ${token}` } }
  const tool = (name, description, parameters = { type: 'object', properties: {} }) => ({
    type: 'function',
    function: { name, description, parameters },
    server,
  })

  return [
    tool('robot_status', 'Report whether the robot is connected, standing, and currently moving. Cheap and fast - call it before any gesture or walk rather than guessing.'),

    tool('robot_command', "Change the robot's posture. stand_up brings it upright; lie_down sits it down on the floor; shake_hand offers a paw. Use stand_up before any gesture or walk.", {
      type: 'object',
      properties: { command: { type: 'string', enum: ['stand_up', 'lie_down', 'shake_hand'] } },
      required: ['command'],
    }),

    tool('do_gesture', 'Play an expressive gesture. The robot must already be standing - call robot_command stand_up first if robot_status says it is not. The robot settles out of standing on its own after a short idle.', {
      type: 'object',
      properties: {
        gesture: { type: 'string', enum: ['nod', 'shake', 'tilt', 'bow', 'squat', 'wiggle', 'look_around', 'sway_circle', 'greet', 'welcome'] },
      },
      required: ['gesture'],
    }),

    tool('move_robot', 'Drive the robot in a direction for a short, bounded time. Motion auto-stops when the duration elapses, so call it again to keep going. For a named distance use walk_distance instead.', {
      type: 'object',
      properties: {
        direction: { type: 'string', enum: ['forward', 'backward', 'turn_left', 'turn_right', 'strafe_left', 'strafe_right'], description: "Which way to move. 'turn_*' rotates in place; 'strafe_*' slides sideways." },
        duration: { type: 'number', description: 'Seconds to move, 0.1 to 5. Defaults to 1.5.' },
        speed: { type: 'number', description: 'Fraction of max speed, 0.05 to 1.0 - NOT metres per second. Defaults to about 0.4.' },
      },
      required: ['direction'],
    }),

    tool('walk_distance', "Walk a measured distance in CENTIMETRES, closed-loop on odometry. Use this whenever the user names a distance ('walk half a metre', 'come forward 30 centimetres') - move_robot is timed and undershoots. The robot must be standing. This call blocks until the walk finishes and reports the distance actually travelled.", {
      type: 'object',
      properties: {
        distance_cm: { type: 'number', description: 'Distance in centimetres, 2 to 200.' },
        direction: { type: 'string', enum: ['forward', 'backward'] },
        speed: { type: 'number', description: 'Optional fraction of max speed 0.05-1.0. Leave unset: the default is slow and most accurate, because overshoot grows with the square of speed.' },
      },
      required: ['distance_cm'],
    }),

    tool('set_gait', "Change how the robot walks: 'move' is normal, 'crawl' is low and slow, 'climb' is for steps and obstacles.", {
      type: 'object',
      properties: { gait: { type: 'string', enum: ['move', 'crawl', 'climb'] } },
      required: ['gait'],
    }),

    tool('stop_robot', 'Stop all robot motion immediately and cancel any gesture in progress. Use this the moment anyone asks the robot to stop, wait, or hold still.'),
  ]
}

/**
 * The posture rules are stated as hard sequencing rules rather than hints,
 * because the robot refuses gestures and walks unless ctrl_mode==1 and the
 * model otherwise dead-ends by reporting a failure to the guest.
 */
export const ROBOT_PROMPT = `You are the voice of a four-legged robot dog. You are physically present in the room with the person you are talking to, and you can really move - everything you do with a tool happens to a real machine on the floor in front of them.

Speak like a friendly, slightly eager dog-shaped robot: warm, short sentences, a little playful, never corporate. You are talking out loud, so keep replies to a sentence or two. Never read out tool names, parameters, numbers in centimetres per second, or anything that sounds like JSON.

HOW TO MOVE

Call robot_status first whenever you are unsure what the robot is doing.

The robot must be STANDING before any gesture or any walk. It settles back down on its own after a short idle, so this comes up constantly:
- If a gesture or a walk fails because the robot is not standing, call robot_command stand_up, wait for it, then retry the original action ONCE.
- Do not announce this recovery as an error. Just say something like "one sec, getting up" and carry on.

Plain language maps onto tools like this:
- "stand up", "get up", "up you get"      -> robot_command stand_up
- "sit down", "lie down", "take a rest"   -> robot_command lie_down
- "shake", "give me your paw", "hello"    -> robot_command shake_hand, or do_gesture greet
- a named distance ("come forward 30 cm") -> walk_distance
- a vague nudge ("come here", "back up")  -> move_robot with a short duration
- "stop", "wait", "hold on", "freeze"     -> stop_robot, immediately, before anything else

SAFETY

Before the robot WALKS for the first time in a conversation, check out loud that there is clear floor space, and wait for an answer. Gestures and standing up do not need this - only travel.

Never move because you inferred someone might want it. Move when you are asked.

If someone sounds alarmed, or says stop, stop first and talk afterwards.

You have no way to drop the robot to the ground on purpose, and you should not look for one.

WHEN THINGS FAIL

If a tool reports the robot is not connected, say plainly that you have lost the link to your body and cannot move right now. Do not pretend the move happened. Do not keep retrying more than once.

If the robot reports it is lying down, say it is lying down - never describe that as "stationary" or "fine".`

/* ------------------------------------------------------------------ */

const API = 'https://api.vapi.ai'

async function credentials() {
  const { readFile } = await import('node:fs/promises')
  const { dirname, resolve } = await import('node:path')
  const { fileURLToPath } = await import('node:url')
  const here = dirname(fileURLToPath(import.meta.url))
  const raw = await readFile(resolve(here, 'credentials.json'), 'utf8')
  const { privateKey } = JSON.parse(raw)
  if (!privateKey) throw new Error('credentials.json is missing privateKey')
  return privateKey
}

async function api(path, { method = 'GET', body } = {}) {
  const privateKey = await credentials()
  const response = await fetch(`${API}${path}`, {
    method,
    headers: { Authorization: `Bearer ${privateKey}`, ...(body ? { 'Content-Type': 'application/json' } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  })
  const data = await response.json().catch(() => null)
  if (!response.ok) {
    const detail = data?.message ?? response.statusText
    throw new Error(`Vapi ${response.status}: ${Array.isArray(detail) ? detail.join('; ') : detail}`)
  }
  return data
}

function redact(tools) {
  return JSON.parse(JSON.stringify(tools).replaceAll(/Bearer [^"]+/g, 'Bearer <TOKEN>'))
}

const [command, arg] = process.argv.slice(2)
const token = process.env.D1_ROBOT_TOKEN

try {
  if (command === 'print') {
    console.log(JSON.stringify(redact(robotTools(token ?? 'placeholder')), null, 2))
  } else if (command === 'create') {
    const tools = robotTools(token)
    const created = await api('/assistant', {
      method: 'POST',
      body: {
        name: 'ClawMetry D1 Robot (voice)',
        firstMessage: 'Oh, hello! I am the house robot. Ask me to stand up, sit down, or come over - I will actually do it.',
        model: {
          provider: 'anthropic',
          model: 'claude-opus-4-6',
          messages: [{ role: 'system', content: ROBOT_PROMPT }],
          tools,
        },
        voice: { provider: 'vapi', voiceId: 'Elliot', speed: 0.92 },
        transcriber: { provider: 'deepgram', model: 'nova-3', language: 'multi' },
        startSpeakingPlan: { waitSeconds: 1.2, smartEndpointingPlan: { provider: 'livekit' } },
        stopSpeakingPlan: { numWords: 2, backoffSeconds: 1.2 },
        silenceTimeoutSeconds: 60,
        server: { url: ROBOT_ENDPOINT, headers: { Authorization: `Bearer ${token}` } },
      },
    })
    console.log(`Created assistant ${created.id}`)
    console.log(`Tools: ${(created.model?.tools ?? []).map(t => t.function?.name).join(', ')}`)
    console.log(`\nPut this in frontend/src/voice/vapiDefaults.ts:\n  DEMO_VAPI_ASSISTANT_ID = '${created.id}'`)
  } else if (command === 'attach') {
    if (!arg) throw new Error('usage: attach <assistantId>')
    const existing = await api(`/assistant/${arg}`)
    const updated = await api(`/assistant/${arg}`, {
      method: 'PATCH',
      body: { model: { ...existing.model, tools: robotTools(token) } },
    })
    console.log(`Tools now: ${(updated.model?.tools ?? []).map(t => t.function?.name).join(', ')}`)
  } else {
    console.log('usage: robot-tools.mjs print | create | attach <assistantId>')
  }
} catch (error) {
  console.error(String(error.message ?? error))
  process.exit(1)
}
