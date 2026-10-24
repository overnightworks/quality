import http.client
import io
import json
import re
import threading
import time
import urllib.request
import zipfile
from collections.abc import Callable, Iterable, Mapping, Sequence
from concurrent.futures import Future
from dataclasses import dataclass
from datetime import date
from fnmatch import fnmatchcase
from pathlib import Path, PurePosixPath

from quality import engines, repository
from quality.config import CONFIG_PATH, AdvisoryAllowance
from quality.engines import EngineError, Pin
from quality.report import Finding

RULE = "advisory"
PYTHON_ECOSYSTEM = "PyPI"
EDITABLE_OPTION = re.compile(r"(-e|--editable)(\s|=|$)")
DATABASE_ROOT = "https://osv-vulnerabilities.storage.googleapis.com"  # read at call time; tests point it at a file:// root
DATABASE_ARCHIVE = "all.zip"
DATABASE_DOWNLOAD_DEADLINE_SECONDS = 300  # connect, headers and body of all downloads together, enforced from outside the HTTP machinery; DOWNLOAD_TIMEOUT_SECONDS bounds each socket wait
ADVISORY_SCAN_TIMEOUT_SECONDS = 600
OSV_CONFIG = Path(__file__).with_name("osv-scanner.toml")
OSV_FINDINGS_FOUND = 1
RANGE_TYPES_WITH_VERSIONS = ("ECOSYSTEM", "SEMVER")  # a GIT range's `fixed` is a commit, not a version
DOWNLOAD_FAILURES = (OSError, ValueError, http.client.HTTPException)
OSV_URL = "https://osv.dev/vulnerability/"


@dataclass(frozen=True)
class LockKind:
    ecosystem: str
    parse_as: str  # the format name osv-scanner is told to read the file as: `--lockfile=<parse_as>:<path>`
    upgrade_hint: str  # {package} is the advisory's package


LOCK_KINDS = {
    "uv.lock": LockKind(PYTHON_ECOSYSTEM, "uv.lock", "uv lock --upgrade-package {package}"),
    "pnpm-lock.yaml": LockKind(
        "npm", "pnpm-lock.yaml", "upgrade it, or its parent when it is transitive, or set a pnpm override in package.json"
    ),
}
REQUIREMENTS_KIND = LockKind(PYTHON_ECOSYSTEM, "requirements.txt", "regenerate the file as its header says")
REQUIREMENTS_NAME = "requirements*.txt"


@dataclass(frozen=True)
class Affected:
    """One advisory group that affects one package in one tracked lock file; `ids` are every id and alias of the
    group, `subject` names the group and the package as the finding prints them."""

    lock: str
    subject: str
    ids: frozenset[str]
    fix: str

    def finding(self, note: str = "") -> Finding:
        return Finding(RULE, self.lock, None, f"{engines.OSV_SCANNER} {self.subject} is affected{note}.", self.fix)


@dataclass(frozen=True)
class AdvisoryScan:
    """The rule's findings, and one line per advisory an allowance lets through."""

    findings: tuple[Finding, ...]
    allowed: tuple[str, ...]

    def report(self) -> tuple[str, ...]:
        return (f"{RULE}: {len(self.allowed)} allowed", *self.allowed) if self.allowed else ()


def _find_kind(name: str) -> LockKind | None:
    file_name = PurePosixPath(name).name
    if file_name in LOCK_KINDS:
        return LOCK_KINDS[file_name]
    return REQUIREMENTS_KIND if fnmatchcase(file_name, REQUIREMENTS_NAME) else None


def is_lock(name: str) -> bool:
    return _find_kind(name) is not None


def lock_kind(name: str) -> LockKind:
    kind = _find_kind(name)
    if kind is None:
        raise ValueError(f"{name} is not a lock file the advisory rule judges")
    return kind


def scan(
    engine_dir: Path, pins: dict[str, Pin], locks: Mapping[str, Path], root: Path,
    allowances: Sequence[AdvisoryAllowance], today: date, monotonic: Callable[[], float] = time.monotonic,
) -> AdvisoryScan:
    """The advisories that affect the lock files `locks` (tracked name -> absolute path), with the repository's
    allowances applied; no lock needs no engine, and leaves every allowance matching nothing."""
    if not locks:
        return apply_allowances([], allowances, today)
    names_by_path = _names_by_path(locks)
    with engines.scratch_directory() as scratch:
        try:
            affected = _scan(engine_dir, pins, locks, root, scratch, names_by_path, monotonic)
        except EngineError as error:
            raise EngineError(_without_paths(str(error), names_by_path, [(root.resolve(), "<repository>"), (engine_dir.resolve(), "<engines>"), (scratch, "<scratch>")])) from None
    return apply_allowances(affected, allowances, today)


def apply_allowances(
    affected: Iterable[Affected], allowances: Sequence[AdvisoryAllowance], today: date
) -> AdvisoryScan:
    """Each group is allowed by the one allowance naming any of its ids in its lock, until that allowance's `until`
    (inclusive); two allowances on one group allow it for neither, and an allowance no group uses is a finding."""
    by_lock: dict[str, list[AdvisoryAllowance]] = {}
    for allowance in allowances:
        by_lock.setdefault(allowance.lock, []).append(allowance)
    findings: list[Finding] = []
    allowed: list[str] = []
    used: set[AdvisoryAllowance] = set()
    conflicts: dict[tuple[AdvisoryAllowance, ...], Affected] = {}
    for group in affected:
        matching = tuple(allowance for allowance in by_lock.get(group.lock, ()) if allowance.id in group.ids)
        used.update(matching)
        if len(matching) > 1:
            conflicts.setdefault(matching, group)
        if len(matching) != 1:
            findings.append(group.finding())
        elif matching[0].until < today:
            findings.append(group.finding(f"; its allowance ran out on {matching[0].until} ({matching[0].item})"))
        else:
            allowed.append(_allowed_line(group, matching[0]))
    findings += [_conflict(entries, group) for entries, group in conflicts.items()]
    findings += [_orphan(allowance) for allowance in allowances if allowance not in used]
    return AdvisoryScan(tuple(findings), tuple(allowed))


def _allowed_line(group: Affected, allowance: AdvisoryAllowance) -> str:
    return f"  allowed {group.lock} {group.subject} until {allowance.until} ({allowance.item}) - {allowance.reason}"


def _conflict(entries: tuple[AdvisoryAllowance, ...], group: Affected) -> Finding:
    names = " and ".join(entry.id for entry in entries)
    return Finding(
        RULE, CONFIG_PATH, None,
        f"the allowances for {names} in {group.lock} match one advisory group, so neither allows it.",
        f"keep one [[advisories.allow]] entry for {group.subject}",
    )


def _orphan(allowance: AdvisoryAllowance) -> Finding:
    return Finding(
        RULE, CONFIG_PATH, None, f"the allowance for {allowance.id} in {allowance.lock} matches no advisory.",
        "delete the [[advisories.allow]] entry; deleting an allowance is no loosening and needs no door",
    )


def _scan(
    engine_dir: Path, pins: dict[str, Pin], locks: Mapping[str, Path], root: Path, scratch: Path,
    names_by_path: dict[str, list[str]], monotonic: Callable[[], float],
) -> list[Affected]:
    binary = engines.engine_path(engine_dir, engines.OSV_SCANNER)
    engines.verify_version(binary, engines.OSV_SCANNER, pins[engines.OSV_SCANNER].version)
    _place_databases(scratch, sorted({lock_kind(name).ecosystem for name in locks}), monotonic)
    # cwd is the scratch directory so that nothing in the scanned repository is read by accident.
    command = [
        str(binary), "scan", "source", "--offline", f"--local-db-path={scratch}", f"--config={OSV_CONFIG}",
        "--format=json", "--all-vulns", "--all-packages", "--allow-no-lockfiles",
        *[f"--lockfile={lock_kind(name).parse_as}:{path}" for name, path in locks.items()],
    ]
    done = engines.run(command, cwd=scratch, timeout=ADVISORY_SCAN_TIMEOUT_SECONDS)
    return _findings(done.returncode, done.stdout, done.stderr, root, names_by_path)


def _names_by_path(locks: Mapping[str, Path]) -> dict[str, list[str]]:
    """Every tracked name of each resolved lock path: `uv.lock` and `speech/uv.lock -> ../uv.lock` share one."""
    names_by_path: dict[str, list[str]] = {}
    for name, path in locks.items():
        names_by_path.setdefault(str(path), []).append(name)
    return names_by_path


def _place_databases(scratch: Path, ecosystems: Sequence[str], monotonic: Callable[[], float]) -> None:
    """Each ecosystem's advisory database where osv-scanner looks for it with --local-db-path; one deadline spans them all."""
    deadline = monotonic() + DATABASE_DOWNLOAD_DEADLINE_SECONDS
    for ecosystem in ecosystems:
        _place_database(scratch, ecosystem, deadline, monotonic)


def _place_database(scratch: Path, ecosystem: str, deadline: float, monotonic: Callable[[], float]) -> None:
    archive = _download_database(f"{DATABASE_ROOT}/{ecosystem}/{DATABASE_ARCHIVE}", ecosystem, deadline, monotonic)
    try:
        with zipfile.ZipFile(io.BytesIO(archive)) as database:
            has_advisories = any(entry.endswith(".json") for entry in database.namelist())
    except zipfile.BadZipFile as error:
        raise EngineError(f"the {ecosystem} advisory database is not a valid zip: {error}") from error
    if not has_advisories:
        raise EngineError(f"the {ecosystem} advisory database holds no advisory")
    destination = scratch / "osv-scalibr" / ecosystem
    try:
        destination.mkdir(parents=True)
        (destination / DATABASE_ARCHIVE).write_bytes(archive)
    except OSError as error:
        raise EngineError(f"cannot place the {ecosystem} advisory database in the scratch directory: {error}") from error


def _download_database(url: str, ecosystem: str, deadline: float, monotonic: Callable[[], float]) -> bytes:
    """The archive at `url` if it arrives before `deadline` (a `monotonic()` value); a worker does the download so
    that the deadline holds whatever the server sends or withholds.

    A daemon worker, because a server that stalls in its headers cannot be interrupted and must not keep the process alive.
    The caller judges by the worker's lifetime: still running at the deadline is the deadline; ended without an outcome
    means an unexpected error (its traceback is on stderr); a download failure, a socket timeout included, is the worker's outcome."""
    download: Future[bytes] = Future()
    remaining = deadline - monotonic()
    if remaining <= 0:
        raise _deadline_exceeded(ecosystem)

    def fetch() -> None:
        try:
            with urllib.request.urlopen(url, timeout=min(engines.DOWNLOAD_TIMEOUT_SECONDS, remaining)) as response:
                download.set_result(response.read())
        except DOWNLOAD_FAILURES as error:
            download.set_exception(error)

    worker = threading.Thread(target=fetch, daemon=True)
    worker.start()
    worker.join(remaining)
    if worker.is_alive():
        raise _deadline_exceeded(ecosystem)
    if not download.done():
        raise EngineError(f"the {ecosystem} advisory database download stopped on an unexpected error")
    try:
        return download.result()
    except DOWNLOAD_FAILURES as error:
        raise EngineError(f"cannot download the {ecosystem} advisory database from {url}: {error}") from error


def _deadline_exceeded(ecosystem: str) -> EngineError:
    return EngineError(f"the {ecosystem} advisory database download exceeded {DATABASE_DOWNLOAD_DEADLINE_SECONDS} seconds")


def _without_paths(text: str, names_by_path: Mapping[str, list[str]], directories: list[tuple[Path, str]]) -> str:
    """`text` with every lock, the checkout and the engine and scratch directories named as the gate names them.

    osv-scanner prints some paths without the leading slash, so both forms are replaced, the longest first."""
    replacements = [(path, names[0]) for path, names in names_by_path.items()]
    replacements += [(str(directory), label) for directory, label in directories]
    replacements += [(path.lstrip("/"), label) for path, label in replacements]
    for path, label in sorted(replacements, key=lambda replacement: len(replacement[0]), reverse=True):
        text = text.replace(path, label)
    return text


def _findings(
    exit_code: int, report_text: str, stderr: str, root: Path, names_by_path: Mapping[str, list[str]]
) -> list[Affected]:
    if exit_code not in (engines.CLEAN_EXIT, OSV_FINDINGS_FOUND):
        raise EngineError(f"{engines.OSV_SCANNER} exited {exit_code}, neither 'clean' nor 'findings'{engines.stderr_tail(stderr)}")
    affected, reported = _read_report(report_text, names_by_path)
    _refuse_unread_requirements(root, names_by_path, reported)
    if bool(affected) != (exit_code == OSV_FINDINGS_FOUND):
        raise EngineError(f"{engines.OSV_SCANNER} exited {exit_code} but its report holds {len(affected)} finding(s)")
    return affected


def _refuse_unread_requirements(root: Path, names_by_path: Mapping[str, list[str]], reported: Mapping[str, set[str]]) -> None:
    """osv-scanner skips a requirement line it cannot parse (`pyyaml=5.3.1`) and answers a file of nothing else with no
    result and exit 0; a requirement the report of its file does not name was therefore not judged."""
    for path, names in names_by_path.items():
        if REQUIREMENTS_KIND not in {lock_kind(name) for name in names}:
            continue
        try:
            text = repository.read_text(root, names[0])
        except repository.RepositoryFileError as error:
            raise EngineError(str(error)) from error
        if text is None:
            raise EngineError(f"cannot read {names[0]}: it is not in the repository")
        for number, requirement in _requirements(text):
            name = _project_name(requirement)
            if name not in reported.get(path, set()):
                named = f" ({name})" if name else ""
                raise EngineError(f"{engines.OSV_SCANNER} read no package from line {number} of {names[0]}{named}, so it cannot be judged")


def _requirements(text: str) -> list[tuple[int, str]]:
    """The requirement lines of a requirements file with their line numbers: continuation lines joined, comments,
    options and blank lines left out; an editable install counts, because osv-scanner drops it silently."""
    requirements = []
    joined, first = "", 0
    for number, line in enumerate([*text.splitlines(), ""], start=1):
        first = first or number
        if line.endswith("\\"):
            joined += line[:-1] + " "
            continue
        requirement = re.sub(r"(^|\s)#.*", "", joined + line).strip()
        if requirement and (not requirement.startswith("-") or EDITABLE_OPTION.match(requirement)):
            requirements.append((first, requirement))
        joined, first = "", 0
    return requirements


def _project_name(requirement: str) -> str | None:
    """The leading project name of a requirement as PyPI compares names; none when the line does not start with one."""
    match = re.match(r"[A-Za-z0-9][A-Za-z0-9._-]*", requirement)
    return _comparable(match.group(), PYTHON_ECOSYSTEM) if match else None


def _read_report(report_text: str, names_by_path: Mapping[str, list[str]]) -> tuple[list[Affected], dict[str, set[str]]]:
    """The affected groups, and the normalised package names the report lists for each path it names."""
    try:
        affected = []
        reported: dict[str, set[str]] = {}
        for result in _dicts(json.loads(report_text)["results"]):
            source = _typed(_typed(result["source"], dict)["path"], str)
            if source not in names_by_path:
                raise EngineError(f"the {engines.OSV_SCANNER} report names a lock file quality did not pass")
            packages = _dicts(result["packages"])
            reported[source] = {
                _comparable(_typed(named["name"], str), _typed(named["ecosystem"], str))
                for named in (_typed(package["package"], dict) for package in packages)
            }
            for name in names_by_path[source]:
                if lock_kind(name) is REQUIREMENTS_KIND:
                    _refuse_unpinned(name, packages)
            affected += [
                group for name in names_by_path[source] for package in packages
                for group in _package_findings(name, package)
            ]
        return list(dict.fromkeys(affected)), reported
    except (ValueError, KeyError, TypeError, AttributeError) as error:
        raise EngineError(f"cannot read the {engines.OSV_SCANNER} report: {error!r}") from error


def _refuse_unpinned(lock: str, packages: list[dict]) -> None:
    """osv-scanner lists a requirement it cannot pin to one version (`pyyaml`, `pyyaml>=5.3,<6`) with an empty version and judges nothing."""
    for package in packages:
        named = _typed(package["package"], dict)
        if _typed(named["version"], str) == "":
            raise EngineError(f"{lock} requires {_typed(named['name'], str)} without pinning one version, so it cannot be judged")


def _typed[T](value: object, kind: type[T]) -> T:
    """`value` if it is a `kind`: every field the reader consumes passes here, so a malformed one is never read as clean."""
    if not isinstance(value, kind):
        raise TypeError(f"expected a {kind.__name__}, got {type(value).__name__}")
    return value


def _strings(value: object) -> list[str]:
    return [_typed(item, str) for item in _typed(value, list)]


def _package_findings(lock: str, package: dict) -> list[Affected]:
    affected = _typed(package["package"], dict)
    name, version, ecosystem = (_typed(affected[field], str) for field in ("name", "version", "ecosystem"))
    records = {_typed(record["id"], str): record for record in _dicts(package.get("vulnerabilities", []))}
    groups = []
    for group in _dicts(package.get("groups", [])):
        named, *others = _strings(group["ids"])
        aliases = sorted({*others, *_strings(group["aliases"])} - {named})
        records_of_group = [records[identifier] for identifier in [named, *others] if identifier in records]
        listed = f" ({', '.join(aliases)})" if aliases else ""
        groups.append(Affected(
            lock, f"{named}{listed}: {ecosystem} {name} {version}", frozenset({named, *aliases}),
            _fix(lock, name, named, ecosystem, records_of_group),
        ))
    return groups


def _fix(lock: str, package: str, advisory: str, ecosystem: str, records: list[dict]) -> str:
    entries = [
        entry for record in records for entry in _dicts(record.get("affected", []))
        if _names_package(entry, package, ecosystem)
    ]
    if not entries:
        return f"see {OSV_URL}{advisory}"
    fixed = list(dict.fromkeys(version for entry in entries for version in _fixed_versions(entry)))
    if not fixed:
        return f"osv lists no fixed version: remove or replace {package}; see {OSV_URL}{advisory}"
    directory = PurePosixPath(lock).parent
    folder = "" if str(directory) == "." else f"in {directory}/"
    hint = lock_kind(lock).upgrade_hint.format(package=package)
    if len(fixed) > 1:
        # uv's hint is a command that names one version; with several fixed versions it would be wrong.
        guidance = [folder] if folder else []
        if lock_kind(lock) is not LOCK_KINDS["uv.lock"]:
            guidance.append(hint)
        return f"upgrade {package} to a fixed version: {', '.join(fixed)}" + (f" ({': '.join(guidance)})" if guidance else "")
    return f"upgrade {package} to >= {fixed[0]} ({folder + ': ' if folder else ''}{hint})"


def _dicts(value: object) -> list[dict]:
    return [_typed(item, dict) for item in _typed(value, list)]


def _names_package(entry: dict, package: str, ecosystem: str) -> bool:
    named = _typed(entry.get("package", {}), dict)
    return _typed(named.get("ecosystem", ""), str) == ecosystem and _comparable(_typed(named.get("name", ""), str), ecosystem) == _comparable(package, ecosystem)


def _comparable(name: str, ecosystem: str) -> str:
    """The name as its ecosystem compares it: Python's rule applies to PyPI only, an npm name is exact."""
    return re.sub(r"[-_.]+", "-", name).lower() if ecosystem == PYTHON_ECOSYSTEM else name


def _fixed_versions(entry: dict) -> list[str]:
    events = [
        event for versions in _dicts(entry.get("ranges", [])) if _typed(versions.get("type", ""), str) in RANGE_TYPES_WITH_VERSIONS
        for event in _dicts(versions.get("events", []))
    ]
    return [_typed(event["fixed"], str) for event in events if "fixed" in event]
