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
    unknown = (set(document) - {"gate"}) | (set(document.get("gate", {})) - GATE_KEYS)
    if unknown:
        raise ConfigError(f"{CONFIG_PATH} has unknown key(s) {sorted(unknown)} - only [gate] mandatory_jobs exists")
    names = document.get("gate", {}).get("mandatory_jobs")
    if not isinstance(names, list) or not names or not all(isinstance(name, str) and name for name in names):
        raise ConfigError(
            f"{CONFIG_PATH} needs [gate] mandatory_jobs as a non-empty list of job names, like:\n{EXAMPLE}"
        )
    return tuple(names)


def read_mandatory_jobs(repo: Path) -> tuple[str, ...]:
    path = repo / CONFIG_PATH
    return load_mandatory_jobs(path.read_text(encoding="utf-8") if path.is_file() else None)
