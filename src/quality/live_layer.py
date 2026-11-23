"""The `live-layer` rule: no workflow runs the live layer.

A repository whose tests hold a `tests/e2e/live/` folder checks its deployed app from the operator's machine, never from
CI. The rule reads every pytest call in a `run:` of every tracked workflow, whatever triggers it, with the reader of
`pytest-workers`, and faults a call whose last `-m` expression can select a test marked `live`. No marker clears it."""

import re
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path, PurePosixPath

from quality import layout
from quality.marker_expression import MarkerExpressionError, parse
from quality.pytest_options import running_options
from quality.report import FindingEvent, RuleResult
from quality.rules import Rule
from quality.run_script import Segment
from quality.workflow_document import Marker
from quality.workflow_rules import PYTEST_RULE, Problem, ScriptRule, judge_scripts

LIVE_MARKER = "live"
MARK_OPTION = "-m"
LIVE_FOLDER = PurePosixPath(layout.END_TO_END_DIRECTORY) / LIVE_MARKER
CAN_SELECT_FACT = f"pytest can select tests marked {LIVE_MARKER}"
UNREADABLE_EXPRESSION_FACT = "-m expression cannot be read"
RUN_TIME_FACT = "command is built at run time, pytest -m cannot be read"
FIX = f'add -m "not {LIVE_MARKER}", or "and not {LIVE_MARKER}" to its -m expression'
# A pattern no comment matches: the rule has no marker.
NO_MARKER = Marker(re.compile(r"\b\B"), above_step=False)


def has_live_layer(tracked: Sequence[str]) -> bool:
    """Whether a tracked path lies under `tests/e2e/live/` at the root or in a judged project."""
    tests = [PurePosixPath(layout.TESTS_DIRECTORY), *(project.tests_directory for project in layout.python_projects(tracked))]
    folders = [directory / LIVE_FOLDER for directory in tests]
    return any(folder in PurePosixPath(path).parents for path in tracked for folder in folders)


def check_live_layer(root: Path, tracked: list[str]) -> RuleResult:
    """Every pytest call of a tracked workflow, judged when the repository has a live layer; the extent is how many workflows were read."""
    return judge_scripts(LIVE_RULE, root, tracked) if has_live_layer(tracked) else RuleResult([], 0, False)


def _call_problem(parts: Sequence[Segment], index: int) -> Problem | None:
    called = running_options(parts[index])
    if called is None:
        return None
    expressions = [option.value for option in called if option.name == MARK_OPTION]
    # pytest applies no expression when the last -m is empty, so every test is selected.
    if not expressions or not expressions[-1]:
        return FindingEvent.RUNS_LIVE, CAN_SELECT_FACT
    try:
        selects_live = parse(expressions[-1]).can_select(LIVE_MARKER)
    except MarkerExpressionError:
        return FindingEvent.INVALID, UNREADABLE_EXPRESSION_FACT
    return (FindingEvent.RUNS_LIVE, CAN_SELECT_FACT) if selects_live else None


LIVE_RULE = ScriptRule(
    Rule.LIVE_LAYER,
    NO_MARKER,
    replace(
        PYTEST_RULE.reading,
        judge=_call_problem,
        built_at_run_time=(FindingEvent.INVALID, RUN_TIME_FACT),
    ),
    FIX,
)
