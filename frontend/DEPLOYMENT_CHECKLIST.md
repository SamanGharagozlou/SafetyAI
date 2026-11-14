# ClawMetry Phase 5A deployment checklist

**Status: locally prepared and tested; not deployed.** No public URL, provider account, DNS, certificate, production headers, physical phone, or hosting retention practice has been verified. This profile needs no backend. Do not publish, commit, push, or change production without separate authorization.

## 1. Hosting configuration — Cloudflare Pages, static only

Use these settings when deployment is authorized:

| Setting | Exact value / action |
| --- | --- |
| Project type | Cloudflare **Pages**, static React/Vite; no Worker or Pages Functions |
| Repository root directory | `frontend` |
| Framework preset | React (Vite), with the build command overridden below |
| Build command | `npm ci --include=dev && npm run build:demo` |
| Build output directory | `dist`, relative to `frontend` |
| Node environment setting | `NODE_VERSION=24` |
| Deployment scope | Upload **only the contents of `frontend/dist/`**, never the repository, `.env*`, source, `.git`, `.aws`, or `node_modules` |
| Source branch | Intended source is `feat/visitor-qr-voice-ui`; production-branch selection requires the owner's approval |
| Automatic deployments | Keep disabled/unconnected until publication is authorized; review preview and production branch controls |
| Backend / bindings | None. No Functions, proxy, database, robot endpoint, tokens, or backend secrets |
| Analytics / injected scripts | Disable optional Web Analytics, Zaraz, Rocket Loader, and other script/HTML injection or instrumentation |
| HTTPS | Trusted certificate on the actual selected hostname; verify HTTP redirects to HTTPS without a downgrade |
| Base path | Domain root `/`; subdirectory deployment is not configured |
| Published URL | Record the actual provider-assigned or verified custom HTTPS origin after deployment; none is invented here |

Cloudflare supports a frontend root/build command/output directory configuration. These settings select this repository's guarded demo build. [Cloudflare build configuration](https://developers.cloudflare.com/pages/configuration/build-configuration/)

For an approved direct upload instead of Git integration, first run the same build locally, then upload `dist/` contents with `_headers`, `_redirects`, and `404.html` included. Git integration can only build committed/pushed source; this task deliberately makes no commits or pushes. Do not assume a previous production deployment exists for rollback.

- [ ] Confirm project owner/account, public audience, deployment method, approved source/artifact, and eventual hostname.
- [ ] Confirm no custom-domain cache rules, redirects, challenge pages, or injected scripts override the configuration.
- [ ] Disable optional analytics/instrumentation; inspect the actual resulting page for provider-added scripts.
- [ ] Record the approved artifact digest and hosting deployment identifier outside the public application.

## 2. Environment settings and artifact audit

All `VITE_*` settings are public build inputs, not secrets. Vite embeds client environment values at build time; a dashboard edit is not a runtime change to an already built site. [Vite environment documentation](https://vite.dev/guide/env-and-mode)

| Variable | Static demo value |
| --- | --- |
| `NODE_VERSION` | `24` on the build provider; not a client secret |
| `NODE_ENV` | Leave unset so Vite uses production, or set `production`; never `development` for a release |
| `VITE_DEMO_SESSION_SECONDS` | `600` for the tested release; public integer seconds from 1–86400 are supported |
| `VITE_TRANSCRIBE_ENDPOINT` | **Unset or empty** — no transcription upload |
| `VITE_REQUESTS_ENDPOINT` | Unset or exactly `/api/requests`; the adapter remains unwired |
| `VITE_REQUEST_STATUS_ENDPOINT` | Unset or exactly `/api/requests/{request_id}`; the adapter remains unwired |
| Other `VITE_*` variables | **None**; demo build rejects unreviewed keys |
| Backend keys, privileged/session tokens, robot IPs | **Never put them in frontend settings or source** |

`npm run build:demo` runs TypeScript, `vite build --mode demo`, static-route generation, and the artifact audit. It rejects nonempty transcription configuration, changed API paths, unknown `VITE_*` keys, and invalid session durations before bundling. The `demo` mode loads `.env`, `.env.local`, `.env.demo`, and `.env.demo.local` when present, plus process/provider environment precedence; it does **not** use `.env.production`. These files are ignored by Git; review local/provider values without printing secrets. `.env.example` is documentation, not automatically loaded configuration.

The emitted `demo-config.json` contains only four public fields: `profile: static-demo`, `taskMode: demo`, `transcriptionConnected: false`, and session duration. It is build evidence, not authentication or a source of visitor data. Source maps are explicitly disabled. Only reviewed HTML, hashed JS/CSS, the manifest, and hosting rules are accepted by the audit. Dotfiles, source maps, private-key files, Workers, unexpected files, common credential patterns, private/loopback address literals, and unreviewed URL origins fail the audit. Error messages do not print matched values.

The bundle contains React documentation URLs and XML namespace strings; these are not requests or trackers. The audit explicitly allows those inert references. The browser suite verifies actual network behavior. Pattern checks and an environment allowlist are **not** proof against every possible hardcoded secret or a compromised dependency; source review and operator review remain necessary.

```sh
# From repository root
cd frontend
npm ci --include=dev
npm run typecheck
npm test
npm run build:demo
npm run audit:dist
npm run test:e2e
```

The browser suite expects the tested default session duration of 600 seconds. Playwright uses installed Google Chrome, or an existing binary selected via `PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH`; it downloads no browser and requests no real microphone. Port 4173 must be free. The suite starts/stops its own loopback-only static QA server.

For manual inspection of the exact built policy/routing:

```sh
npm run preview:static
```

Open `http://127.0.0.1:4173/join`. This is **local HTTP**, not TLS verification. Loopback is considered a secure context by supported browsers; a phone's `localhost` points to the phone, and plain HTTP on a LAN IP intentionally disables recording. The standard `npm run preview` remains available but does not apply `_headers` or `_redirects`. The standard `npm run build` is a developer/integration build, not the audited Phase 5A publishing command.

- [ ] Use only reviewed settings; verify the manifest and save the audit SHA-256.
- [ ] Confirm all tests pass against the exact artifact intended for publication.
- [ ] Confirm no environment/source/private files or source maps are present in the upload.

## 3. HTTPS and route checks on the actual host

`public/_headers` and `public/_redirects` are copied into `dist/`. Cloudflare Pages parses these files for static assets; they are not an implementation of Functions/API security. [Headers](https://developers.cloudflare.com/pages/configuration/headers/), [redirects](https://developers.cloudflare.com/pages/configuration/redirects/)

Exact local 200 rewrites cover `/join`, `/join/`, `/session`, `/session/`, `/privacy`, and `/privacy/`. Generated directory entries also support ordinary static directory-index hosts. A generated top-level `404.html` retains the app's recovery screen with **HTTP 404**, preventing Cloudflare's automatic broad SPA fallback from turning missing APIs/assets into apparent success. [Cloudflare serving behavior](https://developers.cloudflare.com/pages/configuration/serving-pages/)

| Path / action | Expected result |
| --- | --- |
| `/` | HTTP 200; client opens `/join` |
| `/join`, `/join/`, direct visit + refresh | HTTP 200; fresh welcome screen |
| `/session`, `/session/`, direct visit without memory | HTTP 200 entry; client returns to `/join` with no-active-session message |
| Refresh during an active session | React memory resets; welcome, no restored nickname/request/recording |
| `/privacy`, `/privacy/`, direct visit + refresh | HTTP 200; truthful privacy information, usable without a session |
| `/unknown-page` | HTTP 404; app recovery button returns to welcome |
| `/assets/missing.js`, `/api/requests`, `/api/transcribe` | HTTP 404; never a 200 SPA or fake backend response |
| Every entry route | Working local JS/CSS, no CSP violations, no mixed content, no unexpected origin redirect |

After a separately authorized deployment, paste the **actual HTTPS origin without a trailing slash** and inspect final headers. These are future operator checks; no public requests were made by this task:

```sh
# zsh; use read -r -p 'Actual HTTPS origin: ' PUBLIC_ORIGIN in bash
read -r "PUBLIC_ORIGIN?Paste the actual verified HTTPS origin (no trailing slash): "
curl --fail --silent --show-error --location --proto '=https' --proto-redir '=https' -D - -o /dev/null "$PUBLIC_ORIGIN/join"
curl --fail --silent --show-error --location --proto '=https' --proto-redir '=https' -D - -o /dev/null "$PUBLIC_ORIGIN/privacy"
```

- [ ] Verify trusted TLS and HTTP→HTTPS redirect on the actual final hostname; no certificate bypass.
- [ ] Check each route above in a new browser tab and after refresh, including trailing slash behavior.
- [ ] Confirm actual response headers match the policy below. Local QA is not verification of Cloudflare's edge, canonical redirects, dashboard settings, or DNS.
- [ ] Verify missing APIs/assets remain 404 and no API/network integration has been added.

## 4. Security headers and CSP

The static demo ships this enforced CSP, with no inline script/style or eval allowance:

```text
default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; media-src 'self' blob:; connect-src 'none'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'; upgrade-insecure-requests
```

`connect-src 'none'` blocks fetch/XHR/WebSocket connections, including same-origin API uploads. It is deliberately suitable for a backend-free demo. Recording uses local browser APIs and playback uses local Blob URLs, allowed by `media-src`. No script, image, font, or media CDN is needed. Native React handlers handle forms without a network form submission.

| Header | Setting / purpose |
| --- | --- |
| `Permissions-Policy` | `microphone=(self), camera=(), geolocation=()`; top-level site mic only; app still requests it only after user action |
| `Referrer-Policy` | `no-referrer` |
| `X-Content-Type-Options` | `nosniff` |
| `X-Frame-Options` | `DENY`; CSP also forbids framing |
| `Cross-Origin-Opener-Policy` | `same-origin` |
| `Cross-Origin-Resource-Policy` | `same-origin` |
| `Strict-Transport-Security` | `max-age=31536000`; no subdomain or preload commitment; preserve HTTPS during rollback |
| `X-Robots-Tag` | `noindex, nofollow`; indexing guidance, not access control |
| `Cache-Control` | `no-store` for this small static prototype, avoiding stale entry/security configuration after rollback |

The public headers do not create authentication, robot authorization, legal compliance, or server-side data deletion. No CORS wildcard or cross-origin audio service is configured. Future verified API integration requires a separately reviewed build profile, CSP/headers, credential contract, and privacy update; do not weaken this demo's CSP or enable arbitrary origins to work around a failed integration. `_headers` does not secure future Pages Functions.

- [ ] Confirm headers on final HTML and assets, not just redirects or local QA.
- [ ] Confirm no provider-injected scripts, CSP violations, external beacons, cookies, or mixed content.
- [ ] Keep HSTS HTTPS support in place; do not add preload/includeSubDomains without reviewing domain ownership and subdomains.

## 5. Real-phone acceptance tests — still pending

Use at least one physical iPhone with Safari and one Android phone with Chrome. Record OS/browser versions and the tested deployment ID privately. Open in a full browser; QR scanner webviews may not support microphone APIs. **Do not use a browser security override or bypass an untrusted certificate.**

- [ ] Scan/open the actual `/join` URL on HTTPS. Confirm no mic prompt on load or joining.
- [ ] Join as Guest, then with a nickname. No real name/account/email required. Confirm **Demo mode**, **Transcription not connected**, and simulated task labels remain visible.
- [ ] Start Recording deliberately, Allow, speak, and Stop. Verify timer, readable indicator, local playback, and the browser's active capture indicator stopping.
- [ ] Test Cancel during recording; Retry; Discard; 20-second automatic stop; repeated taps; an ignored/canceled permission prompt followed by a late Allow. No overlapping/background capture or automatic restart.
- [ ] Deny microphone permission. Typed requests must remain usable. Permission may remain granted/denied in browser site settings after session end; stopping tracks does not revoke that setting.
- [ ] While recording, open privacy, use Back, switch apps/tabs, lock/unlock the phone, and End Session. Verify capture stops and does not resume automatically. Check audio-device/Bluetooth interruption and native playback volume behavior.
- [ ] Verify session expiry during recording, review, confirmation, and task status. Use the default 10-minute expiry or an independently reviewed **preview-only** short-duration artifact; restore/rebuild the normal tested configuration before publication.
- [ ] With disconnected transcription, Continue after review must clear the unsent clip and show manual text entry; no invented transcript and no POST.
- [ ] Type Hello / Robot info / Help; Continue; Edit; Cancel; Confirm. Only Confirm creates a demo task. Observe awaiting-approval and completion labels; unsupported movement must be declined.
- [ ] Start another interaction; End Session; refresh. No old name/request/transcript/audio/task should reappear in a new session.
- [ ] Test 320px-equivalent narrow portrait, short landscape, on-screen keyboard, zoom, VoiceOver/TalkBack, large text, and reduced motion. Allow normal vertical scrolling; verify no horizontal overflow or inaccessible controls.
- [ ] In browser inspection, confirm only same-origin static GETs and local Blob playback: no analytics, API POSTs, WebSockets, cookies, local/session storage, IndexedDB databases, or service-worker registrations created by the app.
- [ ] Test actual direct route visits and refreshes listed above, on cellular as well as Wi-Fi.

## 6. QR code — eventual real HTTPS URL only

No production QR or public target is generated in this task. Select the stable canonical HTTPS origin after deployment approval and successful phone checks; avoid printing an ephemeral preview deployment URL. Rollback should preserve that origin and `/join` path.

From `frontend/`, supply the actual `/join` URL explicitly:

```sh
mkdir -p qr-output
# zsh; bash: read -r -p 'Actual deployed HTTPS /join URL: ' JOIN_URL
read -r "JOIN_URL?Paste the actual deployed HTTPS URL ending in /join: "
npm run qr -- "$JOIN_URL" qr-output/clawmetry-join.svg
```

- [ ] Check the exact printed target, open it directly, and confirm the final HTTPS destination is correct.
- [ ] No nickname, session ID, token, query string, fragment, localhost, or invented public hostname.
- [ ] Generation is local using the installed QR library; no third-party QR-processing website or redirect service.
- [ ] Do not use `--allow-local` for an event/public QR.
- [ ] Keep the four-module white border; print a proof at an appropriate size (at least 35mm square for a short URL; larger for dense codes).
- [ ] Scan the actual proof print on both phones, on cellular and Wi-Fi, including direct visit/refresh tests.
- [ ] Generated SVGs remain in ignored `qr-output/`; no overwrite is performed by the generator.

The script validates URL shape, not DNS ownership, TLS, deployment success, or real reachability; the operator must verify those.

## 7. Rollback procedure — future authorized operation

Before publishing, retain the audited `dist/` artifact, SHA-256, source revision/approved change set, environment values (public only), and deployment ID. Do not overwrite the only known-good artifact by rebuilding over it. No rollback has been performed here.

For a Cloudflare Pages project with an earlier successful **production** deployment, open the project's Deployments list, choose the known-good deployment's actions menu, and use **Rollback to this deployment** after approval. Preview deployments are not rollback targets. [Cloudflare rollback documentation](https://developers.cloudflare.com/pages/configuration/rollbacks/)

If no eligible previous production exists, restoring a retained approved static artifact requires a separately authorized new upload/deployment. Do not invent a previous production version, reset teammate Git changes, or change DNS as an improvised rollback.

- [ ] Verify the restored manifest, digest/source record, `_headers`, `_redirects`, and HTML/assets belong together.
- [ ] Verify actual HTTPS, CSP, microphone policy, `/join`, `/session`, `/privacy`, missing-asset 404s, and a mock task after rollback.
- [ ] Confirm dashboard injection, cache rules, and environment settings remain appropriate; artifact rollback does not verify account-level settings.
- [ ] Retain the stable hostname/path so printed QR codes keep working.
- [ ] Ask active visitors to End Session/reload. Already-open pages may keep their loaded JS/memory until reload; rollback cannot forcibly erase client or server data.

## 8. Privacy/security audit findings and limits

**Local findings:** frontend source uses no persistent visitor storage, analytics, application request/transcript logging, third-party fonts/audio services, robot addresses, privileged credentials, or automatic mic capture. Task APIs remain local mocks. The demo release rejects connected transcription. Recording cleanup, URL revocation, expiry, duplicate/stale task handling, and deliberate consent remain covered by tests. Source maps/private files are not in the audited upload. Strict CSP blocked an intentional same-origin fetch probe while the normal journey had no policy violations or unexpected external requests.

**Remaining gaps:** hosting requests can expose IP/user-agent/timing to the host/CDN and may be logged. Provider analytics, retention, account settings, TLS/DNS, edge header application, and real-phone capture cannot be inferred from a local artifact. The privacy notice already says hosting practices are unverified; update it if actual hosting adds processing. No GDPR/legal certification or backend deletion behavior is claimed.

React memory and object-URL cleanup remove application references, not a cryptographic memory wipe. Browser/OS buffers, browser history/back-forward cache, crash/session restore, device permissions, and suspended timers remain platform-dependent. An inactive page may remain in browser memory; End Session before handing the device to another visitor. Timers/cleanup execute when the browser schedules JavaScript. The app is a public prototype with client-controlled demo sessions, not an authenticated robot-safety boundary. CSP and pattern scans do not protect against every first-party compromise or unknown hardcoded secret.

- [ ] Obtain owner review of actual hosting log/retention practices and privacy wording.
- [ ] Complete the physical-device tests; local Chrome emulation is not Safari/Android hardware verification.
- [ ] Obtain separate deployment authorization after these checks and artifact review.

## 9. Recorded local validation — 2026-11-14

| Check | Result |
| --- | --- |
| Branch / initial working tree | `feat/visitor-qr-voice-ui`; clean before changes |
| `npm run typecheck` | Passed |
| `npm test` | **168 passed**, 12 test files; all 143 previous tests preserved |
| `npm run build` | Passed; developer production build |
| `npm run build:demo` | Passed; 10-file audited static demo artifact |
| `npm run test:e2e` | **43 passed** in 8.4s, including all 33 previous journeys, using the strict local static policy |
| Negative configuration checks | Unexpected synthetic public-token key and nonempty transcription endpoint both rejected before bundling; values not printed |
| Artifact audit | Passed; no detected credential/private-address/unreviewed-origin patterns; matching route entries; no source maps |
| HTTPS host / real phones / public QR | **Not performed**; no deployment or production URL exists in this task |

Audited artifact SHA-256 (aggregate of sorted relative filenames and bytes):

```text
489c1d6a29133d42589802301e12d6fe121c13d9b063463981c88d3f353b388e
```

Rebuilds may produce a different digest; retain the digest for the exact artifact approved for deployment, not a copied value from an earlier build.
