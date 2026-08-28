#!/usr/bin/env bash
# Download one source into .raw/<name>.json (gitignored).
# Usage: scripts/download.sh <name> <url> [--header <header-name>]
#
# --header names a request header to send; its value is read from the
# SOURCE_API_KEY environment variable. The value is never passed as an
# argument, so it cannot surface in `ps` output or a shell trace -- curl
# reads it from a config on stdin instead.
set -euo pipefail
cd "${DATA_SNAPSHOTS_ROOT:-$(cd "$(dirname "$0")/.." && pwd)}"

name="${1:?usage: download.sh <name> <url> [--header <header-name>]}"
url="${2:?usage: download.sh <name> <url> [--header <header-name>]}"
shift 2

header=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --header) header="${2:-}"; shift 2 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

out=".raw/${name}.json"
mkdir -p "$(dirname "$out")"
echo "download $name <- $url"

args=(
  -fsSL --retry 3 --retry-delay 2 --retry-all-errors
  --max-time 120
  -H 'Accept: application/json'
  -H 'User-Agent: statsvine-data-snapshots/1.0'
  -o "$out.tmp" "$url"
)

if [[ -n "$header" ]]; then
  # Fail before the request rather than fetching an anonymous 401 and
  # writing whatever error body the API hands back.
  if [[ -z "${SOURCE_API_KEY:-}" ]]; then
    echo "  FAILED: $name needs $header but SOURCE_API_KEY is empty" >&2
    exit 1
  fi
  printf 'header = "%s: %s"\n' "$header" "$SOURCE_API_KEY" | curl -K - "${args[@]}"
else
  curl "${args[@]}"
fi

# Reject anything unparseable before it can clobber good data.
if ! python3 -c 'import json,sys; json.load(open(sys.argv[1]))' "$out.tmp" 2>/dev/null; then
  echo "  FAILED: $name returned non-JSON" >&2
  rm -f "$out.tmp"
  exit 1
fi

mv "$out.tmp" "$out"
