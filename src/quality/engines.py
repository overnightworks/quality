import hashlib
import io
import json
import os
import re
import signal
import subprocess
import tarfile
import tempfile
import tomllib
import urllib.request
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path

from quality.config import Allowance
from quality.report import Finding

LEAK_RULE = "secret"
GITLEAKS = "gitleaks"
CLEAN_EXIT = 0
GITLEAKS_LEAKS_FOUND = 7  # gitleaks exits 1 on its own errors too, so leaks get a code of their own
DOWNLOAD_TIMEOUT_SECONDS = 120
SECRET_SCAN_TIMEOUT_SECONDS = 1800  # bounds the scan so the scratch directory and its mirror are always deleted
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


def install(directory: Path, pins: dict[str, Pin]) -> dict[str, bool]:
    """Engine name -> whether it was downloaded; an engine that already reports its pinned version is left alone."""
    # The operator picks the install directory on purpose, so Sonar's path-traversal taint is accepted here.
    directory.mkdir(parents=True, exist_ok=True)  # NOSONAR(S8707)
    downloaded = {}
    for name, pin in pins.items():
        target = engine_path(directory, name)
        downloaded[name] = not _is_installed(target, name, pin)
        if downloaded[name]:
            target.write_bytes(_unpack_binary(name, _download_verified(name, pin)))
            target.chmod(0o755)
    return downloaded


def verification_problems(directory: Path, pins: dict[str, Pin]) -> list[str]:
    """One line per pinned engine in `directory` that is missing or not at its pinned version."""
    problems = []
    for name, pin in pins.items():
        try:
            verify_version(engine_path(directory, name), name, pin.version)
        except EngineError as error:
            problems.append(str(error))
    return problems


def _is_installed(binary: Path, name: str, pin: Pin) -> bool:
    try:
        verify_version(binary, name, pin.version)
    except EngineError:
        return False
    return True


def engine_path(directory: Path, name: str) -> Path:
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
    unused_allowances: dict[Allowance, str]  # entries of quality/config.toml that no finding used, with their reasons


def prepare_gitleaks(engine_dir: Path, pins: dict[str, Pin]) -> Path:
    """The pinned gitleaks binary, once it and the git floor are known to hold."""
    binary = engine_path(engine_dir, GITLEAKS)
    verify_version(binary, GITLEAKS, pins[GITLEAKS].version)
    require_git_floor()
    return binary


def scan_secrets(root: Path, engine_dir: Path, pins: dict[str, Pin], allowances: dict[Allowance, str]) -> SecretScan:
    binary = prepare_gitleaks(engine_dir, pins)
    with scratch_directory() as scratch:
        git_environment = attribute_free_git_environment(scratch / "home")
        # gitleaks also reads <source>/.gitleaksignore, so it scans a mirror clone (bare), which has no work tree to
        # read it from. The verdict judges the history reachable from the scanned HEAD only: the mirror also brings
        # other branches, which are not this verdict's.
        source = _mirror_clone(root, scratch / "source", git_environment)
        head = head_commit(source, git_environment)
        return run_gitleaks(binary, scratch, git_environment, source, [f"--log-opts={head}"], allowances)


@contextmanager
def scratch_directory() -> Iterator[Path]:
    """A 0700 directory removed on every exit path, SIGTERM included (a hook runner or `timeout` sends it).

    The signal raises, so subprocess.run kills its child and the directory goes with the unredacted report in it.
    """
    def unwind(signal_number: int, _frame: object) -> None:
        raise SystemExit(128 + signal_number)

    previous = signal.signal(signal.SIGTERM, unwind)
    try:
        with tempfile.TemporaryDirectory() as scratch:
            yield Path(scratch)
    finally:
        signal.signal(signal.SIGTERM, previous)


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
    scan = run(command, env=git_environment, timeout=SECRET_SCAN_TIMEOUT_SECONDS)
    if not report.is_file():
        raise EngineError(f"{GITLEAKS} exited {scan.returncode} without writing its report{stderr_tail(scan.stderr)}")
    leaks = secret_findings(_read_report(report), scan.returncode, scan.stderr)
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


def parse_git_version(version_output: str) -> str | None:
    """The first `major.minor[.patch]` number in `git --version` output, wherever it sits in a word.

    Digit runs and the text between them are tokenised once, so a long run of digits cannot backtrack.
    """
    tokens = re.findall(r"[0-9]+|[^0-9]+", version_output)
    for start in range(len(tokens) - 2):
        major, dot, minor = tokens[start : start + 3]
        if major.isdigit() and dot == "." and minor.isdigit():
            patch = tokens[start + 3 : start + 5]
            if len(patch) == 2 and patch[0] == "." and patch[1].isdigit():
                return f"{major}.{minor}.{patch[1]}"
            return f"{major}.{minor}"
    return None


def require_git_floor() -> None:
    asked = run(["git", "--version"])
    version = parse_git_version(asked.stdout)
    if asked.returncode != 0 or version is None:
        raise EngineError(f"cannot read the git version{stderr_tail(asked.stderr)}")
    if tuple(int(part) for part in version.split("."))[:2] < GIT_FLOOR:
        required = ".".join(map(str, GIT_FLOOR))
        raise EngineError(
            f"git {version} cannot hide .gitattributes from the {GITLEAKS} scan, it needs git {required}"
        )


def head_commit(repository: Path, git_environment: dict[str, str]) -> str:
    """The commit HEAD names."""
    head = unborn_aware_head_commit(repository, git_environment)
    if head is None:
        raise EngineError(f"cannot resolve HEAD of {repository}: it has no commit")
    return head


def unborn_aware_head_commit(repository: Path, git_environment: dict[str, str]) -> str | None:
    """The commit HEAD names; None when the repository has no commit yet."""
    resolved = run(["git", "-C", str(repository), "rev-parse", "--verify", "--quiet", "HEAD^{commit}"], env=git_environment)
    if resolved.returncode == 1 and not resolved.stderr.strip():  # --quiet: no commit is a silent exit 1
        return None
    if resolved.returncode != 0:
        raise EngineError(f"cannot resolve HEAD of {repository}{stderr_tail(resolved.stderr)}")
    return resolved.stdout.strip()


def _mirror_clone(root: Path, destination: Path, git_environment: dict[str, str]) -> Path:
    command = ["git", "clone", "--mirror", "--quiet", "--template=", str(root.resolve()), str(destination)]
    cloned = run(command, env=git_environment)
    if cloned.returncode != 0:
        raise EngineError(f"cannot clone {root} for the {GITLEAKS} scan{stderr_tail(cloned.stderr)}")
    return destination


def verify_version(binary: Path, engine_name: str, pinned: str) -> None:
    asked = run([str(binary), *VERSION_ARGUMENTS[engine_name]])
    if asked.returncode != 0:
        raise EngineError(f"{binary} version exited {asked.returncode}{stderr_tail(asked.stderr)}")
    reported = (asked.stdout.strip().splitlines() or [""])[0].split(" ")[-1]  # "zizmor 1.2.3", or just "1.2.3"
    if reported != pinned:
        raise EngineError(f"{binary} is version {reported!r}, the pin says {pinned!r}")


def run(
    command: list[str], cwd: Path | None = None, env: dict[str, str] | None = None, timeout: float | None = None
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(command, capture_output=True, text=True, check=False, cwd=cwd, env=env, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise EngineError(f"{command[0]} did not finish within {timeout} seconds") from None
    except (OSError, ValueError) as error:  # ValueError: output that is not text
        raise EngineError(f"cannot run {command[0]}: {error}") from error


def stderr_tail(stderr: str) -> str:
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
        LEAK_RULE, path, line,
        f"{GITLEAKS} rule {rule} matched {f'at {commit[:8]}' if commit else 'in the staged change'} (sha256 {secret_sha256}, value not shown).",
        "rotate the secret; for a false positive, allow this rule, file and sha256 with a reason in quality/config.toml.",
        Allowance(rule, path, secret_sha256),
    )


GITLEAKS_ENGINE = _Engine(GITLEAKS, (GITLEAKS_LEAKS_FOUND,), _leak_finding)


def scan_workflows(root: Path, engine_dir: Path, pins: dict[str, Pin], workflows: list[str]) -> list[Finding]:
    if not workflows:
        return []
    findings = []
    for engine in WORKFLOW_ENGINES.values():
        binary = engine_path(engine_dir, engine.name)
        verify_version(binary, engine.name, pins[engine.name].version)
        scan = run([str(binary), *engine.arguments, *workflows], cwd=root)
        findings += engine_findings(engine, scan.stdout, scan.returncode, scan.stderr)
    return findings


def engine_findings(engine: _Engine, report_text: str, exit_code: int, stderr: str) -> list[Finding]:
    if exit_code != CLEAN_EXIT and exit_code not in engine.finding_exits:
        raise EngineError(f"{engine.name} exited {exit_code}, neither 'clean' nor 'findings'{stderr_tail(stderr)}")
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
