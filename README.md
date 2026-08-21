# data-snapshots

[Git scraping](https://simonwillison.net/2020/Oct/9/git-scraping/) for sports data
sources. A GitHub Action polls each source, canonicalizes the response, and
commits only when something actually changed — so the git history becomes a
free, diffable record of how the data moved over time.

## Layout

```
.github/workflows/
  _fetch.yml               reusable core — download, canonicalize, flatten, commit
  sleeper-players-nfl.yml  one caller per source: its schedule, its parameters
  sleeper-state-nfl.yml
.raw/                      verbatim API responses (gitignored)
data/<name>.json           canonical pretty JSON — committed
csv/<name>.csv             flattened table       — committed
scripts/
  fetch.sh                 one source, end to end — the shared entrypoint
  download.sh              .raw/  <- the network
  canonicalize.py          data/  <- .raw/
  flatten.py               csv/   <- data/
```

Configuration lives in the caller workflows and nowhere else. There is no
manifest file to keep in sync, and each source can poll on its own cadence.

## Run it locally

`scripts/fetch.sh` is the same entrypoint CI uses, so a local run and a CI run
cannot drift:

```bash
scripts/fetch.sh sleeper/state-nfl https://api.sleeper.app/v1/state/nfl
git diff --stat
```

Flags: `--no-csv` to skip the CSV, `--drop f1,f2` to strip volatile fields.

When iterating on the transform steps, work off the copy already in `.raw/`
rather than re-pulling — `sleeper/players-nfl` is a 14MB download and there is
no reason to ask for it twice:

```bash
scripts/canonicalize.py sleeper/players-nfl && scripts/flatten.py sleeper/players-nfl
```

## Adding a source

Copy a caller workflow, change `name`, `url`, and the cron. That is the whole
job — `_fetch.yml` needs no edit.

```yaml
name: sleeper/trending-add

on:
  schedule:
    - cron: "0 */6 * * *"
  workflow_dispatch:

jobs:
  fetch:
    uses: ./.github/workflows/_fetch.yml
    permissions:
      contents: write
    with:
      name: sleeper/trending-add
      url: https://api.sleeper.app/v1/players/nfl/trending/add?lookback_hours=24&limit=25
      csv: true
```

## Why pretty JSON, not minified

Git diffs line-by-line. Minified JSON is a single line, so any change renders
as "the whole file changed" and the history stops being useful. The canonical
form is therefore sorted-key, 2-space-indented, one value per line. If you want
compact output downstream, `jq -c . data/sleeper/players-nfl.json`.

The corollary is that canonicalization must be perfectly deterministic —
otherwise every run commits noise. Hence sorted keys everywhere, sorted CSV
columns, and sorted CSV rows for keyed objects.

## Churn

Volatile fields (counters, timestamps, ranks that jitter every poll) produce
diffs that carry no information and bloat the history. Strip them with the
`drop` input on the caller workflow.

`sleeper/players-nfl` is ~19MB pretty (~2.2MB compressed). That is fine as a
baseline, but keep an eye on whether daily deltas stay small.

Observed so far: `news_updated` is a per-player millisecond timestamp that
moves on its own. Two consecutive pulls a few minutes apart already produced a
2-line diff from it alone. It is not pure noise — it does mark that news
landed — so it is left in for now. If daily diffs turn out to be mostly
`news_updated` churn on players whose other fields never move, it is the first
candidate for `drop`.

## Development

```bash
pip install -r requirements-dev.txt
black scripts tests && ruff check scripts tests && shellcheck scripts/*.sh && pytest -q
```

`ci.yml` runs exactly those, plus `actionlint` over the workflows and a check
that every script kept its executable bit — a lost `+x` would otherwise surface
as a failed scheduled run rather than a failed PR.

The tests are offline by construction. They drive the real CLIs as
subprocesses against fixtures in a temp tree, via the `DATA_SNAPSHOTS_ROOT`
environment variable, so neither `pytest` nor CI ever calls a data source.

What they mostly assert is determinism — identical bytes out for the same data
in, whatever order it arrived in. That is the property the whole repo rests on:
if canonicalize or flatten leak dict insertion order anywhere, every scheduled
run commits noise and the history stops being worth having.

## Python version

The project targets **3.14** — CI pins it, and both black and ruff target
`py314`.

The scripts use `#!/usr/bin/env python3`, so locally they run under whatever
`python3` resolves to. If your default is older, either run them through 3.14
explicitly or put it first on `PATH`:

```bash
python3.14 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q
```

Nothing here uses version-specific syntax — only `json`, `csv`, `argparse`,
`os`, `pathlib`, `sys` — so an older interpreter will still run it. But ruff's
`UP` rules are free to suggest 3.14-only idioms, so a bump here is a commitment
to actually running 3.14 rather than a label.
