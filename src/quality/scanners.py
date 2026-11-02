import hashlib
import json
import os
import shutil
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from quality import DISTRIBUTION, installation, process
from quality.config import CONFIG_PATH, SECRETS_ALLOW, Allowance, entry_name, secret_allowance_entry
from quality.output import SEPARATOR
from quality.report import (
    CannotJudge,
    Choice,
    Command,
    Example,
    Finding,
    FindingEvent,
    Instruction,
    Note,
    NoteEvent,
    OneOf,
    RuleResult,
    printable,
    shell_word,
)
from quality.rules import Rule

GITLEAKS = "gitleaks"
CLEAN_EXIT = 0
SECRET_FIX_LABEL = f"rotate it; if it is no secret, allow it in {CONFIG_PATH} with a reason"
COMMIT_COUNT_FAILURE = "cannot count the commits gitleaks scans"
GITLEAKS_LEAKS_FOUND = 7  # gitleaks exits 1 on its own errors too, so leaks get a code of their own
SECRET_SCAN_TIMEOUT_SECONDS = 1800  # bounds the scan so the scratch directory and its mirror are always deleted
LEAK_FIELDS = ("File", "StartLine", "RuleID", "Commit", "Secret")
ACTIONLINT = "actionlint"
ZIZMOR = "zizmor"
OSV_SCANNER = "osv-scanner"
ACTIONLINT_FINDINGS = 1
ZIZMOR_FINDINGS = range(10, 15)  # zizmor encodes the worst severity in 10..14
RUFF = "ruff"
VERSION_ARGUMENTS = {GITLEAKS: ["version"], ACTIONLINT: ["-version"], ZIZMOR: ["--version"], OSV_SCANNER: ["--version"], RUFF: ["--version"]}
GIT_FLOOR = (2, 41)  # GIT_ATTR_SOURCE, which hides the repository's attributes from the scan, arrived in 2.41
MANIFEST_DIRECTORY = Path(__file__).with_name("mise")
MANIFEST_FILE = "mise.toml"
MISE = "mise"
MISE_INSTALLATION_PAGE = "https://mise.jdx.dev/installing-mise.html"
MISE_CEILING_VARIABLE = "MISE_CEILING_PATHS"
ACTIONLINT_CONFIG = Path(__file__).with_name("actionlint.yaml")
ZIZMOR_CONFIG = Path(__file__).with_name("zizmor.yml")
GITLEAKS_CONFIG = Path(__file__).with_name("gitleaks.toml")


REINSTALL = OneOf(
    "reinstall quality with its dependencies",
    (
        Choice("uv sync --locked --reinstall", "in quality's checkout"),
        Choice(f"uv tool upgrade --reinstall {DISTRIBUTION}", "for a tool install"),
    ),
)


class ScannerError(process.ProcessError):
    """A scanner's output cannot be trusted; this is never a clean result."""


@dataclass(frozen=True)
class PinnedTool:
    """A scanner as the manifest pins it: mise's tool key and the exact version."""

    key: str
    version: str


def pinned_tool(name: str) -> PinnedTool:
    with (MANIFEST_DIRECTORY / MANIFEST_FILE).open("rb") as handle:
        tools = tomllib.load(handle)["tools"]
    (key,) = (key for key in tools if key.rsplit("/", 1)[-1] == name)
    return PinnedTool(key, tools[key])


def install_command() -> str:
    """The command that installs quality's scanners from its manifest and lock, as one line to copy."""
    directory = str(MANIFEST_DIRECTORY)
    return f"mise --cd {shell_word(directory) or printable(directory)} install --locked"


def located(name: str) -> Path:
    """The executable of scanner `name` that mise installed from quality's manifest, at exactly the pinned version.

    mise is asked in the manifest directory under a ceiling at the real path of its parent (mise matches the ceiling
    against its canonical working directory), so no mise config of the scanned repository takes part; mise itself is
    found on PATH, and a scanner never is.
    """
    tool = pinned_tool(name)
    install = Command(install_command())
    if shutil.which(MISE) is None:
        raise CannotJudge("mise is not installed", (Instruction(f"install mise: {MISE_INSTALLATION_PAGE}"), install))
    try:
        asked = process.run(
            [MISE, "--cd", str(MANIFEST_DIRECTORY), "which", name, "--tool", f"{tool.key}@{tool.version}"],
            env=os.environ | {MISE_CEILING_VARIABLE: str(MANIFEST_DIRECTORY.resolve().parent)},
            cwd=MANIFEST_DIRECTORY,
            timeout=process.VERSION_TIMEOUT_SECONDS,
        )
    except process.ProcessError as error:
        raise CannotJudge(str(error), (install,)) from error
    if asked.returncode != 0:
        raise CannotJudge(f"mise could not locate {name} {tool.version}", (install,))
    answer = Path((asked.stdout.splitlines() or [""])[0])
    if not (answer.is_absolute() and answer.is_file()):
        raise CannotJudge(f"mise which printed no {name} executable", (install,))
    try:
        verify_version(answer, name, tool.version)
    except process.ProcessError as error:
        raise CannotJudge(str(error), (install,)) from error
    return answer


@dataclass(frozen=True)
class _Scanner:
    name: str
    finding_exits: range | tuple[int, ...]
    read_finding: Callable[[object], Finding]
    arguments: tuple[str, ...] = ()


@dataclass(frozen=True)
class SecretScan:
    """The leaks gitleaks reported that no allowance covers, and the notes for the allowed ones and the entries no leak used."""

    findings: list[Finding]
    notes: tuple[Note, ...]


def prepare_gitleaks() -> Path:
    """The pinned gitleaks binary, once it and the git floor are known to hold."""
    binary = located(GITLEAKS)
    require_git_floor()
    return binary


def scan_secrets(root: Path, allowances: dict[Allowance, str]) -> RuleResult:
    binary = prepare_gitleaks()
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
            raise ScannerError(f"cannot resolve HEAD of {source}: it has no commit")
        commits = process.git(["-C", str(source), "rev-list", "--count", head, "--"], COMMIT_COUNT_FAILURE, git_environment).strip()
        if not (commits.isascii() and commits.isdigit()):
            raise ScannerError(f"{COMMIT_COUNT_FAILURE}: git printed no number")
        scan = run_gitleaks(binary, scratch, git_environment, source, [f"--log-opts={head}"], allowances)
        return RuleResult(scan.findings, int(commits), False, scan.notes)


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
    # gitleaks' stderr may echo what it scanned, so no refusal quotes it.
    if not report.is_file():
        raise ScannerError(f"{GITLEAKS} exited {scan.returncode} without writing its report")
    leaks = scanner_findings(GITLEAKS_SCANNER, _read_report(report), scan.returncode)
    return _split_by_allowance(leaks, allowances)


def _read_report(report: Path) -> str:
    # Never format the caught error: a decode error carries the unredacted bytes.
    try:
        return report.read_bytes().decode("utf-8")
    except OSError as error:
        raise ScannerError(f"cannot read the {GITLEAKS} report: {type(error).__name__}") from None
    except UnicodeDecodeError as error:
        raise ScannerError(f"cannot read the {GITLEAKS} report: not UTF-8 at byte {error.start}") from None


def _split_by_allowance(leaks: list[Finding], allowances: dict[Allowance, str]) -> SecretScan:
    used = {leak.allowance for leak in leaks}
    allowed = [
        Note(leak.path, NoteEvent.ALLOWED, f"{leak.allowance.rule}{SEPARATOR}{allowances[leak.allowance]}", leak.line)
        for leak in leaks if leak.allowance in allowances
    ]
    unused = [
        Note(CONFIG_PATH, NoteEvent.UNUSED, SEPARATOR.join((f"{entry_name(SECRETS_ALLOW, place)} matches no secret", f"{entry.rule} in {entry.file}")))
        for place, entry in enumerate(allowances, start=1) if entry not in used
    ]
    return SecretScan([leak for leak in leaks if leak.allowance not in allowances], (*allowed, *unused))


def require_git_floor() -> None:
    unreadable = "cannot read the git version"
    version = process.parse_git_version(process.git(["--version"], unreadable))
    if version is None:
        raise ScannerError(unreadable)
    if tuple(int(part) for part in version.split("."))[:2] < GIT_FLOOR:
        required = ".".join(map(str, GIT_FLOOR))
        raise ScannerError(
            f"git {version} cannot hide .gitattributes from the {GITLEAKS} scan, it needs git {required}"
        )


def verify_version(binary: Path, scanner_name: str, pinned: str) -> None:
    asked = process.run([str(binary), *VERSION_ARGUMENTS[scanner_name]], timeout=process.VERSION_TIMEOUT_SECONDS)
    if asked.returncode != 0:
        raise ScannerError(f"{scanner_name} version exited {asked.returncode}{process.stderr_tail(asked.stderr)}")
    reported = (asked.stdout.strip().splitlines() or [""])[0].split(" ")[-1]  # "zizmor 1.2.3", or just "1.2.3"
    if not reported:
        raise ScannerError(f"{scanner_name} reports no version, the release pins {pinned}")
    if reported != pinned:
        raise ScannerError(f"{scanner_name} {reported} found, the release pins {pinned}")


def _read_leak(entry: object) -> tuple[str, int, str, str, str]:
    try:
        path, line, rule, commit, secret = (entry[key] for key in LEAK_FIELDS)
    except (KeyError, TypeError) as error:
        raise ScannerError(f"cannot read the {GITLEAKS} report: {error!r}") from error
    if not (isinstance(path, str) and isinstance(line, int) and isinstance(rule, str) and isinstance(commit, str)
            and isinstance(secret, str)):
        raise ScannerError(f"a {GITLEAKS} leak has fields of the wrong type")
    return path, line, rule, commit, secret


def _leak_finding(entry: object) -> Finding:
    path, line, rule, commit, secret = _read_leak(entry)
    allowance = Allowance(rule, path, hashlib.sha256(secret.encode("utf-8")).hexdigest())
    where = (f"commit {commit[:8]}",) if commit else ()
    fact = SEPARATOR.join((f"{GITLEAKS} rule {rule}", *where, "value not shown"))
    fix = Example(SECRET_FIX_LABEL, secret_allowance_entry(allowance))
    return Finding(Rule.LEAK, path, line, fact, fix, allowance, event=FindingEvent.SECRET)


GITLEAKS_SCANNER = _Scanner(GITLEAKS, (GITLEAKS_LEAKS_FOUND,), _leak_finding)


def pinned_installed(name: str, locate: Callable[[], str]) -> Path:
    """The executable of quality's own installation for `name`, at the version quality's metadata pins."""
    try:
        executable = Path(locate())
        pinned = installation.required_version(name)
    except FileNotFoundError as error:
        raise CannotJudge(f"quality's installation holds no {name}", (REINSTALL,)) from error
    except installation.UnpinnedDependency as error:
        raise CannotJudge(str(error)) from error
    try:
        verify_version(executable, name, pinned)
    except process.ProcessError as error:
        raise CannotJudge(str(error), (REINSTALL,)) from error
    return executable


def scan_workflows(root: Path, rule: Rule, workflows: list[str], locate_zizmor: Callable[[], str]) -> list[Finding]:
    """The findings of the one scanner that judges `rule`; nothing to scan needs no scanner."""
    if not workflows:
        return []
    scanner = WORKFLOW_SCANNERS[rule]
    binary = pinned_installed(ZIZMOR, locate_zizmor) if scanner.name == ZIZMOR else located(scanner.name)
    scan = process.run([str(binary), *scanner.arguments, *workflows], cwd=root)
    return scanner_findings(scanner, scan.stdout, scan.returncode, scan.stderr)


def scanner_findings(scanner: _Scanner, report_text: str, exit_code: int, stderr: str = "") -> list[Finding]:
    if exit_code != CLEAN_EXIT and exit_code not in scanner.finding_exits:
        raise ScannerError(f"{scanner.name} exited {exit_code}, neither 'clean' nor 'findings'{process.stderr_tail(stderr)}")
    try:
        entries = json.loads(report_text)
    except ValueError as error:
        raise ScannerError(f"cannot read the {scanner.name} report: {error!r}") from error
    if not isinstance(entries, list):
        raise ScannerError(f"the {scanner.name} report is not a list of findings")
    if bool(entries) != (exit_code != CLEAN_EXIT):
        raise ScannerError(f"{scanner.name} exited {exit_code} but its report holds {len(entries)} finding(s)")
    return [scanner.read_finding(entry) for entry in entries]


def actionlint_finding(entry: object) -> Finding:
    try:
        message, path, line = entry["message"], entry["filepath"], entry["line"]
    except (KeyError, TypeError) as error:
        raise ScannerError(f"cannot read the {ACTIONLINT} report: {error!r}") from error
    if not (isinstance(message, str) and isinstance(path, str) and isinstance(line, int)):
        raise ScannerError(f"an {ACTIONLINT} finding has fields of the wrong type")
    return Finding(Rule.WORKFLOW_LINT, path, line, f"{ACTIONLINT}: {message}", "correct the workflow as the message says.")


def zizmor_finding(entry: object) -> Finding:
    try:
        rule, description, url = entry["ident"], entry["desc"], entry["url"]
        primary = next(place for place in entry["locations"] if place["symbolic"]["kind"] == "Primary")
        path = primary["symbolic"]["key"]["Local"]["verbatim_path"]
        row = primary["concrete"]["location"]["start_point"]["row"]
    except (KeyError, TypeError, StopIteration) as error:
        raise ScannerError(f"cannot read the {ZIZMOR} report: {error!r}") from error
    if not (isinstance(rule, str) and isinstance(description, str) and isinstance(url, str)
            and isinstance(path, str) and isinstance(row, int)):
        raise ScannerError(f"a {ZIZMOR} finding has fields of the wrong type")
    return Finding(Rule.WORKFLOW_SECURITY, path, row + 1, f"{ZIZMOR} {rule}: {description}.", f"see {url}")


WORKFLOW_SCANNERS = {
    # No shellcheck or pyflakes: actionlint would run whichever it finds on PATH, an unpinned scanner in the verdict.
    Rule.WORKFLOW_LINT: _Scanner(
        name=ACTIONLINT, finding_exits=(ACTIONLINT_FINDINGS,), read_finding=actionlint_finding,
        arguments=("-config-file", str(ACTIONLINT_CONFIG), "-shellcheck=", "-pyflakes=", "-format", "{{json .}}"),
    ),
    # The tool's config and no ignore comments: what the scanned repository says never silences a finding.
    Rule.WORKFLOW_SECURITY: _Scanner(
        name=ZIZMOR, finding_exits=ZIZMOR_FINDINGS, read_finding=zizmor_finding,
        arguments=(
            "--config", str(ZIZMOR_CONFIG), "--no-ignores",
            "--format", "json", "--offline", "--strict-collection", "--no-progress",
        ),
    ),
}
