import { describe, expect, it } from 'vitest'
import { resolveVapiConfig } from './vapiSession'

const KEY = '2cdfae81-9e4a-4215-a17b-7ec2b7bf6aa4'
const ASSISTANT = '503c0c1a-5592-4148-af15-166811855801'

describe('live voice configuration', () => {
  it('stays off when nothing is configured', () => {
    expect(resolveVapiConfig({})).toBeNull()
  })
  it.each([
    { VITE_VAPI_PUBLIC_KEY: KEY },
    { VITE_VAPI_ASSISTANT_ID: ASSISTANT },
  ])('stays off when only one key is present', env => {
    expect(resolveVapiConfig(env)).toBeNull()
  })
  it.each(['not-a-uuid', '', '   ', 'sk_live_secret_value', `${KEY} ${KEY}`])('rejects a malformed public key: %s', value => {
    expect(resolveVapiConfig({ VITE_VAPI_PUBLIC_KEY: value, VITE_VAPI_ASSISTANT_ID: ASSISTANT })).toBeNull()
  })
  it('turns on only when both keys are well formed', () => {
    expect(resolveVapiConfig({ VITE_VAPI_PUBLIC_KEY: KEY, VITE_VAPI_ASSISTANT_ID: ASSISTANT }))
      .toEqual({ publicKey: KEY, assistantId: ASSISTANT })
  })
  it('tolerates surrounding whitespace from a .env file', () => {
    expect(resolveVapiConfig({ VITE_VAPI_PUBLIC_KEY: ` ${KEY} `, VITE_VAPI_ASSISTANT_ID: `${ASSISTANT}\n` }))
      .toEqual({ publicKey: KEY, assistantId: ASSISTANT })
  })
})
