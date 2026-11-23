"""The `node-test-random` rule: a `node --test` call in a `run:` of a tracked workflow shuffles its files and tests with its
seed printed to the log. Node's runner runs in a fixed order unless `--test-randomize` (or a `--test-random-seed`) says
otherwise, and prints `Randomized test order seed: N` through its spec, tap and junit reporters.

The calls are read by the reader of `pytest-workers`, as words, never run; the options are the words before the first `--`.
A call behind a package.json script, `make` or `NODE_OPTIONS`, or a flag after the first test file, is not seen. No marker
clears a finding."""

import re
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

from quality.report import FindingEvent, RuleResult
from quality.rules import Rule
from quality.run_script import Segment, Tool, command_words, tool_arguments
from quality.workflow_document import NO_MARKER
from quality.workflow_rules import PYTEST_RULE, UNREADABLE_FACT, WHOLE_NUMBER, Problem, ScriptRule, judge_scripts

NODE = Tool(commands=frozenset({"node"}), module=None)
END_OF_OPTIONS = "--"
TEST_FLAG = "--test"
RANDOMIZE = "--test-randomize"
NO_RANDOMIZE = "--no-test-randomize"
SEED = "--test-random-seed"
REPORTER = "--test-reporter"
DESTINATION = "--test-reporter-destination"
SEED_PRINTING_REPORTERS = frozenset({"spec", "tap", "junit"})
LOG_DESTINATIONS = frozenset({"stdout", "stderr"})
NODE_WORD = re.compile(r"(?<![\w-])node(?![\w-])")
TEST_WORD = re.compile(rf"(?<![\w-]){TEST_FLAG}(?![\w-])")
FIXED_ORDER_FACT = "node --test runs its files and tests in a fixed order"
NO_SEED_FACT = "no reporter of the call prints the seed to the log"
RUN_TIME_FACT = "command is built at run time, node --test cannot be read"
SHUFFLE_FIX = (
    f"pass {RANDOMIZE} before the test files and drop {NO_RANDOMIZE} and a fixed {SEED} (node 24.16 or newer); "
    'node then prints "Randomized test order seed: N"'
)
SEED_FIX = f'add {REPORTER}=spec {DESTINATION}=stdout so the log prints "Randomized test order seed: N"'


def check_node_test_random(root: Path, tracked: Sequence[str]) -> RuleResult:
    """Every `node --test` call in a `run:` of a tracked workflow, judged; the extent is how many workflows were read."""
    judged = judge_scripts(NODE_RULE, root, list(tracked))
    findings = [replace(finding, fix=SEED_FIX) if finding.event is FindingEvent.UNSEEDED else finding for finding in judged.findings]
    return replace(judged, findings=findings)


def _holds_node_test(text: str) -> bool:
    return bool(NODE_WORD.search(text) and TEST_WORD.search(text))


def _options(command: Sequence[str]) -> list[str] | None:
    """The words before the first `--` of a `node --test` call, None for any other command."""
    arguments = tool_arguments(command, NODE)
    if arguments is None:
        return None
    options = arguments[: arguments.index(END_OF_OPTIONS)] if END_OF_OPTIONS in arguments else arguments
    return list(options) if TEST_FLAG in options else None


def _values(options: Sequence[str], name: str) -> list[str]:
    """The value of each occurrence of the option, written `name=value` or `name value`."""
    return [
        options[index + 1] if word == name and index + 1 < len(options) else word.partition("=")[2]
        for index, word in enumerate(options)
        if word == name or word.startswith(f"{name}=")
    ]


def _shuffles(options: Sequence[str]) -> bool:
    """A seed stands for a shuffle unless its value is a whole number; otherwise the last of the two switches decides, and `--test-randomize=<anything>` counts as set."""
    seeds = _values(options, SEED)
    if seeds:
        return not WHOLE_NUMBER.fullmatch(seeds[-1])
    switches = [word for word in options if word == NO_RANDOMIZE or word == RANDOMIZE or word.startswith(f"{RANDOMIZE}=")]
    return bool(switches) and switches[-1] != NO_RANDOMIZE


def _prints_seed(options: Sequence[str]) -> bool:
    """Whether the call has no reporter, or a spec, tap or junit reporter writing to stdout or stderr; the n-th destination goes with the n-th reporter and a reporter without one writes to stdout."""
    reporters = _values(options, REPORTER)
    destinations = _values(options, DESTINATION)
    return not reporters or any(
        reporter in SEED_PRINTING_REPORTERS and (destinations[index] if index < len(destinations) else "stdout") in LOG_DESTINATIONS
        for index, reporter in enumerate(reporters)
    )


def _problem(parts: Sequence[Segment], index: int) -> Problem | None:
    options = _options(command_words(parts[index]))
    if options is None:
        return None
    if not _shuffles(options):
        return FindingEvent.UNSHUFFLED, FIXED_ORDER_FACT
    return None if _prints_seed(options) else (FindingEvent.UNSEEDED, NO_SEED_FACT)


NODE_RULE = ScriptRule(
    Rule.NODE_TEST_RANDOM,
    NO_MARKER,
    replace(
        PYTEST_RULE.reading,
        judge=_problem,
        starts_call=lambda command: _options(command) is not None,
        holds_hidden_word=_holds_node_test,
        unreadable=(FindingEvent.INVALID, UNREADABLE_FACT),
        built_at_run_time=(FindingEvent.INVALID, RUN_TIME_FACT),
    ),
    SHUFFLE_FIX,
)
