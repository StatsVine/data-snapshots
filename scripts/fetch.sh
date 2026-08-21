#!/usr/bin/env bash
# Fetch one source end to end: download, canonicalize, flatten.
# Same entrypoint the Action uses, so a local run and CI cannot drift.
#
# Usage: scripts/fetch.sh <name> <url> [--no-csv] [--drop f1,f2]
#                         [--columns c1,c2] [--where 'active=true,team']
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"

name="${1:?usage: fetch.sh <name> <url> [--no-csv] [--drop f1,f2]}"
url="${2:?usage: fetch.sh <name> <url> [--no-csv] [--drop f1,f2]}"
shift 2

csv=1
drop=""
columns=""
where=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-csv)  csv=0; shift ;;
    --drop)    drop="${2:-}"; shift 2 ;;
    --columns) columns="${2:-}"; shift 2 ;;
    --where)   where="${2:-}"; shift 2 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

"$here/download.sh" "$name" "$url"
"$here/canonicalize.py" "$name" --drop "$drop"
[[ $csv -eq 1 ]] && "$here/flatten.py" "$name" --columns "$columns" --where "$where"
exit 0
