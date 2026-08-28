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
  sleeper-state-nfl.yml    (07:15, 07:45 and 08:15 UTC daily, offset from
  nfc-players.yml          each other)
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

Flags: `--no-csv` to skip the CSV, `--root key` to hoist a table out of its
envelope (see [Envelopes](#envelopes)), `--keep f1,f2` to publish only an
allowlist of fields (see [Allowlists](#allowlists)), `--drop f1,f2` to strip
volatile fields,
`--sort f1,f2` to pin the order of a top-level array (see [Order](#order)),
`--header x-api-key` for a source behind a key (see
[Authenticated sources](#authenticated-sources)), plus the two CSV knobs below.

## Keeping the CSV small

The JSON is the complete record. The CSV is a **view** of it — deliberately
narrow, because a full flatten of `players-nfl` is 12,221 rows x 101 columns
(4.3MB), which GitHub renders badly and which buries the dozen fields anyone
actually reads.

Two knobs, both set per source in the caller workflow:

- **`columns`** — an ordered allowlist. Cherry-pick the fields worth having.
- **`where`** — comma-separated row filters, ANDed. `field` keeps non-empty
  values, `field=value` and `field!=value` compare.

```yaml
columns: >-
  _key,full_name,team,position,status,injury_status,age,years_exp,
  number,college,espn_id,gsis_id,sportradar_id
where: active=true,team
```

That takes `players-nfl` from **4.3MB to 298KB**. Note that `active` is doing
almost none of that work — Sleeper flags 9,414 of 12,221 players active. A
non-empty `team` is what actually means "on a roster", and cuts to 3,221.

### Multiple views

One source can produce several CSVs from a single download. Set `views` to a
JSON array instead of the `columns`/`where` pair — workflow_call inputs are
strings, so JSON is how a list gets through:

```yaml
views: |
  [
    {"name": "all"},
    {"name": "rostered",
     "columns": "_key,full_name,team,position,...",
     "where": "active=true,team"}
  ]
```

`players-nfl` ships two, chosen so neither contains the other:

- **`-ids`** — every player ever, narrow. A crosswalk table: you look up
  retired players by `espn_id`, so it keeps all rows. Columns are curated
  rather than "every field ending in `_id`" — `player_id` duplicates `_key`
  exactly, `opta_id` and `pandascore_id` arealways empty, and the `metadata.*`
  ones are Sleeper internals.
- **`-rostered`** — the current squad in full detail, `where: team`. A
  non-empty team is what means "on a roster"; `active` alone passes 77% of
  the file and barely filters.

Overlapping views are worth avoiding beyond their size: this repo's history
is the product, and a player changing team should show up as one diff, not
the same diff repeated across five files.

Each view lands at `csv/<source>-<view>.csv`. The source is downloaded and
parsed once and the rows are shared, so views cost nothing upstream. Dropping
or renaming a view prunes its old file, so a config change cannot strand an
orphan CSV in the repo.

`views` takes precedence over `columns`/`where`. Use the simple pair for
single-view sources; reach for `views` when you want more than one.

An explicit `columns` list also makes the header *more* stable than the
default: it is fixed by config, so a field appearing upstream for the first
time can no longer shift every column and blow up the diff. Where the header
is derived instead, it comes from every row in the source rather than only
the rows a filter kept — otherwise a filtered subset that happens not to use
a field would drop that column, and the header would move whenever the data
did. Naming a column
that matches no data warns on stderr and emits it empty, rather than silently
dropping it — a typo should be visible, not invisible.

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

### Authenticated sources

A source that needs a key takes two more pieces: `header`, the name of the
request header, and an `api_key` secret carrying its value.

```yaml
jobs:
  fetch:
    uses: ./.github/workflows/_fetch.yml
    permissions:
      contents: write
    with:
      name: example/players
      url: https://api.example.com/v2/players
      header: x-api-key
    secrets:
      api_key: ${{ secrets.EXAMPLE_API_KEY }}
```

Store the key as a repository secret (Settings → Secrets and variables →
Actions). Only the header's *name* is ever written down here; the value moves
through the secret, which Actions masks in logs.

The key never reaches a command line. `fetch.sh --header x-api-key` passes the
name along, and `download.sh` reads the value from `SOURCE_API_KEY` and hands
it to curl through a config on stdin (`curl -K -`) — so it stays out of argv,
and out of `ps` output and any shell trace. `download.sh` also refuses to make
the request at all when a header is named but the variable is empty, rather
than fetching a 401 body and canonicalizing it over good data.

To run one locally, put the key in the environment rather than in the command:

```bash
read -rs SOURCE_API_KEY && export SOURCE_API_KEY
scripts/fetch.sh example/players https://api.example.com/v2/players \
  --header x-api-key
```

If the API wants `Authorization: Bearer <key>`, set `header: Authorization`
and store the secret with the `Bearer ` prefix already in it — `--header`
takes a name, not a template.

Rate limits are the other constraint an authenticated source usually brings.
Pick a cron that respects them, and note that curl's `--retry 3` counts
against the budget: a 429 is retried like any other transient error.

### A worked example: nfc/players

`nfc/players` is the autocomplete index behind the player search at
`nfc.shgn.com` — the National Fantasy Championship (NFBC/NFFC/NFBKC), *not*
the football conference. It is the only public place their player ids appear,
which makes it a crosswalk worth keeping.

The endpoint ignores query parameters: every request returns the whole dump —
8,965 rows, 604KB minified, 868KB pretty — and the browser filters it. Each
row has exactly four fields: `id`, `team`, `value` (the display name), and
`sport`, covering 3,398 football players, 3,591 baseball and 1,976 basketball.

Two things about that shape drive the config:

- **`id` is unique only within a sport.** 217 ids are reused across the three,
  so the key is `(sport, id)` — which is what `sort: sport,id` pins the file
  to.
- **The three sports partition cleanly**, so it ships a view each, with
  `where: sport=football` and columns `id,value,team` — `sport` is dropped
  there because it is constant within those files. An NFL transaction is then
  never picked out of a day of MLB ones.

It also ships a fourth view, `-all`, holding the whole crosswalk with the
`sport` column kept. That one knowingly breaks the no-overlap rule above:
every row lives in both `-all` and its sport's file, so a player changing team
diffs twice. The trade is deliberate — one lookup table is what you want when
you do not know the sport up front, the slices are what you want when you do —
and it costs nothing upstream, since all four views are projections of a
single parse of a single download.

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

`sleeper/players-nfl` drops two, and the first daily commit is why: of its
1,346 changed JSON lines, 1,122 were `search_rank` and 150 `news_updated` —
94% of the diff, on players whose real fields never moved.

- **`search_rank`** is a *global* popularity rank, which makes it the worse of
  the two. One player's news nudges the rank of everyone below them, so a
  single upstream event rewrites hundreds of unrelated lines.
- **`news_updated`** is a per-player millisecond timestamp that moves on its
  own — two pulls a few minutes apart already produced a 2-line diff from it
  alone.

Dropping them costs less than it looks: `status` and `injury_status` still
carry the news that matters, and they change when the *player* does, which is
the thing this history is for.

The file is still ~18MB pretty (~2.1MB compressed). That is fine as a baseline;
what matters is that the daily deltas stay small.

### Order

Churn also comes from *position*. A source that returns a top-level array picks
that array's order, and it owes you no stability: `nfc/players` arrives sorted
by last name, which is a detail of the search box it feeds. One `ORDER BY`
change upstream and every line moves — the diff reads "the whole file changed"
while the data sat still.

`--sort f1,f2` (`sort:` on the caller workflow) reorders a top-level array by
the named fields, then by the full canonical row as a final tiebreaker. The
order becomes a function of the data alone, with nothing left inheriting the
source's whims. Sorting by an ascending id has a second benefit: new rows land
at the end rather than scattered through the middle, so an insertion reads as
an insertion.

```yaml
sort: sport,id
```

Top level only. A nested list's order is usually itself the data — rankings,
trends — and shuffling it would destroy information. `--sort` against anything
but an array of objects fails loudly, and a field matching no data warns on
stderr rather than quietly sorting by nothing.

### Allowlists

`drop` is a denylist: name the fields you do not want and keep everything
else. That suits churn, where the noisy fields are known and a field appearing
upstream for the first time is probably worth having.

It is the wrong shape when the set of fields you want is fixed, because it
fails open — whatever the source adds next is published automatically, and
nothing warns you. `keep` is the allowlist counterpart, so a new upstream
field defaults to excluded:

```yaml
keep: player_id,player_name,first_name,last_name,position_id,team_id,
      sportsdata_player_id,cbs_id,espn_id,mfl_id,nfl_id,yahoo_id
```

It narrows records, matching what `columns` treats as a record: an array of
objects, an object whose values are objects (the outer keys are identity and
are never filtered), or a single flat object. Nested structure under a kept
field is kept whole — it is not a dotted path. A named field matching no data
warns on stderr, which matters more here than elsewhere: a typo in a denylist
keeps one field too many, a typo in an allowlist keeps one too few.

`keep` and `drop` are mutually exclusive and passing both fails loudly. Use
`keep` when you know the fields you want, `drop` when you know the fields you
do not.

### Envelopes

Some APIs wrap the table in an envelope and put the rows one level down:

```json
{"sport": "NFL", "count": 8022, "season": "2026", "week": "0",
 "players": [ ... ]}
```

That costs twice. The envelope's own fields move without the rows moving — a
`count` ticks, a `week` rolls — so every poll diffs even on a quiet day. And
the table underneath is now a nested list, which `--sort` will not touch, so
nothing pins its order either.

`--root players` (`root:` on the caller workflow) hoists that subtree and
discards what surrounded it. The file ends up the same shape as a source that
returned a bare array, so `--sort` applies again:

```yaml
root: players
sort: player_id
```

It takes a dotted path (`root: data.players`) and runs before `drop` and
`sort`, so both act on the hoisted rows. A path that is not in the response
fails loudly rather than silently canonicalizing the whole envelope, as does
one pointing at a scalar.

The envelope is not automatically noise — a `count` is a real cross-check
against the number of rows you got, and losing it means losing that check.
Hoist when the rows are what the snapshot is for, not by reflex.

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
