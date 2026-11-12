"""The baseline file, `quality/baseline.toml`: the data model of what it holds (the size entries, the clone pairs and the
job limits) and the text that renders and reads it. The `size` rule measures and compares, and owns what a value means; this
module owns the key and the text.
"""

import json
import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import NamedTuple, Self

from quality import __version__, checkout, repository

BASELINE_PATH = "quality/baseline.toml"
FIX_COMMAND = "quality check --fix"

CLONES = "clones"
JOB_MINUTES = "job_minutes"
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
    """What `quality/baseline.toml` holds; `clones` and `job_minutes` are None when the file has no such table, so no pair
    and no job limit is held."""

    size: SizeEntries
    clones: frozenset[ClonePair] | None
    job_minutes: JobMinutes | None = None


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
    clones = job_minutes = None
    for table_name, table in document.items():
        if table_name == CLONES:
            clones = _clone_pairs(table)
        elif table_name == JOB_MINUTES:
            job_minutes = _job_minutes(table)
        else:
            size.update(_size_entries(table_name, table))
    return Baseline(size, clones, job_minutes)


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
        for job, minutes in _table(f"{JOB_MINUTES}.{path}", entry).items():
            limits[JobKey(path, job)] = _count(f"{JOB_MINUTES}.{path}.{job}", minutes)
    return limits


def _metric_named(name: str) -> Metric:
    try:
        return Metric(name)
    except ValueError:
        known = ", ".join([*(metric.value for metric in Metric), CLONES, JOB_MINUTES])
        raise UnknownTable(f"{BASELINE_PATH} has an unknown table [{name}]; quality {__version__} knows {known}") from None


def holds_clones_table(version: bytes) -> bool:
    """Whether a version has a top-level `clones` key; text no parser reads proves nothing."""
    try:
        return CLONES in repository.parse_toml(version.decode("utf-8"))
    except (UnicodeDecodeError, repository.TomlError) as error:
        raise SizeError(f"a version of {BASELINE_PATH} in git cannot be read") from error


def _table(where: str, value: object) -> dict:
    if not isinstance(value, dict):
        raise SizeError(f"{BASELINE_PATH} needs {where} as a table")
    return value


def _count(where: str, value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise SizeError(f"{BASELINE_PATH} needs {where} as a positive count, not {value!r}")
    return value
