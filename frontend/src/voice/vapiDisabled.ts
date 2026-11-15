/**
 * Stand-in for @vapi-ai/web in the static demo build.
 *
 * vite.config.ts aliases the real SDK to this file whenever the `demo` profile
 * is built, so the third-party voice client is never bundled, never loaded and
 * never reachable there - a bundler-level guarantee, not a runtime promise.
 * `resolveVapiConfig` already returns null without keys, so nothing calls this.
 */
export default class VapiDisabled {
  constructor() {
    throw new Error('Live voice is not available in the static demo build.')
  }
}
