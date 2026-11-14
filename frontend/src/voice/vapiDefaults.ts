/**
 * Bundled demo credentials so the branch runs with no setup.
 *
 * Both values are PUBLIC by design: a Vapi public key is meant to ship in a
 * browser bundle, and an assistant id is not a secret. Anything in a VITE_*
 * variable ends up in the client bundle anyway, so there is no secrecy to
 * lose here. This repository is private, so they are not world-readable, but
 * anyone who can read it can place calls billed to our Vapi account - rotate
 * them if the repo is ever opened up, and when the hackathon is over.
 *
 * The Vapi PRIVATE key is never in this repository and never in the browser.
 *
 * Override either by setting the matching VITE_ variable in .env.local.
 * Setting one to an empty string switches live voice off deliberately; that
 * is how the test suite and the static demo build pin it off.
 */
export const DEMO_VAPI_PUBLIC_KEY = '2cdfae81-9e4a-4215-a17b-7ec2b7bf6aa4'
export const DEMO_VAPI_ASSISTANT_ID = '503c0c1a-5592-4148-af15-166811855801'
