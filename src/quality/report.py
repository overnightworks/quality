import shlex
import unicodedata
from dataclasses import dataclass

from quality.config import Allowance

# A line taken from another tool's rendering carries this, so no line the door prints starts with repository text.
DETAIL_MARKER = "  | "


class CannotJudge(Exception):
    """The repository cannot be examined; this is never a clean result."""

    label = "cannot judge"


class Unparsable(CannotJudge):
    """A tracked file Python cannot decode or parse; only an operating-system read error is `cannot judge`."""

    label = "unparsable"


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


# Control, format (bidi overrides and isolates, zero-width characters, tags) and line- or paragraph-separator characters.
ESCAPED_CATEGORIES = frozenset({"Cc", "Cf", "Zl", "Zp"})


def _escaped(character: str) -> str:
    category = unicodedata.category(character)
    if category not in ESCAPED_CATEGORIES:
        return character
    code_point = ord(character)
    if category == "Cc":
        return f"\\x{code_point:02x}"
    return f"\\u{code_point:04x}" if code_point <= 0xFFFF else f"\\U{code_point:08x}"


def printable(text: str) -> str:
    """The one rendering of repository text: invalid UTF-8 prints as `\\udcNN`, every control character (C0, DEL, C1)
    as `\\xNN`, and U+2028, U+2029 and every format character as `\\uNNNN` (`\\UNNNNNNNN` above U+FFFF), so no printed
    line can start with, be split by, or be reordered by what a repository holds."""
    escaped = text.encode("utf-8", "backslashreplace").decode("utf-8")
    return "".join(_escaped(character) for character in escaped)


def shell_word(text: str) -> str | None:
    """`text` as one word every POSIX shell and zsh reads back unchanged, or None where it has no such word:
    what `printable` would change cannot be printed into a command to copy."""
    if printable(text) != text:
        return None
    word = shlex.quote(text)
    # zsh's default EQUALS option expands a word that starts with `=` to a command path; shlex leaves it bare.
    return f"'{word}'" if word.startswith("=") else word


def headline(finding: Finding) -> str:
    return f"{finding.rule} {finding.location} - {printable(finding.reason)}"


def render(finding: Finding) -> str:
    detail = "".join(f"\n{DETAIL_MARKER}{printable(line)}" for line in finding.detail)
    return f"{headline(finding)}{detail}\n  Fix: {printable(finding.fix)}"
