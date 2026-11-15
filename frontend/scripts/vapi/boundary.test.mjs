import { readdir, readFile } from 'node:fs/promises'
import { dirname, extname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'
import { describe, expect, it } from 'vitest'

const FRONTEND = resolve(dirname(fileURLToPath(import.meta.url)), '../..')
const SRC = join(FRONTEND, 'src')

async function sourceFiles(directory) {
  const found = []
  for (const entry of await readdir(directory, { withFileTypes: true })) {
    const path = join(directory, entry.name)
    if (entry.isDirectory()) found.push(...await sourceFiles(path))
    else if (['.ts', '.tsx', '.js', '.jsx'].includes(extname(entry.name))) found.push(path)
  }
  return found
}

/**
 * The browser bundle must never be able to reach the Vapi private key. It is a
 * full-account credential: it can read every call transcript and change or
 * delete assistants. Nothing in src/ may touch scripts/vapi, and the literal
 * key must not appear anywhere under src/ either.
 */
describe('the admin credential cannot reach the browser', () => {
  it('no file under src/ imports anything from scripts/vapi', async () => {
    const offenders = []
    for (const file of await sourceFiles(SRC)) {
      const text = await readFile(file, 'utf8')
      if (/scripts\/vapi/.test(text)) offenders.push(file.slice(FRONTEND.length + 1))
    }
    expect(offenders).toEqual([])
  })

  it('the private key does not appear in any src/ file', async () => {
    const { privateKey } = JSON.parse(await readFile(join(FRONTEND, 'scripts/vapi/credentials.json'), 'utf8'))
    expect(privateKey).toBeTruthy()
    const offenders = []
    for (const file of await sourceFiles(SRC)) {
      if ((await readFile(file, 'utf8')).includes(privateKey)) offenders.push(file.slice(FRONTEND.length + 1))
    }
    // Reported as paths, never as the matched value.
    expect(offenders).toEqual([])
  })

  it('the private key is not exposed through any VITE_ variable', async () => {
    const { privateKey } = JSON.parse(await readFile(join(FRONTEND, 'scripts/vapi/credentials.json'), 'utf8'))
    for (const name of ['.env.example']) {
      const text = await readFile(join(FRONTEND, name), 'utf8').catch(() => '')
      expect(text.includes(privateKey)).toBe(false)
    }
  })
})
