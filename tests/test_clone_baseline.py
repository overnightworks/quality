"""#130: the clone pairs in quality/baseline.toml, the write that names a new pair and the verdict that lets the table only fall."""

import textwrap
from pathlib import Path

import pytest
from conftest import hygiene_output, long_function, put
from test_clones import function as clone_function
from test_emergency import ONE_JOB, SONAR_GREEN, FakeGh, declaring, drive
from test_verdict import ONLY_DECLARED, declaration, verdict_of

from quality import cli
from quality.ratchet import (
    BASELINE_PATH,
    Baseline,
    ClonePair,
    Key,
    Metric,
    Place,
    SizeError,
    load_baseline,
    render_baseline,
)

FIRST_PAIR = ClonePair.of(Place("tests/test_a.py", "run"), Place("tests/test_b.py", "hygiene"))
SECOND_PAIR = ClonePair.of(Place("tests/test_c.py", "walk"), Place("tests/test_d.py", "stroll"))
FIRST_LABEL = "tests/test_a.py run <-> tests/test_b.py hygiene"
SECOND_LABEL = "tests/test_c.py walk <-> tests/test_d.py stroll"
REVERSED = '[clones."tests/test_b.py"]\nhygiene = [["tests/test_a.py", "run"]]\n'
FIRST = '[clones."tests/test_a.py"]\nrun = [["tests/test_b.py", "hygiene"]]\n'
SECOND = '[clones."tests/test_c.py"]\nwalk = [["tests/test_d.py", "stroll"]]\n'
EMPTY_CLONES = "[clones]\n"
LONG_ONE = '[function_lines."src/a.py"]\n"long_one" = 70\n'
SIZE = '[function_lines."src/quality/cli.py"]\n"main" = 82\n'
SIZE_RAISED = SIZE.replace("82", "90")
UNDECLARED = "loosened (undeclared): "
TWO_FILES = {"tests/test_a.py": clone_function("run", "a", "b"), "tests/test_b.py": clone_function("hygiene", "c", "d")}
THREE_FILES = {**TWO_FILES, "tests/test_c.py": clone_function("walk", "e", "f")}
THREE_PAIRS_WRITTEN = (
    '\n[clones."tests/test_a.py"]\nrun = [["tests/test_b.py", "hygiene"], ["tests/test_c.py", "walk"]]\n'
    '\n[clones."tests/test_b.py"]\nhygiene = [["tests/test_c.py", "walk"]]\n'
)
THREE_ADDS = [
    "baseline: adds clone tests/test_a.py run <-> tests/test_b.py hygiene",
    "baseline: adds clone tests/test_a.py run <-> tests/test_c.py walk",
    "baseline: adds clone tests/test_b.py hygiene <-> tests/test_c.py walk",
]


def wrote(repo: Path, entries: str, pairs: str) -> str:
    return f"baseline: wrote {entries} and {pairs} to {repo / BASELINE_PATH}"


def judged(pull_request, capsys, base, head, head_config=None):
    repo, commit_base = pull_request
    commit_base(base, ONE_JOB)
    put(repo / BASELINE_PATH, head)
    if head_config is not None:
        put(repo / "quality/config.toml", head_config)
    code, output = verdict_of(repo, capsys)
    assert "Traceback" not in output
    return code, output


def written(capsys, repo: Path) -> tuple[int, list[str]]:
    code = cli.main(["baseline", "write", str(repo)])
    captured = capsys.readouterr()
    return code, (captured.out + captured.err).splitlines()


def baseline_text(repo: Path) -> str:
    return (repo / BASELINE_PATH).read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("base", "head", "code", "expected"),
    [
        (FIRST, FIRST + SECOND, 1, UNDECLARED + f"clone baseline adds {SECOND_LABEL}"),
        (FIRST + SECOND, FIRST, 0, "verdict: green"),
        (SIZE, SIZE + FIRST, 0, "verdict: green"),
        (FIRST, SIZE, 1, UNDECLARED + "clone baseline removed"),
        (FIRST, None, 1, UNDECLARED + f"{BASELINE_PATH} removed"),
        (None, FIRST, 0, "verdict: green"),
        (FIRST, FIRST.replace("run", "run2"), 1, UNDECLARED + "clone baseline adds tests/test_a.py run2 <-> tests/test_b.py hygiene"),
        (FIRST, EMPTY_CLONES, 0, "verdict: green"),
        (REVERSED, FIRST, 0, "verdict: green"),
    ],
    ids=[
        "cell 1 an added pair", "cell 2 a removed pair", "cell 3 adoption", "cell 4 the table removed",
        "cell 5 the file removed", "cell 6 no base file", "cell 7 a renamed function is an add",
        "an emptied table is green", "cell 24 a reversed base pair against the canonical head",
    ],
)
def test_the_clone_baseline_only_falls_against_the_base(pull_request, capsys, base, head, code, expected):
    """#130 E2, E3, E5, cells 1-7 and 24."""
    actual_code, output = judged(pull_request, capsys, base, head)
    assert (actual_code, expected in output) == (code, True), output
    if expected == UNDECLARED + f"{BASELINE_PATH} removed":
        assert "clone baseline removed" not in output


def test_a_size_raise_and_a_clone_add_are_both_named_size_first(pull_request, capsys):
    """#130 E5, cell 10."""
    _, output = judged(pull_request, capsys, SIZE + FIRST, SIZE_RAISED + FIRST + SECOND)
    lines = output.splitlines()
    raised = lines.index(UNDECLARED + "size baseline raises function_lines src/quality/cli.py main 82 -> 90")
    assert lines[raised + 1] == UNDECLARED + f"clone baseline adds {SECOND_LABEL}"


def test_two_added_pairs_are_named_in_sorted_order_whatever_the_file_order(pull_request, capsys):
    """#130 E5, cell 22: the verdict."""
    _, output = judged(pull_request, capsys, EMPTY_CLONES, SECOND + FIRST)
    lines = output.splitlines()
    first = lines.index(UNDECLARED + f"clone baseline adds {FIRST_LABEL}")
    assert lines[first + 1] == UNDECLARED + f"clone baseline adds {SECOND_LABEL}"


def test_a_declared_clone_add_is_declared_in_the_verdict(pull_request, capsys):
    """#130 E5, cell 8: `what` is the text after `loosened (undeclared): `."""
    what = f"clone baseline adds {SECOND_LABEL}"
    code, output = judged(pull_request, capsys, FIRST, FIRST + SECOND, ONE_JOB + declaration(what))
    assert (code, ONLY_DECLARED in output, UNDECLARED in output) == (1, True, False), output


def test_a_declared_clone_add_merges_through_the_door(capsys):
    """#130 E5, cell 8: the door re-derives the same line from the merge commit and merges."""
    what = f"clone baseline adds {SECOND_LABEL}"
    gh = FakeGh(
        checks=SONAR_GREEN, base_config=ONE_JOB, merge_config=ONE_JOB + declaring(what),
        base_baseline=FIRST, merge_baseline=FIRST + SECOND,
    )
    assert drive(gh)[0] == 0
    assert "merged." in capsys.readouterr().out
    assert len(gh.merge_calls) == 1


def test_a_declaration_for_a_pair_the_head_does_not_add_matches_nothing(pull_request, capsys):
    """#130 E5, cell 9."""
    what = f"clone baseline adds {SECOND_LABEL}"
    code, output = judged(pull_request, capsys, FIRST, FIRST, ONE_JOB + declaration(what))
    assert (code, f"declared loosening matches nothing: {what}" in output) == (1, True), output


MALFORMED = [
    "clones = 1\n",
    '[clones]\n"tests/test_a.py" = 1\n',
    '[clones."tests/test_a.py"]\nrun = 1\n',
    '[clones."tests/test_a.py"]\nrun = [["tests/test_b.py"]]\n',
    '[clones."tests/test_a.py"]\nrun = [["tests/test_b.py", 1]]\n',
    '[clones."tests/test_a.py"]\nrun = ["tests/test_b.py"]\n',
]
MALFORMED_IDS = ["not a table", "a path that is not a table", "a symbol that is not a list", "one string", "a number", "a bare string"]


@pytest.mark.parametrize("text", MALFORMED, ids=MALFORMED_IDS)
def test_a_malformed_clone_table_cannot_be_judged_by_hygiene(build_repo, capsys, text):
    """#130 cell 16: hygiene."""
    code, output = hygiene_output(capsys, build_repo({"src/a.py": "x = 1\n", BASELINE_PATH: text}))
    assert (code, BASELINE_PATH in output, "Traceback" in output) == (2, True, False), output


@pytest.mark.parametrize("text", MALFORMED, ids=MALFORMED_IDS)
def test_a_malformed_clone_table_cannot_be_judged_by_the_verdict(pull_request, capsys, text):
    """#130 cell 16: the verdict names the head's file and carries the rewrite hint (cell 25, head)."""
    code, output = judged(pull_request, capsys, FIRST, text)
    assert code == 2
    assert f"cannot judge - {BASELINE_PATH} at the head: " in output
    assert "rewrite it with `quality baseline write`" in output


def test_a_malformed_clone_table_at_the_base_names_the_base_commit_without_a_rewrite_hint(pull_request, capsys):
    """#130 cell 25, base."""
    repo, commit_base = pull_request
    base_sha = commit_base(MALFORMED[3], ONE_JOB)
    put(repo / BASELINE_PATH, FIRST)
    code, output = verdict_of(repo, capsys)
    assert code == 2
    assert f"{BASELINE_PATH} at the base commit {base_sha}" in output
    assert "rewrite it" not in output


@pytest.mark.parametrize("text", MALFORMED, ids=MALFORMED_IDS)
def test_the_write_overwrites_a_malformed_previous_clone_table_with_the_unchanged_note(build_repo, capsys, text):
    """#130 cell 16: the write's previous-file message is unchanged."""
    repo = build_repo({"src/a.py": "x = 1\n", BASELINE_PATH: text})
    code, (note, _) = written(capsys, repo)
    assert code == 0
    assert note.startswith(f"baseline: the previous {BASELINE_PATH} could not be read (")
    assert note.endswith("), so raises cannot be named")


def test_an_unknown_table_names_clones_among_the_known_ones():
    """#130 cell 17."""
    with pytest.raises(SizeError, match=r"unknown table \[future\]; only .*clones"):
        load_baseline("[future]\n")


@pytest.mark.parametrize("clones", ["", FIRST], ids=["no clone table", "a clone table"])
def test_hygiene_with_a_clone_table_holds_the_size_and_leaves_the_clone_rule_report_only(build_repo, capsys, clones):
    """#130 cell 19: the table changes neither the size rule's verdict nor the clone rule's report-only line."""
    repo = build_repo({"src/a.py": long_function(71), **TWO_FILES, BASELINE_PATH: LONG_ONE + clones})
    code, output = hygiene_output(capsys, repo)
    assert code == 1, output
    assert "the baseline holds 70 (it grew)" in output
    assert "clone: report only (no [clones] in quality/baseline.toml) - production: 0 pair(s), tests: 1 pair(s)" in output


def test_the_write_with_no_previous_file_writes_both_and_names_no_pair(build_repo, capsys):
    """#130 E3, E6, cell 11: nothing to compare against, so nothing is named; the count line counts the pairs."""
    repo = build_repo({"src/a.py": long_function(70), **TWO_FILES})
    assert written(capsys, repo) == (0, [wrote(repo, "1 entry", "1 clone pair")])
    assert baseline_text(repo).endswith(LONG_ONE + "\n" + FIRST)


def test_the_write_over_a_file_without_a_clone_table_is_an_adoption_and_names_no_pair(build_repo, capsys):
    """#130 E3, cell 12."""
    repo = build_repo({"src/a.py": "x = 1\n", **TWO_FILES, BASELINE_PATH: SIZE})
    assert written(capsys, repo) == (0, [wrote(repo, "0 entries", "1 clone pair")])


def test_the_write_names_each_new_pair_before_the_count_line(build_repo, capsys):
    """#130 E6, cell 13."""
    repo = build_repo({**THREE_FILES, BASELINE_PATH: FIRST})
    code, lines = written(capsys, repo)
    assert (code, lines) == (0, [*THREE_ADDS[1:], wrote(repo, "0 entries", "3 clone pairs")])


def test_two_new_pairs_are_named_in_sorted_order_in_the_write(build_repo, capsys):
    """#130 E6, cell 22: the write."""
    repo = build_repo({**THREE_FILES, BASELINE_PATH: EMPTY_CLONES})
    assert written(capsys, repo) == (0, [*THREE_ADDS, wrote(repo, "0 entries", "3 clone pairs")])


def test_a_pair_falling_out_of_the_tree_is_named_by_nothing(build_repo, capsys):
    """#130 E6, cell 21."""
    repo = build_repo({**TWO_FILES, BASELINE_PATH: FIRST + SECOND})
    assert written(capsys, repo) == (0, [wrote(repo, "0 entries", "1 clone pair")])


def test_the_written_file_loads_back_with_each_pair_once_under_its_first_side_sorted(build_repo, capsys):
    """#130 E6, cell 14: the form, and the same file again on a second write."""
    repo = build_repo(THREE_FILES)
    assert written(capsys, repo)[0] == 0
    first = baseline_text(repo)
    assert first.endswith(THREE_PAIRS_WRITTEN)
    pairs = frozenset({
        ClonePair.of(Place("tests/test_a.py", "run"), Place("tests/test_b.py", "hygiene")),
        ClonePair.of(Place("tests/test_a.py", "run"), Place("tests/test_c.py", "walk")),
        ClonePair.of(Place("tests/test_b.py", "hygiene"), Place("tests/test_c.py", "walk")),
    })
    assert load_baseline(first) == Baseline({}, pairs)
    assert written(capsys, repo)[0] == 0
    assert baseline_text(repo) == first


def test_a_reversed_or_repeated_hand_written_pair_loads_as_its_one_identity():
    """#130 E2, cell 15."""
    repeated = REVERSED + '[clones."tests/test_a.py"]\nrun = [["tests/test_b.py", "hygiene"], ["tests/test_b.py", "hygiene"]]\n'
    assert load_baseline(REVERSED) == Baseline({}, frozenset({FIRST_PAIR}))
    assert load_baseline(repeated) == Baseline({}, frozenset({FIRST_PAIR}))
    assert render_baseline(load_baseline(repeated)).endswith("\n" + FIRST)


def test_an_empty_clone_table_is_the_empty_set_and_renders_back_as_the_table(build_repo, capsys):
    """#130 E3, cell 18: an empty table is not an absent one."""
    assert load_baseline(EMPTY_CLONES) == Baseline({}, frozenset())
    assert load_baseline("") == Baseline({}, None)
    assert render_baseline(Baseline({}, frozenset())).endswith("\n" + EMPTY_CLONES)
    repo = build_repo({"src/a.py": "x = 1\n"})
    assert written(capsys, repo) == (0, [wrote(repo, "0 entries", "0 clone pairs")])
    assert baseline_text(repo).endswith("\n" + EMPTY_CLONES)


def test_symbols_and_paths_that_need_quotes_round_trip_through_the_write(build_repo, capsys):
    """#130 cell 20: `Class.method`, `f#2`, a `"` in a path and a non-ASCII symbol, on the key side and the list side."""
    method = textwrap.indent(clone_function("méthode", "a", "b"), "    ")
    files = {
        'tests/a"q.py': "class Holder:\n" + method,
        'tests/b"r.py': clone_function("f", "c", "d") + "\n\n" + clone_function("f", "e", "g"),
    }
    repo = build_repo(files)
    assert written(capsys, repo)[0] == 0
    first = baseline_text(repo)
    assert '[clones."tests/a\\"q.py"]' in first
    assert 'tests/b\\"r.py' in first
    pairs = frozenset({
        ClonePair.of(Place('tests/a"q.py', "Holder.méthode"), Place('tests/b"r.py', "f")),
        ClonePair.of(Place('tests/a"q.py', "Holder.méthode"), Place('tests/b"r.py', "f#2")),
    })
    assert load_baseline(first) == Baseline({}, pairs)
    assert written(capsys, repo)[0] == 0
    assert baseline_text(repo) == first


def test_a_pair_across_production_and_tests_round_trips(build_repo, capsys):
    """#130 cell 23."""
    repo = build_repo({"src/shop.py": clone_function("price", "a", "b"), "tests/test_shop.py": clone_function("cost", "c", "d")})
    assert written(capsys, repo)[0] == 0
    pair = ClonePair.of(Place("src/shop.py", "price"), Place("tests/test_shop.py", "cost"))
    assert load_baseline(baseline_text(repo)) == Baseline({}, frozenset({pair}))


def test_a_size_entry_and_a_pair_load_into_one_baseline():
    key = Key(Metric.FUNCTION_LINES, "src/quality/cli.py", "main")
    assert load_baseline(SIZE + FIRST) == Baseline({key: 82}, frozenset({FIRST_PAIR}))
