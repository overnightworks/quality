import os
import re
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from functools import partial
from pathlib import Path, PurePosixPath

import yaml
from packaging.specifiers import Specifier, SpecifierSet
from packaging.version import Version

from quality import advisories, architecture, clones, engines, layout, process, python_lint, ratchet, repository
from quality.config import (
    CONFIG_PATH,
    Allowance,
    ComposeAllowance,
    ConfigError,
    LayoutRule,
    PythonAllowance,
    RootAllowance,
    read_advisory_allowances,
    read_compose_allowances,
    read_layout_allowances,
    read_python_allowances,
    read_root_allowances,
    read_secret_allowances,
)
from quality.report import Finding, shell_word

ENV_FILE = "env-file"
COMPOSE_PORT = "compose-port"
CLAUDE_MD = "claude-md"
ROOT_LAYOUT = "root-layout"
FILE_SUFFIX = LayoutRule.FILE_SUFFIX.value
SCRIPT_NAME = LayoutRule.SCRIPT_NAME.value
SCRIPT_HEADER = LayoutRule.SCRIPT_HEADER.value
TEST_LAYOUT = LayoutRule.TEST_LAYOUT.value
TEST_IMPORT = LayoutRule.TEST_IMPORT.value
PYTHON_FLOOR = "python-floor"
PYTHON_SECURITY = python_lint.SECURITY_RULE
PYTHON_PRINT = python_lint.PRINT_RULE
PYTHON_TARGET = Version("3.14")
LOWER_BOUND_OPERATORS = frozenset({">=", ">", "~=", "==", "==="})
WORKFLOW_RULE_IDS = tuple(engines.WORKFLOW_ENGINES)
ENGINE_RULE_IDS = (engines.LEAK_RULE, *WORKFLOW_RULE_IDS, advisories.RULE)
WORKFLOW_DIRECTORY = PurePosixPath(".github/workflows")
YAML_SUFFIXES = (".yml", ".yaml")

CLAUDE_NESTED_NAME = ".claude/claude.md"
CLAUDE_FILE_NAMES = frozenset({"claude.md", CLAUDE_NESTED_NAME})
CLAUDE_IMPORTS = frozenset({b"@AGENTS.md", b"@AGENTS.md\n"})
AGENTS_FILE = "AGENTS.md"

# A directory is written with its trailing slash, as `[[root.allow]]` writes it.
ROOT_BASE_FILES = frozenset({
    "README.md", "AGENTS.md", "CLAUDE.md", "HEART.md", "LICENSE",
    ".gitignore", ".gitattributes", ".gitmodules",
    "pyproject.toml", "uv.lock", ".python-version",
    "package.json", "pnpm-lock.yaml", "package-lock.json",
    "Cargo.toml", "Cargo.lock", "rust-toolchain.toml",
    "Dockerfile", ".dockerignore", "compose.yaml", "compose.yml", "docker-compose.yaml", "docker-compose.yml",
    "sonar-project.properties", ".importlinter", ".pre-commit-config.yaml",
})
ROOT_BASE_DIRECTORIES = frozenset({".github/", ".aco/", "quality/", "src/", "tests/", "docs/", "scripts/"})
ROOT_BASE_ENTRIES = ROOT_BASE_FILES | ROOT_BASE_DIRECTORIES

LOOPBACK = "127.0.0.1"
LOOPBACK_HOSTS = (LOOPBACK, "::1")
ENV_TEMPLATE_SUFFIX = ".example"
INDEX_MODE = re.compile(r"[0-7]{6}")
INTERPOLATION = re.compile(r"\$\{[^}]*\}")
COMPOSE_WORD = "compose"
HOST_NETWORK_MODE = "host"
NAME_SEPARATORS = re.compile(r"[._-]")
YAML_NUMBER_TAGS = ("tag:yaml.org,2002:int", "tag:yaml.org,2002:float")


class ComposeLoader(yaml.SafeLoader):
    """Reads Compose's own YAML tags (!override, !reset) as the plain value they wrap."""


def _construct_tagged(loader: ComposeLoader, node: yaml.Node) -> object:
    if isinstance(node, yaml.MappingNode):
        return loader.construct_mapping(node, deep=True)
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node, deep=True)
    is_plain = node.style is None
    implicit_tag = loader.resolve(yaml.ScalarNode, node.value, (is_plain, not is_plain))
    return loader.construct_object(yaml.ScalarNode(implicit_tag, node.value, node.start_mark, node.end_mark))


for compose_tag in ("!override", "!reset"):
    ComposeLoader.add_constructor(compose_tag, _construct_tagged)


def _number_constructor(construct_number: Callable[[yaml.SafeLoader, yaml.Node], object]):
    """Compose reads YAML 1.2, where `22:22` is text; only YAML 1.1 makes it a base-60 number."""
    def construct(loader: ComposeLoader, node: yaml.Node) -> object:
        return node.value if ":" in node.value else construct_number(loader, node)
    return construct


for number_tag in YAML_NUMBER_TAGS:
    ComposeLoader.add_constructor(number_tag, _number_constructor(yaml.SafeLoader.yaml_constructors[number_tag]))


YAML_BOOLEAN_TAG = "tag:yaml.org,2002:bool"
YAML_BOOLEAN = re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$")


def _read_only_true_and_false_as_boolean() -> None:
    """Compose reads YAML 1.2, where `on`, `yes` and `no` are text, as service names; only YAML 1.1 makes them booleans."""
    resolvers = {
        first: [(tag, pattern) for tag, pattern in entries if tag != YAML_BOOLEAN_TAG]
        for first, entries in yaml.SafeLoader.yaml_implicit_resolvers.items()
    }
    ComposeLoader.yaml_implicit_resolvers = resolvers
    ComposeLoader.add_implicit_resolver(YAML_BOOLEAN_TAG, YAML_BOOLEAN, list("tTfF"))


_read_only_true_and_false_as_boolean()


class CannotJudge(Exception):
    """The repository cannot be examined; this is never a clean result."""

    label = "cannot judge"


class Unparsable(CannotJudge):
    """A tracked file Python cannot decode or parse; only an operating-system read error is `cannot judge`."""

    label = "unparsable"


def _refusal(path: Path, because: str) -> CannotJudge:
    word = shell_word(str(path))
    says_why = f"`git -C {word} status` says why" if word else "`git status` run inside it says why"
    return CannotJudge(f"{path} is not a readable git repository: {because} ({says_why})")


def _refusal_without_git(path: Path, what: str, reason: object) -> CannotJudge:
    return CannotJudge(f"{path} is not a readable git repository: {what} ({reason})")


def _git_listing(path: Path, command: list[str]) -> subprocess.CompletedProcess:
    """git's answer for the repository at `path`; one that refuses, or does not run, is a repository git cannot read."""
    try:
        done = process.run(["git", "-C", str(path.absolute()), *command], encoding=None)
    except OSError as error:
        raise _refusal_without_git(path, "the working directory cannot be read", repository.os_error_reason(error)) from error
    except process.ProcessError as error:
        reason = repository.os_error_reason(error.__cause__) if isinstance(error.__cause__, OSError) else error
        raise _refusal_without_git(path, "git did not run", reason) from error
    if done.returncode != 0:
        # git's own words name the checkout it resolved, so only the exit code is printed and git is asked again.
        raise _refusal(path, f"git {command[0]} exited {done.returncode}")
    return done


def work_tree_top(path: Path) -> Path:
    """The top of the work tree `path` lies in, as `path` reaches it; `--show-cdup` never prints where the checkout lies."""
    answer = _git_listing(path, ["rev-parse", "--is-inside-work-tree", "--show-cdup"]).stdout
    inside, _, cdup = os.fsdecode(answer).strip().partition("\n")
    if inside != "true":
        raise _refusal(path, "it lies outside any work tree")
    return path / cdup


def tracked_files(root: Path) -> list[str]:
    listing = _git_listing(root, ["ls-files", "-z", "--"])
    return [os.fsdecode(name) for name in listing.stdout.split(b"\0") if name]


def index_modes(root: Path) -> dict[str, str]:
    """Each tracked path's index mode; the index is the only source of a mode, and a link is never followed."""
    listing = _git_listing(root, ["ls-files", "-z", "-s", "--"])
    modes: dict[str, str] = {}
    for record in listing.stdout.split(b"\0"):
        if not record:
            continue
        fields, tab, name = os.fsdecode(record).partition("\t")
        parts = fields.split(" ")
        if not tab or len(parts) != 3 or not INDEX_MODE.fullmatch(parts[0]):
            raise CannotJudge(f"git ls-files -s printed an index record quality cannot read in {root}")
        modes[name] = parts[0]
    return modes


def _untrack_fix(name: str) -> str:
    word = shell_word(name)
    if word is None:
        return "remove it from the index by its bytes (git ls-files -z shows them), then add it to .gitignore."
    return f"git --literal-pathspecs rm --cached -- {word} and add it to .gitignore."


def check_env_files(tracked: list[str]) -> list[Finding]:
    return [
        Finding(
            ENV_FILE, name, None,
            "a tracked env file publishes configuration and often secrets.",
            _untrack_fix(name),
        )
        for name in tracked
        if _is_env_file(PurePosixPath(name).name)
    ]


def _is_env_file(name: str) -> bool:
    if name.endswith(ENV_TEMPLATE_SUFFIX):
        return False
    return name == ".env" or name.startswith(".env.") or name.endswith(".env")


def is_compose_file(name: str) -> bool:
    """A YAML file whose name holds the word `compose` between `.`, `-` or `_`: `docker-compose.ci.yml`, not `composer.yml`."""
    path = PurePosixPath(name)
    return path.suffix.lower() in YAML_SUFFIXES and COMPOSE_WORD in NAME_SEPARATORS.split(path.stem.casefold())


def check_compose_ports(root: Path, tracked: list[str], allowances: Sequence[ComposeAllowance]) -> list[Finding]:
    """Every published port binds loopback and no service is host-networked (or may be, through an interpolation)
    unless declared; a declaration that matches no such service is a finding too."""
    declared = {(allowance.file, allowance.service) for allowance in allowances}
    findings = []
    host_networked: set[tuple[str, str]] = set()
    for name in [name for name in tracked if is_compose_file(name)]:
        if _judged(repository.is_on_disk, root, name) and (text := _judged(repository.read_text, root, name)) is not None:
            file_findings, services = _compose_file_findings(name, text, {service for file, service in declared if file == name})
            findings.extend(file_findings)
            host_networked.update((name, service) for service in services)
    findings += [_stale_compose_declaration(*key) for key in sorted(declared - host_networked)]
    return findings


def check_claude_md(root: Path, tracked: list[str]) -> list[Finding]:
    """A tracked CLAUDE.md, in any case, is exactly `@AGENTS.md` beside a tracked AGENTS.md; `.claude/CLAUDE.md` is none.

    The findings carry fixed texts and the file's name, never its content.
    """
    findings = []
    for name in (name for name in tracked if name.casefold() in CLAUDE_FILE_NAMES):
        problem = _claude_md_problem(root, name, AGENTS_FILE in tracked)
        if problem is not None:
            findings.append(Finding(CLAUDE_MD, name, None, *problem))
    return findings


def _claude_md_problem(root: Path, name: str, agents_tracked: bool) -> tuple[str, str] | None:
    """The reason and the fix for a CLAUDE.md that is not clean; None when it is clean or gone from the tree."""
    is_link = _judged(repository.is_symlink, root, name)
    if name.casefold() == CLAUDE_NESTED_NAME:
        if not (is_link or _judged(repository.is_on_disk, root, name)):
            return None
        return (f"{name} gives Claude guidance Codex and Grok never read.",
                f"move its lines into {AGENTS_FILE} and delete {name}.")
    if is_link:
        return (f"{name} is a symlink, not a file holding @AGENTS.md.",
                f"replace the link with a file whose only line is @AGENTS.md; {AGENTS_FILE} stays as it is.")
    on_disk = _judged(repository.is_on_disk, root, name)
    content = _judged(repository.read_bytes, root, name) if on_disk else None
    if content is None:
        return None
    if content not in CLAUDE_IMPORTS:
        return (f"{name} is not exactly @AGENTS.md, so Claude reads other guidance than Codex and Grok.",
                f"move its own lines into {AGENTS_FILE}, then leave @AGENTS.md as its only line, or delete it.")
    if not (agents_tracked and _judged(repository.is_on_disk, root, AGENTS_FILE)):
        return (f"{name} imports {AGENTS_FILE}, which the repository does not hold.",
                f"add {AGENTS_FILE} with the repository's guidance, or delete {name}.")
    return None


def check_root_layout(tracked: list[str], allowances: Sequence[RootAllowance]) -> list[Finding]:
    """Every tracked root entry, a directory with its trailing `/`, is on the base list or declared; a declaration that
    matches no tracked root entry is a finding too. Findings are sorted by entry."""
    entries = {name.partition("/")[0] + "/" if "/" in name else name for name in tracked}
    declared = {allowance.entry for allowance in allowances}
    base_spelling = {entry.casefold(): entry for entry in ROOT_BASE_ENTRIES}
    findings = [
        _unlisted_root_entry(entry, base_spelling.get(entry.casefold()))
        for entry in entries - ROOT_BASE_ENTRIES - declared
    ]
    findings += [_stale_root_declaration(entry, entries) for entry in declared - entries]
    return sorted(findings, key=lambda finding: finding.path)


def _unlisted_root_entry(entry: str, base_spelling: str | None) -> Finding:
    hint = f" (the base list knows {base_spelling})" if base_spelling else ""
    return Finding(
        ROOT_LAYOUT, entry, None,
        f"not on quality's base list{hint}.",
        f"move it to its owner's directory, or declare it in {CONFIG_PATH} with [[root.allow]], an entry and a reason.",
    )


@dataclass(frozen=True)
class FloorResult:
    """The python-floor findings and one report line per allowed file."""

    findings: list[Finding]
    reports: tuple[str, ...]


def _lower_bound(clause: Specifier) -> Version:
    """The version a clause sets as a lower bound; `==3.14.*` counts as 3.14, while `===` takes its version as written."""
    return Version(clause.version.removesuffix(".*") if clause.operator == "==" else clause.version)


def _floor(path: str, requires_python: str) -> Version | None:
    """The highest lower bound among the clauses, None when no clause sets one."""
    try:
        bounds = [_lower_bound(clause) for clause in SpecifierSet(requires_python) if clause.operator in LOWER_BOUND_OPERATORS]
    except ValueError as error:
        raise Unparsable(f'{path} has requires-python "{requires_python}", which is no PEP 440 version specifier ({error})') from error
    return max(bounds, default=None)


def _requires_python(path: str, content: bytes) -> tuple[bool, str]:
    """Whether the file holds a `[project]` table, and its `requires-python` ("" when it names none)."""
    try:
        document = repository.parse_toml(content.decode("utf-8"))
    except (UnicodeDecodeError, repository.TomlError) as error:
        raise Unparsable(f"{path} is not UTF-8 TOML that Python reads ({error})") from error
    project = document.get("project")
    if not isinstance(project, dict):
        return False, ""
    value = project.get("requires-python", "")
    if not isinstance(value, str):
        raise Unparsable(f"{path} has requires-python as {type(value).__name__}, not a string")
    return True, value


def _floor_finding(path: str, requires_python: str, floor: Version | None) -> Finding | None:
    if floor is not None and floor >= PYTHON_TARGET:
        return None
    reason = (
        f'requires-python "{requires_python}" names no floor, so it admits every Python, below {PYTHON_TARGET}.'
        if floor is None
        else f'requires-python "{requires_python}" admits Python {floor}, below {PYTHON_TARGET}.'
    )
    return Finding(
        PYTHON_FLOOR, path, None, reason,
        f"raise it to >={PYTHON_TARGET} and move CI, .python-version, Dockerfiles and ruff's target-version with it, "
        f"or declare it in {CONFIG_PATH} with [[python.allow]], its path and a reason.",
    )


def _stale_python_allowance(allowance: PythonAllowance, why: str) -> Finding:
    return Finding(
        PYTHON_FLOOR, allowance.path, None,
        f"declared in {CONFIG_PATH} with [[python.allow]], but {why}.",
        f"delete its [[python.allow]] entry from {CONFIG_PATH}.",
    )


def check_python_floor(pyprojects: dict[str, bytes], allowances: Sequence[PythonAllowance]) -> FloorResult:
    """Each judged `pyproject.toml` (one with a `[project]` table) must name a floor of the target version or higher, or be allowed."""
    declared = {allowance.path: allowance for allowance in allowances}
    findings: list[Finding] = []
    reports: list[str] = []
    judged: set[str] = set()
    for path, content in sorted(pyprojects.items()):
        has_project, requires_python = _requires_python(path, content)
        if not has_project:
            continue
        judged.add(path)
        finding = _floor_finding(path, requires_python, _floor(path, requires_python))
        if finding is None:
            continue
        if (allowance := declared.pop(path, None)) is None:
            findings.append(finding)
        else:
            reports.append(f'python-floor: allowed {path} (requires-python "{requires_python}") - {allowance.reason}')
    for allowance in declared.values():
        why = "that file is already at the floor" if allowance.path in judged else "no tracked pyproject.toml with a [project] table lies there"
        findings.append(_stale_python_allowance(allowance, why))
    return FloorResult(sorted(findings, key=lambda finding: finding.path), tuple(reports))


def _python_floor_result(root: Path) -> FloorResult:
    modes = index_modes(root)
    names = [str(PurePosixPath(project.directory) / layout.PROJECT_FILE) for project in layout.python_projects(modes)]
    present = _on_disk(root, [name for name in names if modes[name] != layout.GITLINK_MODE])
    return check_python_floor(_read_existing(repository.read_bytes, root, present), _declared(read_python_allowances, root))


def _stale_root_declaration(entry: str, tracked_entries: set[str]) -> Finding:
    hint = f" (the repository tracks {entry}/)" if f"{entry}/" in tracked_entries else ""
    return Finding(
        ROOT_LAYOUT, entry, None,
        f"declared in {CONFIG_PATH} with [[root.allow]], but the repository tracks no such root entry{hint}.",
        f"delete its [[root.allow]] entry from {CONFIG_PATH}.",
    )


def _declared[T](read: Callable[[Path], T], root: Path) -> T:
    """The repository's declarations; a config that cannot be read cannot be judged."""
    try:
        return read(root)
    except ConfigError as error:
        raise CannotJudge(str(error)) from error


def _judged[T](ask: Callable[[Path, str], T], root: Path, name: str) -> T:
    """Asks the repository reader; a file it cannot use cannot be judged."""
    try:
        return ask(root, name)
    except repository.RepositoryFileError as error:
        raise CannotJudge(str(error)) from error


def _on_disk(root: Path, names: Sequence[str]) -> list[str]:
    """The tracked names the tree holds: a file deleted, sparse or linked to nowhere is gone, one the filesystem refuses is not."""
    return [name for name in names if _judged(repository.is_on_disk, root, name)]


def _read_existing[T](read: Callable[[Path, str], T | None], root: Path, names: Sequence[str]) -> dict[str, T]:
    """The content of each name the tree holds; a name that vanished since it was listed is gone."""
    return {name: content for name in names if (content := _judged(read, root, name)) is not None}


def _compose_file_findings(name: str, text: str, declared_services: set[str]) -> tuple[list[Finding], list[str]]:
    """The findings of one compose file and its services that are host-networked or may be, declared or not."""
    try:
        document = yaml.load(text, Loader=ComposeLoader)  # noqa: S506 - ComposeLoader subclasses SafeLoader
    except (yaml.YAMLError, RecursionError, ValueError, KeyError, IndexError) as error:
        raise CannotJudge(f"cannot read {name}: {_yaml_error_reason(error)}") from error
    services = document.get("services") if isinstance(document, dict) else None
    if not isinstance(services, dict):
        return [], []
    findings = []
    host_networked = []
    for service_name, service in services.items():
        if not isinstance(service, dict):
            continue
        if (network_finding := _network_mode_finding(name, service_name, service.get("network_mode"))) is not None:
            host_networked.append(str(service_name))
            if str(service_name) not in declared_services:
                findings.append(network_finding)
        ports = service.get("ports") or []
        if not isinstance(ports, list):
            findings.append(Finding(COMPOSE_PORT, name, None,
                                    f"service {service_name} has ports that are not a list (unreadable).",
                                    "write ports as a list so each one can be checked."))
            continue
        findings.extend(_port_findings(name, service_name, ports))
    return findings, host_networked


def _network_mode_finding(name: str, service_name: object, network_mode: object) -> Finding | None:
    """A finding for `host`, and for a value an interpolation may resolve to `host`; None for any other mode."""
    fix = f"give it a network of its own, or declare it in {CONFIG_PATH} with [[compose.allow]], a file, a service and a reason."
    if network_mode == HOST_NETWORK_MODE:
        return Finding(COMPOSE_PORT, name, None,
                       f"service {service_name} has network_mode: {HOST_NETWORK_MODE}, which publishes whatever it binds beyond loopback.", fix)
    if isinstance(network_mode, str) and INTERPOLATION.search(network_mode):
        return Finding(COMPOSE_PORT, name, None,
                       f"service {service_name} has a network_mode that holds an interpolation, which may resolve to {HOST_NETWORK_MODE} (unverifiable).", fix)
    return None


def _stale_compose_declaration(file: str, service: str) -> Finding:
    return Finding(
        COMPOSE_PORT, file, None,
        f"service {service} is declared in {CONFIG_PATH} with [[compose.allow]], but no tracked compose file of that name "
        f"has such a service with network_mode: {HOST_NETWORK_MODE} or an interpolated network_mode.",
        f"delete its [[compose.allow]] entry from {CONFIG_PATH}.",
    )


def _yaml_error_reason(error: Exception) -> str:
    """One line: PyYAML's own message quotes the source and names `<unicode string>`."""
    if isinstance(error, RecursionError):
        return "nested too deeply"
    if isinstance(error, yaml.reader.ReaderError):
        return f"position {error.position}: {error.reason}"
    if isinstance(error, yaml.MarkedYAMLError) and error.problem_mark:
        mark = error.problem_mark
        said = ", ".join(part for part in (error.context, error.problem) if part)
        return f"line {mark.line + 1}, column {mark.column + 1}: {said}"
    if isinstance(error, (ValueError, KeyError, IndexError)):
        return "a value does not fit its tag"
    return "not valid YAML"


def _port_findings(name: str, service_name: object, ports: list) -> list[Finding]:
    findings = []
    for port in ports:
        description = _describe(port)
        if description is None:
            findings.append(Finding(COMPOSE_PORT, name, None,
                                    f"service {service_name} has a port that is not a string, a number or a mapping whose published (or, without one, target) is text or a number (unreadable).",
                                    "write each port as a string, a number or a mapping whose published (or, without one, target) is text or a number."))
        elif not _binds_loopback(port):
            findings.append(Finding(COMPOSE_PORT, name, None,
                                    f"service {service_name} publishes port {description} beyond loopback.",
                                    f"bind it explicitly, for example \"{LOOPBACK}:HOST:CONTAINER\"."))
    return findings


def _binds_loopback(port: object) -> bool:
    if isinstance(port, dict):
        return port.get("host_ip") in LOOPBACK_HOSTS
    spec = INTERPOLATION.sub("-", str(port))
    if spec.startswith("["):
        host, _, ports = spec[1:].partition("]")
        parts = [host, *ports.split(":")[1:]]
    else:
        parts = spec.split(":")
    return len(parts) == 3 and parts[0] in LOOPBACK_HOSTS


def _describe(port: object) -> str | None:
    """The port as written, or None when it is not text, a number or a mapping of one; a container is never printed."""
    if isinstance(port, dict):
        port = port.get("published", port.get("target"))
    return str(port) if isinstance(port, (str, int, float)) else None


def read_sources(root: Path, tracked: list[str]) -> dict[str, bytes]:
    """The tracked files of a table language on disk: a tracked file absent from it (deleted, sparse, a dangling link)
    is gone."""
    on_disk = _on_disk(root, [name for name in tracked if ratchet.language_of(name)])
    return _read_existing(repository.read_bytes, root, on_disk)


@dataclass(frozen=True)
class RuleResult:
    """A baseline rule's findings, and its report line when it only counts."""

    findings: list[Finding]
    report: str | None


def read_baseline(root: Path) -> ratchet.Baseline | None:
    """The repository's baseline on disk, None when it has none.

    A file that cannot be read cannot be judged; a malformed one raises SizeError.
    """
    content = _judged(repository.read_bytes, root, ratchet.BASELINE_PATH)
    if content is None:
        return None
    try:
        return ratchet.load_baseline(content.decode("utf-8"))
    except UnicodeDecodeError as error:
        raise ratchet.SizeError(f"{ratchet.BASELINE_PATH} is not valid UTF-8 ({error})") from error


def judged_baseline(root: Path) -> ratchet.Baseline | None:
    """The baseline both baseline rules judge by, read once; a file that cannot be read cannot be judged."""
    try:
        return read_baseline(root)
    except ratchet.SizeError as error:
        raise CannotJudge(f"{error}; {ratchet.head_advice(error)}") from error


def _complete(tree: ratchet.TreeMeasurement) -> ratchet.Measurements:
    """Holding a baseline needs every function measured, so an unparsable file cannot be judged."""
    try:
        return tree.complete()
    except ratchet.UnparsableSource as error:
        raise Unparsable(str(error)) from error


def check_size(tree: ratchet.TreeMeasurement, baseline: ratchet.Baseline | None) -> RuleResult:
    """Report-only without a baseline; with one, the tree on disk must hold it."""
    if baseline is None:
        return RuleResult([], ratchet.report_counts(tree))
    return RuleResult(ratchet.compare(_complete(tree), baseline.size), None)


def check_clones(tree: ratchet.TreeMeasurement, sources: dict[str, bytes], baseline: ratchet.Baseline | None) -> RuleResult:
    """Report-only without a `[clones]` table, even in a baseline of size tables; with one, every new pair and every
    held pair the tree lost is a finding. A pair can hide in a file the detector cannot read, so then it cannot judge."""
    pairs = clones.find_pairs(sources)
    if baseline is None or baseline.clones is None:
        return RuleResult([], clones.report_line(pairs.pairs))
    _complete(tree)
    return RuleResult(clones.compare_pairs(pairs, baseline.clones), None)


def check_import_contract(root: Path, tracked: list[str]) -> architecture.ContractsVerdict:
    """Report-only without an import-linter config at the top; with one, its contracts must be kept.

    A config counts when its name exists, so a tracked one deleted from the tree is none and a link to nowhere cannot be read.
    """
    configs = _read_existing(repository.read_text, root, [name for name in tracked if name in architecture.CONFIG_FILES])
    try:
        return architecture.check_contracts(root, tracked, configs)
    except architecture.UnparsableModule as error:
        raise Unparsable(str(error)) from error
    except architecture.ContractError as error:
        raise CannotJudge(str(error)) from error


def lint_python(root: Path, locate_ruff: Callable[[], str]) -> python_lint.Lint:
    """ruff's one run over every tracked regular `.py` file, which python-security and python-print both judge."""
    try:
        return python_lint.lint(root, index_modes(root), PYTHON_TARGET, locate_ruff)
    except python_lint.UnparsablePython as error:
        raise Unparsable(str(error)) from error
    except python_lint.LintError as error:
        raise CannotJudge(str(error)) from error


def _judge_lint[T](lint: python_lint.Lint | CannotJudge, judge: Callable[[python_lint.Lint], T]) -> T | CannotJudge:
    """One rule's verdict on the shared run; a run that cannot judge refuses both rules, a judge's own refusal only its rule."""
    if isinstance(lint, CannotJudge):
        return lint
    try:
        return judge(lint)
    except python_lint.LintError as error:
        return CannotJudge(str(error))


def check_secrets(root: Path, engine_dir: Path) -> engines.SecretScan:
    try:
        return engines.scan_secrets(root, engine_dir, engines.read_pins(), read_secret_allowances(root))
    except (process.ProcessError, ConfigError) as error:
        raise CannotJudge(str(error)) from error


def check_workflows(root: Path, engine_dir: Path, rule: str, tracked: list[str]) -> list[Finding]:
    workflows = _on_disk(root, [name for name in tracked if _is_workflow(PurePosixPath(name))])
    try:
        return engines.scan_workflows(root, engine_dir, engines.read_pins(), rule, workflows)
    except process.ProcessError as error:
        raise CannotJudge(str(error)) from error


def check_advisories(root: Path, engine_dir: Path, tracked: list[str]) -> advisories.AdvisoryScan:
    """The allowances' `until` is judged against today's date in UTC, read here, once."""
    try:
        allowances = read_advisory_allowances(root)
    except ConfigError as error:
        raise CannotJudge(str(error)) from error
    locks = {name: _judged(repository.resolve_file, root, name) for name in _on_disk(root, [n for n in tracked if advisories.is_lock(n)])}
    try:
        return advisories.scan(engine_dir, engines.read_pins(), locks, root, allowances, datetime.now(UTC).date())
    except process.ProcessError as error:
        raise CannotJudge(str(error)) from error


def _is_workflow(path: PurePosixPath) -> bool:
    return path.parent == WORKFLOW_DIRECTORY and path.suffix in YAML_SUFFIXES


def skipped_rules(engine_dir: Path | None) -> tuple[str, ...]:
    return ENGINE_RULE_IDS if engine_dir is None else ()


class Outcome(Enum):
    """A rule's verdict, mildest first: the worst verdict of a run decides its exit."""

    CLEAN = "clean"
    FINDINGS = "findings"
    UNPARSABLE = Unparsable.label
    CANNOT_JUDGE = CannotJudge.label


@dataclass(frozen=True)
class RuleVerdict:
    rule: str
    findings: tuple[Finding, ...] = ()
    refusal: CannotJudge | None = None

    @property
    def outcome(self) -> Outcome:
        if self.refusal is not None:
            return Outcome(self.refusal.label)
        return Outcome.FINDINGS if self.findings else Outcome.CLEAN


@dataclass(frozen=True)
class HygieneResult:
    verdicts: tuple[RuleVerdict, ...]
    reports: tuple[str, ...]
    allowed_secrets: list[Finding]
    unused_allowances: dict[Allowance, str]

    @property
    def findings(self) -> list[Finding]:
        return [finding for verdict in self.verdicts for finding in verdict.findings]

    @property
    def worst(self) -> Outcome:
        severity = list(Outcome)
        return max((verdict.outcome for verdict in self.verdicts), key=severity.index, default=Outcome.CLEAN)


def _attempt[T](check: Callable[[], T]) -> T | CannotJudge:
    """The check's result, or the reason it cannot judge, so one rule's refusal never stops the others."""
    try:
        return check()
    except CannotJudge as refusal:
        return refusal


def _verdict(rule: str, outcome: Sequence[Finding] | CannotJudge) -> RuleVerdict:
    """The verdict of the rule a check judges; a check that cannot judge leaves its rule unjudged."""
    if isinstance(outcome, CannotJudge):
        return RuleVerdict(rule, refusal=outcome)
    foreign = sorted({finding.rule for finding in outcome} - {rule})
    if foreign:
        raise ValueError(f"a check judging {rule} returned findings under {', '.join(foreign)}")
    return RuleVerdict(rule, tuple(outcome))


def _judge_baseline_rules(
    sources: dict[str, bytes] | CannotJudge, baseline: ratchet.Baseline | None | CannotJudge
) -> tuple[RuleResult | CannotJudge, RuleResult | CannotJudge]:
    """The size and clone verdicts, both from one measurement of the tree and one read of the baseline."""
    if isinstance(sources, CannotJudge):
        return sources, sources
    if isinstance(baseline, CannotJudge):
        return baseline, baseline
    tree = ratchet.measure(sources)
    return _attempt(lambda: check_size(tree, baseline)), _attempt(lambda: check_clones(tree, sources, baseline))


def _content_findings(
    root: Path, candidates: Callable[[dict[str, str]], list[str]], check: Callable[..., list[Finding]]
) -> list[Finding] | CannotJudge:
    """The findings of a rule over the index modes and the contents of the files `candidates` names."""

    def judge() -> list[Finding]:
        modes = index_modes(root)
        contents = _read_existing(repository.read_bytes, root, _on_disk(root, candidates(modes)))
        try:
            return check(modes, contents, _declared(read_layout_allowances, root))
        except layout.UnparsableTestModule as error:
            raise Unparsable(str(error)) from error

    return _attempt(judge)


def run_hygiene(root: Path, engine_dir: Path | None, locate_ruff: Callable[[], str]) -> HygieneResult:
    """Judges every active rule, in run order, over the repository `root` lies in; only one git cannot list stops the run."""
    root = work_tree_top(root)
    tracked = tracked_files(root)
    sources = _attempt(lambda: read_sources(root, tracked))
    baseline = sources if isinstance(sources, CannotJudge) else _attempt(lambda: judged_baseline(root))
    size, clone = _judge_baseline_rules(sources, baseline)
    contracts = _attempt(lambda: check_import_contract(root, tracked))
    floor = _attempt(lambda: _python_floor_result(root))
    lint = _attempt(lambda: lint_python(root, locate_ruff))
    printing = _judge_lint(lint, python_lint.judge_print)
    verdicts = [
        _verdict(ENV_FILE, check_env_files(tracked)),
        _verdict(COMPOSE_PORT, _attempt(lambda: check_compose_ports(root, tracked, _declared(read_compose_allowances, root)))),
        _verdict(ratchet.SIZE, size if isinstance(size, CannotJudge) else size.findings),
        _verdict(clones.RULE, clone if isinstance(clone, CannotJudge) else clone.findings),
        _verdict(architecture.RULE, contracts if isinstance(contracts, CannotJudge) else contracts.findings),
        _verdict(CLAUDE_MD, _attempt(lambda: check_claude_md(root, tracked))),
        _verdict(ROOT_LAYOUT, _attempt(lambda: check_root_layout(tracked, _declared(read_root_allowances, root)))),
        _verdict(FILE_SUFFIX, _attempt(lambda: layout.check_file_suffix(index_modes(root), _declared(read_layout_allowances, root)))),
        _verdict(SCRIPT_NAME, _content_findings(root, layout.script_candidates, layout.check_script_name)),
        _verdict(SCRIPT_HEADER, _content_findings(root, layout.script_candidates, layout.check_script_header)),
        _verdict(TEST_LAYOUT, _attempt(lambda: layout.check_test_layout(index_modes(root), _declared(read_layout_allowances, root)))),
        _verdict(TEST_IMPORT, _content_findings(root, layout.test_import_candidates, layout.check_test_import)),
        _verdict(PYTHON_FLOOR, floor if isinstance(floor, CannotJudge) else floor.findings),
        _verdict(PYTHON_SECURITY, _judge_lint(lint, python_lint.judge_security)),
        _verdict(PYTHON_PRINT, printing if isinstance(printing, CannotJudge) else printing.findings),
    ]
    reports = _reports(size, clone, contracts, floor, printing)
    if engine_dir is None:
        return HygieneResult(tuple(verdicts), reports, [], {})
    secrets = _attempt(lambda: check_secrets(root, engine_dir))
    verdicts.append(_verdict(engines.LEAK_RULE, secrets if isinstance(secrets, CannotJudge) else secrets.findings))
    verdicts += [_verdict(rule, _attempt(partial(check_workflows, root, engine_dir, rule, tracked))) for rule in WORKFLOW_RULE_IDS]
    advisory = _attempt(partial(check_advisories, root, engine_dir, tracked))
    verdicts.append(_verdict(advisories.RULE, advisory if isinstance(advisory, CannotJudge) else advisory.findings))
    allowed_advisories = () if isinstance(advisory, CannotJudge) else advisory.report()
    allowed, unused = ([], {}) if isinstance(secrets, CannotJudge) else (secrets.allowed, secrets.unused_allowances)
    return HygieneResult(tuple(verdicts), (*reports, *allowed_advisories), allowed, unused)


def _reports(
    size: RuleResult | CannotJudge,
    clone: RuleResult | CannotJudge,
    contracts: architecture.ContractsVerdict | CannotJudge,
    floor: FloorResult | CannotJudge,
    printing: python_lint.PrintResult | CannotJudge,
) -> tuple[str, ...]:
    """The report lines of the rules that judged: the size counts without a baseline, the clone pairs without a `[clones]` table, the import-contract notes, each python-floor exception, each output owner."""
    size_reports = [] if isinstance(size, CannotJudge) or size.report is None else [size.report]
    clone_reports = [] if isinstance(clone, CannotJudge) or clone.report is None else [clone.report]
    contract_notes = [] if isinstance(contracts, CannotJudge) else contracts.notes
    floor_reports = () if isinstance(floor, CannotJudge) else floor.reports
    owner_reports = () if isinstance(printing, CannotJudge) else printing.reports
    return (*size_reports, *clone_reports, *contract_notes, *floor_reports, *owner_reports)
