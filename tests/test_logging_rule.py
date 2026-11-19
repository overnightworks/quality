from dataclasses import dataclass

import pytest
from logline import check as logline_check

from quality.report import NoteEvent
from quality.rules import Area, Rule
from tests.support.check import check_output, check_result, installed_ruff, note_rows, refusals, rule_notes
from tests.support.door import CHECK_WORD, door_in


@dataclass(frozen=True)
class BrokenCall:
    """A source file whose third line breaks the event-name rule, and where the rule's note stands."""

    path: str = "src/app/service.py"
    source: str = 'import structlog\nlog = structlog.get_logger()\nlog.info("loggedIn")\n'
    line: int = 3
    rule: str = logline_check.Rule.EVENT_NAME_SHAPE.value


@pytest.fixture
def broken() -> BrokenCall:
    return BrokenCall()


@pytest.fixture
def covered(broken):
    """The files of a repository that declares logline and holds `broken`, each part replaceable."""

    def build(source: str | None = None, path: str | None = None, declaration: str | None = None) -> dict[str, str]:
        return {
            "pyproject.toml": declaration or '[project]\nname = "app"\ndependencies = ["overnightworks-logline>=1"]\n',
            path or broken.path: broken.source if source is None else source,
        }

    return build


def logging_verdicts(repo):
    return [verdict for verdict in check_result(repo).verdicts if verdict.rule is Rule.LOGGING]


def project(dependencies: str) -> str:
    return f'[project]\nname = "app"\ndependencies = [{dependencies}]\n'


@pytest.mark.parametrize(
    "declaration",
    [
        pytest.param(project('"overnightworks-logline>=1"'), id="a dependency"),
        pytest.param(project('"overnightworks-logline @ git+https://github.com/overnightworks/logline@0123456"'), id="a git dependency"),
        pytest.param(project('"Overnightworks_Logline"'), id="an uncanonical name"),
        pytest.param('[project]\nname = "app"\n[project.optional-dependencies]\nserve = ["overnightworks-logline"]\n', id="an optional group"),
        pytest.param('[dependency-groups]\ndev = ["overnightworks-logline"]\n', id="a dependency group"),
    ],
)
def test_a_repository_that_declares_logline_gets_one_open_note_for_a_broken_call(build_repo, covered, broken, declaration):
    """A covered repository's finding is one open note at its place naming the logline rule value."""
    repo = build_repo(covered(declaration=declaration))
    (note,) = rule_notes(repo, Rule.LOGGING)
    assert (note.path, note.line, note.event, note.fact) == (broken.path, broken.line, NoteEvent.OPEN, broken.rule)


@pytest.mark.parametrize(
    "declaration",
    [
        pytest.param(project('"structlog"'), id="another package"),
        pytest.param(project('"overnightworks-logline-extra"'), id="a longer name"),
        pytest.param('[dependency-groups]\nlint = ["ruff"]\ndev = [{include-group = "lint"}]\n', id="a group that includes another group"),
        pytest.param('[project]\nname = "app"\n', id="no dependencies"),
    ],
)
def test_a_repository_that_does_not_declare_logline_gets_no_row_and_no_file_is_judged(build_repo, covered, declaration):
    repo = build_repo(covered(declaration=declaration))
    assert logging_verdicts(repo) == []


@pytest.mark.parametrize("nested", [False, True], ids=["no pyproject", "a nested pyproject"])
def test_a_repository_without_a_root_declaration_gets_no_row(build_repo, covered, broken, nested):
    files = {f"app/{name}": text for name, text in covered().items()} if nested else {}
    assert logging_verdicts(build_repo({**files, broken.path: broken.source})) == []


@pytest.mark.parametrize(
    ("path", "judged"),
    [
        ("src/app/service.py", True),
        ("scripts/run_it.py", True),
        ("tests/test_service.py", False),
        ("tests/helpers.py", False),
        ("src/app/conftest.py", False),
        ("src/app/service_test.py", False),
        ("src/app/service.txt", False),
    ],
)
def test_every_python_file_that_is_no_test_file_is_judged(build_repo, covered, path, judged):
    notes = rule_notes(build_repo(covered(path=path)), Rule.LOGGING)
    assert [note.path for note in notes] == ([path] if judged else [])


def test_the_notes_stand_in_path_and_line_order(build_repo, covered, broken):
    twice = broken.source + 'log.info("loggedOut")\n'
    repo = build_repo({**covered(twice, "src/b.py"), "src/a.py": broken.source})
    assert [(note.path, note.line) for note in rule_notes(repo, Rule.LOGGING)] == [("src/a.py", 3), ("src/b.py", 3), ("src/b.py", 4)]


def test_a_note_leaves_the_code_area_clean_and_prints_its_row_at_its_place(build_repo, covered, broken, capsys):
    repo = build_repo(covered())
    code = door_in(repo, CHECK_WORD, Area.CODE, locate_ruff=installed_ruff)
    (row,) = note_rows(capsys.readouterr().out, f"{broken.path}:{broken.line}")
    assert (code, row.event, row.fact) == (0, NoteEvent.OPEN, broken.rule)


def test_a_clean_covered_repository_gets_a_row_without_a_note(build_repo, covered):
    (verdict,) = logging_verdicts(build_repo(covered("x = 1\n")))
    assert (verdict.notes, verdict.findings, verdict.refusal) == ((), (), None)


def test_a_file_that_starts_with_a_byte_order_mark_is_judged_as_python_reads_it(build_repo, covered, broken, capsys):
    """Python, ruff and the file readers accept the mark; the rule gets its note and the exit stays 0."""
    repo = build_repo(covered("\ufeff" + broken.source))
    code = door_in(repo, CHECK_WORD, Area.CODE, locate_ruff=installed_ruff)
    (row,) = note_rows(capsys.readouterr().out, f"{broken.path}:{broken.line}")
    assert (code, row.event, row.fact) == (0, NoteEvent.OPEN, broken.rule)


def test_a_text_the_check_cannot_parse_refuses_the_rule_naming_the_file(build_repo, covered, broken, capsys):
    """ruff reads a text that nests 250 parentheses; Python's parser refuses it."""
    nested = f"x = {'(' * 250}1{')' * 250}\n"
    repo = build_repo(covered(nested))
    assert broken.path in refusals(repo)[Rule.LOGGING]
    assert check_output(capsys, repo)[0] == 2


def test_a_root_pyproject_that_cannot_be_read_refuses_the_rule_naming_the_file(build_repo, broken, capsys):
    """The declaration is read before any file, in every repository, so a broken root `pyproject.toml` refuses the code area."""
    repo = build_repo({"pyproject.toml": "[project\n", broken.path: broken.source})
    assert "pyproject.toml" in refusals(repo)[Rule.LOGGING]
    assert check_output(capsys, repo)[0] == 2


def test_a_text_that_exhausts_the_check_refuses_the_rule_naming_the_file(build_repo, covered, broken, monkeypatch):
    def exhausted(source, path):
        raise RecursionError

    monkeypatch.setattr(logline_check, "check", exhausted)
    assert broken.path in refusals(build_repo(covered()))[Rule.LOGGING]


def test_any_other_exception_of_the_check_is_not_caught(build_repo, covered, monkeypatch):
    def broken_check(source, path):
        raise ArithmeticError

    monkeypatch.setattr(logline_check, "check", broken_check)
    with pytest.raises(ArithmeticError):
        check_result(build_repo(covered()))
