import shlex
import unicodedata
from dataclasses import dataclass

from quality.config import Allowance


@dataclass(frozen=True)
class Finding:
    rule: str
    path: str
    line: int | None
    reason: str
    fix: str
    allowance: Allowance | None = None

    @property
    def location(self) -> str:
        path = printable(self.path)
        return path if self.line is None else f"{path}:{self.line}"


def printable(path: str) -> str:
    """The one rendering of a path: a name that is not valid UTF-8 prints backslash-escaped, never as a crash."""
    return path.encode("utf-8", "backslashreplace").decode("utf-8")


def shell_word(text: str) -> str | None:
    """`text` as one word every POSIX shell and zsh reads back unchanged, or None where it has no such word:
    invalid UTF-8 and control characters (C0, DEL, C1) cannot be printed into a command to copy."""
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return None
    if any(unicodedata.category(character) == "Cc" for character in text):
        return None
    word = shlex.quote(text)
    # zsh's default EQUALS option expands a word that starts with `=` to a command path; shlex leaves it bare.
    return f"'{word}'" if word.startswith("=") else word


def render(finding: Finding) -> str:
    return f"{finding.rule} {finding.location} - {finding.reason}\n  Fix: {finding.fix}"
