"""The `catalog-check` rule: a repository whose root `pyproject.toml` declares `overnightworks-design` with the extra
`catalogs` must call `design-catalogs --check` in a `run:` step of a pull-request workflow, directly or through a `make` goal
the root Makefile reaches. Without such a call it gets one `missing` finding, or an `invalid` finding for each line that may
hold the call and cannot be read."""

import os
import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path, PurePosixPath

import yaml
from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name

from quality import checkout, makefile, repository, workflow_rules
from quality.report import Finding, FindingEvent, RuleResult, Unparsable
from quality.rules import Rule
from quality.run_script import LineReading, Segment, Tool, command_words, line_problems, tool_arguments
from quality.workflow_rules import UNREADABLE_FACT, PullRequestRunStep

PYPROJECT = "pyproject.toml"
DESIGN_DISTRIBUTION = "overnightworks-design"
CATALOGS_EXTRA = "catalogs"
CATALOGS_TABLE = "design-catalogs"
CATALOGS_KEY = "catalogs"
DEFAULT_CATALOGS = PurePosixPath("frontend/src/lib/catalogs")
INCLUDE_GROUP = "include-group"
GENERATOR = Tool(commands=frozenset({"design-catalogs"}), module="design.catalogs.command")
CHECK_FLAG = "--check"
CONTINUE_ON_ERROR = "continue-on-error"
WORKING_DIRECTORY = "working-directory"
ROOT_DIRECTORY = "."
WORKFLOWS_PATH = ".github/workflows"
MAKEFILE = "Makefile"
MAKEFILE_ALTERNATIVES = frozenset({"GNUmakefile", "makefile"})
GENERATOR_WORD = re.compile(r"(?<![\w.-])(?:design-catalogs|design\.catalogs\.command)(?![\w.-])")
MISSING_FACT = "no workflow step runs design-catalogs --check on a pull request, directly or through make"
RUN_TIME_FACT = "command is built at run time, design-catalogs --check cannot be read"
FIX = (
    "add a step to a pull-request workflow, or a make target that make lint runs: "
    "uv run design-catalogs --check, from the repository root"
)


class Problem(Enum):
    """What a line says about the check: it calls it, it cannot be read to its end, or its command is built at run time."""

    CALL = "call"
    UNREADABLE = "unreadable"
    RUN_TIME = "run time"


PROBLEM_FACTS = {Problem.UNREADABLE: UNREADABLE_FACT, Problem.RUN_TIME: RUN_TIME_FACT}


def _group_values(table: object) -> list[object]:
    if not isinstance(table, dict):
        raise Unparsable(f"{PYPROJECT} has a dependency group table that is no table")
    return list(table.values())


def _dependency_entries(document: dict) -> list[object]:
    """The entries of each dependency list the file holds: `[project] dependencies`, each group of
    `[project.optional-dependencies]` and of `[dependency-groups]`."""
    project = document.get("project")
    lists: list[object] = []
    if isinstance(project, dict):
        lists.append(project.get("dependencies", []))
        lists.extend(_group_values(project.get("optional-dependencies", {})))
    lists.extend(_group_values(document.get("dependency-groups", {})))
    entries: list[object] = []
    for value in lists:
        if not isinstance(value, list):
            raise Unparsable(f"{PYPROJECT} has a dependency list that is no list")
        entries.extend(value)
    return entries


def _requirements(entries: Sequence[object]) -> Iterator[Requirement]:
    """Each requirement the entries hold; an `{include-group}` table is none, and any other entry must be a requirement string."""
    for entry in entries:
        if isinstance(entry, dict) and INCLUDE_GROUP in entry:
            continue
        if not isinstance(entry, str):
            raise Unparsable(
                f"{PYPROJECT} has an entry that is neither a requirement string nor an include-group table"
            )
        try:
            requirement = Requirement(entry)
        except InvalidRequirement as error:
            raise Unparsable(f"{PYPROJECT} has a requirement quality cannot read ({error})") from error
        yield requirement


def _is_catalogs_declaration(requirement: Requirement) -> bool:
    return canonicalize_name(requirement.name) == DESIGN_DISTRIBUTION and CATALOGS_EXTRA in {
        canonicalize_name(extra) for extra in requirement.extras
    }


@dataclass(frozen=True)
class Catalogs:
    """A covered repository's `[tool.design-catalogs] catalogs` value as the file spells it, None where it is not set."""

    declared: object

    def directory(self) -> PurePosixPath:
        """The catalogs directory, validated as design validates it (`design/catalogs/declaration.py`, `_path`) and
        defaulting to its `DEFAULT.catalogs`, the one copy of that default here."""
        value = self.declared
        if value is None:
            return DEFAULT_CATALOGS
        if not isinstance(value, str):
            raise Unparsable(f"{PYPROJECT} [tool.{CATALOGS_TABLE}] {CATALOGS_KEY} must be a string")
        path = PurePosixPath(os.path.normpath(value))
        if path.is_absolute() or not path.parts or path.parts[0] == "..":
            raise Unparsable(
                f"{PYPROJECT} [tool.{CATALOGS_TABLE}] {CATALOGS_KEY} must be a path inside the repository, not {value!r}"
            )
        return path


def _declared_catalogs(document: dict) -> object:
    tool = document.get("tool")
    table = tool.get(CATALOGS_TABLE) if isinstance(tool, dict) else None
    if table is None:
        return None
    if not isinstance(table, dict):
        raise Unparsable(f"{PYPROJECT} [tool.{CATALOGS_TABLE}] is no table")
    return table.get(CATALOGS_KEY)


def read_catalogs(root: Path, tracked: list[str]) -> Catalogs | None:
    """The catalogs declaration of the tracked root `pyproject.toml`, None where it does not declare the generator's
    distribution with the extra `catalogs`. The file is read whether or not it declares it, so a file that cannot be read
    refuses in every repository; only `Catalogs.directory` validates the path, so a covered repository's bad path refuses
    its own rule alone."""
    names = checkout.on_disk(root, [name for name in tracked if name == PYPROJECT])
    texts = checkout.read_existing(repository.read_text, root, names)
    if PYPROJECT not in texts:
        return None
    try:
        document = repository.parse_toml(texts[PYPROJECT])
    except repository.TomlError as error:
        raise Unparsable(f"{PYPROJECT} is not TOML that Python reads ({error})") from error
    requirements = list(_requirements(_dependency_entries(document)))
    if not any(_is_catalogs_declaration(requirement) for requirement in requirements):
        return None
    return Catalogs(_declared_catalogs(document))


def declares_catalogs(root: Path, tracked: list[str]) -> bool:
    """Whether the tracked root `pyproject.toml` declares the generator's distribution with the extra `catalogs`."""
    return read_catalogs(root, tracked) is not None


def _judge_generator_call(parts: Sequence[Segment], index: int) -> Problem | None:
    return Problem.CALL if _is_check_call(command_words(parts[index])) else None


def _is_check_call(command: Sequence[str]) -> bool:
    arguments = tool_arguments(command, GENERATOR)
    return arguments is not None and CHECK_FLAG in arguments


GENERATOR_READING = LineReading[Problem](
    judge=_judge_generator_call,
    starts_call=_is_check_call,
    holds_hidden_word=lambda text: GENERATOR_WORD.search(text) is not None,
    unreadable=Problem.UNREADABLE,
    built_at_run_time=Problem.RUN_TIME,
)


def _scalar(node: yaml.Node | None) -> str | None:
    return node.value if isinstance(node, yaml.ScalarNode) else None


def _counts(step: PullRequestRunStep) -> bool:
    """Whether the step counts: it is neither `continue-on-error: true` nor run in another directory."""
    fields = checkout.mapping_values(step.step)
    return _scalar(fields.get(CONTINUE_ON_ERROR)) != "true" and _scalar(fields.get(WORKING_DIRECTORY)) in (
        None,
        ROOT_DIRECTORY,
    )


def _read(lines: Sequence[tuple[str, int]]) -> list[tuple[int, Problem]]:
    """The problems of the lines (text and the file line it starts on) read as one script: each line's own problems, with a
    command built at run time counted only when the whole text names the generator."""
    holds_hidden_word = GENERATOR_READING.holds_hidden_word("\n".join(text for text, _ in lines))
    return [
        (number, problem) for text, number in lines for problem in line_problems(text, GENERATOR_READING, holds_hidden_word)
    ]


def _makefile_text(root: Path, tracked: list[str]) -> str | None:
    """The tracked root `Makefile`'s text, read once; None when it is absent, untracked, or shadowed by a tracked `GNUmakefile`
    or `makefile`, which make would read first."""
    if MAKEFILE_ALTERNATIVES & set(tracked):
        return None
    names = checkout.on_disk(root, [name for name in tracked if name == MAKEFILE])
    return checkout.read_existing(repository.read_text, root, names).get(MAKEFILE)


def _makefile_problems(root: Path, tracked: list[str], counted: Sequence[PullRequestRunStep]) -> list[tuple[str, int, Problem]]:
    """The problems of the recipe lines that the `make` goals of the counted steps reach in the Makefile, read as one text."""
    goals = tuple(goal for step in counted for line in step.lines for goal in makefile.goals_in_line(line.text))
    text = _makefile_text(root, tracked) if goals else None
    if text is None:
        return []
    reached = makefile.reached_recipe_lines(text, goals)
    return [(MAKEFILE, number, problem) for number, problem in _read([(line.text, line.number) for line in reached])]


def _has_call(problems: Sequence[tuple[str, int, Problem]]) -> bool:
    return any(problem is Problem.CALL for _, _, problem in problems)


def _finding(path: str, line: int | None, event: FindingEvent, fact: str) -> Finding:
    return Finding(Rule.CATALOG_CHECK, path, line, fact, FIX, event=event)


def check_catalog_call(root: Path, tracked: list[str]) -> RuleResult:
    """A covered repository with a counted pull-request workflow step that calls the check, directly or through a `make` goal,
    gets no row; else each line that cannot be read as a call is an `invalid` finding, else it gets one `missing` finding. Its
    extent is the steps read. The Makefile is read only when no counted step calls the check directly."""
    if not declares_catalogs(root, tracked):
        return RuleResult([], 0, False)
    steps = list(workflow_rules.pull_request_run_steps(root, tracked))
    counted = [step for step in steps if _counts(step)]
    problems = [
        (step.workflow, number, problem)
        for step in counted
        for number, problem in _read([(line.text, line.first) for line in step.lines])
    ]
    if not _has_call(problems):
        problems += _makefile_problems(root, tracked, counted)
    if _has_call(problems):
        return RuleResult([], len(steps), False)
    invalid = [_finding(path, number, FindingEvent.INVALID, PROBLEM_FACTS[problem]) for path, number, problem in problems]
    return RuleResult(invalid or [_finding(WORKFLOWS_PATH, None, FindingEvent.MISSING, MISSING_FACT)], len(steps), False)
