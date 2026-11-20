"""The functions of the script files the `size` rule measures: lizard reads them, one reader per suffix.

quality's suffix table names the reader and hands lizard a stand-in file name with that suffix, since lizard
reads an unknown suffix as C. A function lizard gives no name (a callback, an anonymous default export) is no
record; its branches count in the smallest named function whose lines hold it, as its lines already do.
"""

from collections import Counter
from dataclasses import dataclass

import lizard
from lizard import FunctionInfo

ANONYMOUS = "(anonymous)"
STAND_IN_OF_SUFFIX = {
    ".ts": "stand-in.ts",
    ".js": "stand-in.js",
    ".mjs": "stand-in.js",
    ".svelte": "stand-in.vue",
    ".vue": "stand-in.vue",
}


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
    """The named functions of one file in source order, equal starts in lizard's order."""
    found = lizard.analyze_file.analyze_source_code(STAND_IN_OF_SUFFIX[suffix], text).function_list
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
