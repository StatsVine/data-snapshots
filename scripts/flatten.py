#!/usr/bin/env python3
"""Flatten data/<name>.json into csv/<name>.csv.

Stability is the whole point: column order and row order must depend only on
the data, never on dict insertion order, or every run rewrites the file.
  - columns: sorted union of all flattened keys (`_key` pinned first)
  - rows: sorted by `_key` for keyed objects; source order kept for arrays,
    where position is usually itself the data (rankings, trends)
  - nested objects flatten to dotted paths; nested arrays become compact
    sorted-key JSON in the cell

Usage: scripts/flatten.py <name>
"""

import argparse
import csv
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


def flatten(obj, prefix=""):
    """dict -> {dotted.key: scalar}. Arrays are encoded, not exploded."""
    out = {}
    for k, v in obj.items():
        key = f"{prefix}{k}"
        if isinstance(v, dict):
            out.update(flatten(v, f"{key}."))
        elif isinstance(v, list):
            out[key] = json.dumps(v, sort_keys=True, separators=(",", ":"))
        elif isinstance(v, bool):
            out[key] = "true" if v else "false"
        elif v is None:
            out[key] = ""
        else:
            out[key] = v
    return out


def rows_for(data):
    """Return (rows, sort_by_key). Handles the three shapes these APIs return."""
    if isinstance(data, list):
        if not all(isinstance(x, dict) for x in data):
            return [{"value": json.dumps(x, sort_keys=True)} for x in data], False
        return [flatten(x) for x in data], False

    if isinstance(data, dict):
        vals = list(data.values())
        if vals and all(isinstance(v, dict) for v in vals):
            rows = []
            for k, v in data.items():
                row = {"_key": str(k)}
                row.update(flatten(v))
                rows.append(row)
            return rows, True
        # A single flat object is one row.
        return [flatten(data)], False

    return [{"value": json.dumps(data)}], False


def parse_where(expr):
    """`a=1,b!=2,c` -> conditions, ANDed. A bare field means "non-empty"."""
    conds = []
    for part in expr.split(","):
        part = part.strip()
        if not part:
            continue
        if "!=" in part:
            field, value = part.split("!=", 1)
            conds.append((field.strip(), "!=", value.strip()))
        elif "=" in part:
            field, value = part.split("=", 1)
            conds.append((field.strip(), "==", value.strip()))
        else:
            conds.append((part, "nonempty", None))
    return conds


def matches(row, conds):
    for field, op, value in conds:
        cell = str(row.get(field, ""))
        if op == "nonempty" and not cell:
            return False
        if op == "==" and cell != value:
            return False
        if op == "!=" and cell == value:
            return False
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("name")
    ap.add_argument(
        "--columns",
        default="",
        help="comma-separated columns to keep, in this order (default: all)",
    )
    ap.add_argument(
        "--where",
        default="",
        help="comma-separated row filters, ANDed: 'active=true,team'",
    )
    args = ap.parse_args()

    src = ROOT / "data" / f"{args.name}.json"
    if not src.exists():
        print(f"FAILED {args.name}: no canonical file at {src}", file=sys.stderr)
        return 1

    rows, keyed = rows_for(json.loads(src.read_text(encoding="utf-8")))
    if not rows:
        print(f"skip {args.name}: no rows", file=sys.stderr)
        return 0

    if keyed:
        rows.sort(key=lambda r: r["_key"])

    total = len(rows)
    if args.where:
        conds = parse_where(args.where)
        rows = [r for r in rows if matches(r, conds)]

    if args.columns:
        # An explicit list fixes the header from config, which is the most
        # stable arrangement there is: a field appearing upstream for the
        # first time can no longer shift every column.
        cols = [c.strip() for c in args.columns.split(",") if c.strip()]
        present = {c for r in rows for c in r}
        for c in cols:
            if c not in present:
                print(f"  warning: column {c!r} matched no data", file=sys.stderr)
    else:
        cols = sorted({c for r in rows for c in r})
        if "_key" in cols:
            cols.remove("_key")
            cols.insert(0, "_key")

    out = ROOT / "csv" / f"{args.name}.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(
            fh,
            fieldnames=cols,
            restval="",
            extrasaction="ignore",
            lineterminator="\n",
        )
        w.writeheader()
        w.writerows(rows)
    kept = f"{len(rows)} of {total} rows" if len(rows) != total else f"{total} rows"
    print(f"flatten {args.name} -> {out.relative_to(ROOT)} ({kept}, {len(cols)} cols)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
