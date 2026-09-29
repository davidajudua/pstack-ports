#!/usr/bin/env bash
# Download the pinned upstream pstack archive and print the path of its pstack/ directory.
# Usage: scripts/fetch-upstream.sh [cache-dir]
set -euo pipefail

PIN=ecc249f1e306fc64ddf83c7bed16cacf7c2239db
cache=${1:-"$(cd "$(dirname "$0")/.." && pwd)/.upstream"}
pstack="$cache/plugins-$PIN/pstack"

if [ ! -d "$pstack" ]; then
  mkdir -p "$cache"
  curl -fsSL "https://github.com/cursor/plugins/archive/$PIN.tar.gz" -o "$cache/source.tar.gz"
  tar -xzf "$cache/source.tar.gz" -C "$cache"
  rm "$cache/source.tar.gz"
fi
echo "$pstack"
