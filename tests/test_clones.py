from pathlib import Path

import pytest
from conftest import hygiene_output as hygiene

from quality import clones
from quality.ratchet import ClonePair, Place

REPORT = "clone: report only (no [clones] in quality/baseline.toml) - production: {production} pair(s), tests: {tests} pair(s)"
QUALITY_ITSELF = Path(__file__).parents[1]


def function(name: str, parameter: str, local: str, assignments: int = 7, passes: int = 2) -> str:
    """A function of `3 + 5 * assignments + passes` tokens.

    The parameter is `arguments`, `arg`, `ID` (3 tokens), each `local = parameter` is `Assign`, `Name`, `ID`,
    `Name`, `ID` (5 tokens) and each `pass` is `Pass` (1 token). The defaults give 3 + 35 + 2 = 40.
    """
    body = [f"{local} = {parameter}"] * assignments + ["pass"] * passes
    return f"def {name}({parameter}):\n" + "".join(f"    {line}\n" for line in body)


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
    ],
    ids=[
        "#113 E1 a renamed-locals copy across two files is one pair",
        "#113 E1 a copy inside one file is no pair",
        "#113 E1 a 39-token copy is no pair",
        "#113 E1 a 40-token copy is one pair",
        "#113 E7 a copy between two tests counts as tests",
        "#113 E7 a test that copies production counts as production",
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


def test_a_pair_is_named_by_path_and_qualified_symbol_whatever_the_line():
    """#113 E2: lines inserted above the copy and an edited neighbour leave the pair's name unchanged."""
    first, second = function("first", "a", "b"), function("second", "c", "d")
    before = clones.find_pairs({"src/a.py": first.encode(), "src/b.py": second.encode()})
    after = clones.find_pairs({"src/a.py": first.encode(), "src/b.py": f"import os\n\n\n{second}\nx = 1\n".encode()})
    assert before == after == {ClonePair(Place("src/a.py", "first"), Place("src/b.py", "second"))}


def test_methods_and_repeated_names_get_the_size_rules_symbols():
    """#113 E2: `Class.method` and the `#2` of a name defined twice, as the size rule names them."""
    first = "class Holder:\n" + "".join(f"    {line}\n" for line in function("method", "self", "b").splitlines())
    second = function("method", "a", "b") + function("method", "c", "d")
    pairs = clones.find_pairs({"src/a.py": first.encode(), "src/b.py": second.encode()})
    assert {pair.first.symbol for pair in pairs} | {pair.second.symbol for pair in pairs} == {"Holder.method", "method", "method#2"}


def test_a_file_python_cannot_parse_has_no_pair_and_leaves_the_run_clean(build_repo, capsys):
    """#113 rule 3: report-only mode skips the file; the size rule names it, and `clone` stays clean."""
    files = {"src/a.py": function("first", "a", "b"), "src/broken.py": "def broken(:\n"}
    code, output = hygiene(capsys, build_repo(files))
    assert (code, report(0, 0) in output, "clone unparsable" in output) == (0, True, False), output


def test_quality_reports_no_clone_pair_in_its_own_tree(capsys):
    """#113 row 4a: the four copies of one test helper are one function now."""
    code, output = hygiene(capsys, QUALITY_ITSELF)
    assert (code, report(0, 0) in output) == (0, True), output
