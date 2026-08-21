#!/usr/bin/env bash
# Download one source into .raw/<name>.json (gitignored).
# Usage: scripts/download.sh <name> <url>
set -euo pipefail
cd "${DATA_SNAPSHOTS_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"

name="${1:?usage: download.sh <name> <url>}"
url="${2:?usage: download.sh <name> <url>}"

out=".raw/${name}.json"
mkdir -p "$(dirname "$out")"
echo "download $name <- $url"

curl -fsSL --retry 3 --retry-delay 2 --retry-all-errors \
  --max-time 120 \
  -H 'Accept: application/json' \
  -H 'User-Agent: statsvine-data-snapshots/1.0' \
  -o "$out.tmp" "$url"

# Reject anything unparseable before it can clobber good data.
if ! python3 -c 'import json,sys; json.load(open(sys.argv[1]))' "$out.tmp" 2>/dev/null; then
  echo "  FAILED: $name returned non-JSON" >&2
  rm -f "$out.tmp"
  exit 1
fi

mv "$out.tmp" "$out"
