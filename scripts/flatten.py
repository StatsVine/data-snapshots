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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("name")
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

    cols = sorted({c for r in rows for c in r})
    if "_key" in cols:
        cols.remove("_key")
        cols.insert(0, "_key")

    out = ROOT / "csv" / f"{args.name}.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, restval="", lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    print(
        f"flatten {args.name} -> {out.relative_to(ROOT)} "
        f"({len(rows)} rows, {len(cols)} cols)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
