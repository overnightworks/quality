"""The baseline file, `quality/baseline.toml`: the data model of what it holds (the size entries, the clone pairs, the
job limits, the uncited spec IDs, the adapter import exceptions) and the text that renders and reads it. The `size` rule
measures and compares, and owns what a value means; this module owns the key and the text, and with the key of
`[adapter_imports]` the carrying of its counts across test module moves, which the verdict and the fix share.
"""

import json
import re
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import NamedTuple, Self

from quality import __version__, checkout, repository
from quality.report import Note, NoteEvent

BASELINE_PATH = "quality/baseline.toml"
FIX_COMMAND = "quality check --fix"

CLONES = "clones"
JOB_MINUTES = "job_minutes"
SPEC_CITATIONS = "spec_citations"
UNCITED = "uncited"
ADAPTER_IMPORTS = "adapter_imports"
LITERAL_TEXT = "literal_text"
JOB_LIMIT = "job limit"
UNCITED_SPEC_ID = "uncited spec ID"
ADAPTER_EXCEPTION = "adapter import exception"
BARE_KEY = re.compile(r"[A-Za-z0-9_-]+")
BASELINE_HEADER = f"# Measured by `{FIX_COMMAND}`. Entries only ever go down.\n"


class Metric(Enum):
    CCN = "ccn"
    FILE_LINES = "file_lines"
    FUNCTION_LINES = "function_lines"


class Key(NamedTuple):
    """One baseline entry: a file's lines have no symbol, a function's metrics carry its qualified symbol."""

    metric: Metric
    path: str
    symbol: str = ""

    @property
    def label(self) -> str:
        """The entry as a person reads it: metric, path and, for a function, its symbol."""
        return " ".join(part for part in (self.metric.value, self.path, self.symbol) if part)


SizeEntries = dict[Key, int]


class Place(NamedTuple):
    """A function as the baseline names it: its file and its qualified symbol."""

    path: str
    symbol: str


class JobKey(NamedTuple):
    """One job limit entry: the workflow file that defines the job and the job's id as written in its YAML."""

    path: str
    job: str

    @property
    def label(self) -> str:
        """The entry as a person reads it: its file and its job."""
        return f"{self.path} {self.job}"


JobMinutes = dict[JobKey, int]


class DirectoryKey(NamedTuple):
    """One adapter import entry: the test directory that holds the marked modules."""

    path: str

    @property
    def label(self) -> str:
        return self.path


AdapterCounts = dict[str, int]


def adapter_directory(test: str) -> str:
    """The key of `[adapter_imports]` that a test module counts toward: its direct parent directory."""
    return str(PurePosixPath(test).parent)


def moved_adapter_counts(
    committed: AdapterCounts, moves: Mapping[str, str]
) -> tuple[AdapterCounts, list[tuple[str, str]]]:
    """`committed` after the moves it carries, and each move as (old directory, new directory): a module in `moves`
    (old path to new path) takes one count from its old directory to its new one, while the old directory holds one;
    modules that leave one directory together share its count. The result depends on the committed table and the moves
    alone, so the verdict and the fix, run any number of times in any order, carry the same counts."""
    counts, planned = dict(committed), []
    for old, new in sorted(moves.items()):
        source, target = adapter_directory(old), adapter_directory(new)
        if source != target and counts.get(source, 0) > 0:
            counts[source] -= 1
            counts[target] = counts.get(target, 0) + 1
            planned.append((source, target))
    return {directory: count for directory, count in counts.items() if count}, planned


def lowered_adapter_note(directory: str, before: int, now: int) -> Note:
    """The note of a directory whose count falls from `before` to `now`: dropped when none is left."""
    event = NoteEvent.LOWERED if now else NoteEvent.DROPPED
    return Note(directory, event, f"{before} → {now} {ADAPTER_EXCEPTION}s")


def fixed_adapter_counts(
    held: AdapterCounts, committed: AdapterCounts | None, moves: Mapping[str, str], measured: Mapping[str, int]
) -> tuple[AdapterCounts, list[Note]]:
    """The counts `--fix` writes for the held table, and a note for each change from the held table to them. The held
    counts follow the committed ones as the verdict carries them, then fall to the measured counts and never rise
    beyond a carried move. A directory a move brings a count to takes the carried count, even where a hand edit lowered
    the held one: the carry is what the verdict allows, and the lowering to the measured counts caps it. Any other
    directory keeps no more than the carry and no more than it holds, and one the carry lacks goes. Size and job
    entries move the held value so a hand-lowered entry stays lowered; a brought directory differs on purpose, since
    capping it at the held value would make the written counts depend on the order of the runs. Without a committed
    table nothing is carried. The committed table, the moves and the measured counts alone give the result, so a fix run
    again, or after one more staged move, ends at the same counts and tells no change a file does not have. A move is
    told when its target gained a count, once per count, with a source that lost one first; a count that fell and no
    told move explains is told as lowered or dropped."""
    carried, planned = (held, []) if committed is None else moved_adapter_counts(committed, moves)
    brought = {target for _, target in planned}
    followed = {
        directory: carried[directory] if directory in brought else min(held.get(directory, 0), carried[directory])
        for directory in carried
    }
    counts = {directory: now for directory, count in followed.items() if (now := min(count, measured.get(directory, 0)))}
    return counts, _adapter_notes(held, counts, planned)


def _adapter_notes(held: AdapterCounts, counts: AdapterCounts, planned: list[tuple[str, str]]) -> list[Note]:
    """The notes of the change from `held` to `counts`: each planned move whose target gained a count, a source that lost
    one first, then a count the target gained beyond its moves, then each count that fell and no move explains."""
    change = Counter({directory: counts.get(directory, 0) - held.get(directory, 0) for directory in {*counts, *held}})
    notes, untold = [], list(planned)
    for source_lost in (True, False):
        for move in [move for move in untold if (change[move[0]] < 0) == source_lost]:
            source, target = move
            if change[target] > 0:
                untold.remove(move)
                change[target] -= 1
                if change[source] < 0:
                    change[source] += 1
                notes.append(Note(target, NoteEvent.MOVED, f"{ADAPTER_EXCEPTION} from {source}"))
    origin = {target: source for source, target in planned}
    notes += [
        Note(directory, NoteEvent.MOVED, f"{ADAPTER_EXCEPTION} from {origin[directory]}")
        for directory in sorted(origin)
        for _ in range(max(change[directory], 0))
    ]
    notes += [
        lowered_adapter_note(directory, held[directory], counts.get(directory, 0))
        for directory in sorted(held)
        if change[directory] < 0
    ]
    return notes


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
        """The pair as a person reads it, one wording for the fix and the verdict."""
        return f"{self.first.path} {self.first.symbol} <-> {self.second.path} {self.second.symbol}"


@dataclass(frozen=True)
class Baseline:
    """What `quality/baseline.toml` holds; `clones`, `job_minutes`, `spec_citations` and `adapter_imports` are None when the
    file has no such table, so no pair, no job limit, no uncited spec ID and no adapter import exception is held. A
    `[literal_text]` table is read and ignored."""

    size: SizeEntries
    clones: frozenset[ClonePair] | None
    job_minutes: JobMinutes | None = None
    spec_citations: frozenset[str] | None = None
    adapter_imports: AdapterCounts | None = None


class SizeError(Exception):
    """The tree or the baseline cannot be measured; the message carries the reason."""


class UnknownTable(SizeError):
    """The baseline has a table this quality does not know: a newer quality may have written it, or a hand edit."""


def key_order(key: Key) -> tuple[str, str, str]:
    return key.metric.value, key.path, key.symbol


def render_baseline(baseline: Baseline) -> str:
    entries = baseline.size
    sections: dict[str, list[str]] = {}
    for key in sorted(entries, key=key_order):
        if key.metric is Metric.FILE_LINES:
            header, name = f"[{key.metric.value}]", key.path
        else:
            header, name = f"[{key.metric.value}.{_toml_string(key.path)}]", key.symbol
        sections.setdefault(header, []).append(f"{_toml_string(name)} = {entries[key]}")
    if baseline.clones is not None:
        sections.update(_clone_sections(baseline.clones))
    if baseline.job_minutes is not None:
        sections.update(_job_sections(baseline.job_minutes))
    if baseline.spec_citations is not None:
        sections.update(_spec_sections(baseline.spec_citations))
    if baseline.adapter_imports is not None:
        sections.update(_adapter_sections(baseline.adapter_imports))
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


def _job_sections(limits: JobMinutes) -> dict[str, list[str]]:
    """A table per workflow file, a key per job, both in plain string order."""
    if not limits:
        return {f"[{JOB_MINUTES}]": []}
    sections: dict[str, list[str]] = {}
    for key in sorted(limits):
        sections.setdefault(f"[{JOB_MINUTES}.{_toml_string(key.path)}]", []).append(f"{_toml_key(key.job)} = {limits[key]}")
    return sections


def _spec_sections(uncited: frozenset[str]) -> dict[str, list[str]]:
    """The IDs sorted, one per line, in a key of the one table; an empty table stands as its header alone."""
    if not uncited:
        return {f"[{SPEC_CITATIONS}]": []}
    listed = "".join(f"    {_toml_string(identifier)},\n" for identifier in sorted(uncited))
    return {f"[{SPEC_CITATIONS}]": [f"{UNCITED} = [\n{listed}]"]}


def _adapter_sections(counts: AdapterCounts) -> dict[str, list[str]]:
    """A key per test directory, in plain string order; an empty table stands as its header alone."""
    return {f"[{ADAPTER_IMPORTS}]": [f"{_toml_key(directory)} = {counts[directory]}" for directory in sorted(counts)]}


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
    clones = job_minutes = spec_citations = adapter_imports = None
    for table_name, table in document.items():
        if table_name == CLONES:
            clones = _clone_pairs(table)
        elif table_name == JOB_MINUTES:
            job_minutes = _job_minutes(table)
        elif table_name == SPEC_CITATIONS:
            spec_citations = _uncited_ids(table)
        elif table_name == ADAPTER_IMPORTS:
            adapter_imports = _adapter_counts(table)
        elif table_name == LITERAL_TEXT:
            continue  # read and ignored; `--fix` rewrites the file without it
        else:
            size.update(_size_entries(table_name, table))
    return Baseline(size, clones, job_minutes, spec_citations, adapter_imports)


def _size_entries(table_name: str, table: object) -> SizeEntries:
    metric = _metric_named(table_name)
    entries: SizeEntries = {}
    for path, entry in _table(table_name, table).items():
        if metric is Metric.FILE_LINES:
            entries[Key(metric, path)] = _count(f"{table_name}.{path}", entry)
        else:
            for symbol, value in _table(f"{table_name}.{path}", entry).items():
                entries[Key(metric, path, symbol)] = _count(f"{table_name}.{path}.{symbol}", value)
    return entries


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


def _job_minutes(table: object) -> JobMinutes:
    limits = {}
    for path, entry in _table(JOB_MINUTES, table).items():
        where = f"{JOB_MINUTES}.{_toml_string(path)}"
        for job, minutes in _table(where, entry).items():
            limits[JobKey(path, job)] = _count(f"{where}.{_toml_key(job)}", minutes)
    return limits


def _uncited_ids(table: object) -> frozenset[str]:
    entries = _table(SPEC_CITATIONS, table)
    if entries.keys() - {UNCITED}:
        raise SizeError(f"{BASELINE_PATH} needs {SPEC_CITATIONS} to hold only {UNCITED}")
    listed = entries.get(UNCITED, [])
    if not (isinstance(listed, list) and all(isinstance(identifier, str) for identifier in listed)):
        raise SizeError(f"{BASELINE_PATH} needs {SPEC_CITATIONS}.{UNCITED} as a list of strings")
    return frozenset(listed)


def _adapter_counts(table: object) -> AdapterCounts:
    return {directory: _count(f"{ADAPTER_IMPORTS}.{directory}", count) for directory, count in _table(ADAPTER_IMPORTS, table).items()}


def _metric_named(name: str) -> Metric:
    try:
        return Metric(name)
    except ValueError:
        known = ", ".join([*(metric.value for metric in Metric), CLONES, JOB_MINUTES, SPEC_CITATIONS, ADAPTER_IMPORTS])
        raise UnknownTable(f"{BASELINE_PATH} has an unknown table [{name}]; quality {__version__} knows {known}") from None


def holds_table(version: bytes, name: str) -> bool:
    """Whether a version has a top-level key `name`. A version no parser reads may hold it where its bytes name it, and
    then it raises; where they never name it, it holds nothing."""
    try:
        return name in repository.parse_toml(version.decode("utf-8"))
    except (UnicodeDecodeError, repository.TomlError) as error:
        # A version a parser read is judged by it; one it cannot read still blocks a recording where it could have held the table.
        if name.encode() in version:
            raise SizeError(f"a version of {BASELINE_PATH} in git cannot be read and names {name}") from error
        return False


def _table(where: str, value: object) -> dict:
    if not isinstance(value, dict):
        raise SizeError(f"{BASELINE_PATH} needs {where} as a table")
    return value


def _count(where: str, value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise SizeError(f"{BASELINE_PATH} needs {where} as a positive count, not {value!r}")
    return value
