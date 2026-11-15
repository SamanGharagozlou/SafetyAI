import { createHash } from 'node:crypto'
import { readdir, readFile } from 'node:fs/promises'
import { resolve, relative } from 'node:path'
import { pathToFileURL } from 'node:url'

const entries = new Set(['index.html', '404.html', 'join/index.html', 'session/index.html', 'privacy/index.html', '_headers', '_redirects', 'demo-config.json'])
const safeOrigins = new Set(['https://react.dev', 'http://www.w3.org']) // Non-requesting React error links / XML namespace strings.

/** Best-effort artifact checks, not a guarantee against every possible hardcoded secret. */
export async function auditDist(directory = resolve('dist')) {
  const files = []
  async function walk(path) {
    for (const entry of await readdir(path, { withFileTypes: true })) {
      const file = resolve(path, entry.name)
      const name = relative(directory, file).replaceAll('\\', '/')
      if (entry.isSymbolicLink()) throw new Error(`Symlink in deployment output: ${name}`)
      if (entry.isDirectory()) await walk(file)
      else if (entry.isFile()) files.push(name)
      else throw new Error(`Unexpected deployment entry: ${name}`)
    }
  }
  await walk(directory)
  for (const name of entries) if (!files.includes(name)) throw new Error(`Missing static demo artifact: ${name}. Use npm run build:demo.`)
  const hash = createHash('sha256')
  for (const name of files.sort()) {
    if (!entries.has(name) && !/^assets\/[a-zA-Z0-9_-]+\.(js|css)$/.test(name)) throw new Error(`Unreviewed file in deployment output: ${name}`)
    const bytes = await readFile(resolve(directory, name))
    const text = bytes.toString('utf8')
    if (/-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----|\b(?:AKIA|ASIA)[A-Z0-9]{16}\b|\bsk-(?:proj-)?[A-Za-z0-9_-]{20,}|\beyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{20,}/.test(text)) throw new Error(`Credential-like material detected in ${name}; matched values are not logged.`)
    if (/\b(?:10|127)\.(?:\d{1,3}\.){2}\d{1,3}\b|\b192\.168\.\d{1,3}\.\d{1,3}\b|\b172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}\b|\blocalhost\s*:\s*\d+|\[::1\]/i.test(text)) throw new Error(`Private/loopback address detected in ${name}; matched values are not logged.`)
    const origins = text.match(/https?:\/\/(?:\[[^\]]+\]|[a-zA-Z0-9.-]+)(?::\d+)?/g) || []
    if (origins.some(origin => !safeOrigins.has(origin))) throw new Error(`Unreviewed URL origin in ${name}; matched values are not logged.`)
    hash.update(name).update('\0').update(bytes).update('\0')
  }
  const config = JSON.parse(await readFile(resolve(directory, 'demo-config.json'), 'utf8'))
  if (config.profile !== 'static-demo' || config.taskMode !== 'demo' || config.transcriptionConnected !== false || !Number.isInteger(config.sessionDurationSeconds) || config.sessionDurationSeconds < 1 || config.sessionDurationSeconds > 86400 || Object.keys(config).length !== 4) throw new Error('Invalid static demo configuration manifest.')
  const headers = await readFile(resolve(directory, '_headers'), 'utf8')
  if (!headers.includes("connect-src 'none'") || !headers.includes("frame-ancestors 'none'") || !headers.includes("media-src 'self' blob:") || headers.includes("'unsafe-inline'") || headers.includes("'unsafe-eval'")) throw new Error('Static demo CSP is missing required restrictions.')
  const html = await readFile(resolve(directory, 'index.html'), 'utf8')
  for (const route of ['join/index.html', 'session/index.html', 'privacy/index.html', '404.html']) {
    if (await readFile(resolve(directory, route), 'utf8') !== html) throw new Error(`Stale or different route entry: ${route}`)
  }
  return { count: files.length, sha256: hash.digest('hex') }
}
if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  try {
    const result = await auditDist()
    console.log(`Static demo artifact audit passed: ${result.count} files. SHA-256: ${result.sha256}`)
  } catch (error) {
    console.error(`Artifact audit failed: ${error.message}`)
    process.exitCode = 1
  }
}
