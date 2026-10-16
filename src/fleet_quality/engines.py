import hashlib
import io
import json
import subprocess
import tarfile
import tempfile
import tomllib
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from fleet_quality.report import Finding

SECRET = "secret"
GITLEAKS = "gitleaks"
GITLEAKS_NO_LEAKS = 0
GITLEAKS_LEAKS_FOUND = 7  # gitleaks exits 1 on its own errors too, so leaks get a code of their own
DOWNLOAD_TIMEOUT_SECONDS = 120
STDERR_TAIL_LINES = 5
LEAK_FIELDS = ("File", "StartLine", "RuleID", "Commit")
WORKFLOW_LINT = "workflow-lint"
WORKFLOW_SECURITY = "workflow-security"
ACTIONLINT = "actionlint"
ZIZMOR = "zizmor"
ACTIONLINT_FINDINGS = 1
ZIZMOR_FINDINGS = range(10, 15)  # zizmor encodes the worst severity in 10..14
VERSION_ARGUMENTS = {GITLEAKS: ["version"], ACTIONLINT: ["-version"], ZIZMOR: ["--version"]}
PIN_FILE = Path(__file__).with_name("engines.toml")


class EngineError(Exception):
    """An engine cannot be installed or trusted; this is never a clean result."""


@dataclass(frozen=True)
class Pin:
    version: str
    url: str
    sha256: str


def read_pins(pin_file: Path = PIN_FILE) -> dict[str, Pin]:
    with pin_file.open("rb") as handle:
        return {name: Pin(**fields) for name, fields in tomllib.load(handle).items()}


def install(directory: Path, pins: dict[str, Pin]) -> None:
    # The operator picks the install directory on purpose, so Sonar's path-traversal taint is accepted here.
    directory.mkdir(parents=True, exist_ok=True)  # NOSONAR(S8707)
    for name, pin in pins.items():
        target = _engine_path(directory, name)
        archive = _download_verified(name, pin)
        target.write_bytes(_unpack_binary(name, archive))
        target.chmod(0o755)


def _engine_path(directory: Path, name: str) -> Path:
    root = directory.resolve()
    path = (root / name).resolve()
    if path.parent != root:
        raise EngineError(f"{name} is not a plain file name inside {root}")
    return path


def _download_verified(name: str, pin: Pin) -> bytes:
    try:
        with urllib.request.urlopen(pin.url, timeout=DOWNLOAD_TIMEOUT_SECONDS) as response:  # follows redirects
            archive = response.read()
    except (OSError, ValueError) as error:
        raise EngineError(f"cannot download {name} from {pin.url}: {error}") from error
    actual = hashlib.sha256(archive).hexdigest()
    if actual != pin.sha256:
        raise EngineError(f"{name} download has sha256 {actual}, the pin says {pin.sha256}")
    return archive


def _unpack_binary(name: str, archive: bytes) -> bytes:
    try:
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
            member = tar.extractfile(name)
            if member is None:
                raise EngineError(f"the {name} archive has no file {name}")
            return member.read()
    except (tarfile.TarError, KeyError) as error:
        raise EngineError(f"cannot unpack {name}: {error}") from error


def scan_secrets(root: Path, engine_dir: Path, pins: dict[str, Pin]) -> list[Finding]:
    binary = _engine_path(engine_dir, GITLEAKS)
    _verify_version(binary, pins[GITLEAKS].version)
    with tempfile.TemporaryDirectory() as scratch:
        report = Path(scratch) / "report.json"
        command = [
            str(binary), "git", "--redact", "--no-banner", "--no-color",
            "--exit-code", str(GITLEAKS_LEAKS_FOUND),
            "--report-format", "json", "--report-path", str(report), "--", str(root.resolve()),
        ]
        scan = _run(command)
        if not report.is_file():
            raise EngineError(
                f"{GITLEAKS} exited {scan.returncode} without writing its report{_stderr_tail(scan.stderr)}"
            )
        try:
            report_text = report.read_text(encoding="utf-8")
        except (OSError, ValueError) as error:
            raise EngineError(f"cannot read the {GITLEAKS} report: {error!r}") from error
        return secret_findings(report_text, scan.returncode, scan.stderr)


def _verify_version(binary: Path, pinned: str) -> None:
    asked = _run([str(binary), *VERSION_ARGUMENTS[binary.name]])
    if asked.returncode != 0:
        raise EngineError(f"{binary} version exited {asked.returncode}{_stderr_tail(asked.stderr)}")
    reported = (asked.stdout.strip().splitlines() or [""])[0].split(" ")[-1]  # "zizmor 1.2.3", or just "1.2.3"
    if reported != pinned:
        raise EngineError(f"{binary} is version {reported!r}, the pin says {pinned!r}")


def _run(command: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(command, capture_output=True, text=True, check=False, cwd=cwd)
    except OSError as error:
        raise EngineError(f"cannot run {command[0]}: {error}") from error


def _stderr_tail(stderr: str) -> str:
    tail = "\n".join(stderr.strip().splitlines()[-STDERR_TAIL_LINES:])
    return f": {tail}" if tail else ""


def secret_findings(report_text: str, exit_code: int, stderr: str = "") -> list[Finding]:
    if exit_code not in (GITLEAKS_NO_LEAKS, GITLEAKS_LEAKS_FOUND):
        raise EngineError(f"{GITLEAKS} exited {exit_code}, neither 'no leaks' nor 'leaks found'{_stderr_tail(stderr)}")
    try:
        entries = json.loads(report_text)
    except ValueError as error:
        raise EngineError(f"cannot read the {GITLEAKS} report: {error!r}") from error
    if not isinstance(entries, list):
        raise EngineError(f"the {GITLEAKS} report is not a list of leaks")
    leaks = [_read_leak(entry) for entry in entries]
    if bool(leaks) != (exit_code == GITLEAKS_LEAKS_FOUND):
        raise EngineError(f"{GITLEAKS} exited {exit_code} but its report holds {len(leaks)} leak(s)")
    return [
        Finding(
            SECRET, path, line,
            f"{GITLEAKS} rule {rule} matched in commit {commit[:8]} (secret redacted).",
            "rotate the secret; deleting it from the tree leaves it in history.",
        )
        for path, line, rule, commit in leaks
    ]


def _read_leak(entry: object) -> tuple[str, int, str, str]:
    try:
        path, line, rule, commit = (entry[key] for key in LEAK_FIELDS)
    except (KeyError, TypeError) as error:
        raise EngineError(f"cannot read the {GITLEAKS} report: {error!r}") from error
    if not (isinstance(path, str) and isinstance(line, int) and isinstance(rule, str) and isinstance(commit, str)):
        raise EngineError(f"a {GITLEAKS} leak has fields of the wrong type")
    return path, line, rule, commit


@dataclass(frozen=True)
class _WorkflowEngine:
    name: str
    rule: str
    arguments: tuple[str, ...]
    finding_exits: range | tuple[int, ...]
    read_finding: Callable[[object], Finding]


def scan_workflows(root: Path, engine_dir: Path, pins: dict[str, Pin], workflows: list[str]) -> list[Finding]:
    if not workflows:
        return []
    findings = []
    for engine in WORKFLOW_ENGINES.values():
        binary = _engine_path(engine_dir, engine.name)
        _verify_version(binary, pins[engine.name].version)
        scan = _run([str(binary), *engine.arguments, *workflows], cwd=root)
        findings += workflow_findings(engine, scan.stdout, scan.returncode, scan.stderr)
    return findings


def workflow_findings(engine: _WorkflowEngine, report_text: str, exit_code: int, stderr: str) -> list[Finding]:
    if exit_code != 0 and exit_code not in engine.finding_exits:
        raise EngineError(f"{engine.name} exited {exit_code}, neither 'clean' nor 'findings'{_stderr_tail(stderr)}")
    try:
        entries = json.loads(report_text)
    except ValueError as error:
        raise EngineError(f"cannot read the {engine.name} report: {error!r}") from error
    if not isinstance(entries, list):
        raise EngineError(f"the {engine.name} report is not a list of findings")
    if bool(entries) != (exit_code != 0):
        raise EngineError(f"{engine.name} exited {exit_code} but its report holds {len(entries)} finding(s)")
    return [engine.read_finding(entry) for entry in entries]


def actionlint_finding(entry: object) -> Finding:
    try:
        message, path, line = entry["message"], entry["filepath"], entry["line"]
    except (KeyError, TypeError) as error:
        raise EngineError(f"cannot read the {ACTIONLINT} report: {error!r}") from error
    if not (isinstance(message, str) and isinstance(path, str) and isinstance(line, int)):
        raise EngineError(f"an {ACTIONLINT} finding has fields of the wrong type")
    return Finding(WORKFLOW_LINT, path, line, f"{ACTIONLINT}: {message}", "correct the workflow as the message says.")


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
    return Finding(WORKFLOW_SECURITY, path, row + 1, f"{ZIZMOR} {rule}: {description}.", f"see {url}")


WORKFLOW_ENGINES = {
    ACTIONLINT: _WorkflowEngine(
        ACTIONLINT, WORKFLOW_LINT, ("-format", "{{json .}}"), (ACTIONLINT_FINDINGS,), actionlint_finding
    ),
    ZIZMOR: _WorkflowEngine(
        ZIZMOR, WORKFLOW_SECURITY, ("--format", "json", "--offline", "--strict-collection", "--no-progress"),
        ZIZMOR_FINDINGS, zizmor_finding,
    ),
}
