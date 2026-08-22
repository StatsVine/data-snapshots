#!/usr/bin/env python3
"""Canonicalize .raw/<name>.json into data/<name>.json.

Canonical form is *pretty* on purpose: git diffs line-by-line, so minified
JSON would report every change as "the whole file changed". Rules:
  - keys sorted recursively
  - 2-space indent, one value per line
  - UTF-8 kept literal (no \\uXXXX escaping)
  - trailing newline
  - NaN/Infinity rejected (not valid JSON, and unstable across parsers)
  - optionally, a top-level array reordered by --sort (see sort_rows)

Usage: scripts/canonicalize.py <name> [--drop field1,field2] [--sort f1,f2]
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


def sort_key(row, fields):
    """A total ordering for one row: the named fields, then the whole row."""
    key = []
    for f in fields:
        v = row.get(f)
        # (rank, value) pairs keep mixed types comparable without stringifying
        # numbers, where "10" would sort ahead of "9".
        if v is None:
            key.append((0, ""))
        elif isinstance(v, bool):
            key.append((1, str(v)))
        elif isinstance(v, (int, float)):
            key.append((2, v))
        else:
            key.append((3, str(v)))
    # Rows that agree on every named field still need a fixed order, or they
    # inherit whatever the source happened to emit -- which is the churn this
    # whole flag exists to remove.
    key.append(json.dumps(row, sort_keys=True, ensure_ascii=False))
    return tuple(key)


def sort_rows(data, fields):
    """Reorder a top-level array of objects by `fields`.

    Top level only. A nested list's order is usually itself the data --
    rankings, trends -- and shuffling it would destroy information. A top-level
    array, by contrast, is a table the source is free to hand back in any order
    it likes, and any reorder upstream would otherwise rewrite the whole file.
    """
    if not isinstance(data, list) or not all(isinstance(x, dict) for x in data):
        raise ValueError("--sort needs a top-level array of objects")
    present = {k for row in data for k in row}
    for f in fields:
        if f not in present:
            # Same reasoning as flatten's unknown column: a typo should be
            # visible rather than quietly sorting by nothing.
            print(f"  warning: sort field {f!r} matched no data", file=sys.stderr)
    return sorted(data, key=lambda row: sort_key(row, fields))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("name")
    # Fields that change every poll without carrying real information.
    # Dropping them is the difference between a meaningful diff and pure churn.
    ap.add_argument("--drop", default="", help="comma-separated fields to strip")
    # A source that returns an array in an order of its own choosing will
    # otherwise diff as "everything changed" the day that order moves.
    ap.add_argument(
        "--sort", default="", help="comma-separated fields to sort a top-level array by"
    )
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

    fields = [f.strip() for f in args.sort.split(",") if f.strip()]
    if fields:
        try:
            data = sort_rows(data, fields)
        except ValueError as e:
            print(f"FAILED {args.name}: {e}", file=sys.stderr)
            return 1

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
