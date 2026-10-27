import datetime
import re
import tomllib
from dataclasses import dataclass
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import NamedTuple

from quality import repository

CONFIG_PATH = "quality/config.toml"
GATE_KEYS = {"mandatory_jobs", "legacy", "loosened"}
SECRETS_KEYS = {"allow"}
ADVISORIES_KEYS = {"allow"}
ROOT_KEYS = {"allow"}
LAYOUT_KEYS = {"allow"}
PYTHON_KEYS = {"allow"}
COMPOSE_KEYS = {"allow"}
TABLES = {"gate", "secrets", "advisories", "root", "layout", "python", "compose"}
ALLOWANCE_KEYS = {"rule", "file", "secret_sha256", "reason"}
DECLARATION_KEYS = {"what", "item", "date", "reason"}
ROOT_ALLOWANCE_KEYS = {"entry", "reason"}
NOT_ROOT_ENTRY_NAMES = {".", "..", ".git"}
LAYOUT_ALLOWANCE_KEYS = {"path", "rule", "reason"}
PYTHON_ALLOWANCE_KEYS = {"path", "reason"}
COMPOSE_ALLOWANCE_KEYS = {"file", "service", "reason"}
PATH_PATTERN_CHARACTERS = "*?["
ADVISORY_ALLOWANCE_KEYS = {"id", "lock", "until", "date", "item", "reason"}
SHA256_HEX = re.compile(r"[0-9a-f]{64}")
# ASCII only, so a lookalike name (a Cyrillic letter in it) is no repository.
REPOSITORY_NAME = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
ITEM_REFERENCE = re.compile(rf"(?:{REPOSITORY_NAME.pattern})?#[1-9][0-9]*")
ONE_LINE_REASON = "a non-empty one-line reason"
EXAMPLE = '[gate]\nmandatory_jobs = ["checks"]'
LEGACY_EXAMPLE = '[gate]\nlegacy = "fail"'
ALLOWANCE_EXAMPLE = (
    '[secrets]\nallow = [{ rule = "aws-access-token", file = "tests/fixture.py", '
    'secret_sha256 = "<64 lowercase hex>", reason = "fixture value, not a key" }]'
)
LOOSENED_EXAMPLE = (
    "[[gate.loosened]]\n"
    "what = 'mandatory job \"Acceptance evidence (integration)\" removed'\n"
    'item = "overnightworks/songmaker#1345"\n'
    "date = 2026-10-20\n"
    'reason = "the operator ruled the requirement shelf machinery out"'
)
ADVISORY_ALLOWANCE_EXAMPLE = (
    "[[advisories.allow]]\n"
    'id = "GHSA-462x-pgg6-55fp"\n'
    'lock = "speech/providers/qwen/uv.lock"\n'
    "until = 2027-01-31\n"
    "date = 2026-10-21\n"
    'item = "overnightworks/agent-presentator#371"\n'
    'reason = "gradio caps pillow<13; no image input"'
)
ROOT_ALLOWANCE_EXAMPLE = (
    "[[root.allow]]\n"
    'entry = "frontend/"\n'
    'reason = "the web client, built apart from the Python package"'
)
LAYOUT_ALLOWANCE_EXAMPLE = (
    "[[layout.allow]]\n"
    'path = "deploy/EDGE_SHA"\n'
    'rule = "file-suffix"\n'
    'reason = "the deploy script reads it by this name"'
)
PYTHON_ALLOWANCE_EXAMPLE = (
    "[[python.allow]]\n"
    'path = "speech/providers/qwen/pyproject.toml"\n'
    'reason = "qwen-tts has no cp314 wheel"'
)
COMPOSE_ALLOWANCE_EXAMPLE = (
    "[[compose.allow]]\n"
    'file = "compose.yaml"\n'
    'service = "traefik"\n'
    'reason = "it terminates TLS on the host\'s own interfaces"'
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


class LayoutRule(Enum):
    """A layout rule a repository may declare a tracked file against."""

    FILE_SUFFIX = "file-suffix"
    SCRIPT_NAME = "script-name"
    SCRIPT_HEADER = "script-header"
    TEST_LAYOUT = "test-layout"
    TEST_IMPORT = "test-import"


@dataclass(frozen=True)
class LayoutAllowance:
    """A tracked file, by its exact path, the repository declares against one layout rule."""

    path: str
    rule: LayoutRule
    reason: str


@dataclass(frozen=True)
class ComposeAllowance:
    """A compose service, by its exact tracked file and name, the repository declares host-networked."""

    file: str
    service: str
    reason: str


@dataclass(frozen=True)
class GateSettings:
    """What the gate reads from quality/config.toml: the settings a pull request must not loosen, the loosenings
    it declares, and the advisories it allows."""

    mandatory_jobs: tuple[str, ...]
    legacy: LegacyMode
    loosened: tuple[Declaration, ...] = ()
    advisory_allowances: tuple[AdvisoryAllowance, ...] = ()


class Allowance(NamedTuple):
    """A secret value in a file, as one rule sees it; the value itself is never stored, only its hash."""

    rule: str
    file: str
    secret_sha256: str


class ConfigError(Exception):
    """The quality config cannot be read; the message carries the reason and the fix."""


@dataclass(frozen=True)
class _Document:
    """The whole config, validated once: the [gate] table, its declarations, and both allow lists."""

    gate: dict
    declarations: tuple[Declaration, ...]
    secret_allowances: dict[Allowance, str]
    advisory_allowances: tuple[AdvisoryAllowance, ...]
    root_allowances: tuple[RootAllowance, ...]
    layout_allowances: tuple[LayoutAllowance, ...]
    python_allowances: tuple[PythonAllowance, ...]
    compose_allowances: tuple[ComposeAllowance, ...]


def _parse(text: str, example: str) -> dict:
    try:
        return tomllib.loads(text)
    except (ValueError, RecursionError) as error:
        raise ConfigError(f"{CONFIG_PATH} is not valid TOML ({error}) - write it like:\n{example}") from error


def _table(document: dict, name: str, example: str) -> dict:
    table = document.get(name, {})
    if not isinstance(table, dict):
        raise ConfigError(f"{CONFIG_PATH} has [{name}] as a value, not a table - write it like:\n{example}")
    return table


def _refuse_unknown_secrets_keys(secrets: dict) -> None:
    unknown = set(secrets) - SECRETS_KEYS
    if unknown:
        raise ConfigError(f"{CONFIG_PATH} has unknown key(s) {sorted(unknown)} in [secrets] - only allow exists")


def _refuse_unknown_keys(document: dict, gate: dict, advisories: dict, root: dict, layout: dict, python: dict, compose: dict) -> None:
    unknown = (
        (set(document) - TABLES)
        | (set(gate) - GATE_KEYS)
        | (set(advisories) - ADVISORIES_KEYS)
        | (set(root) - ROOT_KEYS)
        | (set(layout) - LAYOUT_KEYS)
        | (set(python) - PYTHON_KEYS)
        | (set(compose) - COMPOSE_KEYS)
    )
    if unknown:
        example = f", like:\n{ROOT_ALLOWANCE_EXAMPLE}" if set(root) - ROOT_KEYS else ""
        example = f", like:\n{LAYOUT_ALLOWANCE_EXAMPLE}" if set(layout) - LAYOUT_KEYS else example
        example = f", like:\n{PYTHON_ALLOWANCE_EXAMPLE}" if set(python) - PYTHON_KEYS else example
        example = f", like:\n{COMPOSE_ALLOWANCE_EXAMPLE}" if set(compose) - COMPOSE_KEYS else example
        raise ConfigError(
            f"{CONFIG_PATH} has unknown key(s) {sorted(unknown)} - only [gate] mandatory_jobs, [gate] legacy, "
            f"[[gate.loosened]], [secrets] allow, [[advisories.allow]], [[root.allow]], [[layout.allow]], [[python.allow]] and [[compose.allow]] exist{example}"
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
    _refuse_unknown_keys(document, gate, advisories, root, layout, python, compose)
    return _Document(
        gate,
        _declarations(gate),
        _parse_allowances(_table(document, "secrets", ALLOWANCE_EXAMPLE)),
        _advisory_allowances(advisories),
        _root_allowances(root),
        _layout_allowances(layout),
        _python_allowances(python),
        _compose_allowances(compose),
    )


def _mandatory_jobs(gate: dict) -> tuple[str, ...]:
    names = gate.get("mandatory_jobs")
    if not isinstance(names, list) or not names or not all(isinstance(name, str) and name for name in names):
        raise ConfigError(
            f"{CONFIG_PATH} needs [gate] mandatory_jobs as a non-empty list of job names, like:\n{EXAMPLE}"
        )
    return tuple(names)


def _legacy_mode(gate: dict) -> LegacyMode:
    """A missing `legacy` key reports."""
    value = gate.get("legacy", LegacyMode.REPORT.value)
    try:
        return LegacyMode(value)
    except ValueError:
        modes = " or ".join(f'"{mode.value}"' for mode in LegacyMode)
        raise ConfigError(f"{CONFIG_PATH} has [gate] legacy = {value!r}; it must be {modes}, like:\n{LEGACY_EXAMPLE}") from None


def _spans_lines(reason: str) -> bool:
    return "\n" in reason or "\r" in reason


def _is_one_line_text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip()) and not _spans_lines(value)


def _is_date_without_time(value: object) -> bool:
    """A TOML date: tomllib reads a date with a time as a datetime, which is a date too."""
    return isinstance(value, datetime.date) and not isinstance(value, datetime.datetime)


def _declarations(gate: dict) -> tuple[Declaration, ...]:
    entries = gate.get("loosened", [])
    if not isinstance(entries, list):
        raise ConfigError(f"{CONFIG_PATH} needs [[gate.loosened]] as an array of tables, like:\n{LOOSENED_EXAMPLE}")
    declarations: dict[str, Declaration] = {}
    for entry in entries:
        declaration = _read_declaration(entry)
        if declaration.what in declarations:
            raise ConfigError(
                f"{CONFIG_PATH} [[gate.loosened]] declares what = {declaration.what!r} twice; one entry per loosening, "
                f"like:\n{LOOSENED_EXAMPLE}"
            )
        declarations[declaration.what] = declaration
    return tuple(declarations.values())


def _read_declaration(entry: object) -> Declaration:
    def refuse(need: str) -> ConfigError:
        return ConfigError(f"{CONFIG_PATH} [[gate.loosened]] entry {entry!r} needs {need}, like:\n{LOOSENED_EXAMPLE}")

    if not isinstance(entry, dict) or set(entry) != DECLARATION_KEYS:
        raise refuse("exactly a what, an item, a date and a reason")
    what, item, date, reason = (entry[key] for key in ("what", "item", "date", "reason"))
    if not (isinstance(what, str) and what.strip()):
        raise refuse("a non-empty what")
    if not (isinstance(item, str) and ITEM_REFERENCE.fullmatch(item)):
        raise refuse("item as #<number> or <owner>/<repo>#<number>")
    if not _is_date_without_time(date):
        raise refuse("date as a TOML date without a time")
    if not _is_one_line_text(reason):
        raise refuse(ONE_LINE_REASON)
    return Declaration(what, item, date, reason)


def _advisory_allowances(advisories: dict) -> tuple[AdvisoryAllowance, ...]:
    entries = advisories.get("allow", [])
    if not isinstance(entries, list):
        raise ConfigError(
            f"{CONFIG_PATH} needs [[advisories.allow]] as an array of tables, like:\n{ADVISORY_ALLOWANCE_EXAMPLE}"
        )
    allowances: dict[tuple[str, str], AdvisoryAllowance] = {}
    for entry in entries:
        allowance = _read_advisory_allowance(entry)
        if allowance.key in allowances:
            raise ConfigError(
                f"{CONFIG_PATH} [[advisories.allow]] allows {allowance.id} in {allowance.lock} twice; one entry per "
                "advisory and lock file"
            )
        allowances[allowance.key] = allowance
    return tuple(allowances.values())


def _read_advisory_allowance(entry: object) -> AdvisoryAllowance:
    def refuse(need: str) -> ConfigError:
        return ConfigError(
            f"{CONFIG_PATH} [[advisories.allow]] entry {entry!r} needs {need}, like:\n{ADVISORY_ALLOWANCE_EXAMPLE}"
        )

    if not isinstance(entry, dict) or set(entry) != ADVISORY_ALLOWANCE_KEYS:
        raise refuse("exactly an id, a lock, an until, a date, an item and a reason")
    identifier, lock, until, date, item, reason = (
        entry[key] for key in ("id", "lock", "until", "date", "item", "reason")
    )
    if not all(_is_one_line_text(value) for value in (identifier, lock, reason)):
        raise refuse("a non-empty one-line id, lock and reason")
    if not (isinstance(item, str) and ITEM_REFERENCE.fullmatch(item)):
        raise refuse("item as #<number> or <owner>/<repo>#<number>")
    if not (_is_date_without_time(until) and _is_date_without_time(date)):
        raise refuse("until and date as TOML dates without a time")
    return AdvisoryAllowance(identifier, lock, until, date, item, reason)


def _root_allowances(root: dict) -> tuple[RootAllowance, ...]:
    entries = root.get("allow", [])
    if not isinstance(entries, list):
        raise ConfigError(f"{CONFIG_PATH} needs [[root.allow]] as an array of tables, like:\n{ROOT_ALLOWANCE_EXAMPLE}")
    allowances: dict[str, RootAllowance] = {}
    for entry in entries:
        allowance = _read_root_allowance(entry)
        if allowance.entry in allowances:
            raise ConfigError(
                f"{CONFIG_PATH} [[root.allow]] declares entry = {allowance.entry!r} twice; one entry per root entry, "
                f"like:\n{ROOT_ALLOWANCE_EXAMPLE}"
            )
        allowances[allowance.entry] = allowance
    return tuple(allowances.values())


def _is_root_entry(entry: object) -> bool:
    """One name directly in the root, a directory with its trailing `/`; no path, no `.`, `..` or `.git`."""
    if not isinstance(entry, str):
        return False
    name = entry.removesuffix("/")
    return bool(name.strip()) and "/" not in name and name not in NOT_ROOT_ENTRY_NAMES


def _read_root_allowance(entry: object) -> RootAllowance:
    def refuse(need: str) -> ConfigError:
        return ConfigError(f"{CONFIG_PATH} [[root.allow]] entry {entry!r} needs {need}, like:\n{ROOT_ALLOWANCE_EXAMPLE}")

    if not isinstance(entry, dict) or set(entry) != ROOT_ALLOWANCE_KEYS:
        raise refuse("exactly an entry and a reason")
    name, reason = entry["entry"], entry["reason"]
    if not _is_root_entry(name):
        raise refuse("entry as one name directly in the root, a directory with a trailing /, not a path")
    if not _is_one_line_text(reason):
        raise refuse(ONE_LINE_REASON)
    return RootAllowance(name, reason)


def _layout_allowances(layout: dict) -> tuple[LayoutAllowance, ...]:
    entries = layout.get("allow", [])
    if not isinstance(entries, list):
        raise ConfigError(f"{CONFIG_PATH} needs [[layout.allow]] as an array of tables, like:\n{LAYOUT_ALLOWANCE_EXAMPLE}")
    allowances: dict[tuple[LayoutRule, str], LayoutAllowance] = {}
    for entry in entries:
        allowance = _read_layout_allowance(entry)
        key = (allowance.rule, allowance.path)
        if key in allowances:
            raise ConfigError(
                f"{CONFIG_PATH} [[layout.allow]] declares path = {allowance.path!r} for rule = "
                f"{allowance.rule.value!r} twice; one declaration per rule and path, like:\n{LAYOUT_ALLOWANCE_EXAMPLE}"
            )
        allowances[key] = allowance
    return tuple(allowances.values())


def _python_allowances(python: dict) -> tuple[PythonAllowance, ...]:
    entries = python.get("allow", [])
    if not isinstance(entries, list):
        raise ConfigError(f"{CONFIG_PATH} needs [[python.allow]] as an array of tables, like:\n{PYTHON_ALLOWANCE_EXAMPLE}")
    allowances: dict[str, PythonAllowance] = {}
    for entry in entries:
        allowance = _read_python_allowance(entry)
        if allowance.path in allowances:
            raise ConfigError(
                f"{CONFIG_PATH} [[python.allow]] declares path = {allowance.path!r} twice; one entry per path, "
                f"like:\n{PYTHON_ALLOWANCE_EXAMPLE}"
            )
        allowances[allowance.path] = allowance
    return tuple(allowances.values())


def _read_python_allowance(entry: object) -> PythonAllowance:
    def refuse(need: str) -> ConfigError:
        return ConfigError(f"{CONFIG_PATH} [[python.allow]] entry {entry!r} needs {need}, like:\n{PYTHON_ALLOWANCE_EXAMPLE}")

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


def _read_layout_allowance(entry: object) -> LayoutAllowance:
    def refuse(need: str) -> ConfigError:
        return ConfigError(f"{CONFIG_PATH} [[layout.allow]] entry {entry!r} needs {need}, like:\n{LAYOUT_ALLOWANCE_EXAMPLE}")

    if not isinstance(entry, dict) or set(entry) != LAYOUT_ALLOWANCE_KEYS:
        raise refuse("exactly a path, a rule and a reason")
    path, rule, reason = entry["path"], entry["rule"], entry["reason"]
    if not _is_exact_file_path(path):
        raise refuse("path as the exact tracked path of one file: no folder, pattern, .. or absolute path")
    if rule not in [known.value for known in LayoutRule]:
        raise refuse(f"rule as one of {sorted(known.value for known in LayoutRule)}")
    if not _is_one_line_text(reason):
        raise refuse(ONE_LINE_REASON)
    return LayoutAllowance(path, LayoutRule(rule), reason)


def _compose_allowances(compose: dict) -> tuple[ComposeAllowance, ...]:
    entries = compose.get("allow", [])
    if not isinstance(entries, list):
        raise ConfigError(f"{CONFIG_PATH} needs [[compose.allow]] as an array of tables, like:\n{COMPOSE_ALLOWANCE_EXAMPLE}")
    allowances: dict[tuple[str, str], ComposeAllowance] = {}
    for entry in entries:
        allowance = _read_compose_allowance(entry)
        key = (allowance.file, allowance.service)
        if key in allowances:
            raise ConfigError(
                f"{CONFIG_PATH} [[compose.allow]] declares service = {allowance.service!r} of file = "
                f"{allowance.file!r} twice; one declaration per file and service, like:\n{COMPOSE_ALLOWANCE_EXAMPLE}"
            )
        allowances[key] = allowance
    return tuple(allowances.values())


def _read_compose_allowance(entry: object) -> ComposeAllowance:
    def refuse(need: str) -> ConfigError:
        return ConfigError(f"{CONFIG_PATH} [[compose.allow]] entry {entry!r} needs {need}, like:\n{COMPOSE_ALLOWANCE_EXAMPLE}")

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


def load_gate_settings(text: str | None) -> GateSettings:
    """mandatory_jobs is judged before legacy, so a config wrong in both is refused for mandatory_jobs."""
    if text is None:
        raise ConfigError(f"{CONFIG_PATH} is missing - create it with:\n{EXAMPLE}")
    document = _validated_document(text)
    return GateSettings(
        _mandatory_jobs(document.gate), _legacy_mode(document.gate), document.declarations,
        document.advisory_allowances,
    )


def _read_config_text(repo: Path) -> str | None:
    """The repository's own config text, None when it has none; the one reader of quality/config.toml on disk."""
    try:
        return repository.read_text(repo, CONFIG_PATH)
    except repository.RepositoryFileError as error:
        raise ConfigError(str(error)) from error


def read_gate_settings(repo: Path) -> GateSettings:
    return load_gate_settings(_read_config_text(repo))


def load_secret_allowances(text: str | None) -> dict[Allowance, str]:
    """Allowance -> reason of every finding the repository allows; a missing config allows nothing.

    The whole document is validated, as the gate does: hygiene and the verdict refuse an unknown key and a malformed
    entry alike.
    """
    return {} if text is None else _validated_document(text).secret_allowances


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
    entries = secrets.get("allow", [])
    if not isinstance(entries, list):
        raise ConfigError(f"{CONFIG_PATH} needs [secrets] allow as a list, like:\n{ALLOWANCE_EXAMPLE}")
    allowances: dict[Allowance, str] = {}
    for entry in entries:
        allowance, reason = _read_allowance(entry)
        if allowance in allowances:
            raise ConfigError(f"{CONFIG_PATH} [secrets] allow lists {allowance.rule} in {allowance.file} twice for one value")
        allowances[allowance] = reason
    return allowances


def _read_allowance(entry: object) -> tuple[Allowance, str]:
    if not isinstance(entry, dict) or set(entry) != ALLOWANCE_KEYS:
        raise ConfigError(
            f"{CONFIG_PATH} [secrets] allow entry {entry!r} needs exactly a rule, a file, a secret_sha256 and a "
            f"reason (the old fingerprint form is gone), like:\n{ALLOWANCE_EXAMPLE}"
        )
    rule, file, secret_sha256, reason = (entry[key] for key in ("rule", "file", "secret_sha256", "reason"))
    if not all(isinstance(value, str) and value.strip() for value in (rule, file, reason)):
        raise ConfigError(
            f"{CONFIG_PATH} [secrets] allow entry {entry!r} needs a non-empty rule, file and reason, like:\n"
            f"{ALLOWANCE_EXAMPLE}"
        )
    if not (isinstance(secret_sha256, str) and SHA256_HEX.fullmatch(secret_sha256)):
        raise ConfigError(
            f"{CONFIG_PATH} [secrets] allow entry {entry!r} needs secret_sha256 as 64 lowercase hex digits, like:\n"
            f"{ALLOWANCE_EXAMPLE}"
        )
    if _spans_lines(reason):
        raise ConfigError(f"{CONFIG_PATH} [secrets] allow entry {entry!r} needs a one-line reason")
    return Allowance(rule, file, secret_sha256), reason


def read_secret_allowances(repo: Path) -> dict[Allowance, str]:
    return load_secret_allowances(_read_config_text(repo))


def load_advisory_allowances(text: str | None) -> tuple[AdvisoryAllowance, ...]:
    """Every advisory the repository allows, the whole document validated; a missing config allows nothing."""
    return () if text is None else _validated_document(text).advisory_allowances


def read_advisory_allowances(repo: Path) -> tuple[AdvisoryAllowance, ...]:
    return load_advisory_allowances(_read_config_text(repo))


def read_root_allowances(repo: Path) -> tuple[RootAllowance, ...]:
    """The root entries the repository declares, the whole document validated; a missing config declares none."""
    text = _read_config_text(repo)
    return () if text is None else _validated_document(text).root_allowances


def read_layout_allowances(repo: Path) -> tuple[LayoutAllowance, ...]:
    """The files the repository declares against a layout rule, the whole document validated; a missing config declares none."""
    text = _read_config_text(repo)
    return () if text is None else _validated_document(text).layout_allowances


def read_python_allowances(repo: Path) -> tuple[PythonAllowance, ...]:
    """The pyproject files the repository allows below the Python floor, the whole document validated; a missing config allows none."""
    text = _read_config_text(repo)
    return () if text is None else _validated_document(text).python_allowances


def read_compose_allowances(repo: Path) -> tuple[ComposeAllowance, ...]:
    """The host-networked services the repository declares, the whole document validated; a missing config declares none."""
    text = _read_config_text(repo)
    return () if text is None else _validated_document(text).compose_allowances
