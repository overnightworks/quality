"""The `size` rule: lines per file of every tracked file in a table language, and lines and CCN per function of
every tracked Python file, held by a baseline; the baseline file itself, `quality/baseline.toml`, is `baseline_file`'s.

lizard measures; Python's own `ast` only names each function by its qualified symbol (`Class.method`), which
lizard leaves unqualified. Both read the same text, decoded from the file's bytes the way Python reads source.
The other languages are counted by lines only: lizard misplaces their function boundaries.
"""

import ast
import io
import tokenize
from collections import Counter
from collections.abc import Callable, Collection, Iterable, Iterator, Mapping
from dataclasses import dataclass, replace
from enum import Enum
from operator import attrgetter
from pathlib import Path, PurePosixPath

import lizard

from quality import checkout, repository
from quality.baseline_file import (
    BASELINE_PATH,
    FIX_COMMAND,
    Baseline,
    ClonePair,
    JobKey,
    JobMinutes,
    Key,
    Metric,
    SizeEntries,
    SizeError,
    UnknownTable,
    key_order,
)
from quality.config import CONFIG_PATH, GENERATED_FILE_HEADING, GeneratedFile
from quality.output import SEPARATOR, counted
from quality.report import CannotJudge, Finding, FindingEvent, Instruction, Note, NoteEvent, RuleResult, Unparsable
from quality.rules import Rule

RESTORE_STEP = Instruction(f"restore {BASELINE_PATH} from git, or repair it by hand")
UNKNOWN_TABLE_HINT = "run the quality release your gate pins"
RAISE_IS_BY_HAND = "a raise is a hand edit and a declared loosening"
OCCURRENCE_MARK = "#"
PARSE_ERRORS = (SyntaxError, ValueError, LookupError)
TEST_DIRECTORIES = frozenset({"test", "tests"})
RUNNER_TEST_DIRECTORIES = TEST_DIRECTORIES | {"__tests__"}
RUNNER_TEST_WORDS = frozenset({"test", "spec", "test-d", "spec-d"})

FILE_LINES_FROM = 800
FUNCTION_LINES_FROM = 60
CCN_ABOVE = 15


def debt_from(metric: Metric) -> int:
    return {
        Metric.CCN: CCN_ABOVE + 1,
        Metric.FILE_LINES: FILE_LINES_FROM,
        Metric.FUNCTION_LINES: FUNCTION_LINES_FROM,
    }[metric]


def is_debt(metric: Metric, value: int) -> bool:
    return value >= debt_from(metric)


def describe_value(metric: Metric, value: int) -> str:
    return f"CCN {value}" if metric is Metric.CCN else f"{value} lines"


def fix_for(metric: Metric) -> str:
    return {
        Metric.CCN: f"split its branches until its CCN is {CCN_ABOVE} or less",
        Metric.FILE_LINES: f"split the file below {FILE_LINES_FROM} lines",
        Metric.FUNCTION_LINES: f"split the function below {FUNCTION_LINES_FROM} lines",
    }[metric]


class Language(Enum):
    """A language the `size` rule counts: its lines everywhere, its functions only for Python."""

    PYTHON = "Python"
    TYPESCRIPT = "TypeScript"
    JAVASCRIPT = "JavaScript"
    SVELTE = "Svelte"
    VUE = "Vue"

    @property
    def suffixes(self) -> frozenset[str]:
        return {
            Language.PYTHON: frozenset({".py"}),
            Language.TYPESCRIPT: frozenset({".ts", ".tsx", ".mts", ".cts"}),
            Language.JAVASCRIPT: frozenset({".js", ".jsx", ".mjs", ".cjs"}),
            Language.SVELTE: frozenset({".svelte"}),
            Language.VUE: frozenset({".vue"}),
        }[self]

    def is_test_file(self, path: PurePosixPath) -> bool:
        """The directory rule is shared; the file name rule is the language's own: Python's pytest names, the
        TypeScript and JavaScript `*.test.*`, `*.spec.*`, their `-d` type-test forms and `__tests__/`, none for Svelte and Vue."""
        runner = self in {Language.TYPESCRIPT, Language.JAVASCRIPT}
        if (RUNNER_TEST_DIRECTORIES if runner else TEST_DIRECTORIES) & set(path.parts[:-1]):
            return True
        if self is Language.PYTHON:
            return path.name == "conftest.py" or path.name.startswith("test_") or path.stem.endswith("_test")
        return runner and bool(runner_test_words(path.name))


def runner_test_words(name: str) -> frozenset[str]:
    """The words between the first and the last `.` of `name` that a test runner reads as a test: `test`, `spec` and
    their type-test forms."""
    return RUNNER_TEST_WORDS.intersection(name.split(".")[1:-1])


LANGUAGE_OF_SUFFIX = {suffix: language for language in Language for suffix in language.suffixes}


class Area(Enum):
    PRODUCTION = "production"
    TESTS = "tests"


@dataclass(frozen=True)
class Measured:
    value: int
    line: int | None


Measurements = dict[Key, Measured]


class UnparsableSource(SizeError):
    """A tracked file Python cannot decode or parse, so its functions cannot be measured."""


def language_of(name: str) -> Language | None:
    return LANGUAGE_OF_SUFFIX.get(PurePosixPath(name).suffix)


def read_sources(root: Path, tracked: list[str]) -> dict[str, bytes]:
    """The tracked files of a table language on disk: a tracked file absent from it (deleted, sparse, a dangling link)
    is not read."""
    on_disk = checkout.on_disk(root, [name for name in tracked if language_of(name)])
    return checkout.read_existing(repository.read_bytes, root, on_disk)


def area_of(path: str) -> Area:
    """The area of a path in a table language."""
    posix = PurePosixPath(path)
    language = language_of(path)
    return Area.TESTS if language and language.is_test_file(posix) else Area.PRODUCTION


@dataclass(frozen=True)
class TreeMeasurement:
    """Every file's lines, and the functions of every Python file Python can parse; `unparsable` maps the rest of
    the Python files to why."""

    measurements: Measurements
    unparsable: dict[str, str]

    def complete(self) -> Measurements:
        """Holding or writing a baseline needs every function measured, so an unparsable file cannot be judged."""
        if self.unparsable:
            path = min(self.unparsable)
            raise UnparsableSource(f"{path} is not valid Python ({self.unparsable[path]})")
        return self.measurements


def measure(sources: dict[str, bytes]) -> TreeMeasurement:
    """A file's lines are counted in its bytes, so a file Python cannot decode still has them."""
    measurements: Measurements = {}
    unparsable: dict[str, str] = {}
    for path, source in sources.items():
        measurements[Key(Metric.FILE_LINES, path)] = Measured(len(source.splitlines()), None)
        if language_of(path) is not Language.PYTHON:
            continue
        try:
            text = python_text(source)
            tree = ast.parse(text, filename=path)
        except PARSE_ERRORS as error:
            unparsable[path] = str(error)
            continue
        measurements.update(_function_measurements(path, text, tree))
    return TreeMeasurement(measurements, unparsable)


def python_text(source: bytes) -> str:
    """Python's own source reading: newlines are translated on the bytes first, as CPython's tokenizer does, so
    a CR-only file has the lines Python sees both for its PEP 263 cookie and for lizard; then a cookie or a
    UTF-8 BOM names the encoding, UTF-8 otherwise."""
    source = source.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    encoding, _ = tokenize.detect_encoding(io.BytesIO(source).readline)
    return source.decode(encoding)


def _function_measurements(path: str, text: str, tree: ast.Module) -> Iterator[tuple[Key, Measured]]:
    symbols = qualified_symbols(tree)
    functions = sorted(lizard.analyze_file.analyze_source_code(path, text).function_list, key=attrgetter("start_line"))
    occurrences: Counter[str] = Counter()
    for function in functions:
        qualified = symbols.get(function.start_line, function.name)
        occurrences[qualified] += 1
        symbol = nth(qualified, occurrences[qualified])
        yield Key(Metric.FUNCTION_LINES, path, symbol), Measured(function.length, function.start_line)
        yield Key(Metric.CCN, path, symbol), Measured(function.cyclomatic_complexity, function.start_line)


def nth(symbol: str, occurrence: int) -> str:
    """A property's getter and setter, or a conditionally redefined function, share one qualified symbol; each
    keeps its own entry, numbered in source order, so the longer one cannot hide the shorter one growing."""
    return symbol if occurrence == 1 else f"{symbol}{OCCURRENCE_MARK}{occurrence}"


def qualified_symbols(tree: ast.Module) -> dict[int, str]:
    symbols: dict[int, str] = {}

    def visit(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                symbols[child.lineno] = f"{prefix}{child.name}"
                visit(child, f"{prefix}{child.name}.")
            else:
                visit(child, prefix)

    visit(tree, "")
    return symbols


def debt(measurements: Measurements) -> SizeEntries:
    return {key: measured.value for key, measured in measurements.items() if is_debt(key.metric, measured.value)}


@dataclass(frozen=True)
class AreaSummary:
    """One area's physical lines and, per metric, how many files or functions are debt."""

    lines: int
    debt: dict[Metric, int]


def summarise(tree: TreeMeasurement) -> dict[Area, AreaSummary]:
    lines: Counter[Area] = Counter()
    debts: Counter[tuple[Area, Metric]] = Counter()
    for key, measured in tree.measurements.items():
        area = area_of(key.path)
        if key.metric is Metric.FILE_LINES:
            lines[area] += measured.value
        if is_debt(key.metric, measured.value):
            debts[area, key.metric] += 1
    return {area: AreaSummary(lines[area], {metric: debts[area, metric] for metric in Metric}) for area in Area}


def language_lines(tree: TreeMeasurement) -> dict[Language, int]:
    """Every measured language with its lines, production and tests together, largest first and by name on a tie."""
    lines: Counter[Language] = Counter()
    for key, measured in tree.measurements.items():
        if key.metric is Metric.FILE_LINES and (language := language_of(key.path)):
            lines[language] += measured.value
    return dict(sorted(lines.items(), key=lambda entry: (-entry[1], entry[0].value)))


def gone_entries(
    held: SizeEntries, measurements: Measurements, tracked: frozenset[str], generated: Collection[GeneratedFile]
) -> dict[Key, str]:
    """Each held entry with no measurement that can fall, and what became of its subject; any other entry with no
    measurement stands unjudged: a path the index tracks and the disk lacks, a key no measurement makes."""
    declared = {file.path for file in generated}
    gone = {}
    for key in held.keys() - measurements.keys():
        python = language_of(key.path) is Language.PYTHON
        if key.path not in tracked or (python and Key(Metric.FILE_LINES, key.path) in measurements):
            gone[key] = f"{_subject(key)} is gone"
        elif key.path in declared:
            gone[key] = f"{_subject(key)} is left out as generated"
    return gone


def compare(measurements: Measurements, baseline: SizeEntries, gone: Mapping[Key, str]) -> list[Finding]:
    """The tree on disk against the baseline; after the measured findings, each entry `gone` names can fall."""
    findings = []
    for key in sorted(measurements, key=key_order):
        measured, held = measurements[key], baseline.get(key)
        if held is None and is_debt(key.metric, measured.value):
            limit = f"debt from {describe_value(key.metric, debt_from(key.metric))}"
            findings.append(_finding(key, measured, FindingEvent.NEW_DEBT, limit, fix_for(key.metric)))
        elif held is not None and measured.value > held:
            fix = f"bring it back to {describe_value(key.metric, held)} or below"
            findings.append(_finding(key, measured, FindingEvent.GREW, f"ceiling {held}", fix))
        elif held is not None and measured.value < held:
            findings.append(_finding(key, measured, FindingEvent.CAN_FALL, f"ceiling {held}", FIX_COMMAND))
    for key in sorted(gone, key=key_order):
        fact = SEPARATOR.join((gone[key], f"ceiling {describe_value(key.metric, baseline[key])}"))
        findings.append(Finding(Rule.SIZE, key.path, None, fact, FIX_COMMAND, event=FindingEvent.CAN_FALL))
    return findings


@dataclass(frozen=True)
class RaisedEntry:
    """A baseline entry the head holds higher than the base; `base` is None when the head adds the entry."""

    key: Key | JobKey
    base: int | None
    head: int

    def describe(self) -> str:
        """The raise as a person reads it, one wording for the fix and the verdict."""
        if self.base is None:
            return f"adds {self.key.label} {self.head}"
        return f"raises {self.key.label} {self.base} -> {self.head}"


@dataclass(frozen=True)
class Renames:
    """What git renamed between two commits: `moves` maps a source path to its target, `targets` holds every target.

    A target whose source no baseline key can name (not UTF-8) is a target without a move.
    """

    moves: Mapping[str, str]
    targets: frozenset[str]


NO_RENAMES = Renames({}, frozenset())


def renames_of(name_status: bytes) -> Renames:
    """`git diff --name-status -z` records: a status, then one path, or two for a rename or a copy.

    Only a rename counts, and a source git lists as renamed twice cannot be judged. Only a rename's two paths are decoded,
    as UTF-8 without newline translation, so a path holding a carriage return stays the path git named. A rename whose
    target is not UTF-8 is skipped, since no baseline key can name it; one whose source is not UTF-8 still clears its
    target of leftovers and carries nothing.
    """
    renamed = list(_renamed_paths(name_status))
    sources = [old_bytes for old_bytes, _ in renamed]
    if len(set(sources)) != len(sources):
        raise CannotJudge("git listed one source as renamed twice")
    moves: dict[str, str] = {}
    unnamed_sources: set[str] = set()
    for old_bytes, new_bytes in renamed:
        try:
            new = new_bytes.decode("utf-8")
        except UnicodeDecodeError:
            continue
        try:
            moves[old_bytes.decode("utf-8")] = new
        except UnicodeDecodeError:
            unnamed_sources.add(new)
    return Renames(moves, frozenset(moves.values()) | unnamed_sources)


def _renamed_paths(name_status: bytes) -> Iterator[tuple[bytes, bytes]]:
    """The (source, target) bytes of each rename record; a record cut short cannot be judged."""
    fields = name_status.split(b"\0")
    if fields.pop() != b"":
        raise CannotJudge("git's list of renamed files ended inside a record")
    records = iter(fields)
    for status in records:
        try:
            paths = [next(records), *([next(records)] if status.startswith((b"R", b"C")) else [])]
        except StopIteration:
            raise CannotJudge("git's list of renamed files ended inside a record") from None
        if status.startswith(b"R"):
            yield paths[0], paths[1]


def named_paths(baseline: Baseline) -> frozenset[str]:
    """Every path a baseline names, by a size entry, a pair side or a job limit."""
    sides = {place.path for pair in baseline.clones or () for place in (pair.first, pair.second)}
    workflows = {key.path for key in baseline.job_minutes or ()}
    return frozenset({key.path for key in baseline.size} | sides | workflows)


def carried_base(base: Baseline, renames: Renames, kept: Collection[str]) -> Baseline:
    """The base as `renames` read it.

    An entry under a rename's target is a leftover of a file the base no longer has and is dropped. The entries under
    the old path move to the target unless `kept`, the paths the head still names, holds the old path.
    """
    moves = {old: new for old, new in renames.moves.items() if old not in kept}

    def new_path(path: str) -> str | None:
        """Where the base's entries under `path` go in the head's reading, None when they are dropped."""
        if path in renames.targets:
            return None
        return moves.get(path, path)

    size = {key._replace(path=path): value for key, value in base.size.items() if (path := new_path(key.path))}
    limits = base.job_minutes and {
        key._replace(path=path): minutes for key, minutes in base.job_minutes.items() if (path := new_path(key.path))
    }
    if base.clones is None:
        return Baseline(size, None, limits)
    pairs = set()
    for pair in base.clones:
        first, second = new_path(pair.first.path), new_path(pair.second.path)
        if first and second:
            pairs.add(ClonePair.of(pair.first._replace(path=first), pair.second._replace(path=second)))
    return Baseline(size, frozenset(pairs), limits)


def raised_entries(base: SizeEntries, head: SizeEntries) -> list[RaisedEntry]:
    """One baseline against another: an entry may fall or go, never rise or appear; a renamed symbol is a new one."""
    return _raised(base, head, sorted(head, key=key_order))


def raised_job_limits(base: JobMinutes, head: JobMinutes) -> list[RaisedEntry]:
    """The same for job limits, in the order the head file holds them; a renamed job is a new one."""
    return _raised(base, head, head)


def _raised[Entry: (Key, JobKey)](base: Mapping[Entry, int], head: Mapping[Entry, int], order: Iterable[Entry]) -> list[RaisedEntry]:
    return [RaisedEntry(key, base.get(key), head[key]) for key in order if key not in base or head[key] > base[key]]


def added_pairs(base: frozenset[ClonePair], head: frozenset[ClonePair]) -> list[ClonePair]:
    """The pairs only the head holds, in the order the file and the lines name them; a removed pair is no loosening."""
    return sorted(head - base)


def _finding(key: Key, measured: Measured, event: FindingEvent, limit: str, fix: str) -> Finding:
    fact = SEPARATOR.join((f"{_subject(key)} has {describe_value(key.metric, measured.value)}", limit))
    return Finding(Rule.SIZE, key.path, measured.line, fact, fix, event=event)


def _subject(key: Key) -> str:
    return "file" if key.metric is Metric.FILE_LINES else f"function {key.symbol}"


def _fall(key: Key, held: int, value: int) -> str:
    amounts = f"CCN {held} → {value}" if key.metric is Metric.CCN else f"{held} → {value} lines"
    return f"{_subject(key)} {amounts}"


FELL = frozenset({NoteEvent.LOWERED, NoteEvent.DROPPED})
LEFT_BEHIND = "left from before the move"


@dataclass(frozen=True)
class CeilingFix:
    """What `--fix` made of the ceiling; a first recording lists no change, because every entry is new."""

    ceiling: Baseline
    changes: tuple[Note, ...]
    first: bool = False
    text_differs: bool = False  # the file's bytes are not the ceiling's rendering although no entry changed

    @property
    def changed(self) -> bool:
        return self.first or bool(self.changes) or self.text_differs

    @property
    def fell(self) -> int:
        return sum(change.event in FELL for change in self.changes)

    @property
    def moved(self) -> int:
        return sum(change.event is NoteEvent.MOVED for change in self.changes)

    @property
    def recorded(self) -> bool:
        return any(change.event is NoteEvent.RECORDED for change in self.changes)


def lower_ceiling(
    held: Baseline, measurements: Measurements, pairs: frozenset[ClonePair], gone: Mapping[Key, str]
) -> CeilingFix:
    """Each held entry falls to its measured value and goes where it is no debt; one `gone` names goes, any other with
    no measurement stands, and nothing rises or enters. A held pair the tree lost goes. A file without `[clones]`
    records its pairs: the caller has seen that git never held the table."""
    size, changes = {key: value for key, value in held.size.items() if key not in gone}, []
    for key in sorted(held.size, key=key_order):
        ceiling, measured = held.size[key], measurements.get(key)
        if measured is None or measured.value >= ceiling:
            continue
        fall = _fall(key, ceiling, measured.value)
        if is_debt(key.metric, measured.value):
            size[key] = measured.value
            changes.append(Note(key.path, NoteEvent.LOWERED, fall, measured.line))
        else:
            del size[key]
            limit = f"debt from {describe_value(key.metric, debt_from(key.metric))}"
            changes.append(Note(key.path, NoteEvent.DROPPED, SEPARATOR.join((fall, limit)), measured.line))
    changes += [Note(key.path, NoteEvent.DROPPED, gone[key]) for key in sorted(gone, key=key_order)]
    if held.clones is None:
        recorded = Note(BASELINE_PATH, NoteEvent.RECORDED, counted(len(pairs), "clone pair"))
        return CeilingFix(Baseline(size, pairs, held.job_minutes), (*changes, recorded))
    changes += [Note(pair.first.path, NoteEvent.DROPPED, f"pair {pair.label} is gone") for pair in sorted(held.clones - pairs)]
    return CeilingFix(Baseline(size, held.clones & pairs, held.job_minutes), tuple(changes))


def _followed_note(held: str, entry_path: str, source: str | None, target: str) -> Note:
    if entry_path == source:
        return Note(target, NoteEvent.MOVED, f"{held} from {source}")
    return Note(target, NoteEvent.DROPPED, SEPARATOR.join((held, LEFT_BEHIND)))


def _unexplained(path: str) -> CannotJudge:
    return CannotJudge(f"{BASELINE_PATH} holds entries under {path} its last commit does not explain", (RESTORE_STEP,))


@dataclass(frozen=True)
class EntryKind[Entry: (Key, JobKey)]:
    """How one table of the ceiling lists its entries and words a held one when a rename moves it."""

    order: Callable[[Iterable[Entry]], list[Entry]]
    held: Callable[[Entry, int], str]


SIZE_ENTRIES = EntryKind(
    lambda keys: sorted(keys, key=key_order), lambda key, value: f"{_subject(key)} {describe_value(key.metric, value)}"
)
JOB_ENTRIES = EntryKind(list, lambda key, value: f"job {key.job} {value} minutes")


def _require_placed[Entry: (Key, JobKey)](
    entries: Mapping[Entry, int], committed: Mapping[Entry, int], tracked: frozenset[str]
) -> None:
    """An entry the committed ceiling lacks under a path git no longer tracks has no lineage."""
    unplaced = sorted({key.path for key in entries if key.path not in tracked and key not in committed})
    if unplaced:
        raise _unexplained(unplaced[0])


def _entries_to_move[Entry: (Key, JobKey)](
    kind: EntryKind[Entry],
    held: Mapping[Entry, int],
    committed: Mapping[Entry, int] | None,
    carried: Mapping[Entry, int] | None,
    source: str | None,
    target: str,
) -> dict[Entry, int]:
    """The held entries under a rename's two paths that the fix rewrites, none where they already read as carried. An
    entry the committed table does not explain refuses; without a committed table nothing is compared."""
    mine = {key: value for key, value in held.items() if key.path in (source, target)}
    if committed is None or carried is None:
        return {key: value for key, value in mine.items() if key.path == source}
    if not _raised(carried, mine, kind.order(mine)):
        return {}
    if outside := _raised(committed, mine, kind.order(mine)):
        raise _unexplained(outside[0].key.path)
    return mine


def _followed_entries[Entry: (Key, JobKey)](
    kind: EntryKind[Entry],
    held: Mapping[Entry, int],
    committed: Mapping[Entry, int] | None,
    carried: Mapping[Entry, int] | None,
    renames: Renames,
    tracked: frozenset[str],
) -> tuple[dict[Entry, int], list[Note]]:
    """Per rename, held entries still in the committed form leave their source for its target, and the target's
    leftovers go; entries the carry already holds, no higher, stay. Anything else under a renamed path, and an entry the
    committed table lacks under a path git no longer tracks, has no lineage git can show: the fix refuses before it
    writes. Without a committed table the verdict compares nothing, so the source's entries move and no leftover is
    judged."""
    sources = {target: source for source, target in renames.moves.items()}
    entries, changes = dict(held), []
    for target in sorted(renames.targets):
        source = sources.get(target)
        mine = _entries_to_move(kind, held, committed, carried, source, target)
        for key in mine:
            del entries[key]
        entries.update({key._replace(path=target): value for key, value in mine.items() if key.path == source})
        moved_first = sorted(kind.order(mine), key=lambda key: key.path != source)
        changes += [_followed_note(kind.held(key, mine[key]), key.path, source, target) for key in moved_first]
    if committed is not None:
        _require_placed(entries, committed, tracked)
    return entries, changes


def follow_renames(held: Baseline, committed: Baseline, renames: Renames, tracked: frozenset[str]) -> CeilingFix:
    """The held ceiling as the verdict will carry the committed one, for the size entries, the clone pairs and the job
    entries alike; a table the held ceiling lacks stays lacking."""
    carried = carried_base(committed, renames, ())  # git renamed every source out of the index, so the fix keeps none
    size, changes = _followed_entries(SIZE_ENTRIES, held.size, committed.size, carried.size, renames, tracked)
    pairs = held.clones
    if held.clones is not None:
        pairs, pair_changes = _followed_pairs(held.clones, carried.clones, renames)
        changes += pair_changes
    limits = held.job_minutes
    if held.job_minutes is not None:
        limits, limit_changes = _followed_entries(
            JOB_ENTRIES, held.job_minutes, committed.job_minutes, carried.job_minutes, renames, tracked
        )
        changes += limit_changes
    return CeilingFix(Baseline(size, pairs, limits), tuple(changes))


def _followed_pairs(
    held: frozenset[ClonePair], carried: frozenset[ClonePair] | None, renames: Renames
) -> tuple[frozenset[ClonePair], list[Note]]:
    """Each held pair a rename touches, under its sides' new paths where the carry holds it so (any, without a
    committed table, which the verdict does not compare); one the carry lacks is a leftover and goes."""
    touched = {*renames.moves, *renames.targets}
    pairs, notes = set(), []
    for pair in sorted(held):
        if not touched & {pair.first.path, pair.second.path}:
            pairs.add(pair)
            continue
        sides = (place._replace(path=renames.moves.get(place.path, place.path)) for place in (pair.first, pair.second))
        followed = ClonePair.of(*sides)
        if carried is not None and followed not in carried:
            notes.append(Note(pair.first.path, NoteEvent.DROPPED, SEPARATOR.join((f"pair {pair.label}", LEFT_BEHIND))))
            continue
        pairs.add(followed)
        if followed != pair:
            notes.append(Note(followed.first.path, NoteEvent.MOVED, f"pair {followed.label} · was {pair.label}"))
    return frozenset(pairs), notes


def after_fix(result: RuleResult, fix: CeilingFix) -> RuleResult:
    findings = [
        replace(finding, fix=f"{finding.fix}; {RAISE_IS_BY_HAND}") if finding.event is FindingEvent.GREW else finding
        for finding in result.findings
    ]
    return RuleResult(findings, result.extent, result.measured_only, (*result.notes, *fix.changes))


def head_step(error: SizeError) -> Instruction:
    """What the head can do about its own unreadable baseline; the base is not the pull request's to rewrite."""
    return Instruction(UNKNOWN_TABLE_HINT) if isinstance(error, UnknownTable) else RESTORE_STEP


def complete(tree: TreeMeasurement) -> Measurements:
    """Holding a baseline needs every function measured, so an unparsable file cannot be judged."""
    try:
        return tree.complete()
    except UnparsableSource as error:
        raise Unparsable(str(error)) from error


def without_generated(tree: TreeMeasurement, generated: Collection[GeneratedFile]) -> TreeMeasurement:
    """The tree the size rule judges and the baseline records: the declared files' measurements and unparsable
    entries are left out."""
    declared = {file.path for file in generated}
    return TreeMeasurement(
        {key: measured for key, measured in tree.measurements.items() if key.path not in declared},
        {path: reason for path, reason in tree.unparsable.items() if path not in declared},
    )


def _stale_declarations(tree: TreeMeasurement, generated: Collection[GeneratedFile]) -> list[Finding]:
    return [
        Finding(
            Rule.SIZE,
            file.path,
            None,
            f"{GENERATED_FILE_HEADING} matches no tracked file in a measured language",
            f"delete its {GENERATED_FILE_HEADING} entry from {CONFIG_PATH}",
            event=FindingEvent.UNUSED,
        )
        for file in generated
        if Key(Metric.FILE_LINES, file.path) not in tree.measurements
    ]


def _unmeasured_notes(unparsable: Mapping[str, str]) -> tuple[Note, ...]:
    return tuple(Note(path, NoteEvent.NOT_MEASURED, "not valid Python") for path in sorted(unparsable))


def check_size(
    tree: TreeMeasurement, baseline: Baseline | None, generated: Collection[GeneratedFile], tracked: frozenset[str]
) -> RuleResult:
    """Report-only without a baseline; with one, the tree on disk must hold it. A declared file is left out of
    either, and a declaration without a measured file is a finding."""
    stale = _stale_declarations(tree, generated)
    judged = without_generated(tree, generated)
    if baseline is None:
        return RuleResult(stale, len(debt(judged.measurements)), True, _unmeasured_notes(judged.unparsable))
    measurements = complete(judged)
    gone = gone_entries(baseline.size, measurements, tracked, generated)
    return RuleResult([*stale, *compare(measurements, baseline.size, gone)], len(baseline.size), False)
