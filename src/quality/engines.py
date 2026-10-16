import hashlib
import io
import json
import os
import re
import subprocess
import tarfile
import tempfile
import tomllib
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from quality.config import Allowance
from quality.report import Finding

SECRET = "secret"
GITLEAKS = "gitleaks"
CLEAN_EXIT = 0
GITLEAKS_LEAKS_FOUND = 7  # gitleaks exits 1 on its own errors too, so leaks get a code of their own
DOWNLOAD_TIMEOUT_SECONDS = 120
STDERR_TAIL_LINES = 5
LEAK_FIELDS = ("File", "StartLine", "RuleID", "Commit", "Secret")
WORKFLOW_LINT = "workflow-lint"
WORKFLOW_SECURITY = "workflow-security"
ACTIONLINT = "actionlint"
ZIZMOR = "zizmor"
ACTIONLINT_FINDINGS = 1
ZIZMOR_FINDINGS = range(10, 15)  # zizmor encodes the worst severity in 10..14
VERSION_ARGUMENTS = {GITLEAKS: ["version"], ACTIONLINT: ["-version"], ZIZMOR: ["--version"]}
GIT_FLOOR = (2, 41)  # GIT_ATTR_SOURCE, which hides the repository's attributes from the scan, arrived in 2.41
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
PIN_FILE = Path(__file__).with_name("engines.toml")
ACTIONLINT_CONFIG = Path(__file__).with_name("actionlint.yaml")
ZIZMOR_CONFIG = Path(__file__).with_name("zizmor.yml")
GITLEAKS_CONFIG = Path(__file__).with_name("gitleaks.toml")


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


def scan_secrets(root: Path, engine_dir: Path, pins: dict[str, Pin], allowances: dict[Allowance, str]) -> SecretScan:
    binary = _engine_path(engine_dir, GITLEAKS)
    _verify_version(binary, GITLEAKS, pins[GITLEAKS].version)
    _require_git_floor()
    with tempfile.TemporaryDirectory() as scratch:
        git_environment = attribute_free_git_environment(Path(scratch) / "home")
        report = Path(scratch) / "report.json"
        # The gate's own config, and no ignore file or inline "gitleaks:allow" from the scanned repository:
        # only an allowance in quality/config.toml allows a finding. gitleaks also reads <source>/.gitleaksignore,
        # so it scans a mirror clone (bare, every ref kept), which has no work tree to read it from.
        # No --redact: the report must carry Secret to be hashed. It lives only in this 0700 scratch directory.
        source = _mirror_clone(root, Path(scratch) / "source", git_environment)
        command = [
            str(binary), "git", "--no-banner", "--no-color",
            "--config", str(GITLEAKS_CONFIG), "--gitleaks-ignore-path", scratch, "--ignore-gitleaks-allow",
            "--exit-code", str(GITLEAKS_LEAKS_FOUND),
            "--report-format", "json", "--report-path", str(report), "--", str(source),
        ]
        scan = _run(command, env=git_environment)
        if not report.is_file():
            raise EngineError(
                f"{GITLEAKS} exited {scan.returncode} without writing its report{_stderr_tail(scan.stderr)}"
            )
        try:
            report_text = report.read_text(encoding="utf-8")
        except (OSError, ValueError) as error:
            raise EngineError(f"cannot read the {GITLEAKS} report: {error!r}") from error
        leaks = secret_findings(report_text, scan.returncode, scan.stderr)
        return SecretScan(
            findings=[leak for leak in leaks if leak.allowance not in allowances],
            allowed=[leak for leak in leaks if leak.allowance in allowances],
        )


def attribute_free_git_environment(empty_home: Path) -> dict[str, str]:
    """The environment git runs in so that no .gitattributes line can turn a diff into "Binary files differ".

    This cuts off the repository and tree attributes, the system file, the user's global file
    (`$XDG_CONFIG_HOME/git/attributes`) and any `core.attributesFile`, the repository's own config included, since
    the forced command-line value wins. It does not cut off `$GIT_DIR/info/attributes`: git reads it from disk whatever
    the environment says. The hygiene scan stays clear of it by scanning a fresh template-less mirror, which copies
    none; a caller that runs git in the real repository must deal with it itself.
    """
    empty_home.mkdir(parents=True)
    inherited = {name: value for name, value in os.environ.items() if not name.startswith("GIT_")}
    return inherited | {
        "GIT_ATTR_SOURCE": EMPTY_TREE,
        "GIT_ATTR_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_COUNT": "1",
        "GIT_CONFIG_KEY_0": "core.attributesFile",
        "GIT_CONFIG_VALUE_0": os.devnull,
        "HOME": str(empty_home),
        "XDG_CONFIG_HOME": str(empty_home),
    }


def _require_git_floor() -> None:
    asked = _run(["git", "--version"])
    found = re.search(r"\d+\.\d+(?:\.\d+)?", asked.stdout)
    if asked.returncode != 0 or found is None:
        raise EngineError(f"cannot read the git version{_stderr_tail(asked.stderr)}")
    if tuple(int(part) for part in found[0].split("."))[:2] < GIT_FLOOR:
        required = ".".join(map(str, GIT_FLOOR))
        raise EngineError(
            f"git {found[0]} cannot hide .gitattributes from the {GITLEAKS} scan, it needs git {required}"
        )


def _mirror_clone(root: Path, destination: Path, git_environment: dict[str, str]) -> Path:
    command = ["git", "clone", "--mirror", "--quiet", "--template=", str(root.resolve()), str(destination)]
    cloned = _run(command, env=git_environment)
    if cloned.returncode != 0:
        raise EngineError(f"cannot clone {root} for the {GITLEAKS} scan{_stderr_tail(cloned.stderr)}")
    return destination


def _verify_version(binary: Path, engine_name: str, pinned: str) -> None:
    asked = _run([str(binary), *VERSION_ARGUMENTS[engine_name]])
    if asked.returncode != 0:
        raise EngineError(f"{binary} version exited {asked.returncode}{_stderr_tail(asked.stderr)}")
    reported = (asked.stdout.strip().splitlines() or [""])[0].split(" ")[-1]  # "zizmor 1.2.3", or just "1.2.3"
    if reported != pinned:
        raise EngineError(f"{binary} is version {reported!r}, the pin says {pinned!r}")


def _run(
    command: list[str], cwd: Path | None = None, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(command, capture_output=True, text=True, check=False, cwd=cwd, env=env)
    except (OSError, ValueError) as error:  # ValueError: output that is not text
        raise EngineError(f"cannot run {command[0]}: {error}") from error


def _stderr_tail(stderr: str) -> str:
    tail = "\n".join(stderr.strip().splitlines()[-STDERR_TAIL_LINES:])
    return f": {tail}" if tail else ""


def secret_findings(report_text: str, exit_code: int, stderr: str = "") -> list[Finding]:
    return engine_findings(GITLEAKS_ENGINE, report_text, exit_code, stderr)


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
        SECRET, path, line,
        f"{GITLEAKS} rule {rule} matched at {commit[:8]} (sha256 {secret_sha256}, value not shown).",
        "rotate the secret; for a false positive, allow this rule, file and sha256 with a reason in quality/config.toml.",
        Allowance(rule, path, secret_sha256),
    )


GITLEAKS_ENGINE = _Engine(GITLEAKS, (GITLEAKS_LEAKS_FOUND,), _leak_finding)


def scan_workflows(root: Path, engine_dir: Path, pins: dict[str, Pin], workflows: list[str]) -> list[Finding]:
    if not workflows:
        return []
    findings = []
    for engine in WORKFLOW_ENGINES.values():
        binary = _engine_path(engine_dir, engine.name)
        _verify_version(binary, engine.name, pins[engine.name].version)
        scan = _run([str(binary), *engine.arguments, *workflows], cwd=root)
        findings += engine_findings(engine, scan.stdout, scan.returncode, scan.stderr)
    return findings


def engine_findings(engine: _Engine, report_text: str, exit_code: int, stderr: str) -> list[Finding]:
    if exit_code != CLEAN_EXIT and exit_code not in engine.finding_exits:
        raise EngineError(f"{engine.name} exited {exit_code}, neither 'clean' nor 'findings'{_stderr_tail(stderr)}")
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
    # No shellcheck or pyflakes: actionlint would run whichever it finds on PATH, an unpinned engine in the verdict.
    ACTIONLINT: _Engine(
        name=ACTIONLINT, finding_exits=(ACTIONLINT_FINDINGS,), read_finding=actionlint_finding,
        arguments=("-config-file", str(ACTIONLINT_CONFIG), "-shellcheck=", "-pyflakes=", "-format", "{{json .}}"),
    ),
    # The tool's config and no ignore comments: what the scanned repository says never silences a finding.
    ZIZMOR: _Engine(
        name=ZIZMOR, finding_exits=ZIZMOR_FINDINGS, read_finding=zizmor_finding,
        arguments=(
            "--config", str(ZIZMOR_CONFIG), "--no-ignores",
            "--format", "json", "--offline", "--strict-collection", "--no-progress",
        ),
    ),
}
