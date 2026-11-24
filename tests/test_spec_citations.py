import os
from pathlib import Path

import pytest

from quality.baseline_file import BASELINE_PATH
from quality.config_entries import CONFIG_PATH
from quality.report import FindingEvent, NoteEvent
from quality.rules import Area, Rule
from tests.support.baselines import DEBT_BASELINE_STATES, holding_spec_ids, rendered, spec_table
from tests.support.check import (
    area_fact,
    check_output,
    check_result,
    note_rows,
    refusals,
    rule_findings,
    rule_notes,
    runs_apart_by_the_note,
)
from tests.support.specs import SPEC, declaring, foreign_entry, implemented_entry, spec_with

UNCITED = "1 spec ID uncited"


def note_of(repo: Path) -> str | None:
    """The fact of the one note the rule gives over `repo`, None where it gives none."""
    notes = rule_notes(repo, Rule.SPEC_CITATION)
    assert len(notes) <= 1
    return notes[0].fact if notes else None


def cited_by_python(docstring: str, name: str = "test_it") -> dict[str, str]:
    return {"tests/test_a.py": f'def {name}():\n    """{docstring}"""\n'}


def cited_by_script(title: str, call: str = "it", path: str = "web/a.test.ts") -> dict[str, str]:
    return {path: f"{call}({title}, () => {{}});\n"}


def id_table(header: str, second: str = "Text") -> list[str]:
    """A table with the header `header` and `second` that holds one row with an ID and one without."""
    return [f"| {header} | {second} |", "| --- | --- |", "| X-1 | a row |", "| not an id | a row |"]


@pytest.mark.parametrize(
    "spec",
    [
        ["- X-1: a rule."],
        ["* X-1: a rule."],
        ["+ X-1: a rule."],
        ["1. X-1: a rule."],
        ["1) X-1: a rule."],
        ["- Parent:", "    - X-1: nested."],
        ["\ufeff- X-1: a rule."],
    ],
    ids=["dash", "star", "plus", "number and dot", "number and parenthesis", "nested item", "byte order mark"],
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
        ["```", "~~~", "- X-1: an example.", "```"],
        ["````markdown", "```", "- X-1: an example.", "```", "- X-2: still an example.", "````"],
        ["# Rules", "", "| X-1 | row |"],
        ["- Rules:", "", "# Table", "", "| X-1 | row |"],
        ["- Rules:", "", "Prose.", "", "| X-1 | row |"],
        ["\u0662. X-1: an Arabic-Indic list marker."],
        ["\uff12) X-1: a fullwidth list marker."],
        ["- X-\u0662\u0666: Arabic-Indic digits."],
        ["- X-\uff12\uff16: fullwidth digits."],
        ["# Rules", "", *id_table("Id")],
        ["# Rules", "", *id_table("IDs")],
        ["# Rules", "", *id_table("ID number")],
        ["# Rules", "", *id_table("Name", "ID")],
        ["```", *id_table("ID"), "```"],
    ],
    ids=["retired", "no colon", "parenthesised prose", "backtick fence", "tilde fence", "tilde line in a backtick fence", "short fence in a longer one", "table without rule", "table after heading", "table after prose", "arabic-indic marker", "fullwidth marker", "arabic-indic digits", "fullwidth digits", "header Id", "header IDs", "header with other text", "ID in the second column", "ID table in a fence"],
)
def test_nothing_else_defines_an_id(build_repo, spec):
    """CITE-1: retired IDs need no test; a colon-less item, a fence, prose, a table not under a rule whose header's first
    cell is not exactly `ID` and a list marker that is no ASCII digit define nothing."""
    assert note_of(build_repo(spec_with(*spec))) is None


@pytest.mark.parametrize("header", ["ID", "Name", "Id"])
def test_a_table_row_under_a_rule_defines_its_first_cell(build_repo, header):
    """CITE-1: whatever the table's header says."""
    spec = ["- X-1: a rule:", "", f"  | {header} | Text |", "  | --- | --- |", "  | X-2 | a row |", "  | not an id | a row |"]
    assert note_of(build_repo(spec_with(*spec))) == "2 spec IDs uncited"


@pytest.mark.parametrize(
    "spec",
    [
        id_table("ID"),
        ["# Rules", "", *id_table("ID")],
        ["Prose.", "", *id_table("ID")],
        ["- Rules:", "", "# Table", "", *id_table("ID")],
        ["  " + line for line in id_table("ID")],
        [*id_table("ID"), "", "| Name | Text |", "| --- | --- |", "| X-2 | a row |"],
    ],
    ids=["start of the document", "under a heading", "after prose", "after a rule and a heading", "indented", "a later table with another header"],
)
def test_a_table_whose_header_opens_with_id_defines_its_rows_wherever_it_stands(build_repo, spec):
    """CITE-1: a row whose first cell is no ID and a later table with another header define nothing."""
    assert note_of(build_repo(spec_with(*spec))) == UNCITED


def test_only_tracked_markdown_files_that_the_tree_holds_define(build_repo):
    """CITE-1, CITE-5: an untracked `.md` file defines nothing, and a deleted one defines nothing and is named unread."""
    repo = build_repo({**spec_with("- X-1: a rule."), "docs/gone.md": "- X-2: a rule.\n"})
    (repo / "docs/gone.md").unlink()
    (repo / "docs/untracked.md").write_text("- X-3: a rule.\n")
    assert note_of(repo) == f"{UNCITED} · 1 file not read: docs/gone.md"


@pytest.mark.parametrize(
    "files",
    [
        {"a.md": "- X-1: a rule.\n", "b.md": "- X-1: a rule.\n"},
        {"a.md": "- X-1: a rule.\n\n  | X-1 | row |\n"},
        {"a.md": "- X-1: a rule.\n\n# Table\n\n| ID | Text |\n| --- | --- |\n| X-1 | row |\n"},
    ],
    ids=["two files", "an item and a row", "an item and a row of an ID table"],
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
        ("X-1, and X-2: it.", UNCITED),
        ("X-1, X-2 and X-3: it.", None),
        ("Does it. X-1.", "3 spec IDs uncited"),
        ("Does it.\n\n    X-1: it.", "3 spec IDs uncited"),
    ],
    ids=["head", "Spec label", "comma head", "and head", "comma and head", "mixed head", "later mention", "later line"],
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
        {"web/a.test.ts": "expect(/x/.test('X-1: it')).toBe(true);\n"},
        {"web/a.test.ts": "suite.it('X-1: it', () => {});\n"},
    ],
    ids=["describe", "template with a hole", "a modifier", "non-test file", "comment", "later mention", "method test", "method it"],
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


def test_without_the_table_the_note_stands_at_the_repository_as_an_open_row_and_is_no_finding(build_repo, capsys):
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


def test_a_script_test_file_that_is_not_utf_8_is_named(build_repo):
    """CITE-5: the file is left out and named, so the rule changes no exit code."""
    repo = build_repo({**spec_with("- X-1: a."), "web/c.test.ts": ""})
    (repo / "web/c.test.ts").write_bytes(b"\xff")
    assert note_of(repo) == f"{UNCITED} · 1 file not read: web/c.test.ts"


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads every file")
def test_a_markdown_file_the_filesystem_refuses_is_named_and_does_not_refuse_the_area(build_repo):
    """CITE-5: the file is left out and the note says so."""
    repo = build_repo({**spec_with("- X-1: a."), "docs/x.md": "- X-2: a.\n"})
    (repo / "docs/x.md").chmod(0)
    assert Rule.SPEC_CITATION not in refusals(repo)
    assert note_of(repo) == f"{UNCITED} · 1 file not read: docs/x.md"


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


def held(*identifiers: str) -> dict[str, str]:
    """The baseline of a repository whose `[spec_citations]` table holds `identifiers` as uncited."""
    return {BASELINE_PATH: holding_spec_ids(*identifiers)}


def found(repo: Path) -> list[tuple[str, str, int | None]]:
    """The findings of the rule over `repo` as (event word, path, line), in path and line order."""
    findings = rule_findings(repo, Rule.SPEC_CITATION)
    return sorted(((finding.event.value, finding.path, finding.line) for finding in findings), key=lambda each: (each[1], each[2] or 0, each[0]))


def test_a_new_uncited_id_is_a_finding_at_its_definition_and_a_held_one_is_not(build_repo, capsys):
    """CITE-8: with the table, an ID no test cites and the table lacks is `uncited` at its path and line, and the run is red."""
    repo = build_repo({**spec_with("- X-1: a.", "- X-2: b.", "- X-3: c."), **held("X-1")})
    assert found(repo) == [("uncited", SPEC, 2), ("uncited", SPEC, 3)]
    assert check_output(capsys, repo)[0] == 1


def test_a_row_of_an_id_table_that_no_test_cites_is_uncited_at_its_line(build_repo, capsys):
    """CITE-1, CITE-8: a defined row is the finding an uncited ID gets, and a held one is not."""
    repo = build_repo({**spec_with("# Rules", "", *id_table("ID")[:3], "| X-2 | a row |"), **held("X-1")})
    assert found(repo) == [("uncited", SPEC, 6)]
    assert check_output(capsys, repo)[0] == 1


@pytest.mark.parametrize(
    "files", [{"docs/a.md": "- X-1: a.\n"}, {"docs/other/b.md": "\n\n- X-1: a.\n"}], ids=["one file", "another file and line"]
)
def test_a_held_id_is_held_wherever_it_is_defined(build_repo, capsys, files):
    """CITE-7, CITE-8: the ID alone is the key, so the file and the line of its definition change nothing."""
    repo = build_repo({**files, **held("X-1")})
    assert (found(repo), check_output(capsys, repo)[0]) == ([], 0)


@pytest.mark.parametrize(
    ("spec", "table", "event"),
    [(["- X-2: b."], held("X-2"), "undefined"), (["- X-1 (retired)."], held(), "retired")],
    ids=["defined nowhere", "retired"],
)
@pytest.mark.parametrize(
    ("citing", "path", "line"),
    [
        ({"tests/test_a.py": 'def test_one():\n    pass\n\n\ndef test_two():\n    """X-1: it."""\n'}, "tests/test_a.py", 5),
        ({"web/a.test.ts": "// a comment\nit('X-1: it', () => {});\n"}, "web/a.test.ts", 2),
    ],
    ids=["a Python test", "a script test"],
)
def test_a_citation_of_an_undefined_or_retired_id_is_a_finding_at_the_citing_test(build_repo, spec, table, event, citing, path, line):
    """CITE-8: `undefined` for an ID defined nowhere and `retired` for a retired one, at the test that cites it."""
    repo = build_repo({**spec_with(*spec), **citing, **table})
    assert found(repo) == [(event, path, line)]


def test_every_test_that_cites_an_undefined_id_is_a_finding(build_repo):
    """CITE-8."""
    tests = {"tests/test_a.py": 'def test_one():\n    """Z-1: it."""\n\n\ndef test_two():\n    """Z-1: it."""\n'}
    assert found(build_repo({**spec_with("- X-1 (retired)."), **tests, **held()})) == [
        ("undefined", "tests/test_a.py", 1),
        ("undefined", "tests/test_a.py", 5),
    ]


def test_every_definition_after_the_first_is_a_duplicate_naming_the_first_and_is_never_held(build_repo):
    """CITE-8: an ID defined in two files and as a table row is `duplicate` at each later definition, held or not."""
    files = {"docs/a.md": "- X-1: a.\n", "docs/b.md": "\n- X-1: b.\n\n  | X-1 | row |\n", **held("X-1")}
    repo = build_repo(files)
    assert found(repo) == [("duplicate", "docs/b.md", 2), ("duplicate", "docs/b.md", 4)]
    assert all("docs/a.md:1" in finding.fact and "X-1" in finding.fact for finding in rule_findings(repo, Rule.SPEC_CITATION))


def test_an_undefined_citation_stays_red_though_the_table_lists_the_id(build_repo):
    """CITE-8: none of undefined, retired and duplicate is held; the listed ID that is defined nowhere can fall as well."""
    repo = build_repo({**spec_with("- X-1: a."), **cited_by_python("Z-1: it."), **held("X-1", "Z-1")})
    assert found(repo) == [("can fall", BASELINE_PATH, None), ("undefined", "tests/test_a.py", 1)]


def test_a_held_id_that_is_cited_retired_or_no_longer_defined_can_fall(build_repo):
    """CITE-8: each such ID is a `can fall` with `quality check --fix`, said by what happened to it."""
    spec = spec_with("- X-1: a.", "- X-2 (retired).", "- X-4: d.")
    repo = build_repo({**spec, **cited_by_python("X-1: it."), **held("X-1", "X-2", "X-3", "X-4")})
    findings = rule_findings(repo, Rule.SPEC_CITATION)
    assert len(findings) == 3
    assert {(finding.event, finding.fix) for finding in findings} == {(FindingEvent.CAN_FALL, "quality check --fix")}
    said = [("X-1", "cited"), ("X-2", "retired"), ("X-3", "no longer defined")]
    assert [(identifier in finding.fact, word in finding.fact) for (identifier, word), finding in zip(said, findings, strict=True)] == [(True, True)] * 3


def test_with_the_table_the_debt_row_counts_the_held_ids_and_no_note_stands(build_repo, capsys):
    """CITE-8: the rule's extent is the held IDs, it is not measured only, and the note is gone."""
    repo = build_repo({**spec_with("- X-1: a.", "- X-2: b.", "- X-3: c."), **cited_by_python("X-3: it."), **held("X-1", "X-2")})
    code, out = check_output(capsys, repo)
    verdict = next(area for area in check_result(repo).areas if area.area is Area.DEBT).verdicts[-1]
    assert (code, rule_notes(repo, Rule.SPEC_CITATION), verdict.measured_only, verdict.extent) == (0, [], False, 2)
    assert area_fact(out, "debt").endswith(", 2 uncited spec IDs")


def test_a_baseline_without_the_table_leaves_the_rule_report_only(build_repo, capsys):
    """CITE-6: only the table enforces; size tables alone leave the note and no finding."""
    repo = build_repo({**spec_with("- X-1: a."), BASELINE_PATH: rendered("[file_lines]\n")})
    assert (note_of(repo), found(repo), check_output(capsys, repo)[0]) == (UNCITED, [], 0)


@pytest.mark.parametrize(
    ("files", "unread"),
    [
        ({"docs/x.md": b"\xff"}, "docs/x.md"),
        ({"web/a.test.ts": b"const a = `\n"}, "web/a.test.ts"),
    ],
    ids=["a Markdown file that is not UTF-8", "a script test the lexer refuses"],
)
def test_with_the_table_a_file_the_rule_cannot_read_makes_it_cannot_judge(build_repo, capsys, files, unread):
    """CITE-9: an unread file can hide a definition or a citation, so the rule names it and the run exits 2."""
    repo = build_repo({**spec_with("- X-1: a."), **held("X-1"), **{path: "" for path in files}})
    for path, content in files.items():
        (repo / path).write_bytes(content)
    assert unread in refusals(repo)[Rule.SPEC_CITATION]
    assert check_output(capsys, repo)[0] == 2


@pytest.mark.parametrize("name", ["docs/x.md", "web/a.test.ts"], ids=["a Markdown file", "a script test"])
def test_with_the_table_a_tracked_file_the_tree_lacks_makes_it_cannot_judge(build_repo, capsys, name):
    """CITE-9: a sparse checkout or a deletion hides what the file defines or cites, so the rule names it and the run exits 2."""
    repo = build_repo({**spec_with("- X-1: a."), **held("X-1"), name: ""})
    (repo / name).unlink()
    assert name in refusals(repo)[Rule.SPEC_CITATION]
    assert check_output(capsys, repo)[0] == 2


def test_a_baseline_with_the_table_and_without_clones_counts_the_held_ids_in_the_debt_row(build_repo, capsys):
    """CITE-8: the extent names the held IDs although the clone table is missing and the area is only partly measured."""
    repo = build_repo({**spec_with("- X-1: a."), BASELINE_PATH: rendered(f"[file_lines]\n\n{spec_table('X-1')}")})
    code, out = check_output(capsys, repo)
    assert (code, area_fact(out, "debt").split(" · ")[0].endswith("1 uncited spec ID")) == (0, True), out


def test_with_the_table_a_python_test_file_that_does_not_parse_is_skipped(build_repo):
    """CITE-9: size and clone refuse such a file, so this rule does not."""
    repo = build_repo({**spec_with("- X-1: a."), **held("X-1"), "tests/test_a.py": "def test_it(:\n"})
    assert Rule.SPEC_CITATION not in refusals(repo)


@pytest.mark.integration
@pytest.mark.parametrize("state", DEBT_BASELINE_STATES, ids=list(DEBT_BASELINE_STATES))
def test_every_other_line_and_the_exit_stay_as_the_same_run_without_the_note(tmp_path, capsys, state):
    """CITE-4, CITE-6: without the table the note is the only line the rule adds, in every state of the baseline,
    a red debt area included."""
    with_note, without_note = runs_apart_by_the_note(tmp_path, capsys, state, spec_with("- X-1: a."), spec_with("Prose."), "spec ID")
    assert with_note == without_note


CALLING = "docs/calling.md"
OWNED = {CALLING: "- CALLING-1: a rule.\n- CALLING-2: another.\n"}
BOTH_TABLES = {"with the table": held(), "without it": {}}


@pytest.mark.parametrize("table", BOTH_TABLES.values(), ids=BOTH_TABLES.keys())
def test_a_citation_of_an_id_defined_nowhere_is_not_judged_where_its_prefix_is_declared_foreign(build_repo, table):
    """CITE-14: no `undefined` finding and no part of the note, with the table and without it."""
    files = {**spec_with("- X-1: a."), **cited_by_python("X-1 and LANG-7: it."), **declaring(foreign_entry("LANG")), **table}
    repo = build_repo(files)
    assert (found(repo), note_of(repo)) == ([], None)


@pytest.mark.parametrize(
    ("entries", "expected"),
    [
        ((), [("undefined", "tests/test_a.py", 1)]),
        ((foreign_entry("REQ"),), [("undefined", "tests/test_a.py", 1)]),
        ((foreign_entry("REQ-AUTH"),), []),
    ],
    ids=["no declaration", "a shorter prefix", "the whole prefix"],
)
def test_a_prefix_is_compared_exactly_to_the_part_before_the_last_number(build_repo, entries, expected):
    """CITE-14: `REQ` does not cover `REQ-AUTH-3`."""
    repo = build_repo({**cited_by_python("REQ-AUTH-3: it."), **spec_with("- X-1 (retired)."), **declaring(*entries), **held()})
    assert [row for row in found(repo) if row[0] != "unused"] == expected


CONFLICTS = {
    "a live ID of the prefix defined here": (["- LANG-7: a."], "", [("uncited", SPEC, 1), ("conflict", CONFIG_PATH, None)]),
    "a retired ID of the prefix defined here and cited": (
        ["- LANG-7 (retired)."], "LANG-7: it.", [("conflict", CONFIG_PATH, None), ("retired", "tests/test_a.py", 1)]
    ),
}


@pytest.mark.parametrize(("spec", "docstring", "expected"), CONFLICTS.values(), ids=CONFLICTS.keys())
def test_a_declared_prefix_the_tree_defines_is_a_conflict_and_its_ids_are_judged_as_any_other(build_repo, spec, docstring, expected):
    """CITE-14, CITE-19: with the table, the declaration is `conflict` at the config, and the local ID is judged locally."""
    files = {**spec_with(*spec), **(cited_by_python(docstring) if docstring else {}), **declaring(foreign_entry("LANG")), **held()}
    assert found(build_repo(files)) == expected


def test_a_declared_prefix_the_tree_defines_exempts_no_citation_of_a_deleted_id(build_repo):
    """CITE-14, CITE-19: a repository cannot silence the undefined citations of its own prefix."""
    files = {**spec_with("- ST-1: a."), **cited_by_python("ST-1 and ST-9: it."), **declaring(foreign_entry("ST")), **held()}
    assert found(build_repo(files)) == [("conflict", CONFIG_PATH, None), ("undefined", "tests/test_a.py", 1)]


def test_a_conflict_names_the_first_definition_of_the_prefix_and_the_way_out(build_repo):
    """CITE-19."""
    files = {**spec_with("- ST-3: a.", "- ST-1: b."), **cited_by_python("ST-1 and ST-3: it."), **declaring(foreign_entry("ST")), **held()}
    (finding,) = rule_findings(build_repo(files), Rule.SPEC_CITATION)
    assert (finding.fact, finding.fix) == (
        f"[[spec_citations.foreign]] prefix ST is defined here, first at {SPEC}:1",
        "delete its [[spec_citations.foreign]] entry from quality/config.toml, or give the local IDs a prefix of their own",
    )


def test_without_the_table_a_conflict_is_no_finding_and_the_local_id_stays_in_the_note(build_repo):
    """CITE-6, CITE-19."""
    repo = build_repo({**spec_with("- LANG-7: a."), **declaring(foreign_entry("LANG"))})
    assert (found(repo), note_of(repo)) == ([], UNCITED)


def test_a_declared_prefix_no_foreign_citation_matches_is_unused_with_the_table(build_repo):
    """CITE-17: `unused` at the config, fixed by deleting the entry."""
    files = {**spec_with("- X-1: a."), **cited_by_python("X-1: it."), **declaring(foreign_entry("LANG")), **held()}
    (finding,) = rule_findings(build_repo(files), Rule.SPEC_CITATION)
    assert (finding.event, finding.path, finding.fact, finding.fix) == (
        FindingEvent.UNUSED, CONFIG_PATH, "[[spec_citations.foreign]] prefix LANG matches no citation",
        "delete its [[spec_citations.foreign]] entry from quality/config.toml",
    )


def test_without_the_table_a_declared_prefix_no_citation_matches_is_not_judged(build_repo):
    """CITE-17, CITE-6."""
    repo = build_repo({**spec_with("- X-1: a."), **cited_by_python("X-1: it."), **declaring(foreign_entry("LANG"))})
    assert (found(repo), note_of(repo)) == ([], None)


@pytest.mark.parametrize(
    ("table", "declared", "findings", "note"),
    [
        (held(), True, [], None),
        ({}, True, [], None),
        (held(), False, [("uncited", CALLING, 1), ("uncited", CALLING, 2)], None),
        ({}, False, [], "2 spec IDs uncited"),
    ],
    ids=["declared, with the table", "declared, without it", "undeclared, with the table", "undeclared, without it"],
)
def test_an_id_of_a_declared_document_is_neither_uncited_nor_counted(build_repo, table, declared, findings, note):
    """CITE-15: the declared document's ID is no `uncited` finding with the table and no part of the note without it."""
    repo = build_repo({**OWNED, **(declaring(implemented_entry(CALLING)) if declared else {}), **table})
    assert (found(repo), note_of(repo)) == (findings, note)


def test_an_id_of_a_declared_document_that_the_repositorys_own_test_cites_stays_cited(build_repo):
    """CITE-15: a held ID of the document that a test cites falls as `is cited`, not as left to its implementers."""
    files = {**OWNED, **cited_by_python("CALLING-1: it."), **declaring(implemented_entry(CALLING)), **held("CALLING-1")}
    (finding,) = rule_findings(build_repo(files), Rule.SPEC_CITATION)
    assert (finding.event, finding.fact) == (FindingEvent.CAN_FALL, "spec ID CALLING-1 is cited")


def test_a_held_id_of_a_declared_document_can_fall_as_left_to_its_implementers(build_repo):
    """CITE-15."""
    repo = build_repo({**OWNED, **declaring(implemented_entry(CALLING)), **held("CALLING-1")})
    (finding,) = rule_findings(repo, Rule.SPEC_CITATION)
    assert (finding.event, finding.fact, finding.fix) == (
        FindingEvent.CAN_FALL, "spec ID CALLING-1 is left to its implementers", "quality check --fix"
    )


JUDGED_IN_A_DECLARED_DOCUMENT = {
    "a second definition": ({"docs/a.md": "- CALLING-1: first.\n", CALLING: "- CALLING-1: again.\n- CALLING-2: b.\n"}, {}, [("uncited", "docs/a.md", 1), ("duplicate", CALLING, 1)]),
    "a duplicate inside it": ({CALLING: "- CALLING-1: a.\n- CALLING-1: b.\n"}, {}, [("duplicate", CALLING, 2)]),
    "a retired citation": ({CALLING: "- CALLING-1 (retired).\n- CALLING-2: b.\n"}, cited_by_python("CALLING-1: it."), [("retired", "tests/test_a.py", 1)]),
    "an undefined citation": ({CALLING: "- CALLING-1: a.\n"}, cited_by_python("CALLING-9: it."), [("undefined", "tests/test_a.py", 1)]),
}


@pytest.mark.parametrize(("files", "tests", "expected"), JUDGED_IN_A_DECLARED_DOCUMENT.values(), ids=JUDGED_IN_A_DECLARED_DOCUMENT.keys())
def test_a_declared_document_silences_neither_a_duplicate_nor_a_retired_or_undefined_citation(build_repo, files, tests, expected):
    """CITE-15: only the uncited ID is left to the implementers."""
    repo = build_repo({**files, **tests, **declaring(implemented_entry(CALLING)), **held()})
    assert found(repo) == expected


UNUSED_DOCUMENTS = {
    "a path no file holds": {SPEC: "- X-1: a.\n"},
    "a file with a retired ID only": {CALLING: "- CALLING-1 (retired).\n"},
    "a file with no ID": {CALLING: "Prose.\n"},
}


@pytest.mark.parametrize("table", BOTH_TABLES.values(), ids=BOTH_TABLES.keys())
@pytest.mark.parametrize("files", UNUSED_DOCUMENTS.values(), ids=UNUSED_DOCUMENTS.keys())
def test_a_declared_document_that_defines_no_live_id_is_unused_with_the_table_only(build_repo, files, table):
    """CITE-17, CITE-6: `unused` at the path with the table, fixed by deleting the entry, and nothing without it."""
    repo = build_repo({**files, **declaring(implemented_entry(CALLING)), **table})
    unused = [finding for finding in rule_findings(repo, Rule.SPEC_CITATION) if finding.event is FindingEvent.UNUSED]
    assert [(finding.path, finding.fact, finding.fix) for finding in unused] == (
        [
            (
                CALLING, "[[spec_citations.implemented]] matches no tracked file that defines a live spec ID",
                "delete its [[spec_citations.implemented]] entry from quality/config.toml",
            )
        ]
        if table
        else []
    )


@pytest.mark.parametrize("table", BOTH_TABLES.values(), ids=BOTH_TABLES.keys())
def test_a_malformed_declaration_refuses_the_rule(build_repo, table):
    """CITE-16: the rule reads the declarations, so a config it cannot read leaves it unjudged, as every rule that reads the config."""
    repo = build_repo({**spec_with("- X-1: a."), **declaring(implemented_entry("docs/")), **table})
    assert Rule.SPEC_CITATION in refusals(repo)
