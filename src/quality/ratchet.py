"""The `size` rule: lines per file, lines and CCN per function of every tracked Python file, held by a baseline.

lizard measures; Python's own `ast` only names each function by its qualified symbol (`Class.method`), which
lizard leaves unqualified. Both read the same text, decoded from the file's bytes the way Python reads source.
"""

import ast
import io
import json
import tokenize
import tomllib
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass
from enum import Enum
from operator import attrgetter
from pathlib import PurePosixPath
from typing import NamedTuple

import lizard

from quality.report import Finding

SIZE = "size"
BASELINE_PATH = "quality/baseline.toml"
WRITE_COMMAND = "quality baseline write"
PYTHON_SUFFIX = ".py"
OCCURRENCE_MARK = "#"
TEST_DIRECTORIES = frozenset({"test", "tests"})
BASELINE_HEADER = f"# Measured by `{WRITE_COMMAND}`. Entries only ever go down.\n"

FILE_LINES_FROM = 800
FUNCTION_LINES_FROM = 60
CCN_ABOVE = 15


class Metric(Enum):
    CCN = "ccn"
    FILE_LINES = "file_lines"
    FUNCTION_LINES = "function_lines"

    def is_debt(self, value: int) -> bool:
        if self is Metric.CCN:
            return value > CCN_ABOVE
        return value >= (FILE_LINES_FROM if self is Metric.FILE_LINES else FUNCTION_LINES_FROM)

    def describe(self, value: int) -> str:
        return f"CCN {value}" if self is Metric.CCN else f"{value} lines"

    @property
    def threshold(self) -> str:
        return {
            Metric.CCN: f"above {CCN_ABOVE}",
            Metric.FILE_LINES: str(FILE_LINES_FROM),
            Metric.FUNCTION_LINES: str(FUNCTION_LINES_FROM),
        }[self]

    @property
    def fix(self) -> str:
        return {
            Metric.CCN: f"split its branches until its CCN is {CCN_ABOVE} or less",
            Metric.FILE_LINES: f"split the file below {FILE_LINES_FROM} lines",
            Metric.FUNCTION_LINES: f"split the function below {FUNCTION_LINES_FROM} lines",
        }[self]


class Area(Enum):
    PRODUCTION = "production"
    TESTS = "tests"


class Key(NamedTuple):
    """One baseline entry: a file's lines have no symbol, a function's metrics carry its qualified symbol."""

    metric: Metric
    path: str
    symbol: str = ""


@dataclass(frozen=True)
class Measured:
    value: int
    line: int | None


Measurements = dict[Key, Measured]
Baseline = dict[Key, int]


class SizeError(Exception):
    """The tree or the baseline cannot be measured; the message carries the reason."""


def is_python(name: str) -> bool:
    return PurePosixPath(name).suffix == PYTHON_SUFFIX


def area_of(path: str) -> Area:
    posix = PurePosixPath(path)
    is_test = (
        bool(TEST_DIRECTORIES & set(posix.parts[:-1]))
        or posix.name == "conftest.py"
        or posix.name.startswith("test_")
        or posix.stem.endswith("_test")
    )
    return Area.TESTS if is_test else Area.PRODUCTION


@dataclass(frozen=True)
class TreeMeasurement:
    """Every file's lines, and the functions of every file Python can parse; `unparsable` maps the rest to why."""

    measurements: Measurements
    unparsable: dict[str, str]

    def complete(self) -> Measurements:
        """Holding or writing a baseline needs every function measured, so an unparsable file cannot be judged."""
        if self.unparsable:
            path = min(self.unparsable)
            raise SizeError(f"{path} is not valid Python ({self.unparsable[path]})")
        return self.measurements


def measure(sources: dict[str, bytes]) -> TreeMeasurement:
    """A file's lines are counted in its bytes, so a file Python cannot decode still has them."""
    measurements: Measurements = {}
    unparsable: dict[str, str] = {}
    for path, source in sources.items():
        measurements[Key(Metric.FILE_LINES, path)] = Measured(len(source.splitlines()), None)
        try:
            text = _python_text(source)
            tree = ast.parse(text, filename=path)
        except (SyntaxError, ValueError) as error:
            unparsable[path] = str(error)
            continue
        measurements.update(_function_measurements(path, text, tree))
    return TreeMeasurement(measurements, unparsable)


def _python_text(source: bytes) -> str:
    """Python's own source decoding: a PEP 263 cookie or a UTF-8 BOM names the encoding, UTF-8 otherwise."""
    encoding, _ = tokenize.detect_encoding(io.BytesIO(source).readline)
    return source.decode(encoding)


def _function_measurements(path: str, text: str, tree: ast.Module) -> Iterator[tuple[Key, Measured]]:
    symbols = _qualified_symbols(tree)
    functions = sorted(lizard.analyze_file.analyze_source_code(path, text).function_list, key=attrgetter("start_line"))
    occurrences: Counter[str] = Counter()
    for function in functions:
        qualified = symbols.get(function.start_line, function.name)
        occurrences[qualified] += 1
        symbol = _nth(qualified, occurrences[qualified])
        yield Key(Metric.FUNCTION_LINES, path, symbol), Measured(function.length, function.start_line)
        yield Key(Metric.CCN, path, symbol), Measured(function.cyclomatic_complexity, function.start_line)


def _nth(symbol: str, occurrence: int) -> str:
    """A property's getter and setter, or a conditionally redefined function, share one qualified symbol; each
    keeps its own entry, numbered in source order, so the longer one cannot hide the shorter one growing."""
    return symbol if occurrence == 1 else f"{symbol}{OCCURRENCE_MARK}{occurrence}"


def _qualified_symbols(tree: ast.Module) -> dict[int, str]:
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


def debt(measurements: Measurements) -> Baseline:
    return {key: measured.value for key, measured in measurements.items() if key.metric.is_debt(measured.value)}


def report_counts(tree: TreeMeasurement) -> str:
    counts = {(area, metric): 0 for area in Area for metric in Metric}
    for key in debt(tree.measurements):
        counts[area_of(key.path), key.metric] += 1
    areas = "; ".join(
        f"{area.value}: {counts[area, Metric.FILE_LINES]} file(s) >= {FILE_LINES_FROM} lines, "
        f"{counts[area, Metric.FUNCTION_LINES]} function(s) >= {FUNCTION_LINES_FROM} lines, "
        f"{counts[area, Metric.CCN]} function(s) CCN > {CCN_ABOVE}"
        for area in Area
    )
    return f"{SIZE}: report only (no {BASELINE_PATH}) - {areas}{_unparsable_note(tree.unparsable)}"


def _unparsable_note(unparsable: dict[str, str]) -> str:
    if not unparsable:
        return ""
    return f"; {len(unparsable)} file(s) not valid Python, functions unmeasured: {', '.join(sorted(unparsable))}"


def compare(measurements: Measurements, baseline: Baseline) -> list[Finding]:
    """The tree on disk against the baseline; an entry whose file or function is gone is fine."""
    findings = []
    for key in sorted(measurements, key=_order):
        measured, held = measurements[key], baseline.get(key)
        if held is None and key.metric.is_debt(measured.value):
            findings.append(
                _finding(key, measured, f"the threshold is {key.metric.threshold} (new debt)", key.metric.fix)
            )
        elif held is not None and measured.value > held:
            findings.append(
                _finding(key, measured, f"the baseline holds {held} (it grew)", f"bring it back to {held} or below")
            )
        elif held is not None and measured.value < held:
            findings.append(
                _finding(key, measured, f"the baseline holds {held} (stale)", f"lower it with `{WRITE_COMMAND}`")
            )
    return findings


def _finding(key: Key, measured: Measured, verdict: str, fix: str) -> Finding:
    subject = "file" if key.metric is Metric.FILE_LINES else f"function {key.symbol}"
    return Finding(
        SIZE, key.path, measured.line, f"{subject} has {key.metric.describe(measured.value)}; {verdict}.", fix
    )


def _order(key: Key) -> tuple[str, str, str]:
    return key.metric.value, key.path, key.symbol


def render_baseline(entries: Baseline) -> str:
    sections: dict[str, list[str]] = {}
    for key in sorted(entries, key=_order):
        if key.metric is Metric.FILE_LINES:
            header, name = f"[{key.metric.value}]", key.path
        else:
            header, name = f"[{key.metric.value}.{_toml_string(key.path)}]", key.symbol
        sections.setdefault(header, []).append(f"{_toml_string(name)} = {entries[key]}")
    return BASELINE_HEADER + "".join(f"\n{header}\n" + "\n".join(lines) + "\n" for header, lines in sections.items())


def _toml_string(name: str) -> str:
    """JSON's string escapes are TOML's, except that TOML has no surrogate pairs and escapes DEL as well."""
    try:
        name.encode("utf-8")
    except UnicodeEncodeError:
        printable = name.encode("utf-8", "backslashreplace").decode("utf-8")
        raise SizeError(f"{printable} is not valid UTF-8, so {BASELINE_PATH} cannot hold it") from None
    return json.dumps(name, ensure_ascii=False).replace("\x7f", "\\u007f")


def load_baseline(text: str) -> Baseline:
    try:
        document = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise SizeError(f"{BASELINE_PATH} is not valid TOML ({error}); rewrite it with `{WRITE_COMMAND}`") from error
    baseline: Baseline = {}
    for metric_name, table in document.items():
        metric = _metric_named(metric_name)
        for path, entry in _table(metric_name, table).items():
            if metric is Metric.FILE_LINES:
                baseline[Key(metric, path)] = _count(f"{metric_name}.{path}", entry)
            else:
                for symbol, value in _table(f"{metric_name}.{path}", entry).items():
                    baseline[Key(metric, path, symbol)] = _count(f"{metric_name}.{path}.{symbol}", value)
    return baseline


def _metric_named(name: str) -> Metric:
    try:
        return Metric(name)
    except ValueError:
        known = ", ".join(metric.value for metric in Metric)
        raise SizeError(f"{BASELINE_PATH} has an unknown table [{name}]; only {known} exist") from None


def _table(where: str, value: object) -> dict:
    if not isinstance(value, dict):
        raise SizeError(f"{BASELINE_PATH} needs {where} as a table; rewrite it with `{WRITE_COMMAND}`")
    return value


def _count(where: str, value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise SizeError(f"{BASELINE_PATH} needs {where} as a positive count, not {value!r}")
    return value
