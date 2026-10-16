import tomllib
from pathlib import Path

CONFIG_PATH = "quality/config.toml"
GATE_KEYS = {"mandatory_jobs"}
EXAMPLE = '[gate]\nmandatory_jobs = ["checks"]'


class ConfigError(Exception):
    """The quality config cannot be read; the message carries the reason and the fix."""


def load_mandatory_jobs(text: str | None) -> tuple[str, ...]:
    if text is None:
        raise ConfigError(f"{CONFIG_PATH} is missing - create it with:\n{EXAMPLE}")
    try:
        document = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise ConfigError(f"{CONFIG_PATH} is not valid TOML ({error}) - write it like:\n{EXAMPLE}") from error
    gate = document.get("gate", {})
    if not isinstance(gate, dict):
        raise ConfigError(f"{CONFIG_PATH} has [gate] as a value, not a table - write it like:\n{EXAMPLE}")
    unknown = (set(document) - {"gate"}) | (set(gate) - GATE_KEYS)
    if unknown:
        raise ConfigError(f"{CONFIG_PATH} has unknown key(s) {sorted(unknown)} - only [gate] mandatory_jobs exists")
    names = gate.get("mandatory_jobs")
    if not isinstance(names, list) or not names or not all(isinstance(name, str) and name for name in names):
        raise ConfigError(
            f"{CONFIG_PATH} needs [gate] mandatory_jobs as a non-empty list of job names, like:\n{EXAMPLE}"
        )
    return tuple(names)


def read_mandatory_jobs(repo: Path) -> tuple[str, ...]:
    path = repo / CONFIG_PATH
    if not path.is_file():
        return load_mandatory_jobs(None)
    try:
        return load_mandatory_jobs(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as error:
        raise ConfigError(f"{CONFIG_PATH} cannot be read ({error})") from error
