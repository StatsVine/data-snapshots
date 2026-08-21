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
import re
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


VIEW_NAME = re.compile(r"^[A-Za-z0-9_-]+$")


def write_view(source, rows, view):
    """Write one CSV view. `rows` is shared across views and never mutated."""
    total = len(rows)
    if view.get("where"):
        conds = parse_where(view["where"])
        rows = [r for r in rows if matches(r, conds)]

    if view.get("columns"):
        # An explicit list fixes the header from config, which is the most
        # stable arrangement there is: a field appearing upstream for the
        # first time can no longer shift every column.
        cols = [c.strip() for c in view["columns"].split(",") if c.strip()]
        present = {c for r in rows for c in r}
        for c in cols:
            if c not in present:
                print(f"  warning: column {c!r} matched no data", file=sys.stderr)
    else:
        cols = sorted({c for r in rows for c in r})
        if "_key" in cols:
            cols.remove("_key")
            cols.insert(0, "_key")

    stem = source if not view.get("name") else f"{source}-{view['name']}"
    out = ROOT / "csv" / f"{stem}.csv"
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
    print(f"flatten {source} -> {out.relative_to(ROOT)} ({kept}, {len(cols)} cols)")
    return out


def prune(source, written):
    """Drop CSVs this source used to produce but no longer does.

    Renaming or removing a view would otherwise strand its old file in the
    repo forever. Scoped to this source's own outputs -- which does assume no
    source name is a prefix of another with a view-shaped suffix.
    """
    base = ROOT / "csv" / f"{source}.csv"
    for old in [base, *base.parent.glob(f"{base.stem}-*.csv")]:
        if old.exists() and old not in written:
            old.unlink()
            print(f"  pruned stale {old.relative_to(ROOT)}")


def parse_views(raw):
    """Views arrive as a JSON array, since workflow_call inputs are strings."""
    views = json.loads(raw)
    if not isinstance(views, list) or not views:
        raise ValueError("views must be a non-empty JSON array")
    seen = set()
    for v in views:
        if not isinstance(v, dict):
            raise ValueError(f"view must be an object, got {v!r}")
        name = v.get("name", "")
        if not VIEW_NAME.match(str(name)):
            # Names become filenames, so anything path-ish is refused outright.
            raise ValueError(f"view name {name!r} must match [A-Za-z0-9_-]+")
        if name in seen:
            raise ValueError(f"duplicate view name {name!r}")
        seen.add(name)
    return views


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
    ap.add_argument(
        "--views",
        default="",
        help='JSON array of views: \'[{"name":"all"},{"name":"x","where":"team"}]\'',
    )
    args = ap.parse_args()

    src = ROOT / "data" / f"{args.name}.json"
    if not src.exists():
        print(f"FAILED {args.name}: no canonical file at {src}", file=sys.stderr)
        return 1

    if args.views:
        try:
            views = parse_views(args.views)
        except ValueError as e:
            print(f"FAILED {args.name}: {e}", file=sys.stderr)
            return 1
    else:
        views = [{"columns": args.columns, "where": args.where}]

    rows, keyed = rows_for(json.loads(src.read_text(encoding="utf-8")))
    if not rows:
        print(f"skip {args.name}: no rows", file=sys.stderr)
        return 0

    if keyed:
        rows.sort(key=lambda r: r["_key"])

    written = {write_view(args.name, rows, view) for view in views}
    prune(args.name, written)
    return 0


if __name__ == "__main__":
    sys.exit(main())
