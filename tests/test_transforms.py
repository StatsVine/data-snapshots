"""Offline tests for the transform steps.

These never touch the network. The property that matters most is determinism:
if canonicalize or flatten depend on dict insertion order anywhere, every
scheduled run commits noise instead of signal, and the history stops being
worth keeping. Most of what follows is checking exactly that.
"""

import json
import pathlib
import subprocess

import pytest

SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / "scripts"


def run(script, root, *args, expect_ok=True):
    proc = subprocess.run(
        [str(SCRIPTS / script), *args],
        env={"DATA_SNAPSHOTS_ROOT": str(root), "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
    )
    if expect_ok:
        assert proc.returncode == 0, proc.stderr
    return proc


@pytest.fixture
def root(tmp_path):
    return tmp_path


def seed(root, name, payload):
    """Write a raw payload as literal text so key order is under test control."""
    raw = root / ".raw" / f"{name}.json"
    raw.parent.mkdir(parents=True, exist_ok=True)
    raw.write_text(payload if isinstance(payload, str) else json.dumps(payload))
    return raw


def canon(root, name):
    return (root / "data" / f"{name}.json").read_text(encoding="utf-8")


def csv_of(root, name):
    return (root / "csv" / f"{name}.csv").read_text(encoding="utf-8")


# --- canonicalize ---------------------------------------------------------


def test_keys_are_sorted_regardless_of_input_order(root):
    seed(root, "s", '{"zebra": 1, "alpha": 2, "middle": {"z": 1, "a": 2}}')
    run("canonicalize.py", root, "s")
    assert canon(root, "s") == (
        '{\n  "alpha": 2,\n  "middle": {\n'
        '    "a": 2,\n    "z": 1\n  },\n  "zebra": 1\n}\n'
    )


def test_same_data_different_order_produces_identical_bytes(root):
    seed(root, "a", '{"x": 1, "y": [{"b": 2, "a": 1}]}')
    seed(root, "b", '{"y": [{"a": 1, "b": 2}], "x": 1}')
    run("canonicalize.py", root, "a")
    run("canonicalize.py", root, "b")
    assert canon(root, "a") == canon(root, "b")


def test_rerunning_changes_nothing(root):
    seed(root, "s", {"b": 1, "a": [1, 2, 3]})
    run("canonicalize.py", root, "s")
    first = canon(root, "s")
    run("canonicalize.py", root, "s")
    assert canon(root, "s") == first


def test_output_is_pretty_not_minified(root):
    """Minified JSON is one line, so git would diff it as all-or-nothing."""
    seed(root, "s", {"a": 1, "b": 2})
    run("canonicalize.py", root, "s")
    assert canon(root, "s").count("\n") > 2


def test_ends_with_exactly_one_newline(root):
    seed(root, "s", {"a": 1})
    run("canonicalize.py", root, "s")
    assert canon(root, "s").endswith("}\n")


def test_non_ascii_stays_literal(root):
    seed(root, "s", {"name": "Ké'Shawn Ünïcode"})
    run("canonicalize.py", root, "s")
    assert "Ké'Shawn Ünïcode" in canon(root, "s")
    assert "\\u" not in canon(root, "s")


def test_nan_is_rejected(root):
    seed(root, "s", '{"x": NaN}')
    proc = run("canonicalize.py", root, "s", expect_ok=False)
    assert proc.returncode == 1
    assert not (root / "data" / "s.json").exists()


def test_missing_raw_file_fails_loudly(root):
    proc = run("canonicalize.py", root, "nope", expect_ok=False)
    assert proc.returncode == 1


def test_drop_strips_field_at_every_depth(root):
    seed(root, "s", {"keep": 1, "junk": 2, "nested": {"junk": 3, "keep": 4}})
    run("canonicalize.py", root, "s", "--drop", "junk")
    out = json.loads(canon(root, "s"))
    assert out == {"keep": 1, "nested": {"keep": 4}}


def test_drop_accepts_multiple_and_tolerates_spaces(root):
    seed(root, "s", {"a": 1, "b": 2, "c": 3})
    run("canonicalize.py", root, "s", "--drop", "a, b")
    assert json.loads(canon(root, "s")) == {"c": 3}


def test_empty_drop_removes_nothing(root):
    seed(root, "s", {"a": 1})
    run("canonicalize.py", root, "s", "--drop", "")
    assert json.loads(canon(root, "s")) == {"a": 1}


# --- --sort on canonicalize -------------------------------------------------


def test_sorts_a_top_level_array_by_a_single_field(root):
    seed(root, "s", [{"n": "third"}, {"n": "first"}, {"n": "second"}])
    run("canonicalize.py", root, "s", "--sort", "n")
    assert json.loads(canon(root, "s")) == [
        {"n": "first"},
        {"n": "second"},
        {"n": "third"},
    ]


def test_sorted_output_is_byte_identical_regardless_of_input_order(root):
    """The whole reason the flag exists: upstream reorders must not diff."""
    seed(root, "a", [{"x": 2}, {"x": 1}])
    seed(root, "b", [{"x": 1}, {"x": 2}])
    run("canonicalize.py", root, "a", "--sort", "x")
    run("canonicalize.py", root, "b", "--sort", "x")
    assert canon(root, "a") == canon(root, "b")


def test_sorts_by_multiple_fields_in_order(root):
    seed(root, "s", [{"a": 1, "b": 2}, {"a": 1, "b": 1}, {"a": 2, "b": 1}])
    run("canonicalize.py", root, "s", "--sort", "a,b")
    assert json.loads(canon(root, "s")) == [
        {"a": 1, "b": 1},
        {"a": 1, "b": 2},
        {"a": 2, "b": 1},
    ]


def test_numbers_sort_numerically_not_lexically(root):
    seed(root, "s", [{"id": 10}, {"id": 2}])
    run("canonicalize.py", root, "s", "--sort", "id")
    assert json.loads(canon(root, "s")) == [{"id": 2}, {"id": 10}]


def test_tied_rows_get_fixed_order_via_tiebreaker(root):
    seed(root, "a", [{"x": 1, "v": 2}, {"x": 1, "v": 1}])
    seed(root, "b", [{"x": 1, "v": 1}, {"x": 1, "v": 2}])
    run("canonicalize.py", root, "a", "--sort", "x")
    run("canonicalize.py", root, "b", "--sort", "x")
    assert canon(root, "a") == canon(root, "b")


def test_a_nested_list_keeps_its_own_order(root):
    """Only the top level is reordered -- a nested list's order is the data."""
    seed(root, "s", [{"id": 2, "tags": [3, 1, 2]}, {"id": 1, "tags": [9, 8]}])
    run("canonicalize.py", root, "s", "--sort", "id")
    assert json.loads(canon(root, "s")) == [
        {"id": 1, "tags": [9, 8]},
        {"id": 2, "tags": [3, 1, 2]},
    ]


def test_mixed_types_in_one_sort_field_stay_comparable(root):
    """Real sources are consistent; a total order must not depend on that."""
    seed(root, "s", '[{"x": "a"}, {"x": null}, {"x": 2}, {"x": true}]')
    run("canonicalize.py", root, "s", "--sort", "x")
    assert [r["x"] for r in json.loads(canon(root, "s"))] == [None, True, 2, "a"]


def test_unknown_sort_field_warns_but_succeeds(root):
    seed(root, "s", [{"x": 1}])
    proc = run("canonicalize.py", root, "s", "--sort", "missing")
    assert "matched no data" in proc.stderr


def test_sort_against_top_level_object_fails(root):
    seed(root, "s", {"a": 1, "b": 2})
    proc = run("canonicalize.py", root, "s", "--sort", "a", expect_ok=False)
    assert proc.returncode == 1
    assert "FAILED" in proc.stderr
    assert not (root / "data" / "s.json").exists()


def test_empty_sort_leaves_order_untouched(root):
    seed(root, "s", [{"n": "b"}, {"n": "a"}])
    run("canonicalize.py", root, "s", "--sort", "")
    assert canon(root, "s") == (
        '[\n  {\n    "n": "b"\n  },\n  {\n    "n": "a"\n  }\n]\n'
    )


# --- flatten --------------------------------------------------------------


def keyed(root, name, data):
    seed(root, name, data)
    run("canonicalize.py", root, name)
    run("flatten.py", root, name)


def test_keyed_object_becomes_rows_with_key_column_first(root):
    keyed(root, "s", {"p2": {"pos": "RB"}, "p1": {"pos": "QB"}})
    assert csv_of(root, "s") == "_key,pos\np1,QB\np2,RB\n"


def test_rows_are_sorted_by_key_not_input_order(root):
    keyed(root, "s", {"zz": {"n": 1}, "aa": {"n": 2}, "mm": {"n": 3}})
    assert [ln.split(",")[0] for ln in csv_of(root, "s").splitlines()[1:]] == [
        "aa",
        "mm",
        "zz",
    ]


def test_columns_are_stable_when_a_later_row_adds_a_field(root):
    """Union of keys, sorted — not just whatever the first row happened to have."""
    keyed(root, "s", {"a": {"x": 1}, "b": {"x": 2, "y": 3}})
    assert csv_of(root, "s").splitlines()[0] == "_key,x,y"
    assert csv_of(root, "s").splitlines()[1] == "a,1,"


def test_nested_objects_flatten_to_dotted_paths(root):
    keyed(root, "s", {"p1": {"meta": {"team": "SF"}}})
    assert csv_of(root, "s") == "_key,meta.team\np1,SF\n"


def test_nested_arrays_become_compact_json_with_sorted_keys(root):
    keyed(root, "s", {"p1": {"tags": [{"b": 2, "a": 1}]}})
    assert '"[{""a"":1,""b"":2}]"' in csv_of(root, "s")


def test_array_input_keeps_source_order(root):
    """Rank order in a trending feed is itself the data."""
    keyed(root, "s", [{"n": "third"}, {"n": "first"}, {"n": "second"}])
    assert csv_of(root, "s") == "n\nthird\nfirst\nsecond\n"


def test_flat_object_becomes_a_single_row(root):
    keyed(root, "s", {"week": 2, "season": "2026"})
    assert csv_of(root, "s") == "season,week\n2026,2\n"


def test_booleans_and_nulls_render_predictably(root):
    keyed(root, "s", {"p1": {"active": True, "hurt": False, "note": None}})
    assert csv_of(root, "s") == "_key,active,hurt,note\np1,true,false,\n"


def test_rerunning_flatten_changes_nothing(root):
    keyed(root, "s", {"b": {"x": 1}, "a": {"y": 2}})
    first = csv_of(root, "s")
    run("flatten.py", root, "s")
    assert csv_of(root, "s") == first


def test_flatten_uses_unix_line_endings(root):
    keyed(root, "s", {"p1": {"x": 1}})
    assert "\r\n" not in csv_of(root, "s")


# --- projection and filtering ---------------------------------------------


def test_columns_keeps_only_listed_fields_in_the_given_order(root):
    keyed(root, "s", {"p1": {"a": 1, "b": 2, "c": 3}})
    run("flatten.py", root, "s", "--columns", "_key,c,a")
    assert csv_of(root, "s") == "_key,c,a\np1,3,1\n"


def test_columns_tolerates_whitespace_from_folded_yaml(root):
    """A YAML `>-` block joins lines with spaces, so ' b' must still match."""
    keyed(root, "s", {"p1": {"a": 1, "b": 2}})
    run("flatten.py", root, "s", "--columns", "_key, a, b")
    assert csv_of(root, "s") == "_key,a,b\np1,1,2\n"


def test_unknown_column_warns_but_still_emits_a_stable_header(root):
    keyed(root, "s", {"p1": {"a": 1}})
    proc = run("flatten.py", root, "s", "--columns", "_key,a,typo")
    assert "typo" in proc.stderr
    assert csv_of(root, "s") == "_key,a,typo\np1,1,\n"


def test_where_bare_field_keeps_only_non_empty(root):
    keyed(root, "s", {"p1": {"team": "SF"}, "p2": {"team": None}})
    run("flatten.py", root, "s", "--where", "team")
    assert csv_of(root, "s") == "_key,team\np1,SF\n"


def test_where_equality(root):
    keyed(root, "s", {"p1": {"active": True}, "p2": {"active": False}})
    run("flatten.py", root, "s", "--where", "active=true")
    assert csv_of(root, "s") == "_key,active\np1,true\n"


def test_where_inequality(root):
    keyed(root, "s", {"p1": {"pos": "QB"}, "p2": {"pos": "RB"}})
    run("flatten.py", root, "s", "--where", "pos!=RB")
    assert csv_of(root, "s") == "_key,pos\np1,QB\n"


def test_where_conditions_are_anded(root):
    keyed(
        root,
        "s",
        {
            "keep": {"active": True, "team": "SF"},
            "no_team": {"active": True, "team": None},
            "inactive": {"active": False, "team": "GB"},
        },
    )
    run("flatten.py", root, "s", "--where", "active=true,team")
    assert [ln.split(",")[0] for ln in csv_of(root, "s").splitlines()[1:]] == ["keep"]


def test_filter_and_projection_compose(root):
    keyed(root, "s", {"a": {"t": "SF", "junk": 9}, "b": {"t": None, "junk": 8}})
    run("flatten.py", root, "s", "--columns", "_key,t", "--where", "t")
    assert csv_of(root, "s") == "_key,t\na,SF\n"


def test_filtering_everything_out_still_writes_the_header(root):
    """An empty result must not silently leave yesterday's file in place."""
    keyed(root, "s", {"p1": {"team": None}})
    run("flatten.py", root, "s", "--columns", "_key,team", "--where", "team")
    assert csv_of(root, "s") == "_key,team\n"


def test_filtered_output_is_still_deterministic(root):
    keyed(root, "s", {"z": {"t": "SF"}, "a": {"t": "GB"}, "m": {"t": None}})
    run("flatten.py", root, "s", "--columns", "_key,t", "--where", "t")
    first = csv_of(root, "s")
    run("flatten.py", root, "s", "--columns", "_key,t", "--where", "t")
    assert csv_of(root, "s") == first


# --- multiple views -------------------------------------------------------


def prep(root, name, data):
    seed(root, name, data)
    run("canonicalize.py", root, name)


def test_views_write_one_suffixed_file_each(root):
    prep(root, "s", {"a": {"t": "SF"}, "b": {"t": None}})
    run(
        "flatten.py",
        root,
        "s",
        "--views",
        '[{"name":"all"},{"name":"rostered","where":"t"}]',
    )
    assert (root / "csv" / "s-all.csv").read_text() == "_key,t\na,SF\nb,\n"
    assert (root / "csv" / "s-rostered.csv").read_text() == "_key,t\na,SF\n"


def test_views_read_the_source_once_and_stay_independent(root):
    """Filtering one view must not narrow the rows the next view sees."""
    prep(root, "s", {"a": {"t": "SF"}, "b": {"t": None}})
    run(
        "flatten.py",
        root,
        "s",
        "--views",
        '[{"name":"narrow","where":"t"},{"name":"wide"}]',
    )
    assert len((root / "csv" / "s-wide.csv").read_text().splitlines()) == 3


def test_stale_view_is_pruned_when_config_changes(root):
    prep(root, "s", {"a": {"t": "SF"}})
    run("flatten.py", root, "s", "--views", '[{"name":"old"}]')
    assert (root / "csv" / "s-old.csv").exists()
    run("flatten.py", root, "s", "--views", '[{"name":"new"}]')
    assert not (root / "csv" / "s-old.csv").exists()
    assert (root / "csv" / "s-new.csv").exists()


def test_switching_to_views_prunes_the_unsuffixed_file(root):
    prep(root, "s", {"a": {"t": "SF"}})
    run("flatten.py", root, "s")
    assert (root / "csv" / "s.csv").exists()
    run("flatten.py", root, "s", "--views", '[{"name":"all"}]')
    assert not (root / "csv" / "s.csv").exists()


def test_prune_leaves_other_sources_alone(root):
    prep(root, "other", {"a": {"t": "GB"}})
    run("flatten.py", root, "other")
    prep(root, "s", {"a": {"t": "SF"}})
    run("flatten.py", root, "s", "--views", '[{"name":"all"}]')
    assert (root / "csv" / "other.csv").exists()


def test_view_name_that_looks_like_a_path_is_refused(root):
    prep(root, "s", {"a": {"t": "SF"}})
    proc = run(
        "flatten.py", root, "s", "--views", '[{"name":"../escape"}]', expect_ok=False
    )
    assert proc.returncode == 1
    assert "must match" in proc.stderr


def test_duplicate_view_names_are_refused(root):
    prep(root, "s", {"a": {"t": "SF"}})
    proc = run(
        "flatten.py",
        root,
        "s",
        "--views",
        '[{"name":"x"},{"name":"x"}]',
        expect_ok=False,
    )
    assert proc.returncode == 1
    assert "duplicate" in proc.stderr


def test_malformed_views_json_fails_loudly(root):
    prep(root, "s", {"a": {"t": "SF"}})
    proc = run("flatten.py", root, "s", "--views", "{not json", expect_ok=False)
    assert proc.returncode == 1


def test_views_must_be_a_non_empty_array(root):
    prep(root, "s", {"a": {"t": "SF"}})
    proc = run("flatten.py", root, "s", "--views", "[]", expect_ok=False)
    assert proc.returncode == 1
    assert "non-empty JSON array" in proc.stderr


def test_header_does_not_narrow_when_rows_are_filtered(root):
    """A filtered-out row still defines the schema; otherwise the header
    moves whenever the data does."""
    prep(root, "s", {"a": {"t": "SF"}, "b": {"t": None, "rare": 1}})
    run("flatten.py", root, "s", "--where", "t")
    assert csv_of(root, "s") == "_key,rare,t\na,,SF\n"


# --- download auth --------------------------------------------------------
#
# Only the pre-flight checks are testable offline: everything past them makes
# a request. That is enough to cover the case that actually costs us, which is
# a scheduled run quietly fetching an unauthenticated error body and canonical-
# izing it over good data.


def test_header_without_a_key_fails_before_making_a_request(root):
    proc = run(
        "download.sh",
        root,
        "s",
        "https://example.invalid/x",
        "--header",
        "x-api-key",
        expect_ok=False,
    )
    assert proc.returncode == 1
    assert "SOURCE_API_KEY is empty" in proc.stderr
    assert not (root / ".raw" / "s.json").exists()


def test_the_key_is_never_echoed_by_the_failure_path(root):
    proc = run(
        "download.sh",
        root,
        "s",
        "https://example.invalid/x",
        "--header",
        "x-api-key",
        expect_ok=False,
    )
    assert "x-api-key" in proc.stderr  # the header name is fine to print
    assert "SOURCE_API_KEY" in proc.stderr  # the variable name, not its value


def test_unknown_download_flag_is_rejected(root):
    proc = run(
        "download.sh",
        root,
        "s",
        "https://example.invalid/x",
        "--secret",
        "hunter2",
        expect_ok=False,
    )
    assert proc.returncode == 2
    assert "unknown arg" in proc.stderr


# --- root -----------------------------------------------------------------
#
# Envelope-wrapped sources: the rows sit one level down, which both buries
# real churn in the envelope's own fields and puts the table out of --sort's
# reach. Hoisting fixes both, so the two are tested together.


def test_root_hoists_a_nested_array(root):
    seed(root, "s", '{"count": 512, "players": [{"id": 2}, {"id": 1}]}')
    run("canonicalize.py", root, "s", "--root", "players")
    assert json.loads(canon(root, "s")) == [{"id": 2}, {"id": 1}]


def test_root_makes_a_nested_table_sortable(root):
    """The reason --root exists: --sort is top-level only, so the rows have to
    come up before they can be pinned."""
    seed(root, "s", '{"count": 2, "players": [{"id": 2}, {"id": 1}]}')
    run("canonicalize.py", root, "s", "--root", "players", "--sort", "id")
    assert json.loads(canon(root, "s")) == [{"id": 1}, {"id": 2}]


def test_sorting_a_nested_table_without_root_still_fails(root):
    seed(root, "s2", '{"players": [{"id": 2}, {"id": 1}]}')
    proc = run("canonicalize.py", root, "s2", "--sort", "id", expect_ok=False)
    assert proc.returncode == 1
    assert "top-level array" in proc.stderr


def test_root_discards_the_envelope_and_its_churn(root):
    """A ticking `count` or a rolling `week` must not diff once hoisted."""
    seed(root, "s", '{"count": 511, "week": "0", "players": [{"id": 1}]}')
    run("canonicalize.py", root, "s", "--root", "players")
    first = canon(root, "s")
    seed(root, "s", '{"count": 512, "week": "1", "players": [{"id": 1}]}')
    run("canonicalize.py", root, "s", "--root", "players")
    assert canon(root, "s") == first


def test_root_follows_a_dotted_path(root):
    seed(root, "s", '{"a": {"b": [{"id": 1}]}}')
    run("canonicalize.py", root, "s", "--root", "a.b")
    assert json.loads(canon(root, "s")) == [{"id": 1}]


def test_root_hoists_an_object_too(root):
    seed(root, "s", '{"meta": 1, "players": {"x": {"id": 1}}}')
    run("canonicalize.py", root, "s", "--root", "players")
    assert json.loads(canon(root, "s")) == {"x": {"id": 1}}


def test_missing_root_path_fails_loudly(root):
    seed(root, "s", '{"players": [{"id": 1}]}')
    proc = run("canonicalize.py", root, "s", "--root", "results", expect_ok=False)
    assert proc.returncode == 1
    assert "not in this response" in proc.stderr


def test_root_pointing_at_a_scalar_fails_loudly(root):
    seed(root, "s", '{"count": 512}')
    proc = run("canonicalize.py", root, "s", "--root", "count", expect_ok=False)
    assert proc.returncode == 1
    assert "not an array or object" in proc.stderr


def test_empty_root_leaves_the_document_alone(root):
    seed(root, "s", '{"count": 1, "players": [{"id": 1}]}')
    run("canonicalize.py", root, "s", "--root", "")
    assert json.loads(canon(root, "s")) == {"count": 1, "players": [{"id": 1}]}


def test_root_applies_before_drop(root):
    seed(root, "s", '{"count": 1, "players": [{"id": 1, "rank": 9}]}')
    run("canonicalize.py", root, "s", "--root", "players", "--drop", "rank")
    assert json.loads(canon(root, "s")) == [{"id": 1}]


# --- keep -----------------------------------------------------------------
#
# The allowlist counterpart to --drop. The test that matters is that a field
# the source adds later defaults to excluded: a denylist fails open, and this
# is the knob for when that is the wrong way to fail.


def test_keep_narrows_records_to_the_allowlist(root):
    seed(root, "s", '[{"id": 1, "name": "a", "rank": 9}]')
    run("canonicalize.py", root, "s", "--keep", "id,name")
    assert json.loads(canon(root, "s")) == [{"id": 1, "name": "a"}]


def test_a_new_upstream_field_is_excluded_by_default(root):
    """The whole point: --drop would have let this through, --keep must not."""
    seed(root, "s", '[{"id": 1, "rank_ecr": 3}]')
    run("canonicalize.py", root, "s", "--keep", "id")
    before = canon(root, "s")
    seed(root, "s", '[{"id": 1, "rank_ecr": 3, "rank_ecr_superflex": 7}]')
    run("canonicalize.py", root, "s", "--keep", "id")
    assert canon(root, "s") == before


def test_keep_leaves_record_identity_alone_on_a_keyed_object(root):
    """The outer keys are which record it is, not a field of it."""
    seed(root, "s", '{"p1": {"id": 1, "rank": 9}, "p2": {"id": 2, "rank": 8}}')
    run("canonicalize.py", root, "s", "--keep", "id")
    assert json.loads(canon(root, "s")) == {"p1": {"id": 1}, "p2": {"id": 2}}


def test_keep_narrows_a_single_flat_object(root):
    seed(root, "s", '{"week": 3, "season": "2026", "secret": "x"}')
    run("canonicalize.py", root, "s", "--keep", "week,season")
    assert json.loads(canon(root, "s")) == {"week": 3, "season": "2026"}


def test_keep_keeps_nested_structure_under_a_kept_field(root):
    seed(root, "s", '[{"id": 1, "positions": ["QB", "RB"], "rank": 9}]')
    run("canonicalize.py", root, "s", "--keep", "id,positions")
    assert json.loads(canon(root, "s")) == [{"id": 1, "positions": ["QB", "RB"]}]


def test_keep_composes_with_root_and_sort(root):
    seed(root, "s", '{"count": 2, "players": [{"id": 2, "r": 9}, {"id": 1, "r": 8}]}')
    run(
        "canonicalize.py",
        root,
        "s",
        "--root",
        "players",
        "--keep",
        "id",
        "--sort",
        "id",
    )
    assert json.loads(canon(root, "s")) == [{"id": 1}, {"id": 2}]


def test_unknown_keep_field_warns_but_succeeds(root):
    seed(root, "s", '[{"id": 1}]')
    proc = run("canonicalize.py", root, "s", "--keep", "id,nope")
    assert "keep field 'nope' matched no data" in proc.stderr
    assert json.loads(canon(root, "s")) == [{"id": 1}]


def test_keep_and_drop_together_fail_loudly(root):
    seed(root, "s", '[{"id": 1, "rank": 9}]')
    proc = run(
        "canonicalize.py", root, "s", "--keep", "id", "--drop", "rank", expect_ok=False
    )
    assert proc.returncode == 1
    assert "mutually exclusive" in proc.stderr


def test_keep_against_an_array_of_scalars_fails_loudly(root):
    seed(root, "s", "[1, 2, 3]")
    proc = run("canonicalize.py", root, "s", "--keep", "id", expect_ok=False)
    assert proc.returncode == 1
    assert "array of objects" in proc.stderr


def test_empty_keep_leaves_every_field_alone(root):
    seed(root, "s", '[{"id": 1, "rank": 9}]')
    run("canonicalize.py", root, "s", "--keep", "")
    assert json.loads(canon(root, "s")) == [{"id": 1, "rank": 9}]
