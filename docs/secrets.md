# Secrets and credential hygiene

Where every credential in this system lives, why none of them are in the
repository, and the check that keeps it that way.

Rule, from [AGENTS.md](../AGENTS.md): *never commit tokens, Vapi private keys,
SSH passwords or Tailscale auth keys — use env vars.* This doc is the
mechanical half of that rule.

## The inventory

Full reference with defaults and per-service notes: [`.env.example`](../.env.example)
(whole stack) and [`frontend/.env.example`](../frontend/.env.example) (browser bundle).

| Credential | Guards | Where the real value lives |
|---|---|---|
| `D1_ROBOT_TOKEN` | the Jetson robot/voice API — **moves a physical robot** | Jetson: `~/vapi-robot/start_server.sh`, `/etc/d1-console.env` (0600) |
| `VAPI_PRIVATE_KEY` | the whole Vapi account (all transcripts, create/delete assistants) | `frontend/scripts/vapi/credentials.json` (gitignored), operator's env |
| `CLAWMETRY_INBOUND_TOKEN` / `CLAWMETRY_UPSTREAM_TOKEN` | the ClawMetry tap in front of the robot server | Jetson env / `--env-file` |
| `BRAIN_API_KEY`, `COSMOS_API_KEY` | the world-model safety judge | `--env-file` passed to `clawmetry/server.py` |
| `CONSOLE_KEY`, `FACE_KEY` | pages on a public tunnel that can start a billable call and drive the robot | generated per run, never stored |

Every one is read from the environment at startup. None has a committed
default, and three services **refuse to start** without theirs rather than
falling back to something permissive: `tools/robot-console/server.mjs` exits if
`D1_ROBOT_TOKEN` is unset, and exits if it is bound to a non-loopback address
with no `CONSOLE_KEY`.

### Deliberately public, and why

Four values in this repo look like credentials to a scanner and to a reviewer,
and are not:

- **`2cdfae81-…` — the Vapi *public* key.** Designed to ship inside a browser
  bundle. Anything in a `VITE_*` variable is compiled into the bundle anyway,
  so there is no secrecy to lose.
- **`a366d544-…`, `19828665-…`, `503c0c1a-…` — Vapi assistant ids.** Opaque
  handles, not credentials; holding one grants nothing.

They are compiled in as defaults on purpose, documented at each site
([`frontend/src/voice/vapiDefaults.ts`](../frontend/src/voice/vapiDefaults.ts),
`tools/robot-console/server.mjs`, `tools/robot-face/server.mjs`). The reason is
a failure that already happened: a console restart that dropped
`VAPI_ASSISTANT_ID` silently downgraded the spa greeter into a bare robot
remote that answered spa questions with a shrug — no error anywhere, visible
only by talking to it. A working default turns that class of outage into a
non-event. Each is listed in the scanner's `ALLOW` table with that reason, so
it still *matches* and is waived in the open rather than being invisible.

Rotate all four if this repository is ever made public, and after the event.

## The check

```bash
make audit-secrets                       # every tracked file
python3 scripts/audit_secrets.py --all   # tracked + untracked
python3 scripts/audit_secrets.py --staged   # what a commit would contain
make install-hooks                       # run --staged on every commit (opt-in, per clone)
python3 scripts/test_audit_secrets.py    # 14 tests: every rule fires, and stays quiet
```

Pure stdlib Python, no network, under a second on this repo. It looks for
provider key shapes (private-key blocks, AWS, OpenAI/Anthropic-style `sk-`,
Stripe live, Google, GitHub, Slack, Tailscale auth keys, JWTs), passwords
embedded in URLs, `sshpass -p`, and — the rule that matters most — any
secret-named variable assigned a literal that looks generated rather than
written. That last one is what catches a token with no recognisable provider
shape at all.

Three properties worth knowing:

- **It never prints the matched value.** A scanner that echoes what it found
  moves the secret into CI logs, scrollback and bug reports. It reports file,
  line and rule name; you open the line. Same convention as the existing
  bundle audit, `frontend/scripts/audit-dist.mjs`.
- **`--staged` reads the index, not the disk.** `git add` and then editing the
  file leaves the two different, and the one being committed is the blob in
  the index.
- **Exceptions are narrow by construction.** An `ALLOW` entry needs a path, the
  exact value and a written reason; a test asserts every entry has all three,
  and another asserts a test-file waiver does not cover production code. The
  failure message says it outright: never widen a rule to silence a hit.

### What it cannot do

It cannot prove the absence of a secret. A 32-character token with no
recognisable shape, assigned to a variable named `x`, passes. It raises the
floor; it does not replace reading the diff. It is also not a scan of git
*history* — it sees the current tree. History was checked clean by hand when
the scanner was added; a committed-then-deleted secret must be rotated, not
just removed, because the blob stays reachable.

## Related

- Bundle-level audit (the built frontend must contain no credential-like
  material, no private addresses and no unreviewed origins):
  `frontend/scripts/audit-dist.mjs`, run by `npm run build:demo`.
- RTSP URLs with embedded credentials are stripped from logs and diagnostics:
  `human_perception.config.redact_url`.
- Camera-frame privacy is a separate concern with its own doc:
  [feature_privacy_anonymization.md](feature_privacy_anonymization.md).
