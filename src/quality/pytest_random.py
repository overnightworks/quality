"""The `pytest-random` rule: a repository that locks pytest also locks pytest-randomly, and nothing it tracks switches the
plugin off, fixes its order or hides the seed header it prints.

Three readers judge one decision. The lock says whether the plugin is installed. The flags of pytest's `addopts`, of a
workflow's pytest call and of `PYTEST_ADDOPTS` (each source judged alone, as a list of options) say whether it shuffles and
whether the log shows its seed. `PYTEST_DISABLE_PLUGIN_AUTOLOAD` in a workflow says whether pytest loads it at all."""

import re
import shlex
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import replace
from pathlib import Path

from packaging.utils import canonicalize_name

from quality import fleet_sources, repository
from quality.pytest_options import PLUGIN_NAMES, Option, lists_only, options, pytest_arguments
from quality.report import CannotJudge, Finding, FindingEvent, RuleResult
from quality.rules import Rule
from quality.run_script import Segment, command_start, command_words
from quality.workflow_document import Marker, composed, environment_values, readable_workflows, unreadable
from quality.workflow_rules import PYTEST_RULE, Problem, ScriptRule, judge_scripts

PYTEST_PACKAGE = "pytest"
PLUGIN_PACKAGE = "pytest-randomly"
ADDOPTS_VARIABLE = "PYTEST_ADDOPTS"
AUTOLOAD_VARIABLE = "PYTEST_DISABLE_PLUGIN_AUTOLOAD"
ADDOPTS_KEY = "addopts"
EVENTS = frozenset({FindingEvent.UNSHUFFLED, FindingEvent.UNSEEDED})
MARKER = Marker.of(Rule.PYTEST_RANDOM, above_step=False)
MISSING_FIX = "add pytest-randomly to the dev dependency group, then uv lock"
UNSHUFFLED_FIX = (
    "drop -p no:randomly, --randomly-dont-reorganize, --randomly-seed=<integer or last>, --disable-plugin-autoload "
    f"and {AUTOLOAD_VARIABLE}"
)
UNSEEDED_FIX = 'drop -q, --verbosity=-1 and --no-header so the log prints "Using --randomly-seed=N"'
MARKER_FIX = ", or explain on the line above: # pytest-random: <reason>"
SWITCHES_OFF_FACT = "pytest-randomly is switched off or its order fixed by {}"
HIDES_HEADER_FACT = "pytest-randomly's seed header is hidden by {}"
# Pytest reads its configuration from `[tool.pytest.ini_options]` (ini strings) or from `[tool.pytest]` (native TOML types).
TABLES = (("tool", "pytest", "ini_options"), ("tool", "pytest"))
PYTEST_TABLE = re.compile(r"\s*\[\s*tool\s*\.\s*pytest\s*(\.\s*ini_options\s*)?\]")
ADDOPTS_ASSIGNMENT = re.compile(r"\s*addopts\s*=")
WHOLE_NUMBER = re.compile(r"[+-]?\d+", re.ASCII)
PLUGIN_OFF = "no:randomly"
PLUGIN_LOADS = frozenset({"randomly", "pytest_randomly"})
LAST_SEED = "last"
VERBOSE_NAMES = frozenset({"-v", "--verbose"})
QUIET_NAMES = frozenset({"-q", "--quiet"})
VERBOSITY_NAMES = frozenset({"--verbosity"})
HEADER_NAMES = frozenset({"--no-header"})
SEED_NAMES = frozenset({"--randomly-seed"})
REORDER_NAMES = frozenset({"--randomly-dont-reorganize"})
AUTOLOAD_NAMES = frozenset({"--disable-plugin-autoload"})


def check_pytest_random(root: Path, tracked: Sequence[str], pyprojects: Mapping[str, bytes]) -> RuleResult:
    """The locks, the `addopts` of the given pyprojects, the pytest calls and the environments of the tracked workflows; the
    extent is the locks read."""
    locks = fleet_sources.locks_by_file(root, tracked)
    findings = [finding for name in locks.values() for finding in _lock_findings(name, fleet_sources.read_lock(root, name))]
    findings += (finding for name, content in pyprojects.items() for finding in _addopts_findings(name, content))
    findings += _call_findings(root, tracked)
    findings += _environment_findings(root, tracked)
    return RuleResult(findings, len(locks), False)


def _lock_findings(name: str, lock: dict) -> list[Finding]:
    versions = {canonicalize_name(str(package.get("name", ""))): str(package.get("version", "")) for package in lock.get("package", [])}
    if PYTEST_PACKAGE not in versions or PLUGIN_PACKAGE in versions:
        return []
    fact = f"{PYTEST_PACKAGE} {versions[PYTEST_PACKAGE]} is locked without {PLUGIN_PACKAGE}"
    return [Finding(Rule.PYTEST_RANDOM, name, None, fact, MISSING_FIX, event=FindingEvent.MISSING)]


def flags_problem(arguments: Sequence[str], source: str) -> Problem | None:
    """What the options switch off or hide: the no-shuffle problem first; `source` names where they stand."""
    called = list(options(arguments))
    if _shuffle_is_off(called):
        return FindingEvent.UNSHUFFLED, SWITCHES_OFF_FACT.format(source)
    if _header_is_hidden(called):
        return FindingEvent.UNSEEDED, HIDES_HEADER_FACT.format(source)
    return None


def _named(called: Sequence[Option], names: frozenset[str]) -> list[Option]:
    return [option for option in called if option.name in names]


def _shuffle_is_off(called: Sequence[Option]) -> bool:
    """`-p` counts only as a word of its own, as pytest reads the plugin words."""
    plugins = {option.value for option in _named(called, PLUGIN_NAMES) if option.starts_word}
    seeds = [option.value for option in _named(called, SEED_NAMES)]
    autoload_off = bool(_named(called, AUTOLOAD_NAMES)) and plugins.isdisjoint(PLUGIN_LOADS)
    fixed_seed = bool(seeds) and (seeds[-1] == LAST_SEED or WHOLE_NUMBER.fullmatch(seeds[-1]) is not None)
    return PLUGIN_OFF in plugins or bool(_named(called, REORDER_NAMES)) or fixed_seed or autoload_off


def _header_is_hidden(called: Sequence[Option]) -> bool:
    """Whether pytest runs below verbosity 0 or with `--no-header`; `-v` adds one, `-q` takes one and `--verbosity` sets the level."""
    level = 0
    for option in called:
        if option.name in VERBOSE_NAMES:
            level += 1
        elif option.name in QUIET_NAMES:
            level -= 1
        elif option.name in VERBOSITY_NAMES and WHOLE_NUMBER.fullmatch(option.value):
            level = int(option.value)
    return level < 0 or bool(_named(called, HEADER_NAMES))


def _addopts_findings(name: str, content: bytes) -> list[Finding]:
    """The finding of one pyproject's `addopts`, at the key's line; no marker clears it, since it holds for every layer."""
    try:
        document = repository.parse_toml_file(name, content)
    except repository.TomlError as error:
        raise CannotJudge(str(error)) from error
    table = next((table for path in TABLES if ADDOPTS_KEY in (table := _table(path, document))), None)
    if table is None:
        return []
    problem = flags_problem(_addopts_words(name, table[ADDOPTS_KEY]), "addopts")
    if problem is None:
        return []
    event, fact = problem
    line = _addopts_line(content.decode("utf-8"))
    return [Finding(Rule.PYTEST_RANDOM, name, line, fact, _fix(event), event=event)]


def _table(path: Sequence[str], document: dict) -> dict:
    table = document
    for section in path:
        table = table.get(section) if isinstance(table, dict) else None
    return table if isinstance(table, dict) else {}


def _addopts_words(name: str, addopts: object) -> list[str]:
    if isinstance(addopts, list) and all(isinstance(word, str) for word in addopts):
        return addopts
    if not isinstance(addopts, str):
        raise CannotJudge(f"{name}: {ADDOPTS_KEY} is not a string or a list of strings")
    try:
        return shlex.split(addopts)
    except ValueError as error:
        raise CannotJudge(f"{name}: {ADDOPTS_KEY} is not shell words ({error})") from error


def _addopts_line(text: str) -> int | None:
    in_table = False
    for number, line in enumerate(text.splitlines(), 1):
        if line.lstrip().startswith("["):
            in_table = PYTEST_TABLE.match(line) is not None
        elif in_table and ADDOPTS_ASSIGNMENT.match(line):
            return number
    return None


def _fix(event: FindingEvent, marker: bool = False) -> str:
    fix = UNSHUFFLED_FIX if event is FindingEvent.UNSHUFFLED else UNSEEDED_FIX
    return fix + MARKER_FIX if marker else fix


def _call_findings(root: Path, tracked: Sequence[str]) -> list[Finding]:
    """The pytest calls and assignments of the `run:` scripts; the lines pytest-workers reports as unreadable are its own."""
    judged = judge_scripts(CALL_RULE, root, list(tracked)).findings
    return [replace(finding, fix=_fix(finding.event, marker=True)) for finding in judged if finding.event in EVENTS]


def _segment_problem(parts: Sequence[Segment], index: int) -> Problem | None:
    segment = parts[index]
    arguments = pytest_arguments(command_words(segment))
    call = None if arguments is None or lists_only(list(options(arguments))) else flags_problem(arguments, "the call")
    return next((problem for problem in (*_assignment_problems(segment.words), call) if problem is not None), None)


def _assignment_problems(words: tuple[str, ...]) -> Iterator[Problem | None]:
    """The variables of the assignment words that lead a command, or follow `export`."""
    start = command_start(words)
    assigned = [*words[:start], *(words[start + 1 :] if words[start : start + 1] == ("export",) else ())]
    for word in assigned:
        variable, _, value = word.partition("=")
        yield _variable_problem(variable, value, _shell_words(value) if variable == ADDOPTS_VARIABLE else [])


def _shell_words(value: str) -> list[str]:
    """The words pytest reads from `PYTEST_ADDOPTS`: `shlex.split`, which keeps the quotes a script's own lexing left inside the value."""
    try:
        return shlex.split(value)
    except ValueError as error:
        raise CannotJudge(f"{ADDOPTS_VARIABLE} in a run script is not shell words ({error})") from error


def _variable_problem(variable: str, value: str, words: Sequence[str]) -> Problem | None:
    """What the variable's value does: `PYTEST_ADDOPTS` holds options, any value of `PYTEST_DISABLE_PLUGIN_AUTOLOAD` switches the autoload off."""
    if variable == AUTOLOAD_VARIABLE and value:
        return FindingEvent.UNSHUFFLED, SWITCHES_OFF_FACT.format(AUTOLOAD_VARIABLE)
    return flags_problem(words, ADDOPTS_VARIABLE) if variable == ADDOPTS_VARIABLE else None


CALL_RULE = ScriptRule(
    Rule.PYTEST_RANDOM,
    MARKER,
    replace(PYTEST_RULE.reading, judge=_segment_problem),
    UNSHUFFLED_FIX,
)


def _environment_findings(root: Path, tracked: Sequence[str]) -> list[Finding]:
    """The two variables in the `env:` of a workflow, a job or a step, each at its value's line unless a marker clears it."""
    findings = []
    for name, text in readable_workflows(root, list(tracked)):
        file_lines = text.split("\n")
        marked = MARKER.lines(file_lines)
        for variable, value, line in environment_values(name, composed(name, text), (ADDOPTS_VARIABLE, AUTOLOAD_VARIABLE)):
            problem = _environment_problem(name, variable, value)
            if problem is not None and not MARKER.clears(file_lines, marked, line, line):
                event, fact = problem
                findings.append(Finding(Rule.PYTEST_RANDOM, name, line, fact, _fix(event, marker=True), event=event))
    return findings


def _environment_problem(name: str, variable: str, value: str) -> Problem | None:
    """A value built with `${{ }}` is chosen at run time and not read."""
    if "${{" in value:
        return None
    try:
        words = shlex.split(value)
    except ValueError as error:
        raise unreadable(name, f"{variable} is not shell words ({error})") from error
    return _variable_problem(variable, value, words)
