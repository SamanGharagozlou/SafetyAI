#!/usr/bin/env bash
# Install the pre-commit credential scan. Opt-in, per clone:
#
#   make install-hooks          (or: scripts/install-git-hooks.sh)
#   scripts/install-git-hooks.sh --uninstall
#
# WHY OPT-IN: git hooks are not versioned, so this cannot be imposed by a
# merge. Run it once per clone. A commit can still be forced through with
# `git commit --no-verify`, which is the right escape hatch - the hook is
# there to catch an accident, not to win an argument with the person typing.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOOK="$(git -C "$REPO" rev-parse --git-path hooks/pre-commit)"
HOOK="$(cd "$REPO" && cd "$(dirname "$HOOK")" && pwd)/$(basename "$HOOK")"

if [[ "${1:-}" == "--uninstall" ]]; then
  if [[ -f "$HOOK" ]] && grep -q 'audit_secrets.py' "$HOOK"; then
    rm -f "$HOOK"; echo "removed $HOOK"
  else
    echo "nothing of ours installed at $HOOK"
  fi
  exit 0
fi

# Never clobber a hook somebody else wrote.
if [[ -f "$HOOK" ]] && ! grep -q 'audit_secrets.py' "$HOOK"; then
  echo "A pre-commit hook already exists and is not ours:" >&2
  echo "  $HOOK" >&2
  echo "Add this line to it instead:" >&2
  echo '  python3 scripts/audit_secrets.py --staged || exit 1' >&2
  exit 1
fi

mkdir -p "$(dirname "$HOOK")"
cat > "$HOOK" <<'HOOKEOF'
#!/usr/bin/env bash
# Credential scan over the staged changes only, so it stays fast enough to run
# on every commit. Installed by scripts/install-git-hooks.sh.
# Bypass with: git commit --no-verify
set -euo pipefail
REPO="$(git rev-parse --show-toplevel)"
command -v python3 >/dev/null || { echo "pre-commit: python3 not found, skipping credential scan" >&2; exit 0; }
python3 "$REPO/scripts/audit_secrets.py" --staged
HOOKEOF
chmod +x "$HOOK"
echo "installed $HOOK"
echo "Staged changes are now scanned for credentials on every commit."
echo "Bypass once with: git commit --no-verify   |   remove: scripts/install-git-hooks.sh --uninstall"
