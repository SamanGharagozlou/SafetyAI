#!/usr/bin/env node
/**
 * Read and change the Vapi assistant the live voice call dials.
 *
 * NODE ONLY. This reads the private key from credentials.json. Nothing in
 * src/ may import anything from this directory - see scripts/vapi/boundary.test.mjs,
 * which fails the suite if that ever happens.
 *
 *   node scripts/vapi/assistant.mjs show
 *   node scripts/vapi/assistant.mjs voices
 *   node scripts/vapi/assistant.mjs voice Elliot 0.92
 *   node scripts/vapi/assistant.mjs pace 1.2
 *   node scripts/vapi/assistant.mjs greeting "Oh, hello! I am the house robot."
 *   node scripts/vapi/assistant.mjs prompt ./my-prompt.txt
 *   node scripts/vapi/assistant.mjs calls 5
 *
 * Every change takes effect on the next call. No rebuild, no redeploy.
 */
import { readFile } from 'node:fs/promises'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = dirname(fileURLToPath(import.meta.url))
const API = 'https://api.vapi.ai'

/** The only male voices Vapi's built-in provider accepts. Probed, not guessed. */
const MALE_VOICES = ['Elliot', 'Rohan', 'Leo']

async function credentials() {
  const raw = await readFile(resolve(HERE, 'credentials.json'), 'utf8')
  const { privateKey, assistantId } = JSON.parse(raw)
  if (!privateKey || !assistantId) throw new Error('credentials.json is missing privateKey or assistantId')
  return { privateKey, assistantId }
}

async function call(path, { method = 'GET', body } = {}) {
  const { privateKey } = await credentials()
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

async function patch(body) {
  const { assistantId } = await credentials()
  const updated = await call(`/assistant/${assistantId}`, { method: 'PATCH', body })
  console.log('Updated. Takes effect on the next call.\n')
  summarise(updated)
}

function summarise(a) {
  const prompt = a.model?.messages?.[0]?.content ?? ''
  console.log(`  voice            ${a.voice?.voiceId} (${a.voice?.provider}) speed ${a.voice?.speed ?? 1}`)
  console.log(`  model            ${a.model?.provider}/${a.model?.model}`)
  console.log(`  transcriber      ${a.transcriber?.provider}/${a.transcriber?.model} lang=${a.transcriber?.language}`)
  console.log(`  wait before reply ${a.startSpeakingPlan?.waitSeconds ?? 0.4}s  smart-endpointing ${a.startSpeakingPlan?.smartEndpointingPlan ? 'on' : 'off'}`)
  console.log(`  interrupt after  ${a.stopSpeakingPlan?.numWords ?? 0} words, backoff ${a.stopSpeakingPlan?.backoffSeconds ?? 0}s`)
  console.log(`  silence timeout  ${a.silenceTimeoutSeconds}s`)
  console.log(`  tools            ${(a.model?.tools ?? []).map(t => t.function?.name ?? t.type).join(', ') || 'none'}`)
  console.log(`  greeting         ${a.firstMessage}`)
  console.log(`  system prompt    ${prompt.length} chars`)
}

const [command, ...args] = process.argv.slice(2)

try {
  if (command === 'show' || !command) {
    const { assistantId } = await credentials()
    summarise(await call(`/assistant/${assistantId}`))
  } else if (command === 'voices') {
    console.log('Vapi built-in male voices (everything else is rejected):')
    for (const v of MALE_VOICES) console.log(`  ${v}`)
    console.log('\nTry one:  node scripts/vapi/assistant.mjs voice Leo 0.95')
  } else if (command === 'voice') {
    const [voiceId, speed] = args
    if (!voiceId) throw new Error('usage: voice <voiceId> [speed]')
    await patch({ voice: { provider: 'vapi', voiceId, version: '2', ...(speed ? { speed: Number(speed) } : {}) } })
  } else if (command === 'pace') {
    const seconds = Number(args[0])
    if (!Number.isFinite(seconds)) throw new Error('usage: pace <secondsToWaitBeforeReplying>')
    // Vapi's default is ~0.4s, which is what makes an agent feel like a phone bot.
    await patch({ startSpeakingPlan: { waitSeconds: seconds, smartEndpointingPlan: { provider: 'vapi' } } })
  } else if (command === 'greeting') {
    if (!args[0]) throw new Error('usage: greeting "<what it says first>"')
    await patch({ firstMessage: args.join(' ') })
  } else if (command === 'prompt') {
    if (!args[0]) throw new Error('usage: prompt <path-to-text-file>')
    const { assistantId } = await credentials()
    const content = await readFile(resolve(process.cwd(), args[0]), 'utf8')
    const current = await call(`/assistant/${assistantId}`)
    // Keep the tools; replace only the system message.
    await patch({ model: { ...current.model, messages: [{ role: 'system', content }] } })
  } else if (command === 'calls') {
    const limit = Number(args[0] ?? 5)
    const calls = await call(`/call?limit=${limit}`)
    for (const c of calls) {
      console.log(`${(c.createdAt ?? '').slice(11, 19)} -> ${(c.endedAt ?? 'RUNNING').slice(11, 19)}  ${c.status}  ${c.endedReason ?? ''}`)
    }
    console.log('\nendedReason is the diagnostic channel when a call misbehaves.')
  } else {
    console.error(`Unknown command: ${command}`)
    console.error('Try: show | voices | voice | pace | greeting | prompt | calls')
    process.exit(1)
  }
} catch (error) {
  console.error(String(error.message ?? error))
  process.exit(1)
}
