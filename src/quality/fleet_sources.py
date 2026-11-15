"""The `fleet-source` rule: a fleet package a tracked uv.lock resolves must come from where the fleet publishes it,
never from an index, where the bare name may belong to a stranger."""

from collections.abc import Sequence
from pathlib import Path, PurePosixPath

from packaging.utils import canonicalize_name

from quality import checkout, repository
from quality.report import CannotJudge, Finding, FindingEvent, RuleResult
from quality.rules import Rule

LOCK_FILE = "uv.lock"
FLEET_PREFIX = "overnightworks-"
INDEX_SOURCE = "registry"
PINNED_SOURCES = frozenset({"git", "url", "editable", "virtual", "directory", "path"})
PIN_FIX = (
    "pin it by git tag (name @ git+https://github.com/overnightworks/<repository>@vX.Y.Z) or by its release wheel URL "
    "with #sha256=..., then uv lock"
)


def check_fleet_sources(root: Path, tracked: Sequence[str]) -> RuleResult:
    """The fleet packages each tracked uv.lock resolves from an index; the extent is the locks read. A lock tracked under
    several names is judged once, under the first."""
    locks = _locks_by_file(
        root, checkout.on_disk(root, [name for name in tracked if PurePosixPath(name).name == LOCK_FILE])
    )
    findings = [finding for name in locks.values() for finding in _lock_findings(name, _read_lock(root, name))]
    return RuleResult(findings, len(locks), False)


def _locks_by_file(root: Path, names: Sequence[str]) -> dict[Path, str]:
    locks: dict[Path, str] = {}
    for name in names:
        locks.setdefault(checkout.judged(repository.resolve_file, root, name), name)
    return locks


def _read_lock(root: Path, name: str) -> dict:
    content = checkout.judged(repository.read_bytes, root, name)
    try:
        return repository.parse_toml_file(name, content or b"")
    except repository.TomlError as error:
        raise CannotJudge(str(error)) from error


def _lock_findings(name: str, lock: dict) -> list[Finding]:
    packages = lock.get("package", [])
    if not isinstance(packages, list) or not all(isinstance(package, dict) for package in packages):
        raise CannotJudge(f"{name} is not a uv lock: its packages are not a list of tables")
    findings = []
    for package in packages:
        package_name = str(package.get("name", ""))
        if not canonicalize_name(package_name).startswith(FLEET_PREFIX):
            continue
        source = package.get("source")
        kinds = set(source) if isinstance(source, dict) else set()
        if not kinds:
            raise CannotJudge(f"{name}: {package_name} has no source")
        if INDEX_SOURCE in kinds:
            fact = f"{package_name} {package.get('version', '')} resolves from {source[INDEX_SOURCE]}"
            findings.append(Finding(Rule.FLEET_SOURCE, name, None, fact, PIN_FIX, event=FindingEvent.FROM_INDEX))
        elif not kinds <= PINNED_SOURCES:
            raise CannotJudge(f"{name}: {package_name} has a source of a kind quality does not know")
    return findings
