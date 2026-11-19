import pytest
from logline import check as logline_check

from quality.report import NoteEvent
from quality.rules import Area, Rule
from tests.support.check import check_output, check_result, installed_ruff, note_rows, refusals, rule_notes
from tests.support.door import CHECK_WORD, door_in

DECLARING = {
    "a dependency": '[project]\nname = "app"\ndependencies = ["overnightworks-logline>=1"]\n',
    "a git dependency": '[project]\nname = "app"\ndependencies = ["overnightworks-logline @ git+https://github.com/overnightworks/logline@0123456"]\n',
    "an uncanonical name": '[project]\nname = "app"\ndependencies = ["Overnightworks_Logline"]\n',
    "an optional group": '[project]\nname = "app"\n[project.optional-dependencies]\nserve = ["overnightworks-logline"]\n',
    "a dependency group": '[dependency-groups]\ndev = ["overnightworks-logline"]\n',
}
NOT_DECLARING = {
    "another package": '[project]\nname = "app"\ndependencies = ["structlog"]\n',
    "a longer name": '[project]\nname = "app"\ndependencies = ["overnightworks-logline-extra"]\n',
    "a group that includes another group": '[dependency-groups]\nlint = ["ruff"]\ndev = [{include-group = "lint"}]\n',
    "no dependencies": '[project]\nname = "app"\n',
}
BROKEN_NAME = 'import structlog\nlog = structlog.get_logger()\nlog.info("loggedIn")\n'
BROKEN_LINE = 3
BROKEN_RULE = logline_check.Rule.EVENT_NAME_SHAPE
SOURCE = "src/app/service.py"


def covered(source: str = BROKEN_NAME, path: str = SOURCE, declaration: str = DECLARING["a dependency"]) -> dict[str, str]:
    return {"pyproject.toml": declaration, path: source}


def logging_verdicts(repo):
    return [verdict for verdict in check_result(repo).verdicts if verdict.rule is Rule.LOGGING]


@pytest.mark.parametrize("declaration", DECLARING.values(), ids=DECLARING.keys())
def test_a_repository_that_declares_logline_gets_one_open_note_for_a_broken_call(build_repo, declaration):
    """A covered repository's finding is one open note at its place naming the logline rule value."""
    repo = build_repo(covered(declaration=declaration))
    (note,) = rule_notes(repo, Rule.LOGGING)
    assert (note.path, note.line, note.event, note.fact) == (SOURCE, BROKEN_LINE, NoteEvent.OPEN, BROKEN_RULE.value)


@pytest.mark.parametrize("declaration", NOT_DECLARING.values(), ids=NOT_DECLARING.keys())
def test_a_repository_that_does_not_declare_logline_gets_no_row_and_no_file_is_judged(build_repo, declaration):
    repo = build_repo(covered(declaration=declaration))
    assert logging_verdicts(repo) == []


@pytest.mark.parametrize("files", [{}, {"app/pyproject.toml": DECLARING["a dependency"], f"app/{SOURCE}": BROKEN_NAME}], ids=["no pyproject", "a nested pyproject"])
def test_a_repository_without_a_root_declaration_gets_no_row(build_repo, files):
    assert logging_verdicts(build_repo({**files, SOURCE: BROKEN_NAME})) == []


@pytest.mark.parametrize(
    ("path", "judged"),
    [
        (SOURCE, True),
        ("scripts/run_it.py", True),
        ("tests/test_service.py", False),
        ("tests/helpers.py", False),
        ("src/app/conftest.py", False),
        ("src/app/service_test.py", False),
        ("src/app/service.txt", False),
    ],
)
def test_every_python_file_that_is_no_test_file_is_judged(build_repo, path, judged):
    notes = rule_notes(build_repo(covered(path=path)), Rule.LOGGING)
    assert [note.path for note in notes] == ([path] if judged else [])


def test_the_notes_stand_in_path_and_line_order(build_repo):
    twice = BROKEN_NAME + 'log.info("loggedOut")\n'
    repo = build_repo({**covered(twice, "src/b.py"), "src/a.py": BROKEN_NAME})
    assert [(note.path, note.line) for note in rule_notes(repo, Rule.LOGGING)] == [("src/a.py", 3), ("src/b.py", 3), ("src/b.py", 4)]


def test_a_note_leaves_the_code_area_clean_and_prints_its_row_at_its_place(build_repo, capsys):
    repo = build_repo(covered())
    code = door_in(repo, CHECK_WORD, Area.CODE, locate_ruff=installed_ruff)
    (row,) = note_rows(capsys.readouterr().out, f"{SOURCE}:{BROKEN_LINE}")
    assert (code, row.event, row.fact) == (0, NoteEvent.OPEN, BROKEN_RULE.value)


def test_a_clean_covered_repository_gets_a_row_without_a_note(build_repo):
    (verdict,) = logging_verdicts(build_repo(covered("x = 1\n")))
    assert (verdict.notes, verdict.findings, verdict.refusal) == ((), (), None)


def test_a_text_the_check_cannot_parse_refuses_the_rule_naming_the_file(build_repo, capsys):
    """ruff reads a text that nests 250 parentheses; Python's parser refuses it."""
    nested = f"x = {'(' * 250}1{')' * 250}\n"
    repo = build_repo(covered(nested))
    assert SOURCE in refusals(repo)[Rule.LOGGING]
    assert check_output(capsys, repo)[0] == 2


def test_a_text_that_exhausts_the_check_refuses_the_rule_naming_the_file(build_repo, monkeypatch):
    def exhausted(source, path):
        raise RecursionError

    monkeypatch.setattr(logline_check, "check", exhausted)
    assert SOURCE in refusals(build_repo(covered()))[Rule.LOGGING]


def test_any_other_exception_of_the_check_is_not_caught(build_repo, monkeypatch):
    def broken(source, path):
        raise ArithmeticError

    monkeypatch.setattr(logline_check, "check", broken)
    with pytest.raises(ArithmeticError):
        check_result(build_repo(covered()))
