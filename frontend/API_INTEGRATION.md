# FastAPI integration preparation — proposed contract only

Phase 4 always uses `createMockTaskAdapter`. No real request/status endpoint has been verified, instantiated by the UI, or called. No backend, robot SDK, movement command, or safety authorization is implemented here. This document describes a proposal the backend teammate must review, not an existing API guarantee.

## Static deployment boundary (Phase 5A)

The deployable backend-free profile is `npm run build:demo`. It requires blank transcription configuration, defaults/unwired request paths, and an approved public environment allowlist. Its enforced CSP uses `connect-src 'none'`, blocking API fetches/uploads even on the same origin. The prepared adapters remain available for development and future integration; environment paths alone cannot enable a real task backend. A future connection requires a separately reviewed build profile, CSP, authentication/retention contract, and updated acceptance tests. See [DEPLOYMENT_CHECKLIST.md](./DEPLOYMENT_CHECKLIST.md). No API is connected as part of deployment readiness.

## Prepared frontend adapter

`src/tasks/requestApiAdapter.ts` exports `createRequestApiAdapter(config, getAuthorization)` and `requestApiConfig`. The proposed configurable paths are:

```dotenv
VITE_REQUESTS_ENDPOINT=/api/requests
VITE_REQUEST_STATUS_ENDPOINT=/api/requests/{request_id}
```

These public path settings **do not switch the UI to real requests**. A future integration must deliberately wire a real adapter after the contract, session credential, and safety behavior are agreed. Never put tokens in `VITE_*` settings, nicknames, URLs, QR codes, logs, or browser storage.

Both paths must be root-relative and same-origin, with no query, fragment, credentials, backslash, or cross-origin URL. The status path requires exactly one `{request_id}` placeholder. The backend teammate must arrange HTTPS/same-origin reverse-proxy routing; private robot network addresses must stay backend-only.

## Authorization and expiry

The future authorization provider must supply an in-memory, backend-issued credential independently of any nickname:

```ts
interface SessionAuthorization {
  accessToken: string
  expiresAt: number // epoch milliseconds
}
```

The adapter uses `Authorization: Bearer <accessToken>`, omits cookies, disables fetch caching and redirects, and sends no nickname or mock session ID. It rejects missing/expired credentials and `demo-` IDs as tokens. This is a frontend guard, **not verification of credential authenticity**; only the real backend can authenticate or authorize it. The temporary demo session is not an authenticated session.

The generic task adapter also receives an in-memory `{ id, expiresAt }` context for lifecycle checks. That context is not serialized to the real request API. The UI and adapters check expiry before submission/polling and after responses. A future real integration must agree on authoritative expiry/clock skew, renewal, CSRF requirements if the auth scheme changes, and server-side enforcement.

## POST request proposal

`POST /api/requests` with JSON and `Idempotency-Key` set to the same `client_request_id`:

```ts
interface RequestBody {
  text: string                    // trimmed, nonempty, at most 2000 characters
  source: 'typed' | 'transcript'  // provenance, not authorization; text can be edited
  proposed_action: 'GREET' | 'ROBOT_INFO' | 'HELP' | null
  client_request_id: string      // generated per explicitly confirmed draft
}
```

Only explicit visitor confirmation may call submit. The backend must independently interpret/validate the text and enforce its own allowlist and safety policies; `proposed_action` and AI output are suggestions, never authorization. Compound or unsupported movement requests must not become authorized because they contain an allowed word.

Agree on server idempotency scope, key lifetime, replay behavior, and conflict response. The mock deduplicates identical IDs/payloads and rejects changed payload reuse. The UI locks duplicate clicks and does not automatically retry POSTs. A transport failure may leave a real submission outcome unknown; retries must use an agreed reconciliation/idempotency policy.

## POST and GET response proposal

`GET /api/requests/{request_id}` and successful POST return this same typed wire shape:

```ts
type TaskStatus =
  | 'queued' | 'processing' | 'awaiting_approval' | 'executing'
  | 'completed' | 'denied' | 'failed'

interface TaskResponse {
  request_id: string
  client_request_id: string
  status: TaskStatus
  action: 'GREET' | 'ROBOT_INFO' | 'HELP' | null
  revision: number // nonnegative integer; increasing on changes
  demo: boolean   // must accurately describe whether the task is simulated
  reply?: string // required ONLY at completed; nonempty, <=2000 characters
  reason?:
    | 'unsupported_request' | 'approval_denied'
    | 'execution_failed' | 'robot_unavailable'
}
```

Current wire validation accepts request IDs of 1–100 ASCII letters/digits/underscore/hyphen and client IDs of 1–100 ASCII letters/digits/hyphen. Confirm these limits/formats with the backend. Responses with unknown statuses/actions, invalid revisions, mismatched IDs, premature replies, or missing completion text are rejected. Unknown extra fields are dropped. HTTP error bodies and internal diagnostics are never displayed by the prepared adapter. Confirmed request text remains local in the UI rather than being echoed by the server.

The backend must provide visitor-safe completion text, exclude private addresses/diagnostics and sensitive payloads, and report actual verified outcomes. Do not mark an action completed before it completes. Approval must come from the real safety authority, not a visitor-controlled demo selector. The current demo screen rejects non-demo results and cannot be reused unchanged as a real physical task success screen.

The proposed adapter treats HTTP 401/403 as an invalid/expired authorization context and other non-success responses as service unavailable. Confirm real error codes before wiring it; e.g. a policy-denied task may need a typed `denied` snapshot rather than a generic HTTP authorization error. Do not assume these mappings already match FastAPI.

## Polling, cancellation, and retention decisions

The current demo polls sequentially at 500ms plus adapter latency and has a 12-second frontend deadline. It ignores wrong identities, older revisions, expired-session responses, and responses after navigation/unmount. These are demo defaults, not a real robot execution timeout policy. Agree on polling interval/rate limits, maximum task duration, retry/backoff, server timestamps, task ownership, ordering guarantees, and whether polling should use ETags, SSE, or another mechanism later.

The adapter passes AbortSignals to fetch. Canceling a browser request or disposing UI state **cannot guarantee server-side cancellation, halted robot activity, or deletion**. Only the local mock is disposed today. No cancellation endpoint is proposed or invented. Real cancellation, operator interruption, session expiry during execution, and safe-state behavior require an explicit backend contract before real tasks can be wired.

Agree on what the server stores, who can access it, audio/transcript/request retention, deletion, and logging/redaction. Local Leave session clears frontend references; it is not a backend deletion guarantee. There are no application request/transcript logs or analytics in this frontend.

## Existing transcription proposal

The separately optional adapter remains disconnected by default:

- `VITE_TRANSCRIBE_ENDPOINT` blank means no audio uploads.
- Proposed `POST /api/transcribe`: multipart `audio` Blob/file, actual `mime_type`, rounded `duration_ms`.
- Proposed response: `{ text: string }`, nonempty and at most 2000 characters.
- Only **Send audio for transcription** uploads the individually authorized audio turn; recording/review do not.
- Returned text is editable and still requires a separate task confirmation.
- No nickname or mock session ID is sent. Confirm authentication, accepted formats, size/duration limits, errors, retention, and request binding before enabling.

## Backend teammate checklist before integration

1. Confirm actual HTTPS endpoints, request/response schemas, accepted formats, limits, and public error messages.
2. Provide a real in-memory session credential contract; independently enforce task ownership, authorization, expiry, and allowlist validation server-side.
3. Define real approval/denial/execution/completion semantics and idempotency behavior. Never trust frontend or AI-generated commands as authorization.
4. Define status ordering, timing, polling, errors, unavailable robot behavior, cancellation, and session expiry while a task runs.
5. Keep internal robot addresses/SDK details server-side and return only visitor-safe text and codes.
6. Verify retention/deletion and logging practices; update visitor privacy information to match actual behavior.
7. Deliberately wire the real adapter in a separately reviewed change, with tests against the actual API. Setting endpoint environment values alone cannot enable it in Phase 4.

## Local conversational call lifecycle

`src/call/CallController.ts` owns the local call state machine, generation guards, microphone analysis, local amplitude-based turn detector, per-turn upload consent, compact confirmation and mock task polling. `CallScreen` uses the existing transcription adapter and `createMockTaskAdapter` only; the prepared HTTP task adapter is deliberately unwired. The demo controller rejects real/non-demo task responses, so future integration requires deliberate controller changes as well as a verified adapter.

Start Conversation is the first microphone action. VAD finishes turns locally without transmitting audio. The disconnected demo immediately clears audio and offers a manual editor in-call. A configured development transcription client retains one clip for explicit per-turn Send audio for transcription authorization. The proposed multipart contract above is unchanged; authentication and retention are still unverified. No nickname or demo session ID is used as credentials.

After review, Confirm alone creates a mock task. The seven finite task statuses remain simulated. A terminal demo response schedules the next listening turn after 2.5 seconds only while active, visible, unmuted, unexpired and free of pending work. Mute closes tracks/analysis and prevents resumption; End Call additionally clears conversation history, cancels local polling, aborts/ignores uploads and returns to pre-call. Leave session clears the underlying visitor session too. Browser hiding pauses capture and local tracking, requiring deliberate Unmute. Navigation and expiry dispose the call. These controls do not represent backend cancellation, a physical stop command, server deletion, or real robot connection.

The default public demo must remain API-disabled until the backend teammate verifies contracts, credentials, authorization, safety, content/retention policies, timing and idempotency. No deployed Worker changes are included. See [CALL_INTERFACE.md](./CALL_INTERFACE.md) for local state and capture architecture.
