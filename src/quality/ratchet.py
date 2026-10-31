"""The `size` rule: lines per file of every tracked file in a table language, and lines and CCN per function of
every tracked Python file, held by a baseline; `ratchet` also owns that baseline file, `quality/baseline.toml`,
which holds the clone pairs next to the size entries.

lizard measures; Python's own `ast` only names each function by its qualified symbol (`Class.method`), which
lizard leaves unqualified. Both read the same text, decoded from the file's bytes the way Python reads source.
The other languages are counted by lines only: lizard misplaces their function boundaries.
"""

import ast
import io
import json
import re
import tokenize
from collections import Counter
from collections.abc import Collection, Iterator, Mapping
from dataclasses import dataclass
from enum import Enum
from operator import attrgetter
from pathlib import Path, PurePosixPath
from typing import NamedTuple, Self

import lizard

from quality import __version__, checkout, repository
from quality.config import CONFIG_PATH, GENERATED_FILE_HEADING, GeneratedFile
from quality.output import SEPARATOR
from quality.report import Command, Finding, FindingEvent, Instruction, Note, NoteEvent, Unparsable
from quality.rules import Rule

BASELINE_PATH = "quality/baseline.toml"
WRITE_COMMAND = "quality baseline write"
UNKNOWN_TABLE_HINT = "write the baseline with the quality release your gate pins"
OCCURRENCE_MARK = "#"
PARSE_ERRORS = (SyntaxError, ValueError, LookupError)
TEST_DIRECTORIES = frozenset({"test", "tests"})
RUNNER_TEST_DIRECTORIES = TEST_DIRECTORIES | {"__tests__"}
RUNNER_TEST_WORDS = frozenset({"test", "spec", "test-d", "spec-d"})
CLONES = "clones"
BARE_KEY = re.compile(r"[A-Za-z0-9_-]+")
BASELINE_HEADER = f"# Measured by `{WRITE_COMMAND}`. Entries only ever go down.\n"

FILE_LINES_FROM = 800
FUNCTION_LINES_FROM = 60
CCN_ABOVE = 15


class Metric(Enum):
    CCN = "ccn"
    FILE_LINES = "file_lines"
    FUNCTION_LINES = "function_lines"

    @property
    def debt_from(self) -> int:
        return {
            Metric.CCN: CCN_ABOVE + 1,
            Metric.FILE_LINES: FILE_LINES_FROM,
            Metric.FUNCTION_LINES: FUNCTION_LINES_FROM,
        }[self]

    def is_debt(self, value: int) -> bool:
        return value >= self.debt_from

    def describe(self, value: int) -> str:
        return f"CCN {value}" if self is Metric.CCN else f"{value} lines"

    @property
    def fix(self) -> str:
        return {
            Metric.CCN: f"split its branches until its CCN is {CCN_ABOVE} or less",
            Metric.FILE_LINES: f"split the file below {FILE_LINES_FROM} lines",
            Metric.FUNCTION_LINES: f"split the function below {FUNCTION_LINES_FROM} lines",
        }[self]


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


class Key(NamedTuple):
    """One baseline entry: a file's lines have no symbol, a function's metrics carry its qualified symbol."""

    metric: Metric
    path: str
    symbol: str = ""

    @property
    def label(self) -> str:
        """The entry as a person reads it: metric, path and, for a function, its symbol."""
        return " ".join(part for part in (self.metric.value, self.path, self.symbol) if part)


@dataclass(frozen=True)
class Measured:
    value: int
    line: int | None


Measurements = dict[Key, Measured]
SizeEntries = dict[Key, int]


class Place(NamedTuple):
    """A function as the baseline names it: its file and its qualified symbol."""

    path: str
    symbol: str


@dataclass(frozen=True, order=True)
class ClonePair:
    """Two near-identical functions in two files; `first` sorts before `second`, so a pair has one spelling."""

    first: Place
    second: Place

    @classmethod
    def of(cls, one: Place, other: Place) -> Self:
        return cls(*sorted((one, other)))

    @property
    def label(self) -> str:
        """The pair as a person reads it, one wording for the write and the verdict."""
        return f"{self.first.path} {self.first.symbol} <-> {self.second.path} {self.second.symbol}"


@dataclass(frozen=True)
class Baseline:
    """What `quality/baseline.toml` holds; `clones` is None when the file has no `[clones]` table, so no pair is held."""

    size: SizeEntries
    clones: frozenset[ClonePair] | None


class SizeError(Exception):
    """The tree or the baseline cannot be measured; the message carries the reason."""


class UnknownTable(SizeError):
    """The baseline has a table this quality does not know: a newer quality may have written it, or a hand edit."""


class UnparsableSource(SizeError):
    """A tracked file Python cannot decode or parse, so its functions cannot be measured."""


def language_of(name: str) -> Language | None:
    return LANGUAGE_OF_SUFFIX.get(PurePosixPath(name).suffix)


def read_sources(root: Path, tracked: list[str]) -> dict[str, bytes]:
    """The tracked files of a table language on disk: a tracked file absent from it (deleted, sparse, a dangling link)
    is gone."""
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
    return {key: measured.value for key, measured in measurements.items() if key.metric.is_debt(measured.value)}


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
        if key.metric.is_debt(measured.value):
            debts[area, key.metric] += 1
    return {area: AreaSummary(lines[area], {metric: debts[area, metric] for metric in Metric}) for area in Area}


def language_lines(tree: TreeMeasurement) -> dict[Language, int]:
    """Every measured language with its lines, production and tests together, largest first and by name on a tie."""
    lines: Counter[Language] = Counter()
    for key, measured in tree.measurements.items():
        if key.metric is Metric.FILE_LINES and (language := language_of(key.path)):
            lines[language] += measured.value
    return dict(sorted(lines.items(), key=lambda entry: (-entry[1], entry[0].value)))


def compare(measurements: Measurements, baseline: SizeEntries) -> list[Finding]:
    """The tree on disk against the baseline; an entry whose file or function is gone is fine."""
    findings = []
    for key in sorted(measurements, key=_order):
        measured, held = measurements[key], baseline.get(key)
        if held is None and key.metric.is_debt(measured.value):
            limit = f"debt from {key.metric.describe(key.metric.debt_from)}"
            findings.append(_finding(key, measured, FindingEvent.NEW_DEBT, limit, key.metric.fix))
        elif held is not None and measured.value > held:
            fix = f"bring it back to {key.metric.describe(held)} or below"
            findings.append(_finding(key, measured, FindingEvent.GREW, f"ceiling {held}", fix))
        elif held is not None and measured.value < held:
            findings.append(_finding(key, measured, FindingEvent.CAN_FALL, f"ceiling {held}", WRITE_COMMAND))
    return findings


@dataclass(frozen=True)
class RaisedEntry:
    """A baseline entry the head holds higher than the base; `base` is None when the head adds the entry."""

    key: Key
    base: int | None
    head: int

    def describe(self) -> str:
        """The raise as a person reads it, one wording for the write and the verdict."""
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


def carried_base(base: Baseline, head: Baseline, renames: Renames) -> Baseline:
    """The base as the head's renames read it.

    An entry under a rename's target is a leftover of a file the base no longer has and is dropped. The entries under
    the old path move to the target unless the head still holds something under the old path.
    """
    held = {key.path for key in head.size} | {
        place.path for pair in head.clones or () for place in (pair.first, pair.second)
    }
    moves = {old: new for old, new in renames.moves.items() if old not in held}

    def new_path(path: str) -> str | None:
        """Where the base's entries under `path` go in the head's reading, None when they are dropped."""
        if path in renames.targets:
            return None
        return moves.get(path, path)

    size = {key._replace(path=path): value for key, value in base.size.items() if (path := new_path(key.path))}
    if base.clones is None:
        return Baseline(size, None)
    pairs = set()
    for pair in base.clones:
        first, second = new_path(pair.first.path), new_path(pair.second.path)
        if first and second:
            pairs.add(ClonePair.of(pair.first._replace(path=first), pair.second._replace(path=second)))
    return Baseline(size, frozenset(pairs))


def raised_entries(base: SizeEntries, head: SizeEntries) -> list[RaisedEntry]:
    """One baseline against another: an entry may fall or go, never rise or appear; a renamed symbol is a new one."""
    return [
        RaisedEntry(key, base.get(key), head[key])
        for key in sorted(head, key=_order)
        if key not in base or head[key] > base[key]
    ]


def added_pairs(base: frozenset[ClonePair], head: frozenset[ClonePair]) -> list[ClonePair]:
    """The pairs only the head holds, in the order the file and the lines name them; a removed pair is no loosening."""
    return sorted(head - base)


def _finding(key: Key, measured: Measured, event: FindingEvent, limit: str, fix: str) -> Finding:
    subject = "file" if key.metric is Metric.FILE_LINES else f"function {key.symbol}"
    fact = SEPARATOR.join((f"{subject} has {key.metric.describe(measured.value)}", limit))
    return Finding(Rule.SIZE, key.path, measured.line, fact, fix, event=event)


def _order(key: Key) -> tuple[str, str, str]:
    return key.metric.value, key.path, key.symbol


def render_baseline(baseline: Baseline) -> str:
    entries = baseline.size
    sections: dict[str, list[str]] = {}
    for key in sorted(entries, key=_order):
        if key.metric is Metric.FILE_LINES:
            header, name = f"[{key.metric.value}]", key.path
        else:
            header, name = f"[{key.metric.value}.{_toml_string(key.path)}]", key.symbol
        sections.setdefault(header, []).append(f"{_toml_string(name)} = {entries[key]}")
    if baseline.clones is not None:
        sections.update(_clone_sections(baseline.clones))
    return BASELINE_HEADER + "".join(f"\n{header}\n" + "".join(f"{line}\n" for line in lines) for header, lines in sections.items())


def _clone_sections(pairs: frozenset[ClonePair]) -> dict[str, list[str]]:
    """Each pair stands once, under its first side: a table per file, a key per function, a list of the other sides."""
    if not pairs:
        return {f"[{CLONES}]": []}
    others: dict[Place, list[Place]] = {}
    for pair in sorted(pairs):
        others.setdefault(pair.first, []).append(pair.second)
    sections: dict[str, list[str]] = {}
    for first, seconds in others.items():
        listed = ", ".join(f"[{_toml_string(second.path)}, {_toml_string(second.symbol)}]" for second in seconds)
        sections.setdefault(f"[{CLONES}.{_toml_string(first.path)}]", []).append(f"{_toml_key(first.symbol)} = [{listed}]")
    return sections


def _toml_key(name: str) -> str:
    """A key as TOML allows it bare, else quoted."""
    return name if BARE_KEY.fullmatch(name) else _toml_string(name)


def _toml_string(name: str) -> str:
    """JSON's string escapes are TOML's, except that TOML has no surrogate pairs and escapes DEL as well."""
    try:
        name.encode("utf-8")
    except UnicodeEncodeError:
        raise SizeError(f"{name} is not valid UTF-8, so {BASELINE_PATH} cannot hold it") from None
    return json.dumps(name, ensure_ascii=False).replace("\x7f", "\\u007f")


def load_baseline(text: str) -> Baseline:
    try:
        document = repository.parse_toml(text)
    except repository.TomlError as error:
        raise SizeError(f"{BASELINE_PATH} is not valid TOML ({error})") from error
    size: SizeEntries = {}
    clones = None
    for table_name, table in document.items():
        if table_name == CLONES:
            clones = _clone_pairs(table)
            continue
        metric = _metric_named(table_name)
        for path, entry in _table(table_name, table).items():
            if metric is Metric.FILE_LINES:
                size[Key(metric, path)] = _count(f"{table_name}.{path}", entry)
            else:
                for symbol, value in _table(f"{table_name}.{path}", entry).items():
                    size[Key(metric, path, symbol)] = _count(f"{table_name}.{path}.{symbol}", value)
    return Baseline(size, clones)


def read_baseline(root: Path) -> Baseline | None:
    """The repository's baseline on disk, None when it has none.

    A file that cannot be read cannot be judged; a malformed one raises SizeError.
    """
    content = checkout.judged(repository.read_bytes, root, BASELINE_PATH)
    if content is None:
        return None
    try:
        return load_baseline(content.decode("utf-8"))
    except UnicodeDecodeError as error:
        raise SizeError(f"{BASELINE_PATH} is not valid UTF-8 ({error})") from error


def _clone_pairs(table: object) -> frozenset[ClonePair]:
    """Every pair rebuilt through `ClonePair.of`, so a reversed or repeated entry is its one identity."""
    pairs = set()
    for path, entry in _table(CLONES, table).items():
        for symbol, others in _table(f"{CLONES}.{path}", entry).items():
            where = f"{CLONES}.{path}.{symbol}"
            if not isinstance(others, list):
                raise SizeError(f"{BASELINE_PATH} needs {where} as a list of [path, symbol] pairs")
            for other in others:
                if not (isinstance(other, list) and len(other) == 2 and all(isinstance(part, str) for part in other)):
                    raise SizeError(f"{BASELINE_PATH} needs {where} as a list of [path, symbol] pairs of two strings, not {other!r}")
                pairs.add(ClonePair.of(Place(path, symbol), Place(*other)))
    return frozenset(pairs)


def _metric_named(name: str) -> Metric:
    try:
        return Metric(name)
    except ValueError:
        known = ", ".join([*(metric.value for metric in Metric), CLONES])
        raise UnknownTable(f"{BASELINE_PATH} has an unknown table [{name}]; quality {__version__} knows {known}") from None


def head_step(error: SizeError) -> Command | Instruction:
    """What the head can do about its own unreadable baseline; the base is not the pull request's to rewrite."""
    return Instruction(UNKNOWN_TABLE_HINT) if isinstance(error, UnknownTable) else Command(WRITE_COMMAND)


def head_advice(error: SizeError) -> str:
    step = head_step(error)
    return f"rewrite it with `{step.text}`" if isinstance(step, Command) else step.text


def _table(where: str, value: object) -> dict:
    if not isinstance(value, dict):
        raise SizeError(f"{BASELINE_PATH} needs {where} as a table")
    return value


def _count(where: str, value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise SizeError(f"{BASELINE_PATH} needs {where} as a positive count, not {value!r}")
    return value


@dataclass(frozen=True)
class RuleResult:
    """A baseline rule's findings and `extent`, how many entries or pairs the debt row names for it; `measured_only`
    when it counted without enforcing, and the `notes` of what it could not measure."""

    findings: list[Finding]
    extent: int
    measured_only: bool
    notes: tuple[Note, ...] = ()


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


def check_size(tree: TreeMeasurement, baseline: Baseline | None, generated: Collection[GeneratedFile]) -> RuleResult:
    """Report-only without a baseline; with one, the tree on disk must hold it. A declared file is left out of
    either, and a declaration without a measured file is a finding."""
    stale = _stale_declarations(tree, generated)
    judged = without_generated(tree, generated)
    if baseline is None:
        return RuleResult(stale, len(debt(judged.measurements)), True, _unmeasured_notes(judged.unparsable))
    return RuleResult([*stale, *compare(complete(judged), baseline.size)], len(baseline.size), False)
