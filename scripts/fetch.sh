#!/usr/bin/env bash
# Fetch one source end to end: download, canonicalize, flatten.
# Same entrypoint the Action uses, so a local run and CI cannot drift.
#
# Usage: scripts/fetch.sh <name> <url> [--no-csv] [--root key]
#                         [--keep f1,f2 | --drop f1,f2] [--sort f1,f2]
#                         [--columns c1,c2] [--where 'active=true,team']
#                         [--views '[{"name":"all"},{"name":"x","where":"team"}]']
#                         [--header x-api-key]
#
# --header takes only the header's name; the value comes from SOURCE_API_KEY
# in the environment, so a key never lands in argv. See download.sh.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"

name="${1:?usage: fetch.sh <name> <url> [--no-csv] [--drop f1,f2]}"
url="${2:?usage: fetch.sh <name> <url> [--no-csv] [--drop f1,f2]}"
shift 2

csv=1
root=""
keep=""
drop=""
sort=""
columns=""
where=""
views=""
header=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-csv)  csv=0; shift ;;
    --root)    root="${2:-}"; shift 2 ;;
    --keep)    keep="${2:-}"; shift 2 ;;
    --drop)    drop="${2:-}"; shift 2 ;;
    --sort)    sort="${2:-}"; shift 2 ;;
    --columns) columns="${2:-}"; shift 2 ;;
    --where)   where="${2:-}"; shift 2 ;;
    --views)   views="${2:-}"; shift 2 ;;
    --header)  header="${2:-}"; shift 2 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

download_args=("$name" "$url")
if [[ -n "$header" ]]; then
  download_args+=(--header "$header")
fi
"$here/download.sh" "${download_args[@]}"
"$here/canonicalize.py" "$name" --root "$root" --keep "$keep" --drop "$drop" \
  --sort "$sort"
if [[ $csv -eq 1 ]]; then
  if [[ -n "$views" ]]; then
    "$here/flatten.py" "$name" --views "$views"
  else
    "$here/flatten.py" "$name" --columns "$columns" --where "$where"
  fi
fi
exit 0
