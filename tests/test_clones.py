from pathlib import Path

import pytest

from quality import clones
from quality.ratchet import ClonePair, Place
from tests.support.check import hygiene_output as hygiene
from tests.support.clones import function

REPORT = "clone: report only (no [clones] in quality/baseline.toml) - production: {production} pair(s), tests: {tests} pair(s)"
QUALITY_ITSELF = Path(__file__).parents[1]


def report(production: int, tests: int) -> str:
    return REPORT.format(production=production, tests=tests)


@pytest.mark.parametrize(
    ("files", "production", "tests"),
    [
        ({"src/a.py": function("first", "a", "b", 10, 0), "src/b.py": function("second", "left", "right", 10, 0)}, 1, 0),
        ({"src/a.py": function("first", "a", "b") + "\n\n" + function("second", "c", "d")}, 0, 0),
        ({"src/a.py": function("first", "a", "b", passes=1), "src/b.py": function("second", "c", "d", passes=1)}, 0, 0),
        ({"src/a.py": function("first", "a", "b"), "src/b.py": function("second", "c", "d")}, 1, 0),
        ({"tests/test_a.py": function("first", "a", "b"), "tests/test_b.py": function("second", "c", "d")}, 0, 1),
        ({"tests/test_a.py": function("first", "a", "b"), "src/b.py": function("second", "c", "d")}, 1, 0),
        ({"src/a.py": function("first", "a", "b"), "src/b.ts": function("second", "c", "d")}, 0, 0),
        ({"src/a.ts": function("first", "a", "b"), "src/b.ts": function("first", "a", "b")}, 0, 0),
    ],
    ids=[
        "#113 E1 a renamed-locals copy across two files is one pair",
        "#113 E1 a copy inside one file is no pair",
        "#113 E1 a 39-token copy is no pair",
        "#113 E1 a 40-token copy is one pair",
        "#113 E7 a copy between two tests counts as tests",
        "#113 E7 a test that copies production counts as production",
        "#119 cell 22 a .ts copy of a Python function is no pair",
        "#119 cell 22 two identical .ts files are no pair",
    ],
)
def test_pairs_are_counted_in_the_report_line_and_the_exit_stays_clean(build_repo, capsys, files, production, tests):
    """The 39-token function is the 40-token one with one `pass` less (3 + 35 + 1 = 39)."""
    code, output = hygiene(capsys, build_repo(files))
    assert (code, report(production, tests) in output) == (0, True), output


def test_a_docstring_does_not_count_towards_the_tokens(build_repo, capsys):
    """#113 E1: the 39-token body plus a docstring is still 39 tokens, so no pair."""
    documented = function("first", "a", "b", passes=1).replace(":\n", ':\n    """Says what it does."""\n', 1)
    files = {"src/a.py": documented, "src/b.py": function("second", "c", "d", passes=1)}
    assert report(0, 0) in hygiene(capsys, build_repo(files))[1]


BINARY_OPERATORS = ["+", "-", "*", "/", "//", "%", "**", "<<", ">>", "|", "^", "&", "@"]


def operator_chain(name: str, operators: int) -> str:
    """A function that is one `x = a OP 1` statement per operator, each operator different.

    The parameter is `arguments`, `arg`, `ID` (3 tokens) and a statement is `Assign`, `Name`, `ID`, `BinOp`, `Name`,
    `ID`, the operator, `Constant`, `N` (9 tokens). A 5-token window holds at most one operator, so the windows
    holding one are all different, and the 4 windows between two operators are the same 4 windows everywhere.
    """
    body = "".join(f"    x = a {BINARY_OPERATORS[index]} 1\n" for index in range(operators))
    return f"def {name}(a):\n{body}"


@pytest.mark.parametrize(
    ("longer", "shorter", "pairs"),
    [(9, 8, 1), (8, 7, 0)],
    ids=[
        "#113 E1 a copy missing 1 of 9 statements is at the threshold and one pair",
        "#113 E1 a copy missing 1 of 8 statements is below the threshold and no pair",
    ],
)
def test_the_similarity_threshold_is_ninety_percent(build_repo, capsys, longer, shorter, pairs):
    """#113 E1, counted by hand for n statements (3 + 9n tokens, so 9n - 1 windows).

    The windows holding operator i are 5, or 3 for the last operator, which the end of the function cuts short.
    The windows holding none are 3 at the start plus the 4 between two operators, 2 of them the same, so 7. That
    leaves 5(n - 1) + 3 + 7 = 5n + 5 different windows: 50 for 9 statements, 45 for 8, 40 for 7. The shorter
    function has no window the longer one lacks, so the intersection is the shorter's windows and the union the
    longer's: 45 of 50 = 0.9 for 9 and 8 statements, 40 of 45 = 0.889 for 8 and 7.
    """
    files = {"src/a.py": operator_chain("longer", longer), "src/b.py": operator_chain("shorter", shorter)}
    assert report(pairs, 0) in hygiene(capsys, build_repo(files))[1]


def test_a_pair_is_named_by_path_and_qualified_symbol_whatever_the_line():
    """#113 E2: lines inserted above the copy and an edited neighbour leave the pair's name unchanged."""
    first, second = function("first", "a", "b"), function("second", "c", "d")
    neighbour = "def neighbour(a):\n    return a\n"
    edited_neighbour = "def neighbour(a, b):\n    c = a\n    return b\n"
    before = clones.find_pairs({"src/a.py": first.encode(), "src/b.py": f"{neighbour}\n\n{second}".encode()}).pairs
    after = clones.find_pairs({"src/a.py": first.encode(), "src/b.py": f"import os\n\n\n{edited_neighbour}\n\n{second}\nx = 1\n".encode()}).pairs
    assert before == after == {ClonePair(Place("src/a.py", "first"), Place("src/b.py", "second"))}


def test_methods_and_repeated_names_get_the_size_rules_symbols():
    """#113 E2: `Class.method` and the `#2` of a name defined twice, as the size rule names them."""
    first = "class Holder:\n" + "".join(f"    {line}\n" for line in function("method", "self", "b").splitlines())
    second = function("method", "a", "b") + function("method", "c", "d")
    pairs = clones.find_pairs({"src/a.py": first.encode(), "src/b.py": second.encode()}).pairs
    assert {pair.first.symbol for pair in pairs} | {pair.second.symbol for pair in pairs} == {"Holder.method", "method", "method#2"}


def test_a_file_python_cannot_parse_has_no_pair_and_leaves_the_clone_rule_clean_while_the_python_rules_exit_2(build_repo, capsys):
    """#113 rule 3: report-only mode skips the file; `clone` stays clean, python-security (#219) and python-print (#225)
    make the run exit 2."""
    files = {"src/a.py": function("first", "a", "b"), "src/broken.py": "def broken(:\n"}
    code, output = hygiene(capsys, build_repo(files))
    assert (code, report(0, 0) in output, "clone unparsable" in output) == (2, True, False), output
    assert "hygiene: python-security unparsable - ruff cannot parse src/broken.py" in output
    assert "hygiene: python-print unparsable - ruff cannot parse src/broken.py" in output


@pytest.mark.integration
def test_quality_holds_its_own_empty_clone_table_and_prints_no_clone_line(capsys):
    """#136 cell 12: quality enforces its `[clones]`, so a copied helper in its tree turns its own gate red."""
    code, output = hygiene(capsys, QUALITY_ITSELF)
    assert (code, "clone:" in output) == (0, False), output
