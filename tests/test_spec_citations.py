import os
from pathlib import Path

import pytest

from quality.report import NoteEvent
from quality.rules import Area, Rule
from tests.support.check import check_output, check_result, note_rows, refusals, rule_notes

SPEC = "docs/rules.md"
UNCITED = "1 spec ID uncited"


def note_of(repo: Path) -> str | None:
    """The fact of the one note the rule gives over `repo`, None where it gives none."""
    notes = rule_notes(repo, Rule.SPEC_CITATION)
    assert len(notes) <= 1
    return notes[0].fact if notes else None


def spec_with(*lines: str) -> dict[str, str]:
    return {SPEC: "\n".join(lines) + "\n"}


def cited_by_python(docstring: str, name: str = "test_it") -> dict[str, str]:
    return {"tests/test_a.py": f'def {name}():\n    """{docstring}"""\n'}


def cited_by_script(title: str, call: str = "it", path: str = "web/a.test.ts") -> dict[str, str]:
    return {path: f"{call}({title}, () => {{}});\n"}


@pytest.mark.parametrize(
    "spec",
    [
        ["- X-1: a rule."],
        ["* X-1: a rule."],
        ["+ X-1: a rule."],
        ["1. X-1: a rule."],
        ["1) X-1: a rule."],
        ["- Parent:", "    - X-1: nested."],
    ],
    ids=["dash", "star", "plus", "number and dot", "number and parenthesis", "nested item"],
)
def test_a_list_item_that_opens_with_an_id_and_a_colon_defines_it(build_repo, spec):
    """CITE-1: every list marker and any indent define."""
    assert note_of(build_repo(spec_with(*spec))) == UNCITED


@pytest.mark.parametrize(
    "spec",
    [
        ["- X-1 (retired)."],
        ["- X-1 is a rule."],
        ["Prose names (X-1) in passing."],
        ["```", "- X-1: an example.", "```"],
        ["~~~", "- X-1: an example.", "~~~"],
        ["# Rules", "", "| X-1 | row |"],
        ["- Rules:", "", "# Table", "", "| X-1 | row |"],
        ["- Rules:", "", "Prose.", "", "| X-1 | row |"],
    ],
    ids=["retired", "no colon", "parenthesised prose", "backtick fence", "tilde fence", "table without rule", "table after heading", "table after prose"],
)
def test_nothing_else_defines_an_id(build_repo, spec):
    """CITE-1: retired IDs need no test; a colon-less item, a fence, prose and a table not under a rule define nothing."""
    assert note_of(build_repo(spec_with(*spec))) is None


def test_a_table_row_under_a_rule_defines_its_first_cell(build_repo):
    """CITE-1."""
    spec = ["- X-1: a rule:", "", "  | ID | Text |", "  | --- | --- |", "  | X-2 | a row |", "  | not an id | a row |"]
    assert note_of(build_repo(spec_with(*spec))) == "2 spec IDs uncited"


def test_only_tracked_markdown_files_that_the_tree_holds_define(build_repo):
    """CITE-1: an untracked or deleted `.md` file defines nothing."""
    repo = build_repo({**spec_with("- X-1: a rule."), "docs/gone.md": "- X-2: a rule.\n"})
    (repo / "docs/gone.md").unlink()
    (repo / "docs/untracked.md").write_text("- X-3: a rule.\n")
    assert note_of(repo) == UNCITED


@pytest.mark.parametrize(
    "files",
    [
        {"a.md": "- X-1: a rule.\n", "b.md": "- X-1: a rule.\n"},
        {"a.md": "- X-1: a rule.\n\n  | X-1 | row |\n"},
    ],
    ids=["two files", "an item and a row"],
)
def test_an_id_defined_twice_is_one_duplicate(build_repo, files):
    """CITE-1, CITE-4."""
    assert note_of(build_repo(files)) == "1 spec ID uncited · 1 spec ID defined twice"


@pytest.mark.parametrize(
    ("docstring", "note"),
    [
        ("X-1: it.", "2 spec IDs uncited"),
        ("Spec: X-1: it.", "2 spec IDs uncited"),
        ("X-1, X-2: it.", UNCITED),
        ("X-1 and X-2: it.", UNCITED),
        ("X-1, X-2 and X-3: it.", None),
        ("Does it. X-1.", "3 spec IDs uncited"),
        ("Does it.\n\n    X-1: it.", "3 spec IDs uncited"),
    ],
    ids=["head", "Spec label", "comma head", "and head", "mixed head", "later mention", "later line"],
)
def test_a_python_test_docstring_cites_the_ids_at_its_head(build_repo, docstring, note):
    """CITE-2: every ID of the head counts, a later mention does not."""
    spec = spec_with("- X-1: a.", "- X-2: b.", "- X-3: c.")
    assert note_of(build_repo({**spec, **cited_by_python(docstring)})) == note


def test_a_method_of_a_class_cites(build_repo):
    """CITE-2."""
    files = {"tests/test_a.py": 'class TestA:\n    def test_it(self):\n        """X-1: it."""\n'}
    assert note_of(build_repo({**spec_with("- X-1: a."), **files})) is None


@pytest.mark.parametrize(
    "files",
    [
        {"tests/test_a.py": '"""X-1: the module."""\n'},
        {"tests/test_a.py": 'class TestA:\n    """X-1: the class."""\n'},
        {"tests/test_a.py": 'def helper():\n    """X-1: a helper."""\n'},
        {"tests/test_a.py": "def test_it():\n    # X-1: a comment\n    pass\n"},
        {"src/a.py": 'def test_it():\n    """X-1: not a test file."""\n'},
        {"tests/test_a.py": "def test_it(:\n"},
    ],
    ids=["module", "class", "helper", "comment", "non-test file", "unparsable file"],
)
def test_nothing_else_cites_in_python(build_repo, files):
    """CITE-2, CITE-5: only the head of a test function's docstring cites; a file Python cannot parse is skipped."""
    assert note_of(build_repo({**spec_with("- X-1: a."), **files})) == UNCITED


@pytest.mark.parametrize(
    ("files", "note"),
    [
        (cited_by_script('"X-1: it"'), "1 spec ID uncited"),
        (cited_by_script('"X-1: it"', call="test"), "1 spec ID uncited"),
        (cited_by_script("`X-1: it`"), "1 spec ID uncited"),
        (cited_by_script("'X-1: it'", path="web/a.spec.js"), "1 spec ID uncited"),
        (cited_by_script('"X-1: it"', path="web/__tests__/a.ts"), "1 spec ID uncited"),
        (cited_by_script('"X-1 and X-2: it"'), None),
    ],
    ids=["it", "test", "template without a hole", "spec file", "tests directory", "and head"],
)
def test_a_script_test_cites_by_the_head_of_its_name(build_repo, files, note):
    """CITE-3."""
    assert note_of(build_repo({**spec_with("- X-1: a.", "- X-2: b."), **files})) == note


@pytest.mark.parametrize(
    "files",
    [
        cited_by_script('"X-1: it"', call="describe"),
        cited_by_script("`X-1: ${name}`"),
        cited_by_script('"X-1: it"', call="it.skip"),
        cited_by_script('"X-1: it"', path="web/a.ts"),
        {"web/a.test.ts": "// X-1: a comment\nit('does it', () => {});\n"},
        {"web/a.test.ts": "it('does it, X-1', () => {});\n"},
    ],
    ids=["describe", "template with a hole", "a modifier", "non-test file", "comment", "later mention"],
)
def test_nothing_else_cites_in_a_script(build_repo, files):
    """CITE-3: `describe`, a template with a hole, a modifier, a comment and a non-test file do not cite."""
    assert note_of(build_repo({**spec_with("- X-1: a."), **files})) == UNCITED


def test_an_id_that_differs_by_a_token_is_another_id(build_repo):
    """CITE-1, CITE-4: `CSRF-01` is not `CSRF-1`."""
    files = {**spec_with("- CSRF-1: a."), **cited_by_python("CSRF-01: it.")}
    assert note_of(build_repo(files)) == "1 spec ID uncited · 1 cited ID undefined or retired"


def test_a_cited_retired_id_counts_with_the_undefined_by_distinct_id(build_repo):
    """CITE-4: two citations of one ID are one undefined ID."""
    tests = {"tests/test_a.py": 'def test_one():\n    """X-1: it."""\n\n\ndef test_two():\n    """X-1: it."""\n'}
    assert note_of(build_repo({**spec_with("- X-1 (retired)."), **tests})) == "1 cited ID undefined or retired"


def test_the_note_names_every_part_in_order(build_repo):
    """CITE-4."""
    spec = spec_with("- X-1: a.", "- X-2: a.", "- Y-1: a.", "- Y-1: a.")
    assert note_of(build_repo({**spec, **cited_by_python("Z-1: it.")})) == (
        "3 spec IDs uncited · 1 cited ID undefined or retired · 1 spec ID defined twice"
    )


def test_the_note_stands_at_the_repository_as_an_open_row_and_is_no_finding(build_repo, capsys):
    """CITE-4, CITE-6: one `open` note under debt at `.`, no finding, extent 0, never measured only."""
    repo = build_repo(spec_with("- X-1: a."))
    (found,) = rule_notes(repo, Rule.SPEC_CITATION)
    code, output = check_output(capsys, repo)
    assert (found.path, found.line, found.event) == (".", None, NoteEvent.OPEN)
    assert [(row.event, row.fact) for row in note_rows(output, ".") if "spec ID" in row.fact] == [("open", UNCITED)]
    debt = next(area for area in check_result(repo).areas if area.area is Area.DEBT)
    verdict = debt.verdicts[-1]
    assert (code, verdict.rule, verdict.findings, verdict.measured_only, verdict.extent) == (0, Rule.SPEC_CITATION, (), False, 0)
    assert [verdict.rule for verdict in debt.verdicts] == [Rule.SIZE, Rule.CLONE, Rule.JOB_TIMEOUT, Rule.TEST_TWIN, Rule.SPEC_CITATION]


def test_a_repository_whose_ids_are_all_cited_gets_no_note(build_repo):
    """CITE-4: with all three counts at zero no note stands."""
    assert note_of(build_repo({**spec_with("- X-1: a.", "- X-2 (retired)."), **cited_by_python("X-1: it.")})) is None


def test_a_markdown_file_that_is_not_utf_8_is_named_and_changes_no_exit_code(build_repo, capsys):
    """CITE-5: the file is left out, the note says so, and the exit stays."""
    repo = build_repo({**spec_with("- X-1: a."), "docs/x.md": ""})
    (repo / "docs/x.md").write_bytes(b"\xff\xfe- X-2: a.\n")
    code, _ = check_output(capsys, repo)
    assert (code, note_of(repo)) == (0, f"{UNCITED} · 1 file not read: docs/x.md")


def test_a_script_test_file_the_lexer_refuses_is_named(build_repo):
    """CITE-5."""
    files = {**spec_with("- X-1: a."), "web/a.test.ts": "it('does it, () => {});\n", "web/b.test.ts": "const a = `\n"}
    assert note_of(build_repo(files)) == f"{UNCITED} · 2 files not read: web/a.test.ts, web/b.test.ts"


def test_unreadable_files_alone_still_give_a_note(build_repo):
    """CITE-5: what the rule cannot read is never silent."""
    repo = build_repo({"docs/x.md": ""})
    (repo / "docs/x.md").write_bytes(b"\xff")
    assert note_of(repo) == "1 file not read: docs/x.md"


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads every file")
def test_sources_that_cannot_be_read_refuse_the_area_and_print_no_note(build_repo):
    """CITE-5."""
    repo = build_repo({**spec_with("- X-1: a."), "tests/test_a.py": "x = 1\n"})
    Path(repo, "tests/test_a.py").chmod(0)
    assert Rule.SPEC_CITATION in refusals(repo)
    assert note_of(repo) is None
