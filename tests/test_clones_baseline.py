"""#130: the clone pairs in quality/baseline.toml, the write that names a new pair and the verdict that lets the table only fall."""

import re
import textwrap
from pathlib import Path

import pytest

from quality import __version__, cli
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
from tests.support.clones import function as clone_function
from tests.support.emergency import ONE_JOB, SONAR_GREEN, declaring, drive
from tests.support.git_repositories import head_commit, put
from tests.support.hygiene import hygiene_output
from tests.support.source_code import long_function
from tests.support.verdict import ONLY_DECLARED, declaration, verdict_of
from tests.support.verdict_renames import OLD_ENTRY, commit_all, entry, file_text, moved_in_head, moves_in_head, pulled

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


@pytest.mark.parametrize(
    ("moved_symbol", "expected"),
    [("run", "verdict: green"), ("walk", UNDECLARED + "clone baseline adds tests/test_b.py hygiene <-> tests/test_e.py walk")],
    ids=["a pair whose side only moved is the same pair", "a moved side with a renamed function is a new pair"],
)
def test_a_pair_is_held_under_the_path_its_file_was_renamed_from(pull_request, capsys, moved_symbol, expected):
    """#149 R3: the moved side keeps its symbol, so only a renamed function makes a new pair."""
    head = render_baseline(Baseline({}, frozenset({ClonePair.of(Place("tests/test_e.py", moved_symbol), Place("tests/test_b.py", "hygiene"))})))
    moved_in_head(pull_request, FIRST, head, old="tests/test_a.py", new="tests/test_e.py")
    _, output = verdict_of(pull_request[0], capsys)
    assert expected in output


def pairs_text(*pairs):
    return render_baseline(Baseline({}, frozenset(pairs)))


MOVED_PAIR = ClonePair.of(Place("tests/test_e.py", "run"), Place("tests/test_b.py", "hygiene"))
BOTH_MOVED_PAIR = ClonePair.of(Place("tests/test_z.py", "run"), Place("tests/test_y.py", "hygiene"))
NEW_PAIR_LABEL = "tests/test_b.py hygiene <-> tests/test_e.py run"


ONE_MOVE = [("tests/test_a.py", "tests/test_e.py")]
BOTH_MOVES = [("tests/test_a.py", "tests/test_z.py"), ("tests/test_b.py", "tests/test_y.py")]


@pytest.mark.parametrize(
    ("base", "head", "moves", "expected"),
    [
        (FIRST, pairs_text(BOTH_MOVED_PAIR), BOTH_MOVES, "verdict: green"),
        (entry("tests/test_e.py", 99, "run") + FIRST, pairs_text(MOVED_PAIR), ONE_MOVE, "verdict: green"),
        (FIRST, pairs_text(MOVED_PAIR, SECOND_PAIR), ONE_MOVE, UNDECLARED + f"clone baseline adds {SECOND_LABEL}"),
        (FIRST, pairs_text(FIRST_PAIR, MOVED_PAIR), ONE_MOVE, UNDECLARED + f"clone baseline adds {NEW_PAIR_LABEL}"),
    ],
    ids=["both sides moved so the canonical order flips", "a leftover size entry under the target does not block the move",
         "a new pair beside a move is an add", "the head keeps the old pair so the moved one is an add"],
)
def test_a_pair_moves_with_its_files_by_the_rules_of_the_size_entries(pull_request, capsys, base, head, moves, expected):
    """#149 cells 13, 16, 17, 18 (R3, rules 1 and 2) for clone pairs."""
    moves_in_head(pull_request, base, head, moves)
    assert expected in verdict_of(pull_request[0], capsys)[1]


def test_a_moved_pair_is_not_copied_into_a_later_pull_request(pull_request, capsys, monkeypatch):
    """#149 cell 14 for clone pairs: after the move the base holds no pair under the old path."""
    repo, _ = pull_request
    moves_in_head(pull_request, FIRST, pairs_text(MOVED_PAIR), [("tests/test_a.py", "tests/test_e.py")])
    monkeypatch.setenv("GATE_BASE_SHA", head_commit(repo))
    put(repo / "tests/test_a.py", file_text("again"))
    put(repo / BASELINE_PATH, pairs_text(FIRST_PAIR, MOVED_PAIR))
    commit_all(repo, "recreates the old file")
    assert UNDECLARED + f"clone baseline adds {FIRST_LABEL}" in verdict_of(repo, capsys)[1]


MOVED_ENTRY = OLD_ENTRY.replace("old", "new")
GROWN_MOVED_ENTRY = MOVED_ENTRY.replace("70", "90")
MOVED_RAISE = "size baseline raises function_lines src/new.py f 70 -> 90"


@pytest.mark.parametrize(
    ("base", "head", "expected"),
    [
        (None, MOVED_ENTRY, "verdict: green"),
        (OLD_ENTRY, MOVED_ENTRY + FIRST, "verdict: green"),
        (OLD_ENTRY + FIRST, MOVED_ENTRY, UNDECLARED + "clone baseline removed"),
    ],
    ids=["the baseline adopted", "the clone table adopted", "the clone table removed"],
)
def test_adopting_or_dropping_a_table_next_to_a_move_judges_as_without_one(pull_request, capsys, base, head, expected):
    """#149 cells 18-20."""
    moved_in_head(pull_request, base, head)
    assert expected in verdict_of(pull_request[0], capsys)[1]


def test_a_raise_in_a_moved_file_is_declared_under_its_head_path(pull_request, capsys):
    """#149 cell 21: the declaration matches the line, which names the head path."""
    moved_in_head(pull_request, OLD_ENTRY, GROWN_MOVED_ENTRY, head_config=ONE_JOB + declaration(MOVED_RAISE))
    assert ONLY_DECLARED in verdict_of(pull_request[0], capsys)[1]


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


def test_a_declared_clone_add_merges_through_the_door(capsys, tmp_path):
    """#130 E5, cell 8: the door re-derives the same line from the merge commit and merges."""
    what = f"clone baseline adds {SECOND_LABEL}"
    gh = pulled(
        tmp_path, FIRST, FIRST + SECOND, checks=SONAR_GREEN, base_config=ONE_JOB, merge_config=ONE_JOB + declaring(what)
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


def test_an_unknown_table_names_the_tool_version_and_every_known_table_with_clones():
    """#130 cell 17, #153 cell 14."""
    with pytest.raises(SizeError, match=rf"unknown table \[future\]; quality {re.escape(__version__)} knows ccn, .*, clones$"):
        load_baseline("[future]\n")


def test_the_write_names_the_unknown_table_of_the_previous_baseline_without_advice(build_repo, capsys):
    """#153 cell 16: the wrapper is unchanged, the embedded diagnostic is the new one, and no advice follows."""
    repo = build_repo({"src/a.py": "x = 1\n", BASELINE_PATH: "[future]\n"})
    code, (note, _) = written(capsys, repo)
    assert code == 0
    assert note == (
        f"baseline: the previous {BASELINE_PATH} could not be read ({BASELINE_PATH} has an unknown table [future]; "
        f"quality {__version__} knows ccn, file_lines, function_lines, clones), so raises cannot be named"
    )


def test_hygiene_without_a_clone_table_holds_the_size_and_leaves_the_clone_rule_report_only(build_repo, capsys):
    """#136 cell 6 (E3, report half): size tables alone leave the clone rule counting."""
    repo = build_repo({"src/a.py": long_function(71), **TWO_FILES, BASELINE_PATH: LONG_ONE})
    code, output = hygiene_output(capsys, repo)
    assert code == 1, output
    assert "the baseline holds 70 (it grew)" in output
    assert "clone: report only (no [clones] in quality/baseline.toml) - production: 0 pair(s), tests: 1 pair(s)" in output


def test_hygiene_with_a_clone_table_holds_the_size_and_the_pairs_without_a_report_line(build_repo, capsys):
    """#136 E3, cell 2: a held pair that the tree has is clean, and an enforced rule prints no report line."""
    repo = build_repo({"src/a.py": long_function(71), **TWO_FILES, BASELINE_PATH: LONG_ONE + FIRST})
    code, output = hygiene_output(capsys, repo)
    assert code == 1, output
    assert "the baseline holds 70 (it grew)" in output
    assert ("clone:" in output, "clone tests" in output) == (False, False)


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


@pytest.mark.parametrize("class_path, functions_path", [('tests/a"q.py', 'tests/b"r.py'), ('tests/b"r.py', 'tests/a"q.py')])
def test_symbols_and_paths_that_need_quotes_round_trip_through_the_write(build_repo, capsys, class_path, functions_path):
    """#130 cell 20: `Class.method`, `f#2`, a `"` in a path and a non-ASCII symbol, each on the key side and the list side."""
    method = textwrap.indent(clone_function("méthode", "a", "b"), "    ")
    repo = build_repo({
        class_path: "class Holder:\n" + method,
        functions_path: clone_function("f", "c", "d") + "\n\n" + clone_function("f", "e", "g"),
    })
    assert written(capsys, repo)[0] == 0
    first = baseline_text(repo)
    assert '[clones."tests/a\\"q.py"]' in first
    assert 'tests/b\\"r.py' in first
    pairs = frozenset({
        ClonePair.of(Place(class_path, "Holder.méthode"), Place(functions_path, "f")),
        ClonePair.of(Place(class_path, "Holder.méthode"), Place(functions_path, "f#2")),
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


NEW_PAIR = (
    "clone tests/test_b.py:1 - function hygiene is a near copy of tests/test_a.py run (new pair).\n"
    "  Fix: give the shared body one owner and call it, or make the cases one parametrized test"
)
STALE_PAIR = (
    "clone tests/test_a.py - the baseline holds the pair tests/test_a.py run <-> tests/test_b.py hygiene, "
    "which is gone (stale).\n  Fix: lower it with `quality baseline write`"
)


def enforced(capsys, build_repo, files, table=EMPTY_CLONES):
    return hygiene_output(capsys, build_repo({**files, BASELINE_PATH: table}))


def test_an_empty_clone_table_makes_a_new_cross_file_pair_red_at_its_second_side(build_repo, capsys):
    """#136 E3, E4, cell 1."""
    code, output = enforced(capsys, build_repo, TWO_FILES)
    assert code == 1, output
    assert NEW_PAIR in output


def test_a_pair_across_production_and_tests_is_judged_like_any_other(build_repo, capsys):
    """#136 cell 13."""
    files = {"src/a.py": clone_function("run", "a", "b"), "tests/test_b.py": clone_function("hygiene", "c", "d")}
    code, output = enforced(capsys, build_repo, files)
    assert code == 1, output
    assert "clone tests/test_b.py:1 - function hygiene is a near copy of src/a.py run (new pair)." in output


def test_a_held_pair_whose_side_file_was_deleted_is_red_at_the_first_side(build_repo, capsys):
    """#136 E4, cell 3."""
    code, output = enforced(capsys, build_repo, {"tests/test_a.py": TWO_FILES["tests/test_a.py"]}, FIRST)
    assert code == 1, output
    assert STALE_PAIR in output


def test_a_renamed_function_makes_the_held_pair_stale_and_the_new_name_a_new_pair(build_repo, capsys):
    """#136 cell 4."""
    files = {**TWO_FILES, "tests/test_b.py": clone_function("renamed", "c", "d")}
    code, output = enforced(capsys, build_repo, files, FIRST)
    assert code == 1, output
    assert STALE_PAIR in output
    assert "clone tests/test_b.py:1 - function renamed is a near copy of tests/test_a.py run (new pair)." in output


def test_a_held_same_file_pair_and_a_held_self_pair_are_stale(build_repo, capsys):
    """#136 cell 5: the detector never produces them, so they are held and gone."""
    table = '[clones."tests/test_a.py"]\nrun = [["tests/test_a.py", "other"], ["tests/test_a.py", "run"]]\n'
    code, output = enforced(capsys, build_repo, {"tests/test_a.py": TWO_FILES["tests/test_a.py"]}, table)
    assert code == 1, output
    assert "the baseline holds the pair tests/test_a.py other <-> tests/test_a.py run, which is gone (stale)." in output
    assert "the baseline holds the pair tests/test_a.py run <-> tests/test_a.py run, which is gone (stale)." in output


def test_a_copy_fixed_in_place_leaves_the_held_pair_stale(build_repo, capsys):
    """#136 cell 16: both functions remain but are no longer similar."""
    files = {**TWO_FILES, "tests/test_b.py": "def hygiene(a):\n    return a\n"}
    code, output = enforced(capsys, build_repo, files, FIRST)
    assert code == 1, output
    assert STALE_PAIR in output


def test_a_second_side_moved_down_is_named_at_its_new_line(build_repo, capsys):
    """#136 cell 18."""
    files = {**TWO_FILES, "tests/test_b.py": "import os\n\n\n" + TWO_FILES["tests/test_b.py"]}
    code, output = enforced(capsys, build_repo, files)
    assert code == 1, output
    assert "clone tests/test_b.py:4 - function hygiene" in output


def test_two_new_pairs_print_in_sorted_order(build_repo, capsys):
    """#136 cell 10."""
    code, output = enforced(capsys, build_repo, THREE_FILES)
    assert code == 1, output
    positions = [output.index(line) for line in (
        "tests/test_b.py:1 - function hygiene is a near copy of tests/test_a.py run",
        "tests/test_c.py:1 - function walk is a near copy of tests/test_a.py run",
        "tests/test_c.py:1 - function walk is a near copy of tests/test_b.py hygiene",
    )]
    assert positions == sorted(positions)


def test_a_file_python_cannot_parse_leaves_the_enforced_clone_rule_unable_to_judge(build_repo, capsys):
    """#136 cell 8: the same file and wording as the size rule, and both rules count as unparsable."""
    code, output = enforced(capsys, build_repo, {**TWO_FILES, "src/broken.py": "def broken(:\n"})
    assert code == 2, output
    assert "hygiene: size unparsable - src/broken.py is not valid Python" in output
    assert "hygiene: clone unparsable - src/broken.py is not valid Python" in output
    assert "2 unparsable" in output


@pytest.mark.parametrize("text", ["[size\n", *MALFORMED], ids=["no clone table, not TOML", *MALFORMED_IDS])
def test_a_malformed_baseline_leaves_both_rules_unable_to_judge_with_the_rewrite_hint(build_repo, capsys, text):
    """#136 cell 9: one read, so even a file with no `[clones]` leaves clone's mode unknown."""
    code, output = hygiene_output(capsys, build_repo({"src/a.py": "x = 1\n", BASELINE_PATH: text}))
    assert code == 2, output
    assert "hygiene: size cannot judge" in output
    assert "hygiene: clone cannot judge" in output
    assert "rewrite it with `quality baseline write`" in output


def test_the_write_and_the_rule_agree(build_repo, capsys):
    """#136 cell 11."""
    repo = build_repo(THREE_FILES)
    assert written(capsys, repo)[0] == 0
    code, output = hygiene_output(capsys, repo)
    assert (code, "clone" in output.replace("clone,", "")) == (0, False), output


def test_a_control_character_and_undecodable_bytes_in_a_path_print_escaped(build_repo, capsys):
    """#136 cell 17: a new pair can carry both; a held pair only Unicode."""
    new = {"src/a\x01.py": clone_function("run", "a", "b"), "src/b\udcff.py": clone_function("hygiene", "c", "d")}
    code, output = enforced(capsys, build_repo, new)
    assert code == 1, output
    assert "clone src/b\\udcff.py:1 - function hygiene is a near copy of src/a\\x01.py run (new pair)." in output
    pair = ClonePair.of(Place("src/a\x01.py", "run"), Place("src/b.py", "hygiene"))
    stale = render_baseline(Baseline({}, frozenset({pair})))
    code, output = enforced(capsys, build_repo, {"src/c.py": "x = 1\n"}, stale)
    assert code == 1, output
    assert "clone src/a\\x01.py - the baseline holds the pair src/a\\x01.py run <-> src/b.py hygiene" in output


@pytest.mark.parametrize(
    ("merge_config", "code", "expected"),
    [
        (ONE_JOB, 1, UNDECLARED + f"clone baseline adds {SECOND_LABEL}"),
        (ONE_JOB + declaring(f"clone baseline adds {SECOND_LABEL}"), 0, "merged."),
        (ONE_JOB + declaring(f"clone baseline adds {NEW_PAIR_LABEL}"), 1, UNDECLARED + f"clone baseline adds {SECOND_LABEL}"),
    ],
    ids=["undeclared is refused", "declared goes on", "a declaration of another pair is refused"],
)
def test_the_door_judges_a_new_pair_beside_a_move_as_the_verdict_does(capsys, tmp_path, merge_config, code, expected):
    """#149 cell 23b: the moved pair is the same pair, the new one needs its own declaration."""
    gh = pulled(
        tmp_path, FIRST, pairs_text(MOVED_PAIR, SECOND_PAIR), ONE_MOVE, checks=SONAR_GREEN, base_config=ONE_JOB,
        merge_config=merge_config,
    )
    assert drive(gh)[0] == code
    assert expected.replace(UNDECLARED, "") in capsys.readouterr().out
    assert (len(gh.merge_calls) == 1) == (code == 0)
