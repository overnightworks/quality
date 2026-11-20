"""The `logging` rule: a repository whose root `pyproject.toml` declares `overnightworks-logline` has each tracked Python file
that is no test file judged by logline's own checker, and each rule it reports stands as one `open` note. The rule only reports."""

from collections.abc import Mapping
from pathlib import Path, PurePosixPath

from logline import check as logline_check
from packaging.utils import canonicalize_name

from quality import catalog_rules
from quality.ratchet import Language
from quality.report import Note, NoteEvent, RuleResult, Unparsable

LOGLINE_DISTRIBUTION = "overnightworks-logline"


def declares_logline(root: Path, tracked: list[str]) -> bool:
    """Whether a dependency list of the tracked root `pyproject.toml` names `overnightworks-logline`."""
    pyproject = catalog_rules.read_root_pyproject(root, tracked)
    return pyproject is not None and any(
        canonicalize_name(requirement.name) == LOGLINE_DISTRIBUTION for requirement in pyproject.requirements
    )


def check_logging(texts: Mapping[str, str]) -> RuleResult:
    """The notes of every file in `texts`, the text as Python reads it, that is no test file, by path and in source order; the extent is the files judged."""
    judged = sorted(name for name in texts if not Language.PYTHON.is_test_file(PurePosixPath(name)))
    return RuleResult([], len(judged), False, tuple(note for name in judged for note in _notes(name, texts[name])))


def _notes(name: str, text: str) -> list[Note]:
    try:
        findings = logline_check.check(text, PurePosixPath(name))
    except (SyntaxError, RecursionError) as error:
        raise Unparsable(f"logline cannot read {name} ({type(error).__name__})") from error
    return [Note(name, NoteEvent.OPEN, finding.rule.value, finding.line) for finding in findings]
