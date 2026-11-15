import { createServer } from 'node:http'
import { readFile, realpath, stat } from 'node:fs/promises'
import { extname, resolve, sep } from 'node:path'
import { pathToFileURL } from 'node:url'

/** Local read-only QA harness for this project's static rules, not a hosting emulator or API. */
export async function createStaticServer(directory = resolve('dist')) {
  const root = await realpath(directory)
  const headerFile = await readFile(resolve(root, '_headers'), 'utf8')
  const headers = {}
  let globalRule = false
  for (const line of headerFile.split(/\r?\n/)) {
    if (!line.trim() || line.trimStart().startsWith('#')) continue
    if (!/^\s/.test(line)) { globalRule = line === '/*'; continue }
    const split = line.indexOf(':')
    if (globalRule && split !== -1) headers[line.slice(0, split).trim()] = line.slice(split + 1).trim()
  }
  const rewrites = new Map()
  for (const line of (await readFile(resolve(root, '_redirects'), 'utf8')).split(/\r?\n/)) {
    if (!line.trim() || line.trimStart().startsWith('#')) continue
    const [source, target, status] = line.trim().split(/\s+/)
    if (status !== '200' || source.includes('*') || !target.startsWith('/')) throw new Error('QA harness supports only this project’s exact local 200 rewrites.')
    rewrites.set(source, target)
  }
  const types = { '.html': 'text/html; charset=utf-8', '.js': 'application/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8', '.json': 'application/json; charset=utf-8', '.svg': 'image/svg+xml' }
  return createServer(async (request, response) => {
    let status = 200
    let bytes
    let file
    try {
      if (request.method !== 'GET' && request.method !== 'HEAD') {
        response.writeHead(405, { ...headers, Allow: 'GET, HEAD' }); response.end(); return
      }
      const path = decodeURIComponent(new URL(request.url, 'http://localhost').pathname)
      if (path.includes('\\') || path.includes('\0') || path.split('/').some(segment => segment.startsWith('.')) || ['/_headers', '/_redirects'].includes(path)) throw new Error('Unavailable path')
      file = resolve(root, `.${rewrites.get(path) || path}`)
      if (file !== root && !file.startsWith(root + sep)) throw new Error('Unavailable path')
      if ((await stat(file)).isDirectory()) file = resolve(file, 'index.html')
      const actual = await realpath(file)
      if (!actual.startsWith(root + sep)) throw new Error('Unavailable path')
      bytes = await readFile(actual)
    } catch {
      status = 404
      file = resolve(root, '404.html')
      bytes = await readFile(file)
    }
    response.writeHead(status, { ...headers, 'Content-Type': types[extname(file)] || 'application/octet-stream', 'Content-Length': bytes.length })
    response.end(request.method === 'HEAD' ? undefined : bytes)
  })
}
if (process.argv[1] && import.meta.url === pathToFileURL(resolve(process.argv[1])).href) {
  const args = process.argv.slice(2)
  const port = args.length === 0 ? 4173 : args.length === 2 && args[0] === '--port' ? Number(args[1]) : NaN
  if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error('Usage: npm run preview:static -- --port 4173')
  const server = await createStaticServer()
  server.listen(port, '127.0.0.1', () => console.log(`Static QA server: http://127.0.0.1:${port} (local HTTP, not production TLS)`))
  for (const signal of ['SIGTERM', 'SIGINT']) process.on(signal, () => server.close(() => process.exit(0)))
}
