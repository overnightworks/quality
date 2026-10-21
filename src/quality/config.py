import datetime
import re
import tomllib
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import NamedTuple

from quality import repository

CONFIG_PATH = "quality/config.toml"
GATE_KEYS = {"mandatory_jobs", "legacy", "loosened"}
SECRETS_KEYS = {"allow"}
ADVISORIES_KEYS = {"allow"}
TABLES = {"gate", "secrets", "advisories"}
ALLOWANCE_KEYS = {"rule", "file", "secret_sha256", "reason"}
DECLARATION_KEYS = {"what", "item", "date", "reason"}
ADVISORY_ALLOWANCE_KEYS = {"id", "lock", "until", "date", "item", "reason"}
SHA256_HEX = re.compile(r"[0-9a-f]{64}")
# ASCII only, so a lookalike name (a Cyrillic letter in it) is no repository.
REPOSITORY_NAME = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
ITEM_REFERENCE = re.compile(rf"(?:{REPOSITORY_NAME.pattern})?#[1-9][0-9]*")
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


def _parse(text: str, example: str) -> dict:
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
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


def _refuse_unknown_keys(document: dict, gate: dict, advisories: dict) -> None:
    unknown = (set(document) - TABLES) | (set(gate) - GATE_KEYS) | (set(advisories) - ADVISORIES_KEYS)
    if unknown:
        raise ConfigError(
            f"{CONFIG_PATH} has unknown key(s) {sorted(unknown)} - only [gate] mandatory_jobs, [gate] legacy, "
            "[[gate.loosened]], [secrets] allow and [[advisories.allow]] exist"
        )


def _validated_document(text: str) -> _Document:
    """The one validation of the whole config: one parse, one unknown-key check, and every entry of every table."""
    document = _parse(text, EXAMPLE)
    gate = _table(document, "gate", EXAMPLE)
    advisories = _table(document, "advisories", ADVISORY_ALLOWANCE_EXAMPLE)
    _refuse_unknown_keys(document, gate, advisories)
    return _Document(
        gate,
        _declarations(gate),
        _parse_allowances(_table(document, "secrets", ALLOWANCE_EXAMPLE)),
        _advisory_allowances(advisories),
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
        raise refuse("a non-empty one-line reason")
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
