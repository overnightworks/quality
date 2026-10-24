import http.client
import io
import json
import threading
import time
import urllib.request
import zipfile
from collections.abc import Callable, Iterable, Mapping, Sequence
from concurrent.futures import Future
from dataclasses import dataclass
from datetime import date
from pathlib import Path, PurePosixPath

from quality import engines, pip_requirements, repository
from quality.config import CONFIG_PATH, AdvisoryAllowance
from quality.engines import EngineError, Pin
from quality.pip_requirements import Requirement
from quality.report import Finding

RULE = "advisory"
PYTHON_ECOSYSTEM = "PyPI"
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
    hint_is_command: bool = False  # a command lets the tool pick the version, so beside a list of fixed versions to choose from it is left out


LOCK_KINDS = {
    "uv.lock": LockKind(PYTHON_ECOSYSTEM, "uv.lock", "uv lock --upgrade-package {package}", hint_is_command=True),
    "pnpm-lock.yaml": LockKind(
        "npm", "pnpm-lock.yaml", "upgrade it, or its parent when it is transitive, or set a pnpm override in package.json"
    ),
}
REQUIREMENTS_KIND = LockKind(
    PYTHON_ECOSYSTEM, "requirements.txt", "raise its pin, and compile the file again when it is generated"
)


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


@dataclass(frozen=True)
class Handover:
    """One file osv-scanner is told to read; `expected` are the packages it must report for it, None where quality does
    not read the file itself."""

    parse_as: str
    path: Path
    expected: tuple[Requirement, ...] | None = None


def _find_kind(name: str) -> LockKind | None:
    file_name = PurePosixPath(name).name
    if file_name.startswith("requirements") and file_name.endswith(".txt"):
        return REQUIREMENTS_KIND
    return LOCK_KINDS.get(file_name)


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
            handovers = _handovers(root, locks, scratch, names_by_path)
            ecosystems = sorted({lock_kind(name).ecosystem for name in locks})
            affected = _scan(engine_dir, pins, ecosystems, handovers, scratch, names_by_path, monotonic)
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


def _handovers(root: Path, locks: Mapping[str, Path], scratch: Path, names_by_path: dict[str, list[str]]) -> list[Handover]:
    """What osv-scanner reads for each lock. A requirements file is read by it as written, and the wheel lines it
    skips are handed over beside it as a scratch file of `name==version` lines, judged under the same tracked name."""
    handovers = []
    for name, path in locks.items():
        kind = lock_kind(name)
        if not path.stat().st_size:
            raise EngineError(f"{name} is empty")
        if kind is not REQUIREMENTS_KIND:
            handovers.append(Handover(kind.parse_as, path))
            continue
        requirements = _read_requirements(root, name)
        wheels = tuple(requirement for requirement in requirements if requirement.is_wheel)
        handovers.append(Handover(kind.parse_as, path, tuple(requirement for requirement in requirements if not requirement.is_wheel)))
        if wheels:
            wheel_file = scratch / f"wheels-{len(handovers)}.txt"
            wheel_file.write_text("".join(f"{wheel.name}=={wheel.version}\n" for wheel in wheels))
            names_by_path[str(wheel_file)] = [name]
            handovers.append(Handover(kind.parse_as, wheel_file, wheels))
    return handovers


def _read_requirements(root: Path, name: str) -> tuple[Requirement, ...]:
    try:
        return pip_requirements.read(repository.read_text(root, name) or "")
    except (pip_requirements.RequirementsError, repository.RepositoryFileError) as error:
        raise EngineError(f"{name}: {error}") from error


def _scan(
    engine_dir: Path, pins: dict[str, Pin], ecosystems: Sequence[str], handovers: Sequence[Handover], scratch: Path,
    names_by_path: dict[str, list[str]], monotonic: Callable[[], float],
) -> list[Affected]:
    binary = engines.engine_path(engine_dir, engines.OSV_SCANNER)
    engines.verify_version(binary, engines.OSV_SCANNER, pins[engines.OSV_SCANNER].version)
    _place_databases(scratch, ecosystems, monotonic)
    # cwd is the scratch directory so that nothing in the scanned repository is read by accident.
    command = [
        str(binary), "scan", "source", "--offline", f"--local-db-path={scratch}", f"--config={OSV_CONFIG}",
        "--format=json", "--all-vulns", "--all-packages", "--allow-no-lockfiles",
        *[f"--lockfile={handover.parse_as}:{handover.path}" for handover in handovers],
    ]
    done = engines.run(command, cwd=scratch, timeout=ADVISORY_SCAN_TIMEOUT_SECONDS)
    expected = {str(handover.path): handover.expected for handover in handovers if handover.expected is not None}
    return _findings(done.returncode, done.stdout, done.stderr, names_by_path, expected)


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
            entries = [entry for entry in database.namelist() if entry.endswith(".json")]
            for entry in entries:
                json.loads(database.read(entry))
    except zipfile.BadZipFile as error:
        raise EngineError(f"the {ecosystem} advisory database is not a valid zip: {error}") from error
    except ValueError as error:
        raise EngineError(f"the {ecosystem} advisory database holds an entry that is not valid JSON: {error}") from error
    if not entries:
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
    exit_code: int, report_text: str, stderr: str, names_by_path: Mapping[str, list[str]],
    expected: Mapping[str, tuple[Requirement, ...]],
) -> list[Affected]:
    if exit_code not in (engines.CLEAN_EXIT, OSV_FINDINGS_FOUND):
        raise EngineError(f"{engines.OSV_SCANNER} exited {exit_code}, neither 'clean' nor 'findings'{engines.stderr_tail(stderr)}")
    affected = _read_report(report_text, names_by_path, expected)
    if bool(affected) != (exit_code == OSV_FINDINGS_FOUND):
        raise EngineError(f"{engines.OSV_SCANNER} exited {exit_code} but its report holds {len(affected)} finding(s)")
    return affected


def _read_report(
    report_text: str, names_by_path: Mapping[str, list[str]], expected: Mapping[str, tuple[Requirement, ...]]
) -> list[Affected]:
    """The findings of the report; for each file in `expected`, the packages the report lists for it must be exactly those."""
    try:
        affected = []
        listed: dict[str, set[tuple[str, str]]] = {}
        for result in _dicts(json.loads(report_text)["results"]):
            source = _typed(_typed(result["source"], dict)["path"], str)
            if source not in names_by_path:
                raise EngineError(f"the {engines.OSV_SCANNER} report names a lock file quality did not pass")
            packages = _dicts(result["packages"])
            listed[source] = {_name_and_version(package) for package in packages}
            affected += [
                group for name in names_by_path[source] for package in packages
                for group in _package_findings(name, package)
            ]
    except (ValueError, KeyError, TypeError, AttributeError) as error:
        raise EngineError(f"cannot read the {engines.OSV_SCANNER} report: {error!r}") from error
    for path, requirements in expected.items():
        _check_listed(names_by_path[path][0], requirements, listed.get(path, set()))
    return list(dict.fromkeys(affected))


def _name_and_version(package: dict) -> tuple[str, str]:
    named = _typed(package["package"], dict)
    return pip_requirements.normalised_name(_typed(named["name"], str)), _typed(named["version"], str)


def _check_listed(name: str, requirements: tuple[Requirement, ...], listed: set[tuple[str, str]]) -> None:
    """osv-scanner skips a line it cannot parse and still reports clean, so a clean run counts only when it read what quality read."""
    for requirement in requirements:
        if (requirement.name, requirement.version) not in listed:
            raise EngineError(f"{engines.OSV_SCANNER} did not read line {requirement.line} of {name}")
    if listed - {(requirement.name, requirement.version) for requirement in requirements}:
        raise EngineError(f"{engines.OSV_SCANNER} reported a package of {name} that quality did not read")


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
    if ("vulnerabilities" in package) != ("groups" in package):
        raise ValueError("a package lists either both of vulnerabilities and groups or neither")
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
        guidance = [folder] if folder else []
        if not lock_kind(lock).hint_is_command:
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
    return pip_requirements.normalised_name(name) if ecosystem == PYTHON_ECOSYSTEM else name


def _fixed_versions(entry: dict) -> list[str]:
    events = [
        event for versions in _dicts(entry.get("ranges", [])) if _typed(versions.get("type", ""), str) in RANGE_TYPES_WITH_VERSIONS
        for event in _dicts(versions.get("events", []))
    ]
    return [_typed(event["fixed"], str) for event in events if "fixed" in event]
