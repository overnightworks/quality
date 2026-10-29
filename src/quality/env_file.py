"""The `env-file` rule: a tracked env file publishes configuration and often secrets."""

from pathlib import PurePosixPath

from quality.report import Finding, shell_word
from quality.rules import Rule

ENV_TEMPLATE_SUFFIX = ".example"


def _untrack_fix(name: str) -> str:
    word = shell_word(name)
    if word is None:
        return "remove it from the index by its bytes (git ls-files -z shows them), then add it to .gitignore."
    return f"git --literal-pathspecs rm --cached -- {word} and add it to .gitignore."


def check_env_files(tracked: list[str]) -> list[Finding]:
    return [
        Finding(
            Rule.ENV_FILE, name, None,
            "a tracked env file publishes configuration and often secrets.",
            _untrack_fix(name),
        )
        for name in tracked
        if _is_env_file(PurePosixPath(name).name)
    ]


def _is_env_file(name: str) -> bool:
    if name.endswith(ENV_TEMPLATE_SUFFIX):
        return False
    return name == ".env" or name.startswith(".env.") or name.endswith(".env")
