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
