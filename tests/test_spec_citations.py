import os
from pathlib import Path

import pytest

from quality.baseline_file import BASELINE_PATH, render_baseline
from quality.report import FindingEvent, NoteEvent
from quality.rules import Area, Rule
from tests.support.baselines import DEBT_BASELINE_STATES, ceiling, rendered
from tests.support.check import area_fact, check_output, check_result, note_rows, refusals, rule_findings, rule_notes
from tests.support.git_repositories import make_repo

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
    ],
    ids=["retired", "no colon", "parenthesised prose", "backtick fence", "tilde fence", "tilde line in a backtick fence", "short fence in a longer one", "table without rule", "table after heading", "table after prose", "arabic-indic marker", "fullwidth marker", "arabic-indic digits", "fullwidth digits"],
)
def test_nothing_else_defines_an_id(build_repo, spec):
    """CITE-1: retired IDs need no test; a colon-less item, a fence, prose, a table not under a rule and a list marker
    that is no ASCII digit define nothing."""
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
    return {BASELINE_PATH: render_baseline(ceiling({}, frozenset(), spec_citations=frozenset(identifiers)))}


def found(repo: Path) -> list[tuple[str, str, int | None]]:
    """The findings of the rule over `repo` as (event word, path, line), in path and line order."""
    findings = rule_findings(repo, Rule.SPEC_CITATION)
    return sorted(((finding.event.value, finding.path, finding.line) for finding in findings), key=lambda each: (each[1], each[2] or 0, each[0]))


def test_a_new_uncited_id_is_a_finding_at_its_definition_and_a_held_one_is_not(build_repo, capsys):
    """CITE-8: with the table, an ID no test cites and the table lacks is `uncited` at its path and line, and the run is red."""
    repo = build_repo({**spec_with("- X-1: a.", "- X-2: b.", "- X-3: c."), **held("X-1")})
    assert found(repo) == [("uncited", SPEC, 2), ("uncited", SPEC, 3)]
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
    assert [(finding.event, finding.fix) for finding in findings] == [(FindingEvent.CAN_FALL, "quality check --fix")] * 3
    assert ["cited" in findings[0].fact, "retired" in findings[1].fact, "no longer defined" in findings[2].fact] == [True] * 3
    assert [identifier in finding.fact for identifier, finding in zip(["X-1", "X-2", "X-3"], findings, strict=True)] == [True] * 3


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


def test_with_the_table_a_python_test_file_that_does_not_parse_is_skipped(build_repo):
    """CITE-9: size and clone refuse such a file, so this rule does not."""
    repo = build_repo({**spec_with("- X-1: a."), **held("X-1"), "tests/test_a.py": "def test_it(:\n"})
    assert Rule.SPEC_CITATION not in refusals(repo)


@pytest.mark.integration
@pytest.mark.parametrize("state", DEBT_BASELINE_STATES, ids=list(DEBT_BASELINE_STATES))
def test_every_other_line_and_the_exit_stay_as_the_same_run_without_the_note(tmp_path, capsys, state):
    """CITE-4, CITE-6: without the table the note is the only line the rule adds, in every state of the baseline,
    a red debt area included."""
    baseline = {} if DEBT_BASELINE_STATES[state] is None else {BASELINE_PATH: DEBT_BASELINE_STATES[state]}
    (tmp_path / "with").mkdir()
    (tmp_path / "without").mkdir()
    code, with_note = check_output(capsys, make_repo(tmp_path / "with", {**spec_with("- X-1: a."), **baseline}))
    assert note_rows(with_note, ".")
    without_code, without_note = check_output(capsys, make_repo(tmp_path / "without", {**spec_with("Prose."), **baseline}))
    assert not note_rows(without_note, ".")
    kept = [line for line in with_note.splitlines() if " open  " not in line or "spec ID" not in line]
    assert (code, kept) == (without_code, without_note.splitlines())
