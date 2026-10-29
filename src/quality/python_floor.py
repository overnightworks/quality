"""The `python-floor` rule: every Python project's `requires-python` names a floor of the target version or higher,
or is declared with `[[python.allow]]`."""

from collections.abc import Sequence
from dataclasses import dataclass

from packaging.specifiers import Specifier, SpecifierSet
from packaging.version import Version

from quality import repository
from quality.config import CONFIG_PATH, PythonAllowance
from quality.report import Finding, Unparsable
from quality.rules import Rule

PYTHON_TARGET = Version("3.14")
LOWER_BOUND_OPERATORS = frozenset({">=", ">", "~=", "==", "==="})


@dataclass(frozen=True)
class FloorResult:
    """The python-floor findings and one report line per allowed file."""

    findings: list[Finding]
    reports: tuple[str, ...]


def _lower_bound(clause: Specifier) -> Version:
    """The version a clause sets as a lower bound; `==3.14.*` counts as 3.14, while `===` takes its version as written."""
    return Version(clause.version.removesuffix(".*") if clause.operator == "==" else clause.version)


def _floor(path: str, requires_python: str) -> Version | None:
    """The highest lower bound among the clauses, None when no clause sets one."""
    try:
        bounds = [_lower_bound(clause) for clause in SpecifierSet(requires_python) if clause.operator in LOWER_BOUND_OPERATORS]
    except ValueError as error:
        raise Unparsable(f'{path} has requires-python "{requires_python}", which is no PEP 440 version specifier ({error})') from error
    return max(bounds, default=None)


def _requires_python(path: str, content: bytes) -> tuple[bool, str]:
    """Whether the file holds a `[project]` table, and its `requires-python` ("" when it names none)."""
    try:
        document = repository.parse_toml(content.decode("utf-8"))
    except (UnicodeDecodeError, repository.TomlError) as error:
        raise Unparsable(f"{path} is not UTF-8 TOML that Python reads ({error})") from error
    project = document.get("project")
    if not isinstance(project, dict):
        return False, ""
    value = project.get("requires-python", "")
    if not isinstance(value, str):
        raise Unparsable(f"{path} has requires-python as {type(value).__name__}, not a string")
    return True, value


def _floor_finding(path: str, requires_python: str, floor: Version | None) -> Finding | None:
    if floor is not None and floor >= PYTHON_TARGET:
        return None
    reason = (
        f'requires-python "{requires_python}" names no floor, so it admits every Python, below {PYTHON_TARGET}.'
        if floor is None
        else f'requires-python "{requires_python}" admits Python {floor}, below {PYTHON_TARGET}.'
    )
    return Finding(
        Rule.PYTHON_FLOOR, path, None, reason,
        f"raise it to >={PYTHON_TARGET} and move CI, .python-version, Dockerfiles and ruff's target-version with it, "
        f"or declare it in {CONFIG_PATH} with [[python.allow]], its path and a reason.",
    )


def _stale_python_allowance(allowance: PythonAllowance, why: str) -> Finding:
    return Finding(
        Rule.PYTHON_FLOOR, allowance.path, None,
        f"declared in {CONFIG_PATH} with [[python.allow]], but {why}.",
        f"delete its [[python.allow]] entry from {CONFIG_PATH}.",
    )


def check_python_floor(pyprojects: dict[str, bytes], allowances: Sequence[PythonAllowance]) -> FloorResult:
    """Each judged `pyproject.toml` (one with a `[project]` table) must name a floor of the target version or higher, or be allowed."""
    declared = {allowance.path: allowance for allowance in allowances}
    findings: list[Finding] = []
    reports: list[str] = []
    judged: set[str] = set()
    for path, content in sorted(pyprojects.items()):
        has_project, requires_python = _requires_python(path, content)
        if not has_project:
            continue
        judged.add(path)
        finding = _floor_finding(path, requires_python, _floor(path, requires_python))
        if finding is None:
            continue
        if (allowance := declared.pop(path, None)) is None:
            findings.append(finding)
        else:
            reports.append(f'python-floor: allowed {path} (requires-python "{requires_python}") - {allowance.reason}')
    for allowance in declared.values():
        why = "that file is already at the floor" if allowance.path in judged else "no tracked pyproject.toml with a [project] table lies there"
        findings.append(_stale_python_allowance(allowance, why))
    return FloorResult(sorted(findings, key=lambda finding: finding.path), tuple(reports))
