import hashlib
import io
import json
import tarfile
import tomllib
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

from quality import installation, process
from quality.config import Allowance
from quality.report import Finding
from quality.rules import Rule

GITLEAKS = "gitleaks"
CLEAN_EXIT = 0
GITLEAKS_LEAKS_FOUND = 7  # gitleaks exits 1 on its own errors too, so leaks get a code of their own
DOWNLOAD_TIMEOUT_SECONDS = 120
SECRET_SCAN_TIMEOUT_SECONDS = 1800  # bounds the scan so the scratch directory and its mirror are always deleted
LEAK_FIELDS = ("File", "StartLine", "RuleID", "Commit", "Secret")
ACTIONLINT = "actionlint"
ZIZMOR = "zizmor"
OSV_SCANNER = "osv-scanner"
TARBALL = "tar.gz"
BINARY = "binary"
ACTIONLINT_FINDINGS = 1
ZIZMOR_FINDINGS = range(10, 15)  # zizmor encodes the worst severity in 10..14
VERSION_ARGUMENTS = {GITLEAKS: ["version"], ACTIONLINT: ["-version"], ZIZMOR: ["--version"], OSV_SCANNER: ["--version"]}
GIT_FLOOR = (2, 41)  # GIT_ATTR_SOURCE, which hides the repository's attributes from the scan, arrived in 2.41
PIN_FILE = Path(__file__).with_name("engines.toml")
ACTIONLINT_CONFIG = Path(__file__).with_name("actionlint.yaml")
ZIZMOR_CONFIG = Path(__file__).with_name("zizmor.yml")
GITLEAKS_CONFIG = Path(__file__).with_name("gitleaks.toml")


class EngineError(process.ProcessError):
    """An engine cannot be installed or trusted; this is never a clean result."""


@dataclass(frozen=True)
class Pin:
    version: str
    url: str
    sha256: str
    format: str = TARBALL  # "binary" for a release asset that is the executable itself


def read_pins() -> dict[str, Pin]:
    with PIN_FILE.open("rb") as handle:
        pins = {name: Pin(**fields) for name, fields in tomllib.load(handle).items()}
    for name, pin in pins.items():
        if pin.format not in (TARBALL, BINARY):
            raise EngineError(f"the {name} pin has format {pin.format!r}, neither {TARBALL!r} nor {BINARY!r}")
    return pins


def install(directory: Path, pins: dict[str, Pin]) -> dict[str, bool]:
    """Engine name -> whether it was downloaded; an engine that already reports its pinned version is left alone."""
    # The operator picks the install directory on purpose, so Sonar's path-traversal taint is accepted here.
    try:
        directory.mkdir(parents=True, exist_ok=True)  # NOSONAR(S8707)
    except OSError as error:
        raise EngineError(f"cannot create {directory}: {error.strerror}") from error
    downloaded = {}
    for name, pin in pins.items():
        target = engine_path(directory, name)
        downloaded[name] = _version_problem(directory, name, pin) is not None
        if downloaded[name]:
            target.write_bytes(_executable_from(name, pin, _download_verified(name, pin)))
            target.chmod(0o755)
    return downloaded


def verification_problems(directory: Path, pins: dict[str, Pin]) -> list[str]:
    """One line per pinned engine in `directory` that is missing or not at its pinned version."""
    return [problem for name, pin in pins.items() if (problem := _version_problem(directory, name, pin))]


def _version_problem(directory: Path, name: str, pin: Pin) -> str | None:
    try:
        verify_version(engine_path(directory, name), name, pin.version)
    except process.ProcessError as error:
        return str(error)
    return None


def engine_path(directory: Path, name: str) -> Path:
    root = directory.resolve()
    path = (root / name).resolve()
    if path.parent != root:
        raise EngineError(f"{name} is not a plain file name inside {root}")
    return path


def _download_verified(name: str, pin: Pin) -> bytes:
    try:
        with urllib.request.urlopen(pin.url, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response:  # noqa: S310 - pinned https url; follows redirects
            archive = response.read()
    except (OSError, ValueError) as error:
        raise EngineError(f"cannot download {name} from {pin.url}: {error}") from error
    actual = hashlib.sha256(archive).hexdigest()
    if actual != pin.sha256:
        raise EngineError(f"{name} download has sha256 {actual}, the pin says {pin.sha256}")
    return archive


def _executable_from(name: str, pin: Pin, download: bytes) -> bytes:
    return download if pin.format == BINARY else _unpack_binary(name, download)


def _unpack_binary(name: str, archive: bytes) -> bytes:
    try:
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
            member = tar.extractfile(name)
            if member is None:
                raise EngineError(f"the {name} archive has no file {name}")
            return member.read()
    except (tarfile.TarError, KeyError) as error:
        raise EngineError(f"cannot unpack {name}: {error}") from error


@dataclass(frozen=True)
class _Engine:
    name: str
    finding_exits: range | tuple[int, ...]
    read_finding: Callable[[object], Finding]
    arguments: tuple[str, ...] = ()


@dataclass(frozen=True)
class SecretScan:
    findings: list[Finding]
    allowed: list[Finding]
    unused_allowances: dict[Allowance, str]  # entries of quality/config.toml that no finding used, with their reasons


def prepare_gitleaks(engine_dir: Path, pins: dict[str, Pin]) -> Path:
    """The pinned gitleaks binary, once it and the git floor are known to hold."""
    binary = engine_path(engine_dir, GITLEAKS)
    verify_version(binary, GITLEAKS, pins[GITLEAKS].version)
    require_git_floor()
    return binary


def scan_secrets(root: Path, engine_dir: Path, pins: dict[str, Pin], allowances: dict[Allowance, str]) -> SecretScan:
    binary = prepare_gitleaks(engine_dir, pins)
    with process.scratch_directory() as scratch:
        git_environment = process.attribute_free_git_environment(scratch / "home")
        # gitleaks also reads <source>/.gitleaksignore, so it scans a mirror clone (bare), which has no work tree to
        # read it from. The verdict judges the history reachable from the scanned HEAD only: the mirror also brings
        # other branches, which are not this verdict's.
        source = scratch / "source"
        process.git(["clone", "--mirror", "--quiet", "--template=", str(root.resolve()), str(source)],
            f"cannot clone {root} for the {GITLEAKS} scan", git_environment)
        head = process.unborn_aware_head_commit(source, git_environment)
        if head is None:
            raise EngineError(f"cannot resolve HEAD of {source}: it has no commit")
        return run_gitleaks(binary, scratch, git_environment, source, [f"--log-opts={head}"], allowances)


def run_gitleaks(
    binary: Path, scratch: Path, git_environment: dict[str, str], source: Path, scope: list[str],
    allowances: dict[Allowance, str],
) -> SecretScan:
    # The gate's own config, and no ignore file or inline "gitleaks:allow" from the scanned repository:
    # only an allowance in quality/config.toml allows a finding.
    # No --redact: the report must carry Secret to be hashed. It lives only in the 0700 scratch directory.
    report = scratch / "report.json"
    command = [
        str(binary), "git", "--no-banner", "--no-color", *scope,
        "--config", str(GITLEAKS_CONFIG), "--gitleaks-ignore-path", str(scratch), "--ignore-gitleaks-allow",
        "--exit-code", str(GITLEAKS_LEAKS_FOUND),
        "--report-format", "json", "--report-path", str(report), "--", str(source),
    ]
    scan = process.run(command, env=git_environment, timeout=SECRET_SCAN_TIMEOUT_SECONDS)
    if not report.is_file():
        raise EngineError(f"{GITLEAKS} exited {scan.returncode} without writing its report{process.stderr_tail(scan.stderr)}")
    leaks = engine_findings(GITLEAKS_ENGINE, _read_report(report), scan.returncode, scan.stderr)
    return _split_by_allowance(leaks, allowances)


def _read_report(report: Path) -> str:
    # Never format the caught error: a decode error carries the unredacted bytes.
    try:
        return report.read_bytes().decode("utf-8")
    except OSError as error:
        raise EngineError(f"cannot read the {GITLEAKS} report: {type(error).__name__}") from None
    except UnicodeDecodeError as error:
        raise EngineError(f"cannot read the {GITLEAKS} report: not UTF-8 at byte {error.start}") from None


def _split_by_allowance(leaks: list[Finding], allowances: dict[Allowance, str]) -> SecretScan:
    used = {leak.allowance for leak in leaks}
    return SecretScan(
        findings=[leak for leak in leaks if leak.allowance not in allowances],
        allowed=[
            replace(leak, reason=f"{GITLEAKS} rule {leak.allowance.rule} allowed: {allowances[leak.allowance]}")
            for leak in leaks if leak.allowance in allowances
        ],
        unused_allowances={entry: reason for entry, reason in allowances.items() if entry not in used},
    )


def require_git_floor() -> None:
    unreadable = "cannot read the git version"
    version = process.parse_git_version(process.git(["--version"], unreadable))
    if version is None:
        raise EngineError(unreadable)
    if tuple(int(part) for part in version.split("."))[:2] < GIT_FLOOR:
        required = ".".join(map(str, GIT_FLOOR))
        raise EngineError(
            f"git {version} cannot hide .gitattributes from the {GITLEAKS} scan, it needs git {required}"
        )


def verify_version(binary: Path, engine_name: str, pinned: str) -> None:
    asked = process.run([str(binary), *VERSION_ARGUMENTS[engine_name]], timeout=process.VERSION_TIMEOUT_SECONDS)
    if asked.returncode != 0:
        raise EngineError(f"{binary} version exited {asked.returncode}{process.stderr_tail(asked.stderr)}")
    reported = (asked.stdout.strip().splitlines() or [""])[0].split(" ")[-1]  # "zizmor 1.2.3", or just "1.2.3"
    if reported != pinned:
        raise EngineError(f"{binary} is version {reported!r}, the pin says {pinned!r}")


def _read_leak(entry: object) -> tuple[str, int, str, str, str]:
    try:
        path, line, rule, commit, secret = (entry[key] for key in LEAK_FIELDS)
    except (KeyError, TypeError) as error:
        raise EngineError(f"cannot read the {GITLEAKS} report: {error!r}") from error
    if not (isinstance(path, str) and isinstance(line, int) and isinstance(rule, str) and isinstance(commit, str)
            and isinstance(secret, str)):
        raise EngineError(f"a {GITLEAKS} leak has fields of the wrong type")
    return path, line, rule, commit, secret


def _leak_finding(entry: object) -> Finding:
    path, line, rule, commit, secret = _read_leak(entry)
    secret_sha256 = hashlib.sha256(secret.encode("utf-8")).hexdigest()
    return Finding(
        Rule.LEAK, path, line,
        f"{GITLEAKS} rule {rule} matched {f'at {commit[:8]}' if commit else 'in the staged change'} (sha256 {secret_sha256}, value not shown).",
        "rotate the secret; for a false positive, allow this rule, file and sha256 with a reason in quality/config.toml.",
        Allowance(rule, path, secret_sha256),
    )


GITLEAKS_ENGINE = _Engine(GITLEAKS, (GITLEAKS_LEAKS_FOUND,), _leak_finding)


def pinned_zizmor(locate_zizmor: Callable[[], str]) -> Path:
    """The zizmor executable of quality's own installation, at the version quality's metadata pins."""
    try:
        located = Path(locate_zizmor())
        pinned = installation.required_version(ZIZMOR)
    except FileNotFoundError as error:
        raise EngineError(f"quality's installation holds no {ZIZMOR}; {installation.REINSTALL}") from error
    except installation.UnpinnedDependency as error:
        raise EngineError(str(error)) from error
    try:
        verify_version(located, ZIZMOR, pinned)
    except process.ProcessError as error:
        raise EngineError(f"{error}; {installation.REINSTALL}") from error
    return located


def scan_workflows(
    root: Path, engine_dir: Path, pins: dict[str, Pin], rule: Rule, workflows: list[str], locate_zizmor: Callable[[], str],
) -> list[Finding]:
    """The findings of the one engine that judges `rule`; nothing to scan needs no engine."""
    if not workflows:
        return []
    engine = WORKFLOW_ENGINES[rule]
    if engine.name == ZIZMOR:
        binary = pinned_zizmor(locate_zizmor)
    else:
        binary = engine_path(engine_dir, engine.name)
        verify_version(binary, engine.name, pins[engine.name].version)
    scan = process.run([str(binary), *engine.arguments, *workflows], cwd=root)
    return engine_findings(engine, scan.stdout, scan.returncode, scan.stderr)


def engine_findings(engine: _Engine, report_text: str, exit_code: int, stderr: str) -> list[Finding]:
    if exit_code != CLEAN_EXIT and exit_code not in engine.finding_exits:
        raise EngineError(f"{engine.name} exited {exit_code}, neither 'clean' nor 'findings'{process.stderr_tail(stderr)}")
    try:
        entries = json.loads(report_text)
    except ValueError as error:
        raise EngineError(f"cannot read the {engine.name} report: {error!r}") from error
    if not isinstance(entries, list):
        raise EngineError(f"the {engine.name} report is not a list of findings")
    if bool(entries) != (exit_code != CLEAN_EXIT):
        raise EngineError(f"{engine.name} exited {exit_code} but its report holds {len(entries)} finding(s)")
    return [engine.read_finding(entry) for entry in entries]


def actionlint_finding(entry: object) -> Finding:
    try:
        message, path, line = entry["message"], entry["filepath"], entry["line"]
    except (KeyError, TypeError) as error:
        raise EngineError(f"cannot read the {ACTIONLINT} report: {error!r}") from error
    if not (isinstance(message, str) and isinstance(path, str) and isinstance(line, int)):
        raise EngineError(f"an {ACTIONLINT} finding has fields of the wrong type")
    return Finding(Rule.WORKFLOW_LINT, path, line, f"{ACTIONLINT}: {message}", "correct the workflow as the message says.")


def zizmor_finding(entry: object) -> Finding:
    try:
        rule, description, url = entry["ident"], entry["desc"], entry["url"]
        primary = next(place for place in entry["locations"] if place["symbolic"]["kind"] == "Primary")
        path = primary["symbolic"]["key"]["Local"]["verbatim_path"]
        row = primary["concrete"]["location"]["start_point"]["row"]
    except (KeyError, TypeError, StopIteration) as error:
        raise EngineError(f"cannot read the {ZIZMOR} report: {error!r}") from error
    if not (isinstance(rule, str) and isinstance(description, str) and isinstance(url, str)
            and isinstance(path, str) and isinstance(row, int)):
        raise EngineError(f"a {ZIZMOR} finding has fields of the wrong type")
    return Finding(Rule.WORKFLOW_SECURITY, path, row + 1, f"{ZIZMOR} {rule}: {description}.", f"see {url}")


WORKFLOW_ENGINES = {
    # No shellcheck or pyflakes: actionlint would run whichever it finds on PATH, an unpinned engine in the verdict.
    Rule.WORKFLOW_LINT: _Engine(
        name=ACTIONLINT, finding_exits=(ACTIONLINT_FINDINGS,), read_finding=actionlint_finding,
        arguments=("-config-file", str(ACTIONLINT_CONFIG), "-shellcheck=", "-pyflakes=", "-format", "{{json .}}"),
    ),
    # The tool's config and no ignore comments: what the scanned repository says never silences a finding.
    Rule.WORKFLOW_SECURITY: _Engine(
        name=ZIZMOR, finding_exits=ZIZMOR_FINDINGS, read_finding=zizmor_finding,
        arguments=(
            "--config", str(ZIZMOR_CONFIG), "--no-ignores",
            "--format", "json", "--offline", "--strict-collection", "--no-progress",
        ),
    ),
}
