import re
import tomllib
from enum import Enum
from pathlib import Path
from typing import NamedTuple

CONFIG_PATH = "quality/config.toml"
GATE_KEYS = {"mandatory_jobs", "legacy"}
SECRETS_KEYS = {"allow"}
ALLOWANCE_KEYS = {"rule", "file", "secret_sha256", "reason"}
SHA256_HEX = re.compile(r"[0-9a-f]{64}")
EXAMPLE = '[gate]\nmandatory_jobs = ["checks"]'
LEGACY_EXAMPLE = '[gate]\nlegacy = "fail"'
ALLOWANCE_EXAMPLE = (
    '[secrets]\nallow = [{ rule = "aws-access-token", file = "tests/fixture.py", '
    'secret_sha256 = "<64 lowercase hex>", reason = "fixture value, not a key" }]'
)


class LegacyMode(Enum):
    """What the repository's open findings on its default branch do to a pull request."""

    REPORT = "report"
    FAIL = "fail"


class Allowance(NamedTuple):
    """A secret value in a file, as one rule sees it; the value itself is never stored, only its hash."""

    rule: str
    file: str
    secret_sha256: str


class ConfigError(Exception):
    """The quality config cannot be read; the message carries the reason and the fix."""


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


def _refuse_unknown_keys(document: dict, gate: dict, secrets: dict) -> None:
    unknown = (set(document) - {"gate", "secrets"}) | (set(gate) - GATE_KEYS) | (set(secrets) - SECRETS_KEYS)
    if unknown:
        raise ConfigError(
            f"{CONFIG_PATH} has unknown key(s) {sorted(unknown)} - only [gate] mandatory_jobs, [gate] legacy and [secrets] allow exist"
        )


def load_mandatory_jobs(text: str | None) -> tuple[str, ...]:
    if text is None:
        raise ConfigError(f"{CONFIG_PATH} is missing - create it with:\n{EXAMPLE}")
    document = _parse(text, EXAMPLE)
    gate = _table(document, "gate", EXAMPLE)
    _refuse_unknown_keys(document, gate, _table(document, "secrets", ALLOWANCE_EXAMPLE))
    names = gate.get("mandatory_jobs")
    if not isinstance(names, list) or not names or not all(isinstance(name, str) and name for name in names):
        raise ConfigError(
            f"{CONFIG_PATH} needs [gate] mandatory_jobs as a non-empty list of job names, like:\n{EXAMPLE}"
        )
    return tuple(names)


def load_legacy_mode(text: str | None) -> LegacyMode:
    """A missing config or key reports; the verdict is what insists on a config."""
    if text is None:
        return LegacyMode.REPORT
    document = _parse(text, LEGACY_EXAMPLE)
    gate = _table(document, "gate", LEGACY_EXAMPLE)
    _refuse_unknown_keys(document, gate, _table(document, "secrets", ALLOWANCE_EXAMPLE))
    value = gate.get("legacy", LegacyMode.REPORT.value)
    try:
        return LegacyMode(value)
    except ValueError:
        modes = " or ".join(f'"{mode.value}"' for mode in LegacyMode)
        raise ConfigError(f"{CONFIG_PATH} has [gate] legacy = {value!r}; it must be {modes}, like:\n{LEGACY_EXAMPLE}") from None


def read_mandatory_jobs(repo: Path) -> tuple[str, ...]:
    path = repo / CONFIG_PATH
    try:
        return load_mandatory_jobs(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return load_mandatory_jobs(None)
    except (OSError, UnicodeDecodeError) as error:
        raise ConfigError(f"{CONFIG_PATH} cannot be read ({error})") from error


def load_secret_allowances(text: str | None) -> dict[Allowance, str]:
    """Allowance -> reason of every finding the repository allows; a missing config allows nothing."""
    if text is None:
        return {}
    document = _parse(text, ALLOWANCE_EXAMPLE)
    secrets = _table(document, "secrets", ALLOWANCE_EXAMPLE)
    _refuse_unknown_keys(document, _table(document, "gate", EXAMPLE), secrets)
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
    if "\n" in reason or "\r" in reason:
        raise ConfigError(f"{CONFIG_PATH} [secrets] allow entry {entry!r} needs a one-line reason")
    return Allowance(rule, file, secret_sha256), reason


def read_secret_allowances(repo: Path) -> dict[Allowance, str]:
    path = repo / CONFIG_PATH
    try:
        return load_secret_allowances(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return load_secret_allowances(None)
    except (OSError, UnicodeDecodeError) as error:
        raise ConfigError(f"{CONFIG_PATH} cannot be read ({error})") from error


def read_legacy_mode(repo: Path) -> LegacyMode:
    path = repo / CONFIG_PATH
    try:
        return load_legacy_mode(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return load_legacy_mode(None)
    except (OSError, UnicodeDecodeError) as error:
        raise ConfigError(f"{CONFIG_PATH} cannot be read ({error})") from error
