# ClawMetry Phase 5A deployment checklist

**Status: this update is local only; the existing public Worker is unchanged.** Existing deployment headers, provider account, DNS, certificate, physical phones, and hosting retention have not been verified by this update. The Pages configuration below is an alternative future static-hosting plan, not a change to the deployed Worker. This profile needs no backend. Do not publish, commit, push, or change production without separate authorization.

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

After a separately authorized deployment, paste the **actual HTTPS origin without a trailing slash** and inspect final headers. These are future operator checks, not evidence that production headers have been verified:

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

Use physical iPhone Safari and Android Chrome on a trusted HTTPS test host, without updating the public demo until manually approved. Record OS/browser versions. QR webviews may lack required media APIs. Do not bypass certificates.

- [ ] Join as Guest and nickname; no microphone prompt before Start Conversation. Demo and no-real-robot labels stay visible.
- [ ] Start deliberately, Allow, wait for calibration, speak, then pause. Speech finishes locally after silence; no recorder form/player appears in the disconnected demo. Check actual reactive orb/waveform, not decorative motion.
- [ ] Test quiet, crowded rooms, normal pauses, soft speech, bumps, Bluetooth, silence, and continuous speech. Tune VAD with evidence; it is an amplitude heuristic. Finish turn remains a small fallback; 20-second maximum ends only the utterance.
- [ ] Mute during listening and speech: capture indicator stops, partial audio discarded. Wait: no hidden collection. Unmute explicitly resumes.
- [ ] No connected transcription: blank manual editor, no fabricated transcript or POST. Review → Edit/Cancel → Confirm; only Confirm creates a demo task. Unsupported movement is declined. Verify every simulated task state, unavailable service and timeout.
- [ ] After a demo response, another turn resumes safely within the same visible unmuted call. Verify fresh AudioContext/capture on Safari; if interrupted, Unmute is required. Never resume during review/upload/task processing, when muted or hidden.
- [ ] End Call during capture, permission prompt, review, upload development fixture and task processing: resources/history clear, pre-call returns, session remains. Late Allow must stop immediately. Leave session clears nickname/session too.
- [ ] Open privacy, Back, switch tabs/apps, lock/unlock, revoke permission, unplug/change audio devices. Mic stops and does not restart without deliberate action after interruption.
- [ ] Expiry during capture/review/confirmation/task clears data and blocks submission. Use default ten minutes, or a preview-only short session setting; rebuild the normal artifact afterward.
- [ ] Test 320, 375, 390, 430px widths, short landscape, keyboard, zoom/large text, VoiceOver/TalkBack and reduced motion. Mute/End remain reachable; normal page scroll, no horizontal overflow or nested traps.
- [ ] Inspect network/storage: static same-origin GET only, no audio POST or third-party services, analytics, cookies, local/session storage, IndexedDB or service worker. Optional configured integration belongs in a separate reviewed profile with explicit per-turn upload consent.
- [ ] Verify actual HTTPS, direct/refresh routes, missing API/asset 404s, cellular/Wi-Fi behavior and privacy wording.

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
- [ ] Ask active visitors to Leave session/reload. Already-open pages may keep their loaded JS/memory until reload; rollback cannot forcibly erase client or server data.

## 8. Privacy/security audit findings and limits

**Local findings:** frontend source uses no persistent visitor storage, analytics, application request/transcript logging, third-party fonts/audio services, robot addresses, privileged credentials, or automatic mic capture. Task APIs remain local mocks. The demo release rejects connected transcription. Recording cleanup, URL revocation, expiry, duplicate/stale task handling, and deliberate consent remain covered by tests. Source maps/private files are not in the audited upload. Strict CSP blocked an intentional same-origin fetch probe while the normal journey had no policy violations or unexpected external requests.

**Remaining gaps:** hosting requests can expose IP/user-agent/timing to the host/CDN and may be logged. Provider analytics, retention, account settings, TLS/DNS, edge header application, and real-phone capture cannot be inferred from a local artifact. The privacy notice already says hosting practices are unverified; update it if actual hosting adds processing. No GDPR/legal certification or backend deletion behavior is claimed.

React memory and object-URL cleanup remove application references, not a cryptographic memory wipe. Browser/OS buffers, browser history/back-forward cache, crash/session restore, device permissions, and suspended timers remain platform-dependent. An inactive page may remain in browser memory; Leave session before handing the device to another visitor. Timers/cleanup execute when the browser schedules JavaScript. The app is a public prototype with client-controlled demo sessions, not an authenticated robot-safety boundary. CSP and pattern scans do not protect against every first-party compromise or unknown hardcoded secret.

- [ ] Obtain owner review of actual hosting log/retention practices and privacy wording.
- [ ] Complete the physical-device tests; local Chrome emulation is not Safari/Android hardware verification.
- [ ] Obtain separate deployment authorization after these checks and artifact review.

## 9. Historical Phase 5A validation — 2026-11-14 (superseded locally)

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
| HTTPS host / real phones / public QR | **Not performed in that historical phase**; current update leaves the existing public Worker unchanged |

Historical Phase 5A artifact SHA-256 (not the current Phase 5C artifact; aggregate of sorted relative filenames and bytes):

```text
489c1d6a29133d42589802301e12d6fe121c13d9b063463981c88d3f353b388e
```

Rebuilds may produce a different digest; retain the digest for the exact artifact approved for deployment, not a copied value from an earlier build.

## 10. Final conversational interface — local update

The existing deployed Worker, security headers, environment guards, routes and ZIP archives are unchanged. Old archive contents and historical hashes above are not this update’s release artifact. Do not upload those archives. The current implementation uses a dedicated CallScreen, local calibrated amplitude VAD, a bounded in-memory history, explicit review/confirmation, and anchored Mute/End controls. See [CALL_INTERFACE.md](./CALL_INTERFACE.md) for current validation and known limitations.

No deployment, commit, merge, push, physical phone verification, real API connection or production-header verification is performed. Before any separately authorized deployment, rebuild/re-audit the exact artifact, retain its emitted digest and complete the physical-device checklist. Keep the existing production origin and printed QR unchanged.

Current conversational update: TypeScript passed; **246 unit/integration tests** (17 files) and **52 browser tests** passed; audited 10-file Demo build passed. All seven requested screenshot states were reviewed. Screenshots and test artifacts are ignored local output. Physical-phone tests and backend integration remain pending. See CALL_INTERFACE.md for the exact current artifact audit record.
