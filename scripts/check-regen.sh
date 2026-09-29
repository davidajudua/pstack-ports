#!/usr/bin/env bash
# Regenerate a port from the pinned upstream into scratch space and fail unless it matches the committed pack byte for byte.
# Usage: scripts/check-regen.sh <claude|codex|grok> [upstream-pstack-dir]
set -euo pipefail

root=$(cd "$(dirname "$0")/.." && pwd)
port=${1:?usage: check-regen.sh <claude|codex|grok> [upstream-pstack-dir]}
upstream=${2:-"$("$root/scripts/fetch-upstream.sh")"}
scratch=$(mktemp -d)
trap 'rm -rf "$scratch"' EXIT

case "$port" in
  claude)
    # The read-only search hook is hand-maintained, so the port only checks that it is present.
    mkdir -p "$scratch/pack"
    cp -R "$root/claude/pack/hooks" "$scratch/pack/hooks"
    python3 "$root/claude/port/port.py" "$upstream" "$scratch/pack"
    ;;
  grok)
    # The Grok port refuses to replace pack files git does not hold, so give it a clean repository.
    git init -q "$scratch"
    python3 "$root/grok/port/port.py" "$upstream" "$scratch/pack"
    ;;
  codex)
    python3 "$root/codex/port/verify.py"
    python3 "$root/codex/port/port.py" "$upstream" --output "$scratch/pack"
    ;;
  *)
    echo "unknown port: $port" >&2
    exit 2
    ;;
esac

diff -r -x node_modules -x __pycache__ "$root/$port/pack" "$scratch/pack"
echo "$port: committed pack matches a fresh regeneration"
