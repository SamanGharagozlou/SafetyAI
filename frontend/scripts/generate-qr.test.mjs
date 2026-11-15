// @vitest-environment node
import { mkdtemp, readFile, rm } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { afterEach, describe, expect, it } from 'vitest'
import encodeQR from 'qr'
import decodeQR from 'qr/decode.js'
import { generateQR, validateTarget } from './generate-qr.mjs'

const directories = []
afterEach(async () => { await Promise.all(directories.splice(0).map(path => rm(path, { recursive: true, force: true }))) })

describe('local printable QR generation', () => {
  it.each([undefined, 'not a URL', 'file:///join', 'https://example.test/', 'https://example.test/join?name=Nova', 'https://example.test/join#session', 'https://user:pass@example.test/join', 'http://example.test/join', 'https://localhost/join', 'https://127.0.0.1/join', 'https://192.168.1.2/join'])('rejects an unsafe or incomplete target: %s', target => {
    expect(() => validateTarget(target)).toThrow()
  })
  it('requires an explicit local-development override', () => {
    expect(validateTarget('http://localhost:5173/join', true)).toBe('http://localhost:5173/join')
    expect(() => validateTarget(undefined, true)).toThrow()
  })
  it('writes an SVG for the explicitly supplied target and refuses overwrites', async () => {
    // Reserved test domain: this fixture is never presented as a deployed QR target.
    const target = 'https://clawmetry.example.test/join'
    const directory = await mkdtemp(join(tmpdir(), 'clawmetry-qr-test-'))
    directories.push(directory)
    const output = join(directory, 'join.svg')
    expect(await generateQR([target, output])).toEqual({ url: target, output })
    const svg = await readFile(output, 'utf8')
    expect(svg).toContain('<svg')
    expect(svg).not.toContain('<script')
    await expect(generateQR([target, output])).rejects.toThrow()
    await expect(generateQR([])).rejects.toThrow(/Usage/)
    await expect(generateQR([target, join(directory, 'join.png')])).rejects.toThrow(/svg/)
    // Round-trip the exact encoder/options used for print, without any network service.
    const raw = encodeQR(target, 'raw', { ecc: 'quartile', border: 4, scale: 12 })
    const size = raw.length
    const pixels = new Uint8Array(size * size * 4)
    raw.forEach((row, y) => row.forEach((dark, x) => {
      const offset = (y * size + x) * 4
      pixels.fill(dark ? 0 : 255, offset, offset + 3)
      pixels[offset + 3] = 255
    }))
    expect(decodeQR({ width: size, height: size, data: pixels })).toBe(target)
  })
})
