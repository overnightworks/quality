import datetime
import re
import unicodedata
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import NamedTuple

from quality import repository
from quality.rules import Rule

CONFIG_PATH = "quality/config.toml"
GATE_KEYS = {"mandatory_jobs", "legacy", "loosened"}
SECRETS_KEYS = {"allow"}
ADVISORIES_KEYS = {"allow"}
ROOT_KEYS = {"allow"}
LAYOUT_KEYS = {"allow"}
PYTHON_KEYS = {"allow"}
COMPOSE_KEYS = {"allow"}
SIZE_KEYS = {"generated"}
TABLES = {"gate", "secrets", "advisories", "root", "layout", "python", "compose", "size"}
ALLOWANCE_KEYS = {"rule", "file", "secret_sha256", "reason"}
DECLARATION_KEYS = {"what", "item", "date", "reason"}
ROOT_ALLOWANCE_KEYS = {"entry", "reason"}
NOT_ROOT_ENTRY_NAMES = {".", "..", ".git"}
LAYOUT_ALLOWANCE_KEYS = {"path", "rule", "reason"}
PYTHON_ALLOWANCE_KEYS = {"path", "reason"}
COMPOSE_ALLOWANCE_KEYS = {"file", "service", "reason"}
GENERATED_FILE_KEYS = {"path", "job", "item", "date", "reason"}
ITEM_REFERENCE_EXPECTED = "item as #<number> or <owner>/<repo>#<number>"
PATH_PATTERN_CHARACTERS = "*?["
ADVISORY_ALLOWANCE_KEYS = {"id", "lock", "until", "date", "item", "reason"}
SHA256_HEX = re.compile(r"[0-9a-f]{64}")
# ASCII only, so a lookalike name (a Cyrillic letter in it) is no repository.
REPOSITORY_NAME = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
ITEM_REFERENCE = re.compile(rf"(?:{REPOSITORY_NAME.pattern})?#[1-9][0-9]*")
ONE_LINE_REASON = "a non-empty one-line reason"
BARE_KEY = re.compile(r"[A-Za-z0-9_-]+")
type ExampleLines = tuple[str, *tuple[str, ...]]
LOOSENED_HEADING = "[[gate.loosened]]"
ADVISORY_ALLOWANCE_HEADING = "[[advisories.allow]]"
ROOT_ALLOWANCE_HEADING = "[[root.allow]]"
LAYOUT_ALLOWANCE_HEADING = "[[layout.allow]]"
PYTHON_ALLOWANCE_HEADING = "[[python.allow]]"
COMPOSE_ALLOWANCE_HEADING = "[[compose.allow]]"
GENERATED_FILE_HEADING = "[[size.generated]]"
EXAMPLE: ExampleLines = ("[gate]", 'mandatory_jobs = ["checks"]')
LEGACY_EXAMPLE: ExampleLines = ("[gate]", 'legacy = "fail"')
ALLOWANCE_EXAMPLE: ExampleLines = (
    "[secrets]",
    (
        'allow = [{ rule = "aws-access-token", file = "tests/fixture.py", secret_sha256 = "<64 lowercase hex>", '
        'reason = "fixture value, not a key" }]'
    ),
)
LOOSENED_EXAMPLE: ExampleLines = (
    LOOSENED_HEADING,
    "what = 'mandatory job \"Acceptance evidence (integration)\" removed'",
    'item = "overnightworks/songmaker#1345"',
    "date = 2026-10-20",
    'reason = "the operator ruled the requirement shelf machinery out"',
)
ADVISORY_ALLOWANCE_EXAMPLE: ExampleLines = (
    ADVISORY_ALLOWANCE_HEADING,
    'id = "GHSA-462x-pgg6-55fp"',
    'lock = "speech/providers/qwen/uv.lock"',
    "until = 2027-01-31",
    "date = 2026-10-21",
    'item = "overnightworks/agent-presentator#371"',
    'reason = "gradio caps pillow<13; no image input"',
)
ROOT_ALLOWANCE_EXAMPLE: ExampleLines = (
    ROOT_ALLOWANCE_HEADING,
    'entry = "frontend/"',
    'reason = "the web client, built apart from the Python package"',
)
LAYOUT_ALLOWANCE_EXAMPLE: ExampleLines = (
    LAYOUT_ALLOWANCE_HEADING,
    'path = "deploy/EDGE_SHA"',
    'rule = "file-suffix"',
    'reason = "the deploy script reads it by this name"',
)
PYTHON_ALLOWANCE_EXAMPLE: ExampleLines = (
    PYTHON_ALLOWANCE_HEADING,
    'path = "speech/providers/qwen/pyproject.toml"',
    'reason = "qwen-tts has no cp314 wheel"',
)
COMPOSE_ALLOWANCE_EXAMPLE: ExampleLines = (
    COMPOSE_ALLOWANCE_HEADING,
    'file = "compose.yaml"',
    'service = "traefik"',
    'reason = "it terminates TLS on the host\'s own interfaces"',
)
GENERATED_FILE_EXAMPLE: ExampleLines = (
    GENERATED_FILE_HEADING,
    'path = "frontend/src/lib/api/types.ts"',
    'job = "Backend (Python 3.14)"',
    'item = "overnightworks/songmaker#1784"',
    "date = 2026-10-30",
    'reason = "written by scripts/generate_types.py, proven by its --check"',
)


class LegacyMode(Enum):
    """What the repository's open findings on its default branch do to a pull request."""

    REPORT = "report"
    FAIL = "fail"


@dataclass(frozen=True)
class Declaration:
    """A loosening a pull request declares, with the ruling behind it; `what` is the verdict's text after
    `loosened (undeclared): `, unescaped."""

    what: str
    item: str
    date: datetime.date
    reason: str

    def ruling(self) -> str:
        """The ruling as every line naming the declaration shows it: `<item> of <date>`."""
        return f"{self.item} of {self.date.isoformat()}"


@dataclass(frozen=True)
class AdvisoryAllowance:
    """An advisory the repository allows in one lock file until `until` (inclusive, in UTC), ruled on `date` in `item`;
    `id` is any id or alias of the advisory's group."""

    id: str
    lock: str
    until: datetime.date
    date: datetime.date
    item: str
    reason: str

    @property
    def key(self) -> tuple[str, str]:
        """One allowance per advisory id and lock file."""
        return self.id, self.lock


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


LAYOUT_RULES = (Rule.FILE_SUFFIX, Rule.SCRIPT_NAME, Rule.SCRIPT_HEADER, Rule.TEST_LAYOUT, Rule.TEST_IMPORT)


@dataclass(frozen=True)
class LayoutAllowance:
    """A tracked file, by its exact path, the repository declares against one layout rule."""

    path: str
    rule: Rule
    reason: str


@dataclass(frozen=True)
class ComposeAllowance:
    """A compose service, by its exact tracked file and name, the repository declares host-networked."""

    file: str
    service: str
    reason: str


@dataclass(frozen=True)
class GeneratedFile:
    """A tracked file the repository declares generated by its exact path: the size rule leaves it out because `job`,
    one of the gate's mandatory jobs, proves its generator, ruled on `date` in `item`."""

    path: str
    job: str
    item: str
    date: datetime.date
    reason: str


@dataclass(frozen=True)
class GateSettings:
    """What the gate reads from quality/config.toml: the settings a pull request must not loosen, the loosenings
    it declares, and the advisories it allows."""

    mandatory_jobs: tuple[str, ...]
    legacy: LegacyMode
    loosened: tuple[Declaration, ...] = ()
    advisory_allowances: tuple[AdvisoryAllowance, ...] = ()
    generated_files: tuple[GeneratedFile, ...] = ()


class Allowance(NamedTuple):
    """A secret value in a file, as one rule sees it; the value itself is never stored, only its hash."""

    rule: str
    file: str
    secret_sha256: str


class ConfigError(Exception):
    """The quality config cannot be read: `fact` says why, `example` (TOML lines to copy) how to write it, where there is one.

    `str` is the one-line form for the doors that print a single line; the structured fields are the contract."""

    def __init__(self, fact: str, example: ExampleLines | None = None) -> None:
        super().__init__(fact)
        self.fact = fact
        self.example = example

    def __str__(self) -> str:
        return self.fact if self.example is None else f"{self.fact}, like:\n" + "\n".join(self.example)


@dataclass(frozen=True)
class AllowLists:
    """What the repository allows or declares, one list per table; the default allows nothing, as a missing config does."""

    secrets: dict[Allowance, str] = field(default_factory=dict)
    advisories: tuple[AdvisoryAllowance, ...] = ()
    root: tuple[RootAllowance, ...] = ()
    layout: tuple[LayoutAllowance, ...] = ()
    python: tuple[PythonAllowance, ...] = ()
    compose: tuple[ComposeAllowance, ...] = ()
    generated: tuple[GeneratedFile, ...] = ()


@dataclass(frozen=True)
class _Document:
    """The whole config, validated once: the [gate] table, its declarations, and every allow list."""

    gate: dict
    declarations: tuple[Declaration, ...]
    allow_lists: AllowLists


def _toml_string(value: str) -> str:
    """`value` as a TOML basic string: `\\` and `"` escaped, every control character (category Cc) as `\\uNNNN`, every
    other character as itself, so what a person copies reads back as `value`; `report.printable` escapes the rest
    when the line is printed."""
    return f'"{"".join(_escaped_in_string(character) for character in value)}"'


def _escaped_in_string(character: str) -> str:
    if character in '\\"':
        return f"\\{character}"
    return f"\\u{ord(character):04x}" if unicodedata.category(character) == "Cc" else character


def _key_name(key: str) -> str:
    """A key as TOML writes it: bare when it can be, else a basic string."""
    return key if BARE_KEY.fullmatch(key) else _toml_string(key)


def _unknown_keys(unknown: set[str]) -> str:
    return f"unknown {'key' if len(unknown) == 1 else 'keys'} {', '.join(_key_name(key) for key in sorted(unknown))}"


def _parse(text: str, example: ExampleLines) -> dict:
    try:
        return repository.parse_toml(text)
    except repository.TomlError as error:
        raise ConfigError(f"{CONFIG_PATH} is not valid TOML ({error})", example) from error


def _table(document: dict, name: str, example: ExampleLines) -> dict:
    table = document.get(name, {})
    if not isinstance(table, dict):
        raise ConfigError(f"{CONFIG_PATH} has [{name}] as a value, not a table", example)
    return table


def _entries(table: dict, key: str, heading: str, example: ExampleLines, shape: str = "an array of tables") -> Iterator[tuple[int, object]]:
    """The entries of a list in `table` with their 1-based positions, counted once for every reader."""
    entries = table.get(key, [])
    if not isinstance(entries, list):
        raise ConfigError(f"{CONFIG_PATH} needs {heading} as {shape}", example)
    return enumerate(entries, start=1)


def _entry_refusal(heading: str, position: int, example: ExampleLines) -> Callable[[str], ConfigError]:
    def refuse(need: str) -> ConfigError:
        return ConfigError(f"{CONFIG_PATH} {heading} entry {position} needs {need}", example)

    return refuse


def _refuse_unknown_secrets_keys(secrets: dict) -> None:
    unknown = set(secrets) - SECRETS_KEYS
    if unknown:
        raise ConfigError(f"{CONFIG_PATH} has {_unknown_keys(unknown)} in [secrets] - only allow exists")


def _refuse_unknown_keys(
    document: dict, gate: dict, advisories: dict, root: dict, layout: dict, python: dict, compose: dict, size: dict
) -> None:
    unknown = (
        (set(document) - TABLES)
        | (set(gate) - GATE_KEYS)
        | (set(advisories) - ADVISORIES_KEYS)
        | (set(root) - ROOT_KEYS)
        | (set(layout) - LAYOUT_KEYS)
        | (set(python) - PYTHON_KEYS)
        | (set(compose) - COMPOSE_KEYS)
        | (set(size) - SIZE_KEYS)
    )
    if unknown:
        example = None
        for table, keys, table_example in (
            (root, ROOT_KEYS, ROOT_ALLOWANCE_EXAMPLE),
            (layout, LAYOUT_KEYS, LAYOUT_ALLOWANCE_EXAMPLE),
            (python, PYTHON_KEYS, PYTHON_ALLOWANCE_EXAMPLE),
            (compose, COMPOSE_KEYS, COMPOSE_ALLOWANCE_EXAMPLE),
            (size, SIZE_KEYS, GENERATED_FILE_EXAMPLE),
        ):
            example = table_example if set(table) - keys else example
        raise ConfigError(
            f"{CONFIG_PATH} has {_unknown_keys(unknown)} - only [gate] mandatory_jobs, [gate] legacy, "
            "[[gate.loosened]], [secrets] allow, [[advisories.allow]], [[root.allow]], [[layout.allow]], "
            "[[python.allow]], [[compose.allow]] and [[size.generated]] exist",
            example,
        )


def _validated_document(text: str) -> _Document:
    """The one validation of the whole config: one parse, one unknown-key check, and every entry of every table."""
    document = _parse(text, EXAMPLE)
    gate = _table(document, "gate", EXAMPLE)
    advisories = _table(document, "advisories", ADVISORY_ALLOWANCE_EXAMPLE)
    root = _table(document, "root", ROOT_ALLOWANCE_EXAMPLE)
    layout = _table(document, "layout", LAYOUT_ALLOWANCE_EXAMPLE)
    python = _table(document, "python", PYTHON_ALLOWANCE_EXAMPLE)
    compose = _table(document, "compose", COMPOSE_ALLOWANCE_EXAMPLE)
    size = _table(document, "size", GENERATED_FILE_EXAMPLE)
    _refuse_unknown_keys(document, gate, advisories, root, layout, python, compose, size)
    return _Document(
        gate,
        _declarations(gate),
        AllowLists(
            _parse_allowances(_table(document, "secrets", ALLOWANCE_EXAMPLE)),
            _advisory_allowances(advisories),
            _root_allowances(root),
            _layout_allowances(layout),
            _python_allowances(python),
            _compose_allowances(compose),
            _generated_files(size, gate),
        ),
    )


def _job_names(gate: dict) -> tuple[str, ...]:
    """The [gate] mandatory_jobs when they are a non-empty list of non-empty names, else none."""
    names = gate.get("mandatory_jobs")
    if isinstance(names, list) and all(isinstance(name, str) and name for name in names):
        return tuple(names)
    return ()


def _mandatory_jobs(gate: dict) -> tuple[str, ...]:
    names = _job_names(gate)
    if not names:
        raise ConfigError(f"{CONFIG_PATH} needs [gate] mandatory_jobs as a non-empty list of job names", EXAMPLE)
    return names


def _legacy_mode(gate: dict) -> LegacyMode:
    """A missing `legacy` key reports."""
    value = gate.get("legacy", LegacyMode.REPORT.value)
    try:
        return LegacyMode(value)
    except ValueError:
        modes = " or ".join(f'"{mode.value}"' for mode in LegacyMode)
        raise ConfigError(f"{CONFIG_PATH} [gate] legacy must be {modes}", LEGACY_EXAMPLE) from None


def _spans_lines(reason: str) -> bool:
    return "\n" in reason or "\r" in reason


def _is_one_line_text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip()) and not _spans_lines(value)


def _is_date_without_time(value: object) -> bool:
    """A TOML date: tomllib reads a date with a time as a datetime, which is a date too."""
    return isinstance(value, datetime.date) and not isinstance(value, datetime.datetime)


def _declarations(gate: dict) -> tuple[Declaration, ...]:
    declarations: dict[str, Declaration] = {}
    for position, entry in _entries(gate, "loosened", LOOSENED_HEADING, LOOSENED_EXAMPLE):
        declaration = _read_declaration(entry, position)
        if declaration.what in declarations:
            raise ConfigError(
                f"{CONFIG_PATH} {LOOSENED_HEADING} declares what = {_toml_string(declaration.what)} twice; "
                "one entry per loosening",
                LOOSENED_EXAMPLE,
            )
        declarations[declaration.what] = declaration
    return tuple(declarations.values())


def _read_declaration(entry: object, position: int) -> Declaration:
    refuse = _entry_refusal(LOOSENED_HEADING, position, LOOSENED_EXAMPLE)
    if not isinstance(entry, dict) or set(entry) != DECLARATION_KEYS:
        raise refuse("exactly a what, an item, a date and a reason")
    what, item, date, reason = (entry[key] for key in ("what", "item", "date", "reason"))
    if not (isinstance(what, str) and what.strip()):
        raise refuse("a non-empty what")
    if not (isinstance(item, str) and ITEM_REFERENCE.fullmatch(item)):
        raise refuse(ITEM_REFERENCE_EXPECTED)
    if not _is_date_without_time(date):
        raise refuse("date as a TOML date without a time")
    if not _is_one_line_text(reason):
        raise refuse(ONE_LINE_REASON)
    return Declaration(what, item, date, reason)


def _advisory_allowances(advisories: dict) -> tuple[AdvisoryAllowance, ...]:
    allowances: dict[tuple[str, str], AdvisoryAllowance] = {}
    for position, entry in _entries(advisories, "allow", ADVISORY_ALLOWANCE_HEADING, ADVISORY_ALLOWANCE_EXAMPLE):
        allowance = _read_advisory_allowance(entry, position)
        if allowance.key in allowances:
            raise ConfigError(
                f"{CONFIG_PATH} {ADVISORY_ALLOWANCE_HEADING} allows id = {_toml_string(allowance.id)} in lock = "
                f"{_toml_string(allowance.lock)} twice; one entry per advisory and lock file"
            )
        allowances[allowance.key] = allowance
    return tuple(allowances.values())


def _read_advisory_allowance(entry: object, position: int) -> AdvisoryAllowance:
    refuse = _entry_refusal(ADVISORY_ALLOWANCE_HEADING, position, ADVISORY_ALLOWANCE_EXAMPLE)
    if not isinstance(entry, dict) or set(entry) != ADVISORY_ALLOWANCE_KEYS:
        raise refuse("exactly an id, a lock, an until, a date, an item and a reason")
    identifier, lock, until, date, item, reason = (
        entry[key] for key in ("id", "lock", "until", "date", "item", "reason")
    )
    if not all(_is_one_line_text(value) for value in (identifier, lock, reason)):
        raise refuse("a non-empty one-line id, lock and reason")
    if not (isinstance(item, str) and ITEM_REFERENCE.fullmatch(item)):
        raise refuse(ITEM_REFERENCE_EXPECTED)
    if not (_is_date_without_time(until) and _is_date_without_time(date)):
        raise refuse("until and date as TOML dates without a time")
    return AdvisoryAllowance(identifier, lock, until, date, item, reason)


def _root_allowances(root: dict) -> tuple[RootAllowance, ...]:
    allowances: dict[str, RootAllowance] = {}
    for position, entry in _entries(root, "allow", ROOT_ALLOWANCE_HEADING, ROOT_ALLOWANCE_EXAMPLE):
        allowance = _read_root_allowance(entry, position)
        if allowance.entry in allowances:
            raise ConfigError(
                f"{CONFIG_PATH} {ROOT_ALLOWANCE_HEADING} declares entry = {_toml_string(allowance.entry)} twice; "
                "one entry per root entry",
                ROOT_ALLOWANCE_EXAMPLE,
            )
        allowances[allowance.entry] = allowance
    return tuple(allowances.values())


def _is_root_entry(entry: object) -> bool:
    """One name directly in the root, a directory with its trailing `/`; no path, no `.`, `..` or `.git`."""
    if not isinstance(entry, str):
        return False
    name = entry.removesuffix("/")
    return bool(name.strip()) and "/" not in name and name not in NOT_ROOT_ENTRY_NAMES


def _read_root_allowance(entry: object, position: int) -> RootAllowance:
    refuse = _entry_refusal(ROOT_ALLOWANCE_HEADING, position, ROOT_ALLOWANCE_EXAMPLE)
    if not isinstance(entry, dict) or set(entry) != ROOT_ALLOWANCE_KEYS:
        raise refuse("exactly an entry and a reason")
    name, reason = entry["entry"], entry["reason"]
    if not _is_root_entry(name):
        raise refuse("entry as one name directly in the root, a directory with a trailing /, not a path")
    if not _is_one_line_text(reason):
        raise refuse(ONE_LINE_REASON)
    return RootAllowance(name, reason)


def _layout_allowances(layout: dict) -> tuple[LayoutAllowance, ...]:
    allowances: dict[tuple[Rule, str], LayoutAllowance] = {}
    for position, entry in _entries(layout, "allow", LAYOUT_ALLOWANCE_HEADING, LAYOUT_ALLOWANCE_EXAMPLE):
        allowance = _read_layout_allowance(entry, position)
        key = (allowance.rule, allowance.path)
        if key in allowances:
            raise ConfigError(
                f"{CONFIG_PATH} {LAYOUT_ALLOWANCE_HEADING} declares path = {_toml_string(allowance.path)} for rule = "
                f"{_toml_string(allowance.rule.value)} twice; one declaration per rule and path",
                LAYOUT_ALLOWANCE_EXAMPLE,
            )
        allowances[key] = allowance
    return tuple(allowances.values())


def _python_allowances(python: dict) -> tuple[PythonAllowance, ...]:
    allowances: dict[str, PythonAllowance] = {}
    for position, entry in _entries(python, "allow", PYTHON_ALLOWANCE_HEADING, PYTHON_ALLOWANCE_EXAMPLE):
        allowance = _read_python_allowance(entry, position)
        if allowance.path in allowances:
            raise ConfigError(
                f"{CONFIG_PATH} {PYTHON_ALLOWANCE_HEADING} declares path = {_toml_string(allowance.path)} twice; one entry per path",
                PYTHON_ALLOWANCE_EXAMPLE,
            )
        allowances[allowance.path] = allowance
    return tuple(allowances.values())


def _read_python_allowance(entry: object, position: int) -> PythonAllowance:
    refuse = _entry_refusal(PYTHON_ALLOWANCE_HEADING, position, PYTHON_ALLOWANCE_EXAMPLE)
    if not isinstance(entry, dict) or set(entry) != PYTHON_ALLOWANCE_KEYS:
        raise refuse("exactly a path and a reason")
    path, reason = entry["path"], entry["reason"]
    if not _is_exact_file_path(path) or PurePosixPath(path).name != "pyproject.toml":
        raise refuse("path as the exact tracked path of one pyproject.toml, no folder, pattern or ..")
    if not _is_one_line_text(reason):
        raise refuse(ONE_LINE_REASON)
    return PythonAllowance(path, reason)


def _is_exact_file_path(path: object) -> bool:
    """A relative path to one file: no folder (trailing `/`), no pattern character, no empty, `.` or `..` component, not absolute."""
    if not isinstance(path, str) or not path.strip() or path.startswith("/") or path.endswith("/"):
        return False
    components = path.split("/")
    return not any(character in path for character in PATH_PATTERN_CHARACTERS) and all(
        component not in ("", ".", "..") for component in components
    )


def _read_layout_allowance(entry: object, position: int) -> LayoutAllowance:
    refuse = _entry_refusal(LAYOUT_ALLOWANCE_HEADING, position, LAYOUT_ALLOWANCE_EXAMPLE)
    if not isinstance(entry, dict) or set(entry) != LAYOUT_ALLOWANCE_KEYS:
        raise refuse("exactly a path, a rule and a reason")
    path, rule, reason = entry["path"], entry["rule"], entry["reason"]
    if not _is_exact_file_path(path):
        raise refuse("path as the exact tracked path of one file: no folder, pattern, .. or absolute path")
    if rule not in LAYOUT_RULES:
        raise refuse(f"rule as one of {', '.join(sorted(str(known) for known in LAYOUT_RULES))}")
    if not _is_one_line_text(reason):
        raise refuse(ONE_LINE_REASON)
    return LayoutAllowance(path, Rule(rule), reason)


def _compose_allowances(compose: dict) -> tuple[ComposeAllowance, ...]:
    allowances: dict[tuple[str, str], ComposeAllowance] = {}
    for position, entry in _entries(compose, "allow", COMPOSE_ALLOWANCE_HEADING, COMPOSE_ALLOWANCE_EXAMPLE):
        allowance = _read_compose_allowance(entry, position)
        key = (allowance.file, allowance.service)
        if key in allowances:
            raise ConfigError(
                f"{CONFIG_PATH} {COMPOSE_ALLOWANCE_HEADING} declares service = {_toml_string(allowance.service)} of file = "
                f"{_toml_string(allowance.file)} twice; one declaration per file and service",
                COMPOSE_ALLOWANCE_EXAMPLE,
            )
        allowances[key] = allowance
    return tuple(allowances.values())


def _read_compose_allowance(entry: object, position: int) -> ComposeAllowance:
    refuse = _entry_refusal(COMPOSE_ALLOWANCE_HEADING, position, COMPOSE_ALLOWANCE_EXAMPLE)
    if not isinstance(entry, dict) or set(entry) != COMPOSE_ALLOWANCE_KEYS:
        raise refuse("exactly a file, a service and a reason")
    file, service, reason = entry["file"], entry["service"], entry["reason"]
    if not _is_exact_file_path(file):
        raise refuse("file as the exact tracked path of one compose file: no folder, pattern, .. or absolute path")
    if not _is_one_line_text(service):
        raise refuse("service as the one-line name of a service in that file")
    if not _is_one_line_text(reason):
        raise refuse(ONE_LINE_REASON)
    return ComposeAllowance(file, service, reason)


def _generated_files(size: dict, gate: dict) -> tuple[GeneratedFile, ...]:
    jobs = _job_names(gate)
    declared: dict[str, GeneratedFile] = {}
    for position, entry in _entries(size, "generated", GENERATED_FILE_HEADING, GENERATED_FILE_EXAMPLE):
        generated = _read_generated_file(entry, position, jobs)
        if generated.path in declared:
            raise ConfigError(
                f"{CONFIG_PATH} {GENERATED_FILE_HEADING} declares path = {_toml_string(generated.path)} twice; "
                "one entry per file",
                GENERATED_FILE_EXAMPLE,
            )
        declared[generated.path] = generated
    return tuple(declared.values())


def _read_generated_file(entry: object, position: int, mandatory_jobs: tuple[str, ...]) -> GeneratedFile:
    refuse = _entry_refusal(GENERATED_FILE_HEADING, position, GENERATED_FILE_EXAMPLE)
    if not isinstance(entry, dict) or set(entry) != GENERATED_FILE_KEYS:
        raise refuse("exactly a path, a job, an item, a date and a reason")
    path, job, item, date, reason = (entry[key] for key in ("path", "job", "item", "date", "reason"))
    if not _is_exact_file_path(path):
        raise refuse("path as the exact tracked path of one file: no folder, pattern, .. or absolute path")
    if job not in mandatory_jobs:
        raise refuse("job as one of [gate] mandatory_jobs, the job whose generator check proves the file")
    if not (isinstance(item, str) and ITEM_REFERENCE.fullmatch(item)):
        raise refuse(ITEM_REFERENCE_EXPECTED)
    if not _is_date_without_time(date):
        raise refuse("date as a TOML date without a time")
    if not _is_one_line_text(reason):
        raise refuse(ONE_LINE_REASON)
    return GeneratedFile(path, job, item, date, reason)


def load_gate_settings(text: str | None) -> GateSettings:
    """mandatory_jobs is judged before legacy, so a config wrong in both is refused for mandatory_jobs."""
    if text is None:
        raise ConfigError(f"{CONFIG_PATH} is missing", EXAMPLE)
    document = _validated_document(text)
    return GateSettings(
        _mandatory_jobs(document.gate), _legacy_mode(document.gate), document.declarations,
        document.allow_lists.advisories, document.allow_lists.generated,
    )


def _read_config_text(repo: Path) -> str | None:
    """The repository's own config text, None when it has none; the one reader of quality/config.toml on disk."""
    try:
        return repository.read_text(repo, CONFIG_PATH)
    except repository.RepositoryFileError as error:
        raise ConfigError(str(error)) from error


def read_gate_settings(repo: Path) -> GateSettings:
    return load_gate_settings(_read_config_text(repo))


def load_allow_lists(text: str | None) -> AllowLists:
    """Every list the repository allows or declares; a missing config allows nothing.

    The whole document is validated, as the gate does: hygiene and the verdict refuse an unknown key and a malformed
    entry alike.
    """
    return AllowLists() if text is None else _validated_document(text).allow_lists


def read_allow_lists(repo: Path) -> AllowLists:
    return load_allow_lists(_read_config_text(repo))


def load_staged_secret_allowances(text: str | None) -> dict[Allowance, str]:
    """The allow list as the commit hook reads it: only [secrets].

    Every other table and key belongs to the gate, which a newer quality may have grown, and must not stop the
    commit hook of an older one.
    """
    if text is None:
        return {}
    return _parse_allowances(_table(_parse(text, ALLOWANCE_EXAMPLE), "secrets", ALLOWANCE_EXAMPLE))


def _parse_allowances(secrets: dict) -> dict[Allowance, str]:
    _refuse_unknown_secrets_keys(secrets)
    allowances: dict[Allowance, str] = {}
    for position, entry in _entries(secrets, "allow", "[secrets] allow", ALLOWANCE_EXAMPLE, "a list"):
        allowance, reason = _read_allowance(entry, position)
        if allowance in allowances:
            raise ConfigError(
                f"{CONFIG_PATH} [secrets] allow lists rule = {_toml_string(allowance.rule)} in file = "
                f"{_toml_string(allowance.file)} twice for one value"
            )
        allowances[allowance] = reason
    return allowances


def _read_allowance(entry: object, position: int) -> tuple[Allowance, str]:
    refuse = _entry_refusal("[secrets] allow", position, ALLOWANCE_EXAMPLE)
    if not isinstance(entry, dict) or set(entry) != ALLOWANCE_KEYS:
        raise refuse("exactly a rule, a file, a secret_sha256 and a reason (the old fingerprint form is gone)")
    rule, file, secret_sha256, reason = (entry[key] for key in ("rule", "file", "secret_sha256", "reason"))
    if not all(isinstance(value, str) and value.strip() for value in (rule, file, reason)):
        raise refuse("a non-empty rule, file and reason")
    if not (isinstance(secret_sha256, str) and SHA256_HEX.fullmatch(secret_sha256)):
        raise refuse("secret_sha256 as 64 lowercase hex digits")
    if _spans_lines(reason):
        raise ConfigError(f"{CONFIG_PATH} [secrets] allow entry {position} needs a one-line reason")
    return Allowance(rule, file, secret_sha256), reason
