import { copyFile, mkdir } from 'node:fs/promises'

// Directory-index entries support direct visits/refresh on ordinary static hosts.
// All pages boot the same React app; generated HTML contains no visitor data.
for (const route of ['join', 'session', 'privacy']) {
  await mkdir(`dist/${route}`, { recursive: true })
  await copyFile('dist/index.html', `dist/${route}/index.html`)
}
console.log('Static route entries generated: /join, /session, /privacy')
