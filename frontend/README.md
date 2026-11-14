# ClawMetry frontend — Phase 2

Mobile-first visitor onboarding for the SafetyAI Robotics Hackathon. React, Vite, TypeScript, and Tailwind CSS. The Phase 1 avatar, palette, and card design are preserved.

## Run locally

Use Node.js 22.12+ (Node 24 recommended) and npm. From the repository root:

```sh
cd frontend
npm ci
npm run dev
```

Open **http://localhost:5173/join** (or the port Vite prints). `/` also opens welcome. Direct visits and refresh on `/join` work with Vite. For a phone on the same trusted network:

```sh
npm run dev -- --host 0.0.0.0
```

Use the actual network URL printed by Vite, with `/join` appended. A phone's `localhost` points to the phone, not your computer. This command exposes the dev server to your network. Phase 2 requests no microphone access, including over local HTTP.

## Visitor journey and demo sessions

- Enter an optional nickname (40 characters maximum), leave it blank, or choose **Continue as Guest**. Whitespace-only names become Guest.
- The session-ready screen says **Demo mode**, greets the visitor, explains the simulation, and shows the future voice interaction with a disabled microphone control.
- **End Session** clears the active in-memory session and nickname, returning to welcome. Browser Back to welcome also ends the session.
- Privacy opens a separate view; returning preserves an unexpired session. Expiry while reading privacy clears the session without interrupting the page; Back then returns to welcome with an expiry message.
- Refreshing `/join` starts fresh. Refreshing `/session`, opening it in a new tab, or navigating forward to an ended session returns to welcome. No session is restored.
- Unknown client routes provide a welcome-page recovery button (requires the hosting fallback below for direct unknown URLs).
- Failed demo-session creation produces an accessible error and allows retry. Page navigation moves focus to the heading. Controls support keyboard navigation and reduced motion.

`src/services/demoSession.ts` is an explicitly simulated synchronous service. It creates random `demo-…` IDs locally and records an absolute expiry timestamp. IDs are not credentials; no backend has authenticated or authorized visitors. State lives only in React. There is no API call or server session to delete. Real backend lifecycle and error contracts must be agreed with the integration teammate later.

### Configure expiry

Default: **600 seconds (10 minutes)**. Copy `.env.example` to `.env.local` and edit:

```dotenv
VITE_DEMO_SESSION_SECONDS=30
```

Allowed range: integer seconds from 1 through 86400. Invalid values prevent demo-session creation and show the retry/help message. Restart Vite after changing the value. Production values are embedded at build time; this is public configuration, not a secret. The app rechecks the absolute deadline on focus and visibility changes to handle sleeping phones. Automatic UI updates wait until the browser resumes execution.

## Production build and routes

```sh
npm run typecheck
npm test
npm run build
npm run preview
```

Build checks TypeScript, bundles local assets to `dist/`, and creates `dist/join/index.html`, `dist/session/index.html`, and `dist/privacy/index.html`. Hosts supporting directory indexes can serve direct/refresh visits without a special SPA rewrite (some redirect `/join` to `/join/`, which the app also handles). The app expects deployment at the domain root, not a URL subdirectory.

For a host without directory-index support, configure internal fallback rewrites for `/join`, `/session`, and `/privacy` to `/index.html`, preserving the requested browser path. Optionally use that fallback for other non-asset routes to show the in-app missing-page screen. Do not rewrite missing assets to HTML. FastAPI deployment/static-serving configuration belongs to the backend teammate; it is not changed here.

Preview normally serves **http://localhost:4173/join**. Deploy `dist/` only when deployment is authorized. On the real host, verify direct `/join` access and refresh from a phone before printing a QR code.

## Generate a QR code locally

The script uses the maintained [paulmillr/qr library](https://github.com/paulmillr/qr) (`qr` on npm). It runs entirely on your computer; no QR-generation website, redirect service, or external QR processing is used. It requires **both an explicit target URL and SVG output path**, with no default target. It prints the exact encoded URL, uses a four-module quiet zone, black on white, and quartile error correction. Existing files are not overwritten.

After deployment to a **real HTTPS URL**, supply that actual site's `/join` URL:

```sh
mkdir -p qr-output
read -r "JOIN_URL?Paste the deployed HTTPS URL ending in /join: "
npm run qr -- "$JOIN_URL" qr-output/clawmetry-join.svg
```

The `read` command above is for zsh. In bash use `read -r -p 'Deployed HTTPS /join URL: ' JOIN_URL`. Never put a nickname, token, or session ID in the QR URL. The script rejects query parameters, fragments, credentials, paths other than `/join`, and local/non-HTTPS targets by default. It validates URL shape, **not DNS ownership, deployment success, or reachability**. It cannot tell whether a supplied public hostname is real, so verify it yourself. No final public QR is supplied because a deployed URL has not been provided.

For an intentional local development target only:

```sh
read -r "JOIN_URL?Paste the actual reachable development URL ending in /join: "
npm run qr -- "$JOIN_URL" qr-output/development-only.svg --allow-local
```

`--allow-local` explicitly permits local/HTTP targets; the exact target and a development warning are printed. A localhost QR will not reach your computer from another device. Use the actual reachable LAN address. Do not print this development QR for event visitors.

Open the SVG in a local viewer and print without cropping its white border, at least 35 mm square for a short URL (larger for dense/long URLs). Scan a proof print with the actual visitor phones before final use. Generated outputs in `qr-output/` are ignored by Git.

## Tests and mobile checks

```sh
npm run typecheck
npm test
npm run build
npm run test:e2e
```

Vitest covers named and guest onboarding, retry errors, config validation, session expiry, wake-from-sleep expiry, ending sessions, privacy navigation, and missing/refresh session handling. QR tests verify validation, file generation, overwrite protection, and encoding/decoding round-trip, using a reserved test-only domain in temporary files.

Playwright runs against the production preview using **installed Google Chrome** (`channel: chrome`); it does not download a browser. Install Chrome separately if absent, or set `PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH` to an existing compatible binary. The preview port 4173 must be free. Tests cover direct `/join` and refresh, a 40-character name, disabled voice controls, privacy, ending/rejoining, browser history, empty browser storage, reduced motion, and horizontal overflow at 320×568, 375×667, 390×844, 412×915, and 667×375. Long pages scroll naturally; nickname input text is 16px to avoid iOS focus zoom. Chrome viewport checks do not replace testing on actual iOS Safari and Android devices.

`npm run test:watch` runs the unit tests interactively.

## Privacy and integration scope

Nicknames, mock session IDs, and expiry times remain only in page memory. No cookies, local/session storage, analytics, trackers, backend requests, external fonts, microphone access, audio recording, speech recognition, or robot control are implemented. Artwork is local SVG. The local QR generator encodes only the provided link. A hosting provider may retain standard request logs; its practices are unverified. There is no certified GDPR compliance claim or invented backend deletion behavior.

Remaining work: deploy to the real HTTPS host and verify its routes; generate and proof-scan the real QR; agree on the FastAPI session API and privacy/retention contract; integrate speech, robot responses, SDK, and hardware in a future phase. None of those integrations is simulated as backend authentication.

## Source layout

- `src/App.tsx`: onboarding, session-ready, privacy, and recovery views.
- `src/Artwork.tsx`: preserved Phase 1 robot and shield.
- `src/useRoute.ts`: client navigation and browser history; only paths enter history.
- `src/services/demoSession.ts`: modular mock session creation and expiry rules.
- `src/styles.css`: Phase 1 styling plus responsive Phase 2 controls.
- `scripts/generate-qr.mjs`: explicit-target local printable SVG generation.
- `scripts/static-routes.mjs`: build-time directory entries for static hosting.
- `src/**/*.test.ts*`, `scripts/*.test.mjs`: unit/integration tests.
- `e2e/visitor.spec.ts`: production-preview mobile viewport checks.
- `.env.example`: documented public demo configuration.
