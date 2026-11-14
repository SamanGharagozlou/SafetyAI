# Shared helpers for scripts/setup_*.sh (sourced, not executed).
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
export REPO_ROOT
cd "$REPO_ROOT"
if [ -f scripts/isaac/env.sh ]; then source scripts/isaac/env.sh; fi   # ISAAC_PYTHON, SIMREADY_DIR, VENDOR_*_DIR, UNITREE_MUJOCO_DIR (absent in the SafetyAI kit)
PARENT="$(dirname "$REPO_ROOT")"
VENV="$REPO_ROOT/.venv"
PY="$VENV/bin/python"

if [ -t 1 ]; then B=$'\e[1m'; G=$'\e[32m'; R=$'\e[31m'; Y=$'\e[33m'; N=$'\e[0m'; else B=; G=; R=; Y=; N=; fi
step() { echo; echo "${B}== $*${N}"; }
ok()   { echo "${G}ok${N}   $*"; }
warn() { echo "${Y}warn${N} $*"; }
die()  { echo "${R}FAIL${N} $*" >&2; exit 1; }

FAILS=0
check() {   # check "label" command...   (self-check line; never aborts, counts failures)
  local label="$1"; shift
  if "$@" >/tmp/.setup-check.$$ 2>&1; then ok "$label"; else
    echo "${R}FAIL${N} $label"; tail -15 /tmp/.setup-check.$$ | sed 's/^/     /'; FAILS=$((FAILS + 1)); fi
  rm -f /tmp/.setup-check.$$
}
finish() {  # finish "<what>"
  echo
  if [ "$FAILS" = 0 ]; then echo "${G}${B}$1: self-check passed${N}"; else echo "${R}${B}$1: $FAILS self-check(s) failed${N}"; exit 1; fi
}

need() { command -v "$1" >/dev/null 2>&1 || die "'$1' not found: $2"; }

# git_pin DIR REPO FULL_SHA : shallow clone pinned to one commit; no-op when already there
git_pin() {
  local d="$1" repo="$2" sha="$3"
  if [ -d "$d/.git" ] && [ "$(git -C "$d" rev-parse HEAD 2>/dev/null)" = "$sha" ]; then ok "$(basename "$d") at ${sha:0:7}"; return; fi
  mkdir -p "$d"
  [ -d "$d/.git" ] || { git -C "$d" init -q; git -C "$d" remote add origin "$repo"; }
  git -C "$d" fetch -q --depth 1 origin "$sha"
  git -C "$d" checkout -q --force FETCH_HEAD
  ok "$(basename "$d") checked out at ${sha:0:7} ($repo)"
}

require_core() { [ -x "$PY" ] || die "no $VENV: run scripts/setup_core.sh first"; }
