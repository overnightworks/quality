"""The `test-twin` rule: groups of tests in one file that differ only in their literals, and groups of test helpers
copied into other test files. The rule only reports, as one `open` note under the debt area.

A function is the token stream `clones.tokens` gives it. A test is a function whose name starts with `test` in a test
file, wherever it stands; every other function of a test file is a helper. Tests of one file whose streams are equal with
names kept and literals folded, from 12 tokens, are a group. Helpers of two or more files whose streams are equal with
names and literals kept, from 8 tokens, are a group. A case table stands in a decorator, which no stream holds.
"""

import ast
from collections import defaultdict
from dataclasses import dataclass
from pathlib import PurePosixPath

from quality import clones
from quality.output import counted
from quality.ratchet import Language, language_of
from quality.report import Note, NoteEvent, RuleResult

MIN_TEST_TOKENS = 12
MIN_HELPER_TOKENS = 8
NOTE_PATH = "."
TEST_NAME_PREFIX = "test"


@dataclass(frozen=True)
class TwinCounts:
    """The groups of same-file test twins and of helpers copied across test files."""

    tests: int
    helpers: int


def count_twins(sources: dict[str, bytes]) -> TwinCounts:
    """The two counts over the Python test files among `sources`; a file Python cannot parse has none."""
    test_streams: defaultdict[tuple[str, tuple[str, ...]], int] = defaultdict(int)
    helper_files: defaultdict[tuple[str, ...], set[str]] = defaultdict(set)
    for path, source in sources.items():
        if language_of(path) is not Language.PYTHON or not Language.PYTHON.is_test_file(PurePosixPath(path)):
            continue
        if (tree := clones.parsed(path, source)) is None:
            continue
        for function in (node for node in ast.walk(tree) if isinstance(node, clones.FUNCTION_NODES)):
            if function.name.startswith(TEST_NAME_PREFIX):
                if len(stream := clones.tokens(function, names_kept=True, literals_kept=False)) >= MIN_TEST_TOKENS:
                    test_streams[path, tuple(stream)] += 1
            elif len(stream := clones.tokens(function, names_kept=True, literals_kept=True)) >= MIN_HELPER_TOKENS:
                helper_files[tuple(stream)].add(path)
    return TwinCounts(
        sum(count > 1 for count in test_streams.values()), sum(len(files) > 1 for files in helper_files.values())
    )


def check_test_twins(sources: dict[str, bytes]) -> RuleResult:
    """The note with both counts, a count of zero left out and no note when both are zero; no finding, no extent."""
    found = count_twins(sources)
    facts = [
        f"{counted(count, 'group')} of {what}"
        for count, what in ((found.tests, "test twins"), (found.helpers, "copied test helpers"))
        if count
    ]
    notes = (Note(NOTE_PATH, NoteEvent.OPEN, " · ".join(facts)),) if facts else ()
    return RuleResult([], 0, False, notes)
