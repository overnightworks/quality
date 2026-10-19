import http.client
import io
import json
import re
import threading
import urllib.request
import zipfile
from collections.abc import Mapping
from concurrent.futures import Future
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from quality import engines
from quality.engines import EngineError, Pin
from quality.report import Finding

RULE = "advisory"
DATABASE_ROOT = "https://osv-vulnerabilities.storage.googleapis.com"  # read at call time; tests point it at a file:// root
DATABASE_ARCHIVE = "all.zip"
DATABASE_DOWNLOAD_DEADLINE_SECONDS = 300  # connect, headers and body together, enforced from outside the HTTP machinery; DOWNLOAD_TIMEOUT_SECONDS bounds each socket wait
ADVISORY_SCAN_TIMEOUT_SECONDS = 600
OSV_CONFIG = Path(__file__).with_name("osv-scanner.toml")
OSV_FINDINGS_FOUND = 1
RANGE_TYPES_WITH_VERSIONS = ("ECOSYSTEM", "SEMVER")  # a GIT range's `fixed` is a commit, not a version
DOWNLOAD_FAILURES = (OSError, ValueError, http.client.HTTPException)
OSV_URL = "https://osv.dev/vulnerability/"


@dataclass(frozen=True)
class LockKind:
    ecosystem: str
    upgrade_command: str  # {package} is the advisory's package


# The lock file name is also the format osv-scanner is told to read it as (`--lockfile=uv.lock:<path>`).
LOCK_KINDS = {"uv.lock": LockKind("PyPI", "uv lock --upgrade-package {package}")}


def is_lock(name: str) -> bool:
    return PurePosixPath(name).name in LOCK_KINDS


def scan(engine_dir: Path, pins: dict[str, Pin], locks: Mapping[str, Path], root: Path) -> list[Finding]:
    """The advisories that affect the lock files `locks` (tracked name -> absolute path); no lock needs no engine."""
    if not locks:
        return []
    names_by_path = _names_by_path(locks)
    with engines.scratch_directory() as scratch:
        try:
            return _scan(engine_dir, pins, locks, scratch, names_by_path)
        except EngineError as error:
            raise EngineError(_without_paths(str(error), names_by_path, [(root.resolve(), "<repository>"), (engine_dir.resolve(), "<engines>"), (scratch, "<scratch>")])) from None


def _scan(engine_dir: Path, pins: dict[str, Pin], locks: Mapping[str, Path], scratch: Path, names_by_path: dict[str, list[str]]) -> list[Finding]:
    binary = engines.engine_path(engine_dir, engines.OSV_SCANNER)
    engines.verify_version(binary, engines.OSV_SCANNER, pins[engines.OSV_SCANNER].version)
    for ecosystem in sorted({LOCK_KINDS[PurePosixPath(name).name].ecosystem for name in locks}):
        _place_database(scratch, ecosystem)
    # cwd is the scratch directory so that nothing in the scanned repository is read by accident.
    command = [
        str(binary), "scan", "source", "--offline", f"--local-db-path={scratch}", f"--config={OSV_CONFIG}",
        "--format=json", "--all-vulns", "--allow-no-lockfiles",
        *[f"--lockfile={PurePosixPath(name).name}:{path}" for name, path in locks.items()],
    ]
    done = engines.run(command, cwd=scratch, timeout=ADVISORY_SCAN_TIMEOUT_SECONDS)
    return _findings(done.returncode, done.stdout, done.stderr, names_by_path)


def _names_by_path(locks: Mapping[str, Path]) -> dict[str, list[str]]:
    """Every tracked name of each resolved lock path: `uv.lock` and `speech/uv.lock -> ../uv.lock` share one."""
    names_by_path: dict[str, list[str]] = {}
    for name, path in locks.items():
        names_by_path.setdefault(str(path), []).append(name)
    return names_by_path


def _place_database(scratch: Path, ecosystem: str) -> None:
    """The ecosystem's advisory database where osv-scanner looks for it with --local-db-path."""
    archive = _download_database(f"{DATABASE_ROOT}/{ecosystem}/{DATABASE_ARCHIVE}", ecosystem)
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


def _download_database(url: str, ecosystem: str) -> bytes:
    """The archive at `url`; a worker does the download so that the deadline holds whatever the server sends or withholds.

    A daemon worker, because a server that stalls in its headers cannot be interrupted and must not keep the process alive.
    The caller judges by the worker's lifetime: still running at the deadline is the deadline; ended without an outcome
    means an unexpected error (its traceback is on stderr); a download failure, a socket timeout included, is the worker's outcome."""
    download: Future[bytes] = Future()

    def fetch() -> None:
        try:
            with urllib.request.urlopen(url, timeout=min(engines.DOWNLOAD_TIMEOUT_SECONDS, DATABASE_DOWNLOAD_DEADLINE_SECONDS)) as response:
                download.set_result(response.read())
        except DOWNLOAD_FAILURES as error:
            download.set_exception(error)

    worker = threading.Thread(target=fetch, daemon=True)
    worker.start()
    worker.join(DATABASE_DOWNLOAD_DEADLINE_SECONDS)
    if worker.is_alive():
        raise EngineError(f"the {ecosystem} advisory database download exceeded {DATABASE_DOWNLOAD_DEADLINE_SECONDS} seconds")
    if not download.done():
        raise EngineError(f"the {ecosystem} advisory database download stopped on an unexpected error")
    try:
        return download.result()
    except DOWNLOAD_FAILURES as error:
        raise EngineError(f"cannot download the {ecosystem} advisory database from {url}: {error}") from error


def _without_paths(text: str, names_by_path: Mapping[str, list[str]], directories: list[tuple[Path, str]]) -> str:
    """`text` with every lock, the checkout and the engine and scratch directories named as the gate names them.

    osv-scanner prints some paths without the leading slash, so both forms are replaced, the longest first."""
    replacements = [(path, names[0]) for path, names in names_by_path.items()]
    replacements += [(str(directory), label) for directory, label in directories]
    replacements += [(path.lstrip("/"), label) for path, label in replacements]
    for path, label in sorted(replacements, key=lambda replacement: len(replacement[0]), reverse=True):
        text = text.replace(path, label)
    return text


def _findings(exit_code: int, report_text: str, stderr: str, names_by_path: Mapping[str, list[str]]) -> list[Finding]:
    if exit_code not in (engines.CLEAN_EXIT, OSV_FINDINGS_FOUND):
        raise EngineError(f"{engines.OSV_SCANNER} exited {exit_code}, neither 'clean' nor 'findings'{engines.stderr_tail(stderr)}")
    findings = _read_report(report_text, names_by_path)
    if bool(findings) != (exit_code == OSV_FINDINGS_FOUND):
        raise EngineError(f"{engines.OSV_SCANNER} exited {exit_code} but its report holds {len(findings)} finding(s)")
    return findings


def _read_report(report_text: str, names_by_path: Mapping[str, list[str]]) -> list[Finding]:
    try:
        findings = []
        for result in _dicts(json.loads(report_text)["results"]):
            source = _typed(_typed(result["source"], dict)["path"], str)
            if source not in names_by_path:
                raise EngineError(f"the {engines.OSV_SCANNER} report names a lock file quality did not pass")
            packages = _dicts(result["packages"])
            findings += [
                finding for name in names_by_path[source] for package in packages for finding in _package_findings(name, package)
            ]
        return list(dict.fromkeys(findings))
    except (ValueError, KeyError, TypeError, AttributeError) as error:
        raise EngineError(f"cannot read the {engines.OSV_SCANNER} report: {error!r}") from error


def _typed[T](value: object, kind: type[T]) -> T:
    """`value` if it is a `kind`: every field the reader consumes passes here, so a malformed one is never read as clean."""
    if not isinstance(value, kind):
        raise TypeError(f"expected a {kind.__name__}, got {type(value).__name__}")
    return value


def _strings(value: object) -> list[str]:
    return [_typed(item, str) for item in _typed(value, list)]


def _package_findings(lock: str, package: dict) -> list[Finding]:
    affected = _typed(package["package"], dict)
    name, version, ecosystem = (_typed(affected[field], str) for field in ("name", "version", "ecosystem"))
    records = {_typed(record["id"], str): record for record in _dicts(package["vulnerabilities"])}
    findings = []
    for group in _dicts(package["groups"]):
        named, *others = _strings(group["ids"])
        aliases = sorted({*others, *_strings(group["aliases"])} - {named})
        records_of_group = [records[identifier] for identifier in [named, *others] if identifier in records]
        listed = f" ({', '.join(aliases)})" if aliases else ""
        findings.append(Finding(
            RULE, lock, None, f"{engines.OSV_SCANNER} {named}{listed}: {ecosystem} {name} {version} is affected.",
            _fix(lock, name, named, ecosystem, records_of_group),
        ))
    return findings


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
    if len(fixed) > 1:
        return f"upgrade {package} to a fixed version: {', '.join(fixed)}"
    directory = PurePosixPath(lock).parent
    command = LOCK_KINDS[PurePosixPath(lock).name].upgrade_command.format(package=package)
    where = "" if str(directory) == "." else f"in {directory}/: "
    return f"upgrade {package} to >= {fixed[0]} ({where}{command})"


def _dicts(value: object) -> list[dict]:
    return [_typed(item, dict) for item in _typed(value, list)]


def _names_package(entry: dict, package: str, ecosystem: str) -> bool:
    named = _typed(entry.get("package", {}), dict)
    return _typed(named.get("ecosystem", ""), str) == ecosystem and _normalized(_typed(named.get("name", ""), str)) == _normalized(package)


def _normalized(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _fixed_versions(entry: dict) -> list[str]:
    events = [
        event for versions in _dicts(entry.get("ranges", [])) if _typed(versions.get("type", ""), str) in RANGE_TYPES_WITH_VERSIONS
        for event in _dicts(versions.get("events", []))
    ]
    return [_typed(event["fixed"], str) for event in events if "fixed" in event]
