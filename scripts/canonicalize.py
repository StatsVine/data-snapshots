#!/usr/bin/env python3
"""Canonicalize .raw/<name>.json into data/<name>.json.

Canonical form is *pretty* on purpose: git diffs line-by-line, so minified
JSON would report every change as "the whole file changed". Rules:
  - keys sorted recursively
  - 2-space indent, one value per line
  - UTF-8 kept literal (no \\uXXXX escaping)
  - trailing newline
  - NaN/Infinity rejected (not valid JSON, and unstable across parsers)

Usage: scripts/canonicalize.py <name> [--drop field1,field2]
"""

import argparse
import json
import os
import pathlib
import sys

# Overridable so tests can run against a temp tree instead of the real repo.
ROOT = pathlib.Path(
    os.environ.get(
        "DATA_SNAPSHOTS_ROOT", pathlib.Path(__file__).resolve().parent.parent
    )
)


def strip(node, drop):
    if isinstance(node, dict):
        return {k: strip(v, drop) for k, v in node.items() if k not in drop}
    if isinstance(node, list):
        return [strip(v, drop) for v in node]
    return node


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("name")
    # Fields that change every poll without carrying real information.
    # Dropping them is the difference between a meaningful diff and pure churn.
    ap.add_argument("--drop", default="", help="comma-separated fields to strip")
    args = ap.parse_args()

    src = ROOT / ".raw" / f"{args.name}.json"
    if not src.exists():
        print(f"FAILED {args.name}: no raw file at {src}", file=sys.stderr)
        return 1

    try:
        # parse_constant fires on NaN/Infinity, which json.loads otherwise accepts.
        data = json.loads(
            src.read_text(encoding="utf-8"),
            parse_constant=lambda c: (_ for _ in ()).throw(
                ValueError(f"non-JSON constant {c!r}")
            ),
        )
    except ValueError as e:
        print(f"FAILED {args.name}: {e}", file=sys.stderr)
        return 1

    drop = {f.strip() for f in args.drop.split(",") if f.strip()}
    if drop:
        data = strip(data, drop)

    out = ROOT / "data" / f"{args.name}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(data, sort_keys=True, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"canonicalize {args.name} -> {out.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
