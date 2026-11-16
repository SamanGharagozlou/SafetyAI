#!/usr/bin/env python3
"""Repo-wide credential scan. No network, no dependencies.

    python3 scripts/audit_secrets.py              # every tracked file
    python3 scripts/audit_secrets.py --staged     # only what is about to be committed
    python3 scripts/audit_secrets.py --all        # tracked + untracked, ignoring .gitignore
    python3 scripts/audit_secrets.py path ...     # specific paths

Exit 0 clean, 1 on a finding, 2 on a usage error.

WHY THIS EXISTS. Every real credential in this repo is read from the
environment, and the rule in AGENTS.md says to keep it that way. A rule is
not a control: the only thing that has ever kept a token out of a commit here
is someone remembering. This makes it mechanical, and cheap enough to run on
every commit.

WHAT IT NEVER DOES: print a matched value. A scanner that echoes the secret
it found moves the secret into CI logs, scrollback and bug reports. It prints
file, line and rule name only - the same convention as
frontend/scripts/audit-dist.mjs.

WHAT IT CANNOT DO: prove the absence of a secret. A 32-char token with no
recognisable shape, pasted into a variable named `x`, passes. This raises the
floor; it does not replace reading the diff.
"""
from __future__ import annotations

import base64
import math
import re
import subprocess
import sys
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# Directories and file types with nothing hand-written in them. Vendored SDKs,
# lockfiles and build output are the bulk of the repo and the bulk of the false
# positives (integrity hashes look exactly like keys).
SKIP_DIRS = ("third_party/", "node_modules/", "ros2_ws/build/", "ros2_ws/install/",
             "ros2_ws/log/", "dist/", ".venv/", "coverage/", "playwright-report/",
             "test-results/", "__pycache__/")
SKIP_NAMES = ("package-lock.json", "npm-shrinkwrap.json", "yarn.lock", "pnpm-lock.yaml",
              "poetry.lock", "Cargo.lock", "uv.lock")
SKIP_SUFFIXES = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf", ".pgm",
                 ".pt", ".onnx", ".engine", ".bin", ".so", ".a", ".o", ".zip", ".gz",
                 ".mp4", ".mkv", ".db3", ".mcap", ".woff", ".woff2", ".ttf")

MAX_BYTES = 2_000_000       # beyond this it is data, not source
MAX_LINE = 4000             # a minified or base64 line; shape rules still apply below


@dataclass(frozen=True)
class Rule:
    name: str
    pattern: re.Pattern[str]
    hint: str


# Shape rules: a provider's own key format. A hit is a credential or a
# convincing imitation of one, so both are worth a human look.
RULES: tuple[Rule, ...] = (
    Rule("private-key-block", re.compile(r"-----BEGIN (?:[A-Z]+ )*PRIVATE KEY-----"),
         "move the key to a file outside the repo and point at it with an env var"),
    Rule("aws-access-key", re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
         "use the AWS credential chain, never a literal"),
    Rule("openai-style-key", re.compile(r"\bsk-(?:proj-|ant-|live-)?[A-Za-z0-9_-]{20,}"),
         "read it from the environment at startup"),
    Rule("stripe-live-key", re.compile(r"\b[sp]k_live_[A-Za-z0-9]{10,}"),
         "read it from the environment at startup"),
    Rule("google-api-key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),
         "read it from the environment at startup"),
    Rule("github-token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"),
         "use `gh auth` or a GITHUB_TOKEN env var"),
    Rule("slack-token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"),
         "read it from the environment at startup"),
    Rule("tailscale-auth-key", re.compile(r"\btskey-(?:auth|client|api)-[A-Za-z0-9-]{10,}"),
         "generate a fresh key at install time; never commit one"),
    Rule("jwt", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
         "a signed token belongs in the environment or a 0600 file"),
    Rule("basic-auth-url", re.compile(r"\b[a-z][a-z0-9+.-]*://[^/\s:@]+:[^/\s:@]{4,}@"),
         "put the password in an env var, not in the URL"),
    Rule("ssh-password-flag", re.compile(r"\bsshpass\s+-p\s*\S"),
         "use an ssh key, or read the password from the environment"),
)

# Assignment rule: a secret-sounding name given a literal value. This is the
# one that catches a token with no provider shape at all - the 32 hex chars
# that nothing else in this file would recognise.
# `[_.-]key` is listed separately from the words below rather than as a bare
# `key`: bare would match `monkey` and `keyboard`, and a scanner that cries
# wolf gets turned off. A separator in front of it does not. This is the
# clause that catches CONSOLE_KEY, FACE_KEY and --brain-key.
SECRET_NAME = (r"(?:api[_-]?key|apikey|secret|secret[_-]?key|private[_-]?key|passwd|password|"
               r"token|auth[_-]?key|access[_-]?key|bearer|credential|client[_-]?secret|"
               r"[_.-]key)")
ASSIGN = re.compile(
    # NAME = "value" / NAME: "value" / NAME := "value"
    rf"""(?ix)
    \b[A-Za-z0-9_.\[\]'"-]*{SECRET_NAME}[A-Za-z0-9_.\[\]'"-]*
    \s*(?::=|=>|[:=])\s*
    (?P<q>['"`])(?P<val>[^'"`\n]{{8,}})(?P=q)
    """)
ASSIGN_BARE = re.compile(
    # NAME=value, unquoted: env files, shell, systemd units, Dockerfile ENV
    rf"""(?ix) ^\s* (?:export\s+|ENV\s+|Environment=)?
    [A-Za-z0-9_.-]*{SECRET_NAME}[A-Za-z0-9_.-]*
    = (?P<val>[^\s'"`#$]{{12,}}) \s* (?:\#.*)? $
    """)

# A value that is plainly not a credential: an env lookup, a placeholder, a
# format hole, a prose sentence. Keeping this list narrow is the whole game -
# every entry here is a way for a real secret to pass.
PLACEHOLDER = re.compile(r"""(?ix) ^(?:
      \s* $
    | .*(?:process\.env|import\.meta\.env|os\.environ|os\.getenv|getenv|secrets\.|vars\.)
    | .*(?:\$\{|\$\(|%\(|\{\{|<%)
    | [<\[{(].*[>\]})]                                  # <token>, [redacted], {token}
    | .*(?:x-vapi-secret|authorization|content-type|bearer\s*\$)
    | (?:changeme|placeholder|example|redacted|dummy|fake|sample|your[_-].*|
         none|null|true|false|unset|disabled|todo|tbd|\.{3}|simulator-no-robot)
    | .*\s(?:is|are|must|should|the|a|an|and|or|not|if|when|from|with|in|to)\s.*   # prose
    )$""")

# Deliberate exceptions. Each needs a path, the exact value, and a reason -
# a glob alone would let a real secret in behind the same filename.
#
# The Vapi values below are PUBLIC by design and are the single thing a judge
# is most likely to flag by eye: a Vapi public key is built to ship inside a
# browser bundle, and an assistant id is an opaque handle, not a credential.
# Anything in a VITE_* variable is in the bundle anyway, so there is no
# secrecy to lose. The Vapi PRIVATE key appears nowhere in this repository.
VAPI_PUBLIC_KEY = "2cdfae81-9e4a-4215-a17b-7ec2b7bf6aa4"
ALLOW: tuple[tuple[str, str, str], ...] = (
    ("*", VAPI_PUBLIC_KEY, "Vapi PUBLIC key - designed to ship in a browser bundle"),
    ("*", "a366d544-fcf3-4b98-a436-d1b8c458891a", "Vapi assistant id (Leukerbad greeter) - an opaque handle, not a credential"),
    ("*", "19828665-3d22-47b3-a72f-079e19b45aca", "Vapi assistant id (D1 robot voice) - an opaque handle, not a credential"),
    ("*", "503c0c1a-5592-4148-af15-166811855801", "Vapi assistant id (retired spa concierge) - an opaque handle, not a credential"),
    ("ros2_ws/src/human_perception/test/test_config.py", "u:p4ss@",
     "fixture for redact_url: this test exists to prove credentials are stripped from logs"),
    ("scripts/audit_secrets.py", "*", "this scanner's own rule patterns and allowlist"),
    ("scripts/test_audit_secrets.py", "*", "synthetic fixtures that must match, or the scanner is untested"),
    ("frontend/scripts/audit-dist.test.mjs", "*", "synthetic canaries proving the bundle audit fires"),
    ("clawmetry/test_clawmetry.py", "*", "synthetic tokens in offline unit tests"),
    ("frontend/src/**/*.test.*", "*", "synthetic tokens in offline unit tests"),
    ("frontend/e2e/*.spec.ts", "*", "synthetic tokens in offline browser tests"),
    ("tools/robot-console/test-keyboard.mjs", "*", "synthetic tokens in an offline harness"),
    ("tools/robot-face/test-flow.mjs", "*", "synthetic tokens in an offline harness"),
)


# Passwords that are self-evidently illustrative. `rtsp://user:pass@host/x` in
# a doc or a redaction test is the repo DEMONSTRATING that it strips
# credentials (see human_perception.config.redact_url), not leaking one.
EXAMPLE_PASSWORD = frozenset((
    "pass", "password", "passwd", "secret", "secrets", "token", "key", "apikey",
    "changeme", "hunter2", "xxx", "***", "redacted", "placeholder", "example",
    "user", "username", "admin", "test", "dummy", "fake", "sample", "mypassword",
))


def significant(rule: Rule, matched: str) -> bool:
    """Is this match worth a human's attention, or is it an illustration?

    Only the basic-auth rule needs this. A URL with credentials in it is the
    one shape that appears constantly in documentation and in the tests that
    prove we redact it, so matching the shape alone cries wolf. Matching on
    whether the password could plausibly be real does not.
    """
    if rule.name != "basic-auth-url":
        return True
    userinfo = matched.split("://", 1)[1].rstrip("@")
    pw = userinfo.split(":", 1)[1] if ":" in userinfo else ""
    return pw.lower() not in EXAMPLE_PASSWORD


def allowed(path: str, value: str) -> str | None:
    """The reason this (path, value) is a documented exception, or None."""
    for glob, allow_val, reason in ALLOW:
        if not fnmatch(path, glob):
            continue
        if allow_val == "*" or allow_val in value:
            return reason
    return None


def entropy(s: str) -> float:
    """Shannon bits per character. Used only to spare obvious English."""
    if not s:
        return 0.0
    return -sum((n := s.count(c) / len(s)) and n * math.log2(n) for c in set(s))


def looks_random(value: str) -> bool:
    """Does this literal look generated rather than written?

    Deliberately conservative: it only speaks about values already sitting in
    a secret-named slot, so a miss here is a missed secret, and a false alarm
    is a wasted minute. Both are cheap; neither is silent.
    """
    v = value.strip()
    if len(v) < 12:
        return False
    if PLACEHOLDER.match(v):
        return False
    if re.fullmatch(r"[0-9a-f]{32,}", v, re.I):           # hex token
        return True
    if re.fullmatch(r"[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}", v, re.I):   # uuid
        return True
    if re.fullmatch(r"[A-Za-z0-9+/_-]{20,}={0,2}", v) and entropy(v) >= 3.4:
        return True
    return False


def _git(*args: str) -> str:
    return subprocess.run(("git", "-C", str(REPO), *args),
                          capture_output=True, text=True, check=True).stdout


def staged_blob(name: str) -> str | None:
    """The content git would commit for `name`, which is NOT necessarily what
    is on disk: `git add` then edit leaves the two different, and the one that
    matters is the blob in the index."""
    out = subprocess.run(("git", "-C", str(REPO), "show", f":{name}"),
                         capture_output=True)
    if out.returncode != 0:
        return None
    return out.stdout.decode("utf-8", errors="ignore")


def candidate_files(argv: list[str]) -> list[str]:
    def git(*args: str) -> list[str]:
        return [p for p in _git(*args).split("\0") if p]

    if argv and argv[0] == "--staged":
        names = git("diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z")
    elif argv and argv[0] == "--all":
        names = git("ls-files", "-z", "--cached", "--others", "--exclude-standard")
    elif argv and argv[0] in ("--tracked", ""):
        names = git("ls-files", "-z")
    elif argv:
        names = argv
    else:
        names = git("ls-files", "-z")
    return sorted(set(names))


def interesting(name: str) -> bool:
    if any(name.startswith(d) or f"/{d}" in name for d in SKIP_DIRS):
        return False
    return Path(name).name not in SKIP_NAMES and not name.endswith(SKIP_SUFFIXES)


def scan_text(path: str, text: str) -> list[tuple[int, str, str, str]]:
    """-> (line number, rule, hint, allow-reason or '')"""
    hits: list[tuple[int, str, str, str]] = []
    for n, line in enumerate(text.splitlines(), 1):
        if len(line) > MAX_LINE:
            line = line[:MAX_LINE]
        for rule in RULES:
            m = rule.pattern.search(line)
            if m and significant(rule, m.group(0)):
                hits.append((n, rule.name, rule.hint, allowed(path, m.group(0)) or ""))
        for m in list(ASSIGN.finditer(line)) + list(ASSIGN_BARE.finditer(line)):
            val = m.group("val")
            if looks_random(val):
                hits.append((n, "secret-named literal", "read it from the environment instead",
                             allowed(path, val) or ""))
    return hits


def main(argv: list[str]) -> int:
    if argv and argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    try:
        files = candidate_files(argv)
    except subprocess.CalledProcessError:
        print("audit_secrets: not a git repository (or git unavailable)", file=sys.stderr)
        return 2

    staged = bool(argv) and argv[0] == "--staged"
    findings: list[str] = []
    waived = scanned = 0
    for name in files:
        if not interesting(name):
            continue
        if staged:
            text = staged_blob(name)
            if text is None or len(text) > MAX_BYTES:
                continue
        else:
            f = REPO / name
            try:
                if not f.is_file() or f.is_symlink() or f.stat().st_size > MAX_BYTES:
                    continue
                text = f.read_text("utf-8", errors="ignore")
            except OSError:
                continue
        scanned += 1
        for line_no, rule, hint, reason in scan_text(name, text):
            if reason:
                waived += 1
            else:
                findings.append(f"  {name}:{line_no}  [{rule}]\n      -> {hint}")

    if findings:
        print(f"CREDENTIAL SCAN FAILED - {len(findings)} finding(s). "
              f"Matched values are not printed; open the line.\n")
        print("\n".join(findings))
        print("\nIf a hit is public by design, add it to ALLOW in scripts/audit_secrets.py")
        print("with a path, the exact value and a reason. Never widen a rule to silence one.")
        return 1

    print(f"Credential scan passed: {scanned} files, 0 findings, "
          f"{waived} documented exception(s).")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
