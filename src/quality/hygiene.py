import os
import re
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import Enum
from functools import partial
from pathlib import Path, PurePosixPath

import yaml

from quality import engines, ratchet, repository
from quality.config import Allowance, ConfigError, read_secret_allowances
from quality.report import Finding, printable

ENV_FILE = "env-file"
COMPOSE_PORT = "compose-port"
WORKFLOW_RULE_IDS = tuple(engines.WORKFLOW_ENGINES)
ENGINE_RULE_IDS = (engines.LEAK_RULE, *WORKFLOW_RULE_IDS)
WORKFLOW_DIRECTORY = PurePosixPath(".github/workflows")
WORKFLOW_SUFFIXES = (".yml", ".yaml")

LOOPBACK = "127.0.0.1"
LOOPBACK_HOSTS = (LOOPBACK, "::1")
ENV_TEMPLATE_SUFFIX = ".example"
INTERPOLATION = re.compile(r"\$\{[^}]*\}")
COMPOSE_PATTERNS = ("compose*.yml", "compose*.yaml", "docker-compose*.yml", "docker-compose*.yaml")


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


class CannotJudge(Exception):
    """The repository cannot be examined; this is never a clean result."""

    label = "cannot judge"


class Unparsable(CannotJudge):
    """A tracked file Python cannot decode or parse; only an operating-system read error is `cannot judge`."""

    label = "unparsable"


def tracked_files(root: Path) -> list[str]:
    if not root.is_dir():
        raise CannotJudge(f"{root} is not a directory")
    try:
        listing = subprocess.run(["git", "-C", str(root.resolve()), "ls-files", "-z", "--"], capture_output=True, check=False)
    except OSError as error:
        raise CannotJudge(f"{root} is not a readable git repository: git did not run ({repository.os_error_reason(error)})") from error
    if listing.returncode != 0:
        # git's own words name the checkout it resolved, so only the exit code is printed and git is asked again.
        raise CannotJudge(
            f"{root} is not a readable git repository: git ls-files exited {listing.returncode}"
            f" (`git -C {root} status` says why)"
        )
    return [os.fsdecode(name) for name in listing.stdout.split(b"\0") if name]


def check_env_files(tracked: list[str]) -> list[Finding]:
    return [
        Finding(
            ENV_FILE, name, None,
            "a tracked env file publishes configuration and often secrets.",
            f"git rm --cached {printable(name)} and add it to .gitignore.",
        )
        for name in tracked
        if _is_env_file(PurePosixPath(name).name)
    ]


def _is_env_file(name: str) -> bool:
    if name.endswith(ENV_TEMPLATE_SUFFIX):
        return False
    return name == ".env" or name.startswith(".env.") or name.endswith(".env")


def check_compose_ports(root: Path, tracked: list[str]) -> list[Finding]:
    findings = []
    for name in tracked:
        if any(PurePosixPath(name).match(pattern) for pattern in COMPOSE_PATTERNS):
            findings.extend(_compose_file_findings(root, name))
    return findings


def _judged[T](ask: Callable[[Path, str], T], root: Path, name: str) -> T:
    """Asks the repository reader; a file it cannot use cannot be judged."""
    try:
        return ask(root, name)
    except repository.RepositoryFileError as error:
        raise CannotJudge(str(error)) from error


def _read_tracked[T](read: Callable[[Path, str], T | None], root: Path, name: str) -> T:
    """A tracked file's content; until every rule asks is_on_disk (Q1b-3d), one the tree lacks cannot be judged."""
    content = _judged(read, root, name)
    if content is None:
        raise CannotJudge(f"{name} is tracked but not on disk")
    return content


def _compose_file_findings(root: Path, name: str) -> list[Finding]:
    text = _read_tracked(repository.read_text, root, name)
    try:
        document = yaml.load(text, Loader=ComposeLoader)
    except yaml.YAMLError as error:
        raise CannotJudge(f"cannot read {name}: {error}") from error
    services = document.get("services") if isinstance(document, dict) else None
    if not isinstance(services, dict):
        return []
    findings = []
    for service_name, service in services.items():
        if not isinstance(service, dict):
            continue
        ports = service.get("ports") or []
        if not isinstance(ports, list):
            findings.append(Finding(COMPOSE_PORT, name, None,
                                    f"service {service_name} has ports that are not a list (unreadable).",
                                    "write ports as a list so each one can be checked."))
            continue
        findings.extend(
            Finding(COMPOSE_PORT, name, None,
                    f"service {service_name} publishes port {_describe(port)} beyond loopback.",
                    f"bind it explicitly, for example \"{LOOPBACK}:HOST:CONTAINER\".")
            for port in ports
            if not _binds_loopback(port)
        )
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


def _describe(port: object) -> str:
    return str(port) if not isinstance(port, dict) else str(port.get("published", port.get("target", port)))


def measure_size(root: Path, tracked: list[str]) -> ratchet.TreeMeasurement:
    """Measures the tree on disk: a tracked file absent from it (deleted, sparse, a dangling link) is gone."""
    on_disk = [name for name in tracked if ratchet.is_python(name) and _judged(repository.is_on_disk, root, name)]
    return ratchet.measure({name: _read_tracked(repository.read_bytes, root, name) for name in on_disk})


@dataclass(frozen=True)
class SizeResult:
    findings: list[Finding]
    report: str | None


def read_baseline(root: Path) -> ratchet.Baseline | None:
    """The repository's size baseline on disk, None when it has none.

    A file that cannot be read cannot be judged; a malformed one raises SizeError.
    """
    text = _judged(repository.read_text, root, ratchet.BASELINE_PATH)
    return None if text is None else ratchet.load_baseline(text)


def check_size(root: Path, tracked: list[str]) -> SizeResult:
    """Report-only without a baseline; with one, the tree on disk must hold it."""
    tree = measure_size(root, tracked)
    try:
        baseline = read_baseline(root)
    except ratchet.SizeError as error:
        raise CannotJudge(f"{error}; {ratchet.REWRITE_HINT}") from error
    if baseline is None:
        return SizeResult([], ratchet.report_counts(tree))
    try:
        measurements = tree.complete()
    except ratchet.UnparsableSource as error:
        raise Unparsable(str(error)) from error
    return SizeResult(ratchet.compare(measurements, baseline), None)


def check_secrets(root: Path, engine_dir: Path) -> engines.SecretScan:
    try:
        return engines.scan_secrets(root, engine_dir, engines.read_pins(), read_secret_allowances(root))
    except (engines.EngineError, ConfigError) as error:
        raise CannotJudge(str(error)) from error


def check_workflows(root: Path, engine_dir: Path, rule: str, tracked: list[str]) -> list[Finding]:
    workflows = [name for name in tracked if _is_workflow(PurePosixPath(name))]
    try:
        return engines.scan_workflows(root, engine_dir, engines.read_pins(), rule, workflows)
    except engines.EngineError as error:
        raise CannotJudge(str(error)) from error


def _is_workflow(path: PurePosixPath) -> bool:
    return path.parent == WORKFLOW_DIRECTORY and path.suffix in WORKFLOW_SUFFIXES


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
    size_report: str | None
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


def _verdicts(rules: Sequence[str], outcome: Sequence[Finding] | CannotJudge) -> list[RuleVerdict]:
    """One verdict per rule a check judges; a check that cannot judge leaves each of its rules unjudged."""
    if isinstance(outcome, CannotJudge):
        return [RuleVerdict(rule, refusal=outcome) for rule in rules]
    foreign = sorted({finding.rule for finding in outcome} - set(rules))
    if foreign:
        raise ValueError(f"a check judging {', '.join(rules)} returned findings under {', '.join(foreign)}")
    return [RuleVerdict(rule, tuple(finding for finding in outcome if finding.rule == rule)) for rule in rules]


def run_hygiene(root: Path, engine_dir: Path | None = None) -> HygieneResult:
    """Judges every active rule, in run order; only a repository git cannot list stops the run."""
    tracked = tracked_files(root)
    size = _attempt(lambda: check_size(root, tracked))
    verdicts = [
        *_verdicts([ENV_FILE], check_env_files(tracked)),
        *_verdicts([COMPOSE_PORT], _attempt(lambda: check_compose_ports(root, tracked))),
        *_verdicts([ratchet.SIZE], size if isinstance(size, CannotJudge) else size.findings),
    ]
    size_report = None if isinstance(size, CannotJudge) else size.report
    if engine_dir is None:
        return HygieneResult(tuple(verdicts), size_report, [], {})
    secrets = _attempt(lambda: check_secrets(root, engine_dir))
    verdicts += _verdicts([engines.LEAK_RULE], secrets if isinstance(secrets, CannotJudge) else secrets.findings)
    for rule in WORKFLOW_RULE_IDS:
        verdicts += _verdicts([rule], _attempt(partial(check_workflows, root, engine_dir, rule, tracked)))
    allowed, unused = ([], {}) if isinstance(secrets, CannotJudge) else (secrets.allowed, secrets.unused_allowances)
    return HygieneResult(tuple(verdicts), size_report, allowed, unused)
