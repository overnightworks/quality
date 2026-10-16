import tomllib
from pathlib import Path

CONFIG_PATH = "quality/config.toml"
GATE_KEYS = {"mandatory_jobs"}
SECRETS_KEYS = {"allow"}
ALLOWANCE_KEYS = {"fingerprint", "reason"}
EXAMPLE = '[gate]\nmandatory_jobs = ["checks"]'
ALLOWANCE_EXAMPLE = (
    '[secrets]\nallow = [{ fingerprint = "<commit>:<file>:<rule>:<line>", reason = "enum value, not a key" }]'
)


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
            f"{CONFIG_PATH} has unknown key(s) {sorted(unknown)} - only [gate] mandatory_jobs and [secrets] allow exist"
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


def read_mandatory_jobs(repo: Path) -> tuple[str, ...]:
    path = repo / CONFIG_PATH
    try:
        return load_mandatory_jobs(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return load_mandatory_jobs(None)
    except (OSError, UnicodeDecodeError) as error:
        raise ConfigError(f"{CONFIG_PATH} cannot be read ({error})") from error


def load_secret_allowances(text: str | None) -> dict[str, str]:
    """Fingerprint -> reason of every finding the repository allows; a missing config allows nothing."""
    if text is None:
        return {}
    document = _parse(text, ALLOWANCE_EXAMPLE)
    secrets = _table(document, "secrets", ALLOWANCE_EXAMPLE)
    _refuse_unknown_keys(document, _table(document, "gate", EXAMPLE), secrets)
    entries = secrets.get("allow", [])
    if not isinstance(entries, list):
        raise ConfigError(f"{CONFIG_PATH} needs [secrets] allow as a list, like:\n{ALLOWANCE_EXAMPLE}")
    return dict(_read_allowance(entry) for entry in entries)


def _read_allowance(entry: object) -> tuple[str, str]:
    if not isinstance(entry, dict) or set(entry) != ALLOWANCE_KEYS:
        raise ConfigError(
            f"{CONFIG_PATH} [secrets] allow entry {entry!r} needs exactly a fingerprint and a reason, like:\n"
            f"{ALLOWANCE_EXAMPLE}"
        )
    fingerprint, reason = entry["fingerprint"], entry["reason"]
    if not (isinstance(fingerprint, str) and fingerprint.strip() and isinstance(reason, str) and reason.strip()):
        raise ConfigError(
            f"{CONFIG_PATH} [secrets] allow entry {entry!r} needs a non-empty fingerprint and a reason, like:\n"
            f"{ALLOWANCE_EXAMPLE}"
        )
    return fingerprint, reason


def read_secret_allowances(repo: Path) -> dict[str, str]:
    path = repo / CONFIG_PATH
    try:
        return load_secret_allowances(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return load_secret_allowances(None)
    except (OSError, UnicodeDecodeError) as error:
        raise ConfigError(f"{CONFIG_PATH} cannot be read ({error})") from error
