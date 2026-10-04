#!/usr/bin/env bash
# Download the upstream pstack archive a port is pinned to and print the path of its pstack/ directory.
# Each port has its own pin, so one port can move to a newer pstack while the others stay where they are.
# Usage: scripts/fetch-upstream.sh <claude|codex|grok> [cache-dir]
set -euo pipefail

case "${1:-}" in
  claude) PIN=a58628271271837ef5f386adca29c0812683a19a ;; # pstack 0.15.8
  codex | grok) PIN=ecc249f1e306fc64ddf83c7bed16cacf7c2239db ;; # pstack 0.15.5
  *)
    echo "usage: scripts/fetch-upstream.sh <claude|codex|grok> [cache-dir]" >&2
    exit 2
    ;;
esac
cache=${2:-"$(cd "$(dirname "$0")/.." && pwd)/.upstream"}
pstack="$cache/plugins-$PIN/pstack"

if [ ! -d "$pstack" ]; then
  mkdir -p "$cache"
  curl -fsSL "https://github.com/cursor/plugins/archive/$PIN.tar.gz" -o "$cache/source.tar.gz"
  tar -xzf "$cache/source.tar.gz" -C "$cache"
  rm "$cache/source.tar.gz"
fi
echo "$pstack"
