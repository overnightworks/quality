from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import PurePosixPath

from quality import config_entries
from quality.config_entries import ONE_LINE_REASON, RULING_KEYS, Ruling, is_exact_file_path
from quality.rules import Rule

ROOT_ALLOWANCE_KEYS = {"entry", "reason"}
NOT_ROOT_ENTRY_NAMES = {".", "..", ".git"}
LAYOUT_ALLOWANCE_KEYS = {"path", "rule", "reason"}
HEAD_ENTRY_EXTRA_KEYS = {"ends"} | RULING_KEYS
ENDS_EXPECTED = "ends as the open item whose landing removes it: #<number> or <owner>/<repo>#<number>"
RULING_TOGETHER = "an item and a date together or not at all"
# What a person writes for an entry of the two tables, as a fix asking for one names it.
DECLARATION_NEEDS = "a reason, the item that ends it, and the item and date of the operator's ruling that lets it in"
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
    'entry = "specs/"',
    'reason = "the written requirements, kept apart from the code"',
    'ends = "#123"',
)
LAYOUT_ALLOWANCE_EXAMPLE: config_entries.ExampleLines = (
    LAYOUT_ALLOWANCE_HEADING,
    'path = "deploy/EDGE_SHA"',
    'rule = "file-suffix"',
    'reason = "the deploy script reads it by this name"',
    'ends = "#123"',
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


class ConfigSide(Enum):
    """Which side of a pull request a config is read as: the head must carry every key, the base is read by identity only."""

    HEAD = "head"
    BASE = "base"


@dataclass(frozen=True)
class RootEntry:
    """A root entry as the base reads it: its name, a file `Makefile` or a directory `specs/`, and nothing else."""

    entry: str

    @property
    def identity(self) -> tuple[str, str]:
        """The path and the qualifier that tell the entry from every other."""
        return self.entry, ""


@dataclass(frozen=True)
class RootAllowance(RootEntry):
    """A root entry the head declares beyond quality's base list and its cases: why, the open item whose landing removes
    it and, once an added entry is ruled in, the operator's ruling."""

    reason: str
    ends: str
    ruling: Ruling | None = None


@dataclass(frozen=True)
class PythonAllowance:
    """A `pyproject.toml` the repository allows below the Python floor: its exact tracked path and why."""

    path: str
    reason: str


LAYOUT_RULES = (
    Rule.AGENTS_MD, Rule.FILE_SUFFIX, Rule.SCRIPT_NAME, Rule.SCRIPT_HEADER, Rule.TEST_LAYOUT, Rule.TEST_IMPORT
)


@dataclass(frozen=True)
class LayoutEntry:
    """A layout entry as the base reads it: the exact path of a tracked file and the rule it is declared against."""

    path: str
    rule: str

    @property
    def identity(self) -> tuple[str, str]:
        """The path and the qualifier that tell the entry from every other."""
        return self.path, self.rule


@dataclass(frozen=True)
class LayoutAllowance(LayoutEntry):
    """A tracked file, by its exact path, the head declares against one layout rule: why, the open item whose landing
    removes it and, once an added entry is ruled in, the operator's ruling."""

    reason: str
    ends: str
    ruling: Ruling | None = None


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


def _is_root_entry(entry: object) -> bool:
    """One name directly in the root, a directory with its trailing `/`; no path, no `.`, `..` or `.git`."""
    if not isinstance(entry, str):
        return False
    name = entry.removesuffix("/")
    return bool(name.strip()) and "/" not in name and name not in NOT_ROOT_ENTRY_NAMES


def _head_entry(refuse: Callable[[str], config_entries.ConfigError], entry: object, keys: set[str], need: str) -> dict:
    """`entry` as a table of `keys`, beside which it may hold only `ends` and a ruling's `item` and `date`."""
    if not isinstance(entry, dict) or not keys <= set(entry) <= keys | HEAD_ENTRY_EXTRA_KEYS:
        raise refuse(need)
    return entry


def _read_ends(refuse: Callable[[str], config_entries.ConfigError], entry: dict) -> str:
    ends = entry.get("ends")
    if not (isinstance(ends, str) and config_entries.ITEM_REFERENCE.fullmatch(ends)):
        raise refuse(ENDS_EXPECTED)
    return ends


def _read_root_allowance(entry: object, position: int) -> RootAllowance:
    refuse = config_entries.entry_refusal(ROOT_ALLOWANCE_HEADING, position, ROOT_ALLOWANCE_EXAMPLE)
    table = _head_entry(refuse, entry, ROOT_ALLOWANCE_KEYS, f"exactly an entry, a reason and an ends, and {RULING_TOGETHER}")
    name, reason = table["entry"], table["reason"]
    if not _is_root_entry(name):
        raise refuse("entry as one name directly in the root, a directory with a trailing /, not a path")
    if not config_entries.is_one_line_text(reason):
        raise refuse(ONE_LINE_REASON)
    return RootAllowance(name, reason, _read_ends(refuse, table), config_entries.optional_ruling(refuse, table))


def _read_root_entry(entry: object, position: int) -> RootEntry:
    """The identity of a root entry, which is all a base is read for."""
    if not isinstance(entry, dict) or not isinstance(entry.get("entry"), str):
        raise config_entries.entry_refusal(ROOT_ALLOWANCE_HEADING, position, ROOT_ALLOWANCE_EXAMPLE)("an entry as text")
    return RootEntry(entry["entry"])


def layout_allowances(layout: dict, side: ConfigSide) -> tuple[LayoutEntry, ...]:
    return config_entries.unique_entries(
        layout, "allow", LAYOUT_ALLOWANCE_HEADING, LAYOUT_ALLOWANCE_EXAMPLE,
        _read_layout_allowance if side is ConfigSide.HEAD else _read_layout_entry,
        lambda allowance: allowance.identity,
        lambda allowance: f"declares path = {config_entries.toml_string(allowance.path)} for rule = "
        f"{config_entries.toml_string(str(allowance.rule))} twice; one declaration per rule and path",
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
    table = _head_entry(refuse, entry, LAYOUT_ALLOWANCE_KEYS, f"exactly a path, a rule, a reason and an ends, and {RULING_TOGETHER}")
    path, rule, reason = table["path"], table["rule"], table["reason"]
    if not is_exact_file_path(path):
        raise refuse("path as the exact tracked path of one file: no folder, pattern, .. or absolute path")
    if rule not in LAYOUT_RULES:
        raise refuse(f"rule as one of {', '.join(sorted(str(known) for known in LAYOUT_RULES))}")
    if not config_entries.is_one_line_text(reason):
        raise refuse(ONE_LINE_REASON)
    return LayoutAllowance(path, Rule(rule), reason, _read_ends(refuse, table), config_entries.optional_ruling(refuse, table))


def _read_layout_entry(entry: object, position: int) -> LayoutEntry:
    """The identity of a layout entry, which is all a base is read for."""
    if not isinstance(entry, dict) or not all(isinstance(entry.get(key), str) for key in ("path", "rule")):
        raise config_entries.entry_refusal(LAYOUT_ALLOWANCE_HEADING, position, LAYOUT_ALLOWANCE_EXAMPLE)("a path and a rule as text")
    return LayoutEntry(entry["path"], entry["rule"])


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


def _one_per_field[Allowance](
    heading: str, example: config_entries.ExampleLines, read: Callable[[object, int], Allowance], field: str, subject: str
) -> Callable[[dict], tuple[Allowance, ...]]:
    """The reader of one `allow` list whose entries are told apart by `field`, one entry per `subject`."""
    return lambda table: config_entries.unique_entries(
        table, "allow", heading, example, read, lambda allowance: getattr(allowance, field),
        lambda allowance: f"declares {field} = {config_entries.toml_string(getattr(allowance, field))} twice; one entry per {subject}",
    )


def root_allowances(root: dict, side: ConfigSide) -> tuple[RootEntry, ...]:
    read = _read_root_allowance if side is ConfigSide.HEAD else _read_root_entry
    return _one_per_field(ROOT_ALLOWANCE_HEADING, ROOT_ALLOWANCE_EXAMPLE, read, "entry", "root entry")(root)


python_allowances = _one_per_field(PYTHON_ALLOWANCE_HEADING, PYTHON_ALLOWANCE_EXAMPLE, _read_python_allowance, "path", "path")
workflow_allowances = _one_per_field(WORKFLOW_ALLOWANCE_HEADING, WORKFLOW_ALLOWANCE_EXAMPLE, _read_workflow_allowance, "file", "workflow")
