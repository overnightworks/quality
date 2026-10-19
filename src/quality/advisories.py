import io
import json
import re
import time
import urllib.request
import zipfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from quality import engines
from quality.engines import EngineError, Pin
from quality.report import Finding

RULE = "advisory"
DATABASE_ROOT = "https://osv-vulnerabilities.storage.googleapis.com"  # read at call time; tests point it at a file:// root
DATABASE_ARCHIVE = "all.zip"
DATABASE_DOWNLOAD_DEADLINE_SECONDS = 300  # the whole download, where DOWNLOAD_TIMEOUT_SECONDS bounds each wait
DATABASE_CHUNK_BYTES = 1 << 20
ADVISORY_SCAN_TIMEOUT_SECONDS = 600
OSV_CONFIG = Path(__file__).with_name("osv-scanner.toml")
OSV_FINDINGS_FOUND = 1
RANGE_TYPES_WITH_VERSIONS = ("ECOSYSTEM", "SEMVER")  # a GIT range's `fixed` is a commit, not a version
OSV_URL = "https://osv.dev/vulnerability/"


@dataclass(frozen=True)
class LockKind:
    ecosystem: str
    upgrade_command: str  # {package} is the advisory's package


# The lock file name is also the format osv-scanner is told to read it as (`--lockfile=uv.lock:<path>`).
LOCK_KINDS = {"uv.lock": LockKind("PyPI", "uv lock --upgrade-package {package}")}


def is_lock(name: str) -> bool:
    return PurePosixPath(name).name in LOCK_KINDS


def scan(engine_dir: Path, pins: dict[str, Pin], locks: Mapping[str, Path]) -> list[Finding]:
    """The advisories that affect the lock files `locks` (tracked name -> absolute path); no lock needs no engine."""
    if not locks:
        return []
    binary = engines.engine_path(engine_dir, engines.OSV_SCANNER)
    engines.verify_version(binary, engines.OSV_SCANNER, pins[engines.OSV_SCANNER].version)
    with engines.scratch_directory() as scratch:
        for ecosystem in sorted({LOCK_KINDS[PurePosixPath(name).name].ecosystem for name in locks}):
            _place_database(scratch, ecosystem)
        # cwd is the scratch directory so that nothing in the scanned repository is read by accident.
        command = [
            str(binary), "scan", "source", "--offline", f"--local-db-path={scratch}", f"--config={OSV_CONFIG}",
            "--format=json", "--all-vulns", "--allow-no-lockfiles",
            *[f"--lockfile={PurePosixPath(name).name}:{path}" for name, path in locks.items()],
        ]
        done = engines.run(command, cwd=scratch, timeout=ADVISORY_SCAN_TIMEOUT_SECONDS)
        names_by_path = {str(path): name for name, path in locks.items()}
        stderr = _without_paths(done.stderr, names_by_path, scratch)
    return _findings(done.returncode, done.stdout, stderr, names_by_path)


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
    destination.mkdir(parents=True)
    (destination / DATABASE_ARCHIVE).write_bytes(archive)


def _download_database(url: str, ecosystem: str) -> bytes:
    deadline = time.monotonic() + DATABASE_DOWNLOAD_DEADLINE_SECONDS
    chunks = []
    try:
        with urllib.request.urlopen(url, timeout=engines.DOWNLOAD_TIMEOUT_SECONDS) as response:
            while chunk := response.read(DATABASE_CHUNK_BYTES):
                chunks.append(chunk)
                if time.monotonic() > deadline:
                    raise EngineError(
                        f"the {ecosystem} advisory database download exceeded {DATABASE_DOWNLOAD_DEADLINE_SECONDS} seconds"
                    )
    except (OSError, ValueError) as error:
        raise EngineError(f"cannot download the {ecosystem} advisory database from {url}: {error}") from error
    return b"".join(chunks)


def _without_paths(text: str, names_by_path: Mapping[str, str], scratch: Path) -> str:
    """`text` with the checkout's absolute paths and the scratch directory named as the gate names them."""
    for path, name in names_by_path.items():
        text = text.replace(path, name)
    return text.replace(str(scratch), "<scratch>")


def _findings(exit_code: int, report_text: str, stderr: str, names_by_path: Mapping[str, str]) -> list[Finding]:
    if exit_code not in (engines.CLEAN_EXIT, OSV_FINDINGS_FOUND):
        raise EngineError(f"{engines.OSV_SCANNER} exited {exit_code}, neither 'clean' nor 'findings'{engines.stderr_tail(stderr)}")
    findings = _read_report(report_text, names_by_path)
    if bool(findings) != (exit_code == OSV_FINDINGS_FOUND):
        raise EngineError(f"{engines.OSV_SCANNER} exited {exit_code} but its report holds {len(findings)} finding(s)")
    return findings


def _read_report(report_text: str, names_by_path: Mapping[str, str]) -> list[Finding]:
    try:
        findings = []
        for result in json.loads(report_text)["results"]:
            source = result["source"]["path"]
            if source not in names_by_path:
                raise EngineError(f"the {engines.OSV_SCANNER} report names a lock file quality did not pass")
            name = names_by_path[source]
            findings += [finding for package in result["packages"] for finding in _package_findings(name, package)]
        return findings
    except (ValueError, KeyError, TypeError, AttributeError) as error:
        raise EngineError(f"cannot read the {engines.OSV_SCANNER} report: {error!r}") from error


def _package_findings(lock: str, package: dict) -> list[Finding]:
    affected = package["package"]
    name, version, ecosystem = affected["name"], affected["version"], affected["ecosystem"]
    records = {record["id"]: record for record in package["vulnerabilities"]}
    findings = []
    for group in package["groups"]:
        named, *others = group["ids"]
        aliases = sorted({*others, *group["aliases"]} - {named})
        records_of_group = [records[identifier] for identifier in group["ids"] if identifier in records]
        listed = f" ({', '.join(aliases)})" if aliases else ""
        findings.append(Finding(
            RULE, lock, None, f"{engines.OSV_SCANNER} {named}{listed}: {ecosystem} {name} {version} is affected.",
            _fix(lock, name, named, ecosystem, records_of_group),
        ))
    return findings


def _fix(lock: str, package: str, advisory: str, ecosystem: str, records: list[dict]) -> str:
    entries = [entry for record in records for entry in record.get("affected", []) if _names_package(entry, package, ecosystem)]
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


def _names_package(entry: dict, package: str, ecosystem: str) -> bool:
    named = entry.get("package", {})
    return named.get("ecosystem") == ecosystem and _normalized(named.get("name", "")) == _normalized(package)


def _normalized(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _fixed_versions(entry: dict) -> list[str]:
    return [
        event["fixed"] for versions in entry.get("ranges", []) if versions.get("type") in RANGE_TYPES_WITH_VERSIONS
        for event in versions.get("events", []) if "fixed" in event
    ]
