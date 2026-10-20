import shlex
import unicodedata
from dataclasses import dataclass

from quality.config import Allowance

# A line taken from another tool's rendering carries this, so no line the door prints starts with repository text.
DETAIL_MARKER = "  | "


@dataclass(frozen=True)
class Finding:
    rule: str
    path: str
    line: int | None
    reason: str
    fix: str
    allowance: Allowance | None = None
    detail: tuple[str, ...] = ()

    @property
    def location(self) -> str:
        path = printable(self.path)
        return path if self.line is None else f"{path}:{self.line}"


def printable(text: str) -> str:
    """The one rendering of repository text: invalid UTF-8 prints as `\\udcNN` and every control character (C0, DEL,
    C1) as `\\xNN`, so no printed line can start with, or be split by, what a repository holds."""
    escaped = text.encode("utf-8", "backslashreplace").decode("utf-8")
    return "".join(f"\\x{ord(character):02x}" if unicodedata.category(character) == "Cc" else character for character in escaped)


def shell_word(text: str) -> str | None:
    """`text` as one word every POSIX shell and zsh reads back unchanged, or None where it has no such word:
    what `printable` would change cannot be printed into a command to copy."""
    if printable(text) != text:
        return None
    word = shlex.quote(text)
    # zsh's default EQUALS option expands a word that starts with `=` to a command path; shlex leaves it bare.
    return f"'{word}'" if word.startswith("=") else word


def render(finding: Finding) -> str:
    detail = "".join(f"\n{DETAIL_MARKER}{printable(line)}" for line in finding.detail)
    return f"{finding.rule} {finding.location} - {printable(finding.reason)}{detail}\n  Fix: {printable(finding.fix)}"
