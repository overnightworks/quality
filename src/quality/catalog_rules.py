"""The `catalog-check` rule: a repository whose root `pyproject.toml` declares `overnightworks-design` with the extra
`catalogs` should call `design-catalogs --check` directly in a `run:` step of a pull-request workflow. Until a step does,
it gets one note, never a finding, so the rule reports and never turns a run red."""

from collections.abc import Iterator, Sequence
from pathlib import Path

import yaml
from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name

from quality import checkout, repository, workflow_rules
from quality.report import Note, NoteEvent, RuleResult, Unparsable
from quality.run_script import LineReading, Segment, Tool, command_words, line_problems, tool_arguments
from quality.workflow_rules import PullRequestRunStep

PYPROJECT = "pyproject.toml"
DESIGN_DISTRIBUTION = "overnightworks-design"
CATALOGS_EXTRA = "catalogs"
INCLUDE_GROUP = "include-group"
GENERATOR = Tool(commands=frozenset({"design-catalogs"}), module="design.catalogs.command")
CHECK_FLAG = "--check"
CONTINUE_ON_ERROR = "continue-on-error"
WORKING_DIRECTORY = "working-directory"
ROOT_DIRECTORY = "."
NOTE_PATH = ".github/workflows"
NOT_CALLED_FACT = (
    "no workflow step runs design-catalogs --check directly on a pull request; a call through make is not read yet"
)


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


def _declares_catalogs(root: Path, tracked: list[str]) -> bool:
    """Whether the tracked root `pyproject.toml` declares the generator's distribution with the extra `catalogs`. The file is
    read whether or not it declares it, so a file that cannot be read refuses in every repository."""
    names = checkout.on_disk(root, [name for name in tracked if name == PYPROJECT])
    texts = checkout.read_existing(repository.read_text, root, names)
    if PYPROJECT not in texts:
        return False
    try:
        document = repository.parse_toml(texts[PYPROJECT])
    except repository.TomlError as error:
        raise Unparsable(f"{PYPROJECT} is not TOML that Python reads ({error})") from error
    return any(_is_catalogs_declaration(requirement) for requirement in _requirements(_dependency_entries(document)))


def _judge_generator_call(parts: Sequence[Segment], index: int) -> bool | None:
    return True if _is_check_call(command_words(parts[index])) else None


def _is_check_call(command: Sequence[str]) -> bool:
    arguments = tool_arguments(command, GENERATOR)
    return arguments is not None and CHECK_FLAG in arguments


# A line the reader refuses, or a command built at run time, reads as no call here (a named gap), so only a call is ever a problem.
GENERATOR_READING = LineReading[bool](
    judge=_judge_generator_call,
    starts_call=_is_check_call,
    holds_hidden_word=lambda text: False,
    unreadable=False,
    built_at_run_time=False,
)


def _scalar(node: yaml.Node | None) -> str | None:
    return node.value if isinstance(node, yaml.ScalarNode) else None


def _calls_generator(step: PullRequestRunStep) -> bool:
    """Whether the step calls the check with `--check`, and is neither `continue-on-error: true` nor run in another directory."""
    fields = checkout.mapping_values(step.step)
    if _scalar(fields.get(CONTINUE_ON_ERROR)) == "true" or _scalar(fields.get(WORKING_DIRECTORY)) not in (
        None,
        ROOT_DIRECTORY,
    ):
        return False
    return any(
        problem
        for line in step.lines
        for problem in line_problems(line.text, GENERATOR_READING, script_holds_hidden_word=False)
    )


def check_catalog_call(root: Path, tracked: list[str]) -> RuleResult:
    """A note when a covered repository has no pull-request workflow step that calls the check; its extent is the steps read."""
    if not _declares_catalogs(root, tracked):
        return RuleResult([], 0, False)
    steps = list(workflow_rules.pull_request_run_steps(root, tracked))
    notes = (
        ()
        if any(_calls_generator(step) for step in steps)
        else (Note(NOTE_PATH, NoteEvent.NOT_CALLED, NOT_CALLED_FACT),)
    )
    return RuleResult([], len(steps), False, notes)
