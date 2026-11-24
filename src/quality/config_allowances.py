from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import PurePosixPath

from quality import config_entries
from quality.config_entries import ONE_LINE_REASON, is_exact_file_path
from quality.rules import Rule

ROOT_ALLOWANCE_KEYS = {"entry", "reason"}
NOT_ROOT_ENTRY_NAMES = {".", "..", ".git"}
LAYOUT_ALLOWANCE_KEYS = {"path", "rule", "reason"}
PYTHON_ALLOWANCE_KEYS = {"path", "reason"}
COMPOSE_ALLOWANCE_KEYS = {"file", "service", "reason"}
WORKFLOW_ALLOWANCE_KEYS = {"file", "reason"}
ROOT_ALLOWANCE_HEADING = "[[root.allow]]"
LAYOUT_ALLOWANCE_HEADING = "[[layout.allow]]"
PYTHON_ALLOWANCE_HEADING = "[[python.allow]]"
COMPOSE_ALLOWANCE_HEADING = "[[compose.allow]]"
WORKFLOW_ALLOWANCE_HEADING = "[[workflow.allow]]"
ROOT_ALLOWANCE_EXAMPLE: config_entries.ExampleLines = (
    ROOT_ALLOWANCE_HEADING,
    'entry = "frontend/"',
    'reason = "the web client, built apart from the Python package"',
)
LAYOUT_ALLOWANCE_EXAMPLE: config_entries.ExampleLines = (
    LAYOUT_ALLOWANCE_HEADING,
    'path = "deploy/EDGE_SHA"',
    'rule = "file-suffix"',
    'reason = "the deploy script reads it by this name"',
)
PYTHON_ALLOWANCE_EXAMPLE: config_entries.ExampleLines = (
    PYTHON_ALLOWANCE_HEADING,
    'path = "speech/providers/qwen/pyproject.toml"',
    'reason = "qwen-tts has no cp314 wheel"',
)
COMPOSE_ALLOWANCE_EXAMPLE: config_entries.ExampleLines = (
    COMPOSE_ALLOWANCE_HEADING,
    'file = "compose.yaml"',
    'service = "traefik"',
    'reason = "it terminates TLS on the host\'s own interfaces"',
)
WORKFLOW_ALLOWANCE_EXAMPLE: config_entries.ExampleLines = (
    WORKFLOW_ALLOWANCE_HEADING,
    'file = ".github/workflows/preview.yml"',
    'reason = "it deploys the pull request preview and no check belongs to it"',
)


@dataclass(frozen=True)
class RootAllowance:
    """A root entry the repository declares beyond quality's base list: a file `Makefile` or a directory `frontend/`."""

    entry: str
    reason: str


@dataclass(frozen=True)
class PythonAllowance:
    """A `pyproject.toml` the repository allows below the Python floor: its exact tracked path and why."""

    path: str
    reason: str


LAYOUT_RULES = (
    Rule.AGENTS_MD, Rule.FILE_SUFFIX, Rule.SCRIPT_NAME, Rule.SCRIPT_HEADER, Rule.TEST_LAYOUT, Rule.TEST_IMPORT
)


@dataclass(frozen=True)
class LayoutAllowance:
    """A tracked file, by its exact path, the repository declares against one layout rule."""

    path: str
    rule: Rule
    reason: str


def declared_paths(allowances: Sequence[LayoutAllowance], rule: Rule) -> set[str]:
    return {allowance.path for allowance in allowances if allowance.rule is rule}


@dataclass(frozen=True)
class ComposeAllowance:
    """A compose service, by its exact tracked file and name, the repository declares host-networked."""

    file: str
    service: str
    reason: str


@dataclass(frozen=True)
class WorkflowAllowance:
    """A workflow a pull request triggers that is neither the gate's caller nor CodeQL's, declared by its exact tracked
    path with the reason it stands outside them."""

    file: str
    reason: str


def workflow_allowance_entry(file: str) -> config_entries.ExampleLines:
    """The entry that declares the workflow `file`, as the block the reader takes; the reason is the person's to write."""
    return (WORKFLOW_ALLOWANCE_HEADING, f"file = {config_entries.toml_string(file)}")


def root_allowances(root: dict) -> tuple[RootAllowance, ...]:
    return config_entries.unique_entries(
        root, "allow", ROOT_ALLOWANCE_HEADING, ROOT_ALLOWANCE_EXAMPLE, _read_root_allowance,
        lambda allowance: allowance.entry,
        lambda allowance: f"declares entry = {config_entries.toml_string(allowance.entry)} twice; one entry per root entry",
    )


def _is_root_entry(entry: object) -> bool:
    """One name directly in the root, a directory with its trailing `/`; no path, no `.`, `..` or `.git`."""
    if not isinstance(entry, str):
        return False
    name = entry.removesuffix("/")
    return bool(name.strip()) and "/" not in name and name not in NOT_ROOT_ENTRY_NAMES


def _read_root_allowance(entry: object, position: int) -> RootAllowance:
    refuse = config_entries.entry_refusal(ROOT_ALLOWANCE_HEADING, position, ROOT_ALLOWANCE_EXAMPLE)
    if not isinstance(entry, dict) or set(entry) != ROOT_ALLOWANCE_KEYS:
        raise refuse("exactly an entry and a reason")
    name, reason = entry["entry"], entry["reason"]
    if not _is_root_entry(name):
        raise refuse("entry as one name directly in the root, a directory with a trailing /, not a path")
    if not config_entries.is_one_line_text(reason):
        raise refuse(ONE_LINE_REASON)
    return RootAllowance(name, reason)


def layout_allowances(layout: dict) -> tuple[LayoutAllowance, ...]:
    return config_entries.unique_entries(
        layout, "allow", LAYOUT_ALLOWANCE_HEADING, LAYOUT_ALLOWANCE_EXAMPLE, _read_layout_allowance,
        lambda allowance: (allowance.rule, allowance.path),
        lambda allowance: f"declares path = {config_entries.toml_string(allowance.path)} for rule = "
        f"{config_entries.toml_string(allowance.rule.value)} twice; one declaration per rule and path",
    )


def python_allowances(python: dict) -> tuple[PythonAllowance, ...]:
    return config_entries.unique_entries(
        python, "allow", PYTHON_ALLOWANCE_HEADING, PYTHON_ALLOWANCE_EXAMPLE, _read_python_allowance,
        lambda allowance: allowance.path,
        lambda allowance: f"declares path = {config_entries.toml_string(allowance.path)} twice; one entry per path",
    )


def _read_python_allowance(entry: object, position: int) -> PythonAllowance:
    refuse = config_entries.entry_refusal(PYTHON_ALLOWANCE_HEADING, position, PYTHON_ALLOWANCE_EXAMPLE)
    if not isinstance(entry, dict) or set(entry) != PYTHON_ALLOWANCE_KEYS:
        raise refuse("exactly a path and a reason")
    path, reason = entry["path"], entry["reason"]
    if not is_exact_file_path(path) or PurePosixPath(path).name != "pyproject.toml":
        raise refuse("path as the exact tracked path of one pyproject.toml, no folder, pattern or ..")
    if not config_entries.is_one_line_text(reason):
        raise refuse(ONE_LINE_REASON)
    return PythonAllowance(path, reason)


def _read_layout_allowance(entry: object, position: int) -> LayoutAllowance:
    refuse = config_entries.entry_refusal(LAYOUT_ALLOWANCE_HEADING, position, LAYOUT_ALLOWANCE_EXAMPLE)
    if not isinstance(entry, dict) or set(entry) != LAYOUT_ALLOWANCE_KEYS:
        raise refuse("exactly a path, a rule and a reason")
    path, rule, reason = entry["path"], entry["rule"], entry["reason"]
    if not is_exact_file_path(path):
        raise refuse("path as the exact tracked path of one file: no folder, pattern, .. or absolute path")
    if rule not in LAYOUT_RULES:
        raise refuse(f"rule as one of {', '.join(sorted(str(known) for known in LAYOUT_RULES))}")
    if not config_entries.is_one_line_text(reason):
        raise refuse(ONE_LINE_REASON)
    return LayoutAllowance(path, Rule(rule), reason)


def compose_allowances(compose: dict) -> tuple[ComposeAllowance, ...]:
    return config_entries.unique_entries(
        compose, "allow", COMPOSE_ALLOWANCE_HEADING, COMPOSE_ALLOWANCE_EXAMPLE, _read_compose_allowance,
        lambda allowance: (allowance.file, allowance.service),
        lambda allowance: f"declares service = {config_entries.toml_string(allowance.service)} of file = "
        f"{config_entries.toml_string(allowance.file)} twice; one declaration per file and service",
    )


def _read_compose_allowance(entry: object, position: int) -> ComposeAllowance:
    refuse = config_entries.entry_refusal(COMPOSE_ALLOWANCE_HEADING, position, COMPOSE_ALLOWANCE_EXAMPLE)
    if not isinstance(entry, dict) or set(entry) != COMPOSE_ALLOWANCE_KEYS:
        raise refuse("exactly a file, a service and a reason")
    file, service, reason = entry["file"], entry["service"], entry["reason"]
    if not is_exact_file_path(file):
        raise refuse("file as the exact tracked path of one compose file: no folder, pattern, .. or absolute path")
    if not config_entries.is_one_line_text(service):
        raise refuse("service as the one-line name of a service in that file")
    if not config_entries.is_one_line_text(reason):
        raise refuse(ONE_LINE_REASON)
    return ComposeAllowance(file, service, reason)


def workflow_allowances(workflow: dict) -> tuple[WorkflowAllowance, ...]:
    return config_entries.unique_entries(
        workflow, "allow", WORKFLOW_ALLOWANCE_HEADING, WORKFLOW_ALLOWANCE_EXAMPLE, _read_workflow_allowance,
        lambda allowance: allowance.file,
        lambda allowance: f"declares file = {config_entries.toml_string(allowance.file)} twice; one entry per workflow",
    )


def _read_workflow_allowance(entry: object, position: int) -> WorkflowAllowance:
    refuse = config_entries.entry_refusal(WORKFLOW_ALLOWANCE_HEADING, position, WORKFLOW_ALLOWANCE_EXAMPLE)
    if not isinstance(entry, dict) or set(entry) != WORKFLOW_ALLOWANCE_KEYS:
        raise refuse("exactly a file and a reason")
    file, reason = entry["file"], entry["reason"]
    if not is_exact_file_path(file):
        raise refuse("file as the exact tracked path of one workflow: no folder, pattern, .. or absolute path")
    if not config_entries.is_one_line_text(reason):
        raise refuse(ONE_LINE_REASON)
    return WorkflowAllowance(file, reason)
