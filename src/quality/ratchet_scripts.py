"""The functions of the script files the `size` rule measures: lizard reads them, one reader per suffix.

quality's suffix table names the reader and hands lizard a stand-in file name with that suffix, since lizard
reads an unknown suffix as C. A function lizard gives no name (a callback, an anonymous default export) is no
record; its branches count in the smallest named function whose lines hold it, as its lines already do. A file
nested deeper than the interpreter recurses raises `RecursionError`, which lizard's own analyzer would print to stderr
and swallow, returning the functions read so far.
"""

from collections import Counter
from dataclasses import dataclass

import lizard
from lizard import FileAnalyzer, FileInfoBuilder, FunctionInfo

ANONYMOUS = "(anonymous)"
STAND_IN_OF_SUFFIX = {
    ".ts": "stand-in.ts",
    ".js": "stand-in.js",
    ".mjs": "stand-in.js",
    ".svelte": "stand-in.vue",
    ".vue": "stand-in.vue",
}


class _StrictFileAnalyzer(FileAnalyzer):
    """lizard's analyzer with the same processors and reader, but a `RecursionError` goes to the caller."""

    def analyze_source_code(self, filename, code):
        context = FileInfoBuilder(filename)
        reader = lizard.get_reader_for(filename)(context)
        tokens = reader.generate_tokens(code)
        for processor in self.processors:
            tokens = processor(tokens, reader)
        for _ in reader(tokens, reader):
            pass
        return context.fileinfo


_ANALYZER = _StrictFileAnalyzer(lizard.analyze_file.processors)


@dataclass(frozen=True)
class ScriptFunction:
    """A named function as lizard names it, with its first line, length and CCN (a callback's branches included)."""

    name: str
    line: int
    lines: int
    ccn: int


def reads_functions(suffix: str) -> bool:
    return suffix in STAND_IN_OF_SUFFIX


def script_functions(suffix: str, text: str) -> list[ScriptFunction]:
    """The named functions of one file in source order, equal starts in lizard's order; a file nested too deeply for
    lizard to read raises `RecursionError`."""
    found = _ANALYZER.analyze_source_code(STAND_IN_OF_SUFFIX[suffix], text).function_list
    named = [function for function in found if function.name != ANONYMOUS]
    callbacks = [function for function in found if function.name == ANONYMOUS]
    branches = Counter[int]()
    for callback in callbacks:
        if (holder := _smallest_holder(callback, named)) is not None:
            branches[holder] += callback.cyclomatic_complexity - 1
    order = sorted(range(len(named)), key=lambda index: named[index].start_line)
    return [
        ScriptFunction(named[index].name, named[index].start_line, named[index].length, named[index].cyclomatic_complexity + branches[index])
        for index in order
    ]


def _smallest_holder(callback: FunctionInfo, named: list[FunctionInfo]) -> int | None:
    """The index of the named function with the fewest lines among those holding the callback's lines; of equals, the
    one lizard lists first."""
    holders = [index for index, function in enumerate(named) if function.start_line <= callback.start_line and callback.end_line <= function.end_line]
    return min(holders, key=lambda index: named[index].length, default=None)
