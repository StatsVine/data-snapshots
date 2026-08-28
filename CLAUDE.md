# CLAUDE.md

Guidance for Claude Code working in this repo. `README.md` is the full
reference — this file is the short version plus the things that are easy to
break.

## What this is

Polls sports-data API endpoints on a schedule, canonicalizes each response to
deterministic pretty JSON in `data/`, flattens narrow CSV views into `csv/`,
and commits only when the bytes changed. The git history is the product: a
diffable record of the data over time.

## Pipeline

- `scripts/download.sh` → `.raw/<name>.json` — gitignored; refuses non-JSON so
  a bad response never clobbers good raw data. `--header <name>` sends an auth
  header whose value it reads from `$SOURCE_API_KEY`.
- `scripts/canonicalize.py` → `data/<name>.json` — `--root` hoists a subtree
  out of its envelope, `--keep`/`--drop` narrow record fields (allowlist and
  denylist; mutually exclusive), `--sort` reorders a top-level array, in that
  order. Sorted keys, 2-space indent, trailing newline; rejects
  `NaN`/`Infinity`.
- `scripts/flatten.py` → `csv/<name>[-<view>].csv` — `--columns` allowlist,
  `--where` ANDed row filters, `--views` JSON for several named views. Prunes
  CSV files for views that no longer exist.
- `scripts/fetch.sh` runs all three and is the same entrypoint CI uses. Put
  pipeline logic in these scripts, never in the workflow YAML — that is what
  keeps a local run and a CI run from drifting.

## Commands

```bash
scripts/fetch.sh <name> <url> [--no-csv] [--drop f1,f2] [--sort f1,f2] \
                              [--columns c1,c2] [--where 'active=true,team'] \
                              [--views '[{"name":"all"}]']

black --check --diff scripts tests
ruff check scripts tests
shellcheck scripts/*.sh
pytest -q          # offline; fixtures build a temp tree
```

CI additionally runs `actionlint` on the workflows and fails if anything in
`scripts/` has lost its executable bit.

`canonicalize.py` and `flatten.py` can be re-run alone against cached `.raw/`
data, but pass the same flags the source's workflow passes — otherwise you
write a `data/`/`csv/` diff the next scheduled run will just undo.

## Adding a source

Add a caller workflow in `.github/workflows/` that calls `_fetch.yml`. No
script edits and no `_fetch.yml` edits are needed. Inputs: `name`, `url`,
`csv`, `root`, `keep`, `drop`, `sort`, `columns`, `where`, `views`, `header` —
`views` takes
precedence over `columns`/`where`. `.github/workflows/sleeper-players-nfl.yml`
is the fullest example. `chmod +x` any new script.

Prefer `keep` over `drop` when the field set you want is fixed — a denylist
admits whatever the source adds next.

A source behind a key sets `header: x-api-key` and passes the value as the
`api_key` secret. Respect its rate limit when picking the cron, and remember
`--retry 3` spends against that budget.

## Invariants

- **Determinism.** The same input bytes must produce identical output bytes
  regardless of dict insertion order. Anything nondeterministic turns into a
  phantom diff on every scheduled commit.
- **Only `data/` and `csv/` get committed.** A fetch that changes nothing must
  exit 0 without committing.
- **Snapshot commits are machine-written** as `<source>: <ISO8601 UTC>`. Never
  hand-write a commit in that shape; code changes get a normal descriptive
  message.
- **`.raw/` is gitignored working state** and is never committed.
- **Keep CSV views narrow.** A full flatten of `players-nfl` is 12,221 rows ×
  101 columns, which GitHub renders badly and which buries the fields anyone
  actually reads.
- **`--sort` applies only to a top-level array** and fails loudly otherwise.
  A source that nests its table under a key needs `--root` first.
- **A key is never an argument.** Only the header *name* travels through argv;
  the value goes through `$SOURCE_API_KEY` into a curl stdin config, so it
  stays out of `ps` and shell traces. Keep it that way.

## Style

Python 3.14 is the `black`/`ruff` target (line length 88, rules E, F, I, B,
UP, SIM), though the scripts use `#!/usr/bin/env python3` and avoid
version-specific syntax. Stdlib only — `json`, `csv`, `argparse`, `os`,
`pathlib`, `sys`. No runtime dependencies.
