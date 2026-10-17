import errno
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import yaml

from quality import engines, ratchet
from quality.config import Allowance, ConfigError, read_secret_allowances
from quality.report import Finding

ENV_FILE = "env-file"
COMPOSE_PORT = "compose-port"
FILE_RULE_IDS = (ENV_FILE, COMPOSE_PORT, ratchet.SIZE)
ENGINE_RULE_IDS = (engines.LEAK_RULE, engines.WORKFLOW_LINT, engines.WORKFLOW_SECURITY)
WORKFLOW_DIRECTORY = PurePosixPath(".github/workflows")
WORKFLOW_SUFFIXES = (".yml", ".yaml")
GONE_ERRNOS = frozenset({errno.ENOENT, errno.ENOTDIR, errno.ELOOP})

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

    status = "cannot judge"


class Unparsable(CannotJudge):
    """A tracked file Python cannot decode or parse; only an operating-system read error is `cannot judge`."""

    status = "unparsable"


def tracked_files(root: Path) -> list[str]:
    if not root.is_dir():
        raise CannotJudge(f"{root} is not a directory")
    try:
        listing = subprocess.run(
            ["git", "-C", str(root.resolve()), "ls-files", "-z", "--"],
            capture_output=True, check=True,
        )
    except (OSError, subprocess.CalledProcessError) as error:
        raise CannotJudge(f"{root} is not a readable git repository: {error}") from error
    return [os.fsdecode(name) for name in listing.stdout.split(b"\0") if name]


def _printable(name: str) -> str:
    return name.encode("utf-8", "backslashreplace").decode("utf-8")


def check_env_files(tracked: list[str]) -> list[Finding]:
    return [
        Finding(
            ENV_FILE, _printable(name), None,
            "a tracked env file publishes configuration and often secrets.",
            f"git rm --cached {_printable(name)} and add it to .gitignore.",
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


def read_repository_bytes(root: Path, name: str) -> bytes:
    """A file of the repository; one that resolves outside it, or cannot be read, cannot be judged."""
    repository = root.resolve()
    path = repository / name
    try:
        if not path.resolve().is_relative_to(repository):
            raise CannotJudge(f"{name} resolves outside the repository")
        return path.read_bytes()
    except (OSError, RuntimeError) as error:
        raise CannotJudge(f"cannot read {name}: {error}") from error


def read_repository_text(root: Path, name: str) -> str:
    """A file of the repository as UTF-8 text; one that is not UTF-8 cannot be judged."""
    try:
        return read_repository_bytes(root, name).decode("utf-8")
    except UnicodeDecodeError as error:
        raise CannotJudge(f"cannot read {name}: {error}") from error


def write_repository_text(root: Path, name: str, text: str) -> None:
    """Writes a file of the repository, only where `name` itself lies; one it cannot write cannot be judged.

    A symlink on the way would redirect the write, possibly onto a file outside the repository.
    """
    path = root.resolve() / name
    try:
        resolved = path.resolve()
        if resolved != path:
            raise CannotJudge(f"{name} resolves to {resolved}, not to the repository's own {name}")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    except (OSError, RuntimeError) as error:
        raise CannotJudge(f"cannot write {name}: {error}") from error


def _compose_file_findings(root: Path, name: str) -> list[Finding]:
    text = read_repository_text(root, name)
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
    on_disk = [name for name in tracked if ratchet.is_python(name) and _is_on_disk(root, name)]
    return ratchet.measure({name: read_repository_bytes(root, name) for name in on_disk})


def _is_on_disk(root: Path, name: str) -> bool:
    """A path that leads nowhere is gone; one the filesystem refuses to reach cannot be judged."""
    try:
        (root / name).stat()
    except OSError as error:
        if error.errno in GONE_ERRNOS:
            return False
        raise CannotJudge(f"cannot reach {name}: {error}") from error
    return True


@dataclass(frozen=True)
class SizeResult:
    findings: list[Finding]
    report: str | None


def read_baseline(root: Path) -> ratchet.Baseline | None:
    """The repository's size baseline on disk, None when it has none; an unreadable one raises SizeError."""
    if not (root / ratchet.BASELINE_PATH).exists(follow_symlinks=False):
        return None
    return ratchet.load_baseline(read_repository_text(root, ratchet.BASELINE_PATH))


def check_size(root: Path, tracked: list[str]) -> SizeResult:
    """Report-only without a baseline; with one, the tree on disk must hold it."""
    tree = measure_size(root, tracked)
    try:
        baseline = read_baseline(root)
    except ratchet.SizeError as error:
        raise CannotJudge(str(error)) from error
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


def check_workflows(root: Path, engine_dir: Path, tracked: list[str]) -> list[Finding]:
    workflows = [name for name in tracked if _is_workflow(PurePosixPath(name))]
    try:
        return engines.scan_workflows(root, engine_dir, engines.read_pins(), workflows)
    except engines.EngineError as error:
        raise CannotJudge(str(error)) from error


def _is_workflow(path: PurePosixPath) -> bool:
    return path.parent == WORKFLOW_DIRECTORY and path.suffix in WORKFLOW_SUFFIXES


def active_rules(engine_dir: Path | None) -> tuple[str, ...]:
    return FILE_RULE_IDS if engine_dir is None else (*FILE_RULE_IDS, *ENGINE_RULE_IDS)


def skipped_rules(engine_dir: Path | None) -> tuple[str, ...]:
    return tuple(rule for rule in ENGINE_RULE_IDS if rule not in active_rules(engine_dir))


@dataclass(frozen=True)
class HygieneResult:
    findings: list[Finding]
    size_report: str | None
    allowed_secrets: list[Finding]
    unused_allowances: dict[Allowance, str]


def run_hygiene(root: Path, engine_dir: Path | None = None) -> HygieneResult:
    tracked = tracked_files(root)
    size = check_size(root, tracked)
    findings = check_env_files(tracked) + check_compose_ports(root, tracked) + size.findings
    if engine_dir is None:
        return HygieneResult(findings, size.report, [], {})
    secrets = check_secrets(root, engine_dir)
    findings += secrets.findings + check_workflows(root, engine_dir, tracked)
    return HygieneResult(findings, size.report, secrets.allowed, secrets.unused_allowances)
