import shlex
import unicodedata
from dataclasses import dataclass
from enum import StrEnum

from quality.config import Allowance


@dataclass(frozen=True)
class Command:
    """One line a person copies whole and runs; the label says where or when it applies."""

    text: str
    label: str | None = None

    def __str__(self) -> str:
        return self.text if self.label is None else f"{self.label}: {self.text}"


@dataclass(frozen=True)
class Instruction:
    """A step in words, for a remedy no single command performs."""

    text: str

    def __str__(self) -> str:
        return self.text


@dataclass(frozen=True)
class Choice:
    """One of the commands a person picks from; the label says when this one fits."""

    text: str
    label: str

    def __str__(self) -> str:
        return f"{self.label}: {self.text}"


@dataclass(frozen=True)
class OneOf:
    """Commands of which a person runs the one whose label fits, never one after the other; each choice carries the
    label a person chooses it by."""

    label: str
    choices: tuple[Choice, Choice, *tuple[Choice, ...]]

    def __str__(self) -> str:
        return f"{self.label}, one of: " + " or ".join(str(choice) for choice in self.choices)


@dataclass(frozen=True)
class Example:
    """Lines a person copies as they stand into the place `label` names; never a command and never shortened. It has no
    one-line form: lines joined onto one line are no example to copy."""

    label: str
    lines: tuple[str, *tuple[str, ...]]


type Step = Command | Instruction | OneOf | Example


@dataclass(frozen=True)
class Reason:
    """Why a rule could not judge: a fact of a few words and the steps that remedy it, in the order a person takes them."""

    fact: str
    next_steps: tuple[Step, ...] = ()


class CannotJudge(Exception):
    """The repository cannot be examined; this is never a clean result."""

    label = "cannot judge"

    def __init__(self, fact: str, next_steps: tuple[Step, ...] = ()) -> None:
        super().__init__(fact)
        self.reason = Reason(fact, next_steps)

    def __str__(self) -> str:
        fact, next_steps = self.reason.fact, self.reason.next_steps
        if not next_steps:
            return fact
        first, *later = next_steps
        return "; ".join([fact, f"next: {first}", *(f"then: {step}" for step in later)])


class Unparsable(CannotJudge):
    """A tracked file Python cannot decode or parse; only an operating-system read error is `cannot judge`."""

    label = "unparsable"


class FindingEvent(StrEnum):
    """What a finding's row says is the case: one fixed word per kind of finding, for every rule."""

    TRACKED = "tracked"
    PUBLISHED_PORT = "published port"
    UNREADABLE_PORT = "unreadable port"
    HOST_NETWORK = "host network"
    UNUSED = "unused"
    UNLISTED = "unlisted"
    CLAUDE_ONLY = "claude only"
    SYMLINK = "symlink"
    DIVERGES = "diverges"
    NO_AGENTS_FILE = "no AGENTS.md"
    MISNAMED = "misnamed"
    MISPLACED = "misplaced"
    NO_HEADER = "no header"
    STRAY = "stray"
    TEST_IMPORT = "test import"
    BELOW_FLOOR = "below floor"
    NO_FLOOR = "no floor"
    NEW_DEBT = "new debt"
    GREW = "grew"
    CAN_FALL = "can fall"
    NEAR_COPY = "near copy"
    BROKEN = "broken"
    SECRET = "secret"


class NoteEvent(StrEnum):
    """What a note's row says; a separate enum, so a finding can never carry one of these."""

    ALLOWED = "allowed"
    NOT_MEASURED = "not measured"
    UNUSED = "unused"
    WARNING = "warning"


@dataclass(frozen=True)
class Finding:
    """With an `event`, `reason` is the fact its row shows; without one, the sentence of `headline`. `fix` is a sentence,
    or an `Example`: a label with the lines a person copies."""

    rule: str
    path: str
    line: int | None
    reason: str
    fix: str | Example
    allowance: Allowance | None = None
    detail: tuple[str, ...] = ()
    event: FindingEvent | None = None

    @property
    def location(self) -> str:
        path = printable(self.path)
        return path if self.line is None else f"{path}:{self.line}"


@dataclass(frozen=True)
class Note:
    """What a rule reports without asking for a change: a `·` row under its area, which counts as no finding."""

    path: str
    event: NoteEvent
    fact: str
    line: int | None = None

    @property
    def location(self) -> str:
        path = printable(self.path)
        return path if self.line is None else f"{path}:{self.line}"


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


@dataclass(frozen=True)
class RuleResult:
    """What a rule judged: its `findings`, its `extent` (how much its area row says it covered), `measured_only` when
    it judged without enforcing, and the `notes` it reports without asking for a change."""

    findings: list[Finding]
    extent: int
    measured_only: bool
    notes: tuple[Note, ...] = ()


def headline(finding: Finding) -> str:
    return f"{finding.rule} {finding.location} - {printable(finding.reason)}"


def render(finding: Finding) -> str:
    return f"{headline(finding)}\n  Fix: {printable(finding.fix)}"
