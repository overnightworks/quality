import shlex
import unicodedata
from dataclasses import dataclass, field
from enum import StrEnum

from quality.config import CONFIG_PATH, LAYOUT_ALLOWANCE_HEADING, Allowance, ConfigError


@dataclass(frozen=True)
class Command:
    """One line a person copies whole and runs; the label says where or when it applies, and `own_line` stands it below
    a bare `next:`, so selecting the line copies the command alone."""

    text: str
    label: str | None = None
    own_line: bool = False

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

CONFIG_EXAMPLE_LABEL = f"write it in {CONFIG_PATH} like this"


def allowance_clause(rule: str) -> str:
    return f"declare it in {CONFIG_PATH} with {LAYOUT_ALLOWANCE_HEADING}, its path, the rule {rule} and a reason"


def stale_declaration(path: str, rule: str) -> Finding:
    return Finding(
        rule, path, None,
        f"{LAYOUT_ALLOWANCE_HEADING} matches no tracked file",
        f"delete every {LAYOUT_ALLOWANCE_HEADING} entry for it from {CONFIG_PATH}",
        event=FindingEvent.UNUSED,
    )


# A gate run's own steps: a fix in the checkout reaches the gate through a push, any other through a re-run.
FIX_THEN_RERUN = Instruction("fix what the error names, then re-run this run's failed jobs")
PUSH_THE_FIX = Instruction("push the fix; the gate judges the new commit")
RERUN = Instruction("re-run this run's failed jobs")


@dataclass(frozen=True)
class Reason:
    """Why a rule could not judge: a fact of a few words and the steps that remedy it, in the order a person takes them."""

    fact: str
    next_steps: tuple[Step, ...] = ()


@dataclass(frozen=True)
class Release:
    """A quality release a row names: its tag, or the version of an installation that is no tagged release."""

    tag: str

    def __str__(self) -> str:
        return self.tag


class CannotJudge(Exception):
    """The repository cannot be examined or the invocation is wrong; this is never a clean result."""

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


def config_refusal(error: ConfigError, *then: Step) -> CannotJudge:
    """A config refusal with its fact and example apart, never read back from `str(error)`; `then` is what the config's
    place asks of a person next."""
    example = () if error.example is None else (Example(CONFIG_EXAMPLE_LABEL, error.example),)
    return CannotJudge(error.fact, (*example, *then))


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
    TITLE = "title"
    SECTION = "section"
    TOO_LONG = "too long"
    MISSING = "missing"
    SHARED_GROUP = "shared group"
    CANCELS_MAIN = "cancels main"
    SERIAL = "serial"
    INSTALLS = "installs"
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
    INVALID = "invalid"
    INSECURE = "insecure"
    AFFECTED = "affected"
    CONFLICT = "conflict"
    SECRET = "secret"  # noqa: S105 - the event word of a secret finding and no password
    PRINT_CALL = "print call"
    OPEN = "open"


class NoteEvent(StrEnum):
    """What a note's row says; a separate enum, so a finding can never carry one of these."""

    ALLOWED = "allowed"
    DROP = "drop"
    DROPPED = "dropped"
    EXITED = "exited"
    LOWER = "lower"
    LOWERED = "lowered"
    MOVE = "move"
    MOVED = "moved"
    NOT_CALLED = "not called"
    NOT_MEASURED = "not measured"
    OPEN = "open"
    RECORD = "record"
    RECORDED = "recorded"
    UNUSED = "unused"
    WARNING = "warning"


@dataclass(frozen=True)
class Finding:
    """`fix` is a sentence, or an `Example`: a label with the lines a person copies."""

    rule: str
    path: str
    line: int | None
    fact: str
    fix: str | Example
    allowance: Allowance | None = None
    detail: tuple[str, ...] = ()
    event: FindingEvent = field(kw_only=True)

    @property
    def location(self) -> str:
        path = printable(self.path)
        return path if self.line is None else f"{path}:{self.line}"


@dataclass(frozen=True)
class Note:
    """What a rule reports without asking for a change: a row under its area, which counts as no finding."""

    path: str
    event: NoteEvent
    fact: str
    line: int | None = None

    @property
    def location(self) -> str:
        path = printable(self.path)
        return path if self.line is None else f"{path}:{self.line}"


@dataclass(frozen=True)
class ScannerRun:
    """What one scanner said in one run, for `-v`: its exit, and its stderr's lines or what stands instead of them."""

    scanner: str
    exit_code: int
    lines: tuple[str, ...]
    withheld: str = ""


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
class LockExtent:
    """How much the advisory rule judged: the tracked lock files, and the packages osv-scanner lists for them."""

    lockfiles: int
    packages: int


@dataclass(frozen=True)
class StagedExtent:
    """How much a staged run judged: the files the next commit changes."""

    files: int


type Extent = int | LockExtent | StagedExtent


@dataclass(frozen=True)
class RuleResult:
    """What a rule judged: its `findings`, its `extent` (how much its area row says it covered), `measured_only` when
    it judged without enforcing, and the `notes` it reports without asking for a change."""

    findings: list[Finding]
    extent: Extent
    measured_only: bool
    notes: tuple[Note, ...] = ()
