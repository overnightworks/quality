"""A tracked workflow as the rules over `.github/workflows` read it: its text, its node tree, the one guarded walk over its
jobs, steps and `env:` mappings, the comment marker that clears a finding, and the `run:` steps of the workflows a pull
request triggers. An alias or a merge key that could hide what a rule reads, and a `jobs` or a job that is no mapping, make
the workflow one the rules cannot judge. The module reads text only; the rules judge what it yields."""

import re
from collections.abc import Collection, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path

import yaml

from quality import checkout
from quality.checkout import Document
from quality.report import CannotJudge, Instruction
from quality.rules import Rule
from quality.run_script import ScriptLine, logical_lines, physical_lines

MERGE_KEY = "<<"


def unreadable(name: str, reason: str) -> CannotJudge:
    return CannotJudge(f"cannot read {name}: {reason}", (Instruction(f"fix {name}"),))


def hidden(name: str) -> CannotJudge:
    return unreadable(name, "an alias or a merge key can hide what the rule reads")


def readable_workflows(root: Path, tracked: list[str]) -> Iterator[tuple[str, str]]:
    return readable(checkout.workflow_texts(root, tracked))


def readable(texts: Mapping[str, str | checkout.Unread]) -> Iterator[tuple[str, str]]:
    """Each workflow with its text; one gone from the tree is skipped and one the reader refuses cannot be judged."""
    for name, text in texts.items():
        if text is checkout.Unread.REFUSED:
            raise unreadable(name, "the file cannot be read")
        if text is not checkout.Unread.ABSENT:
            yield name, text


def composed(name: str, text: str) -> Document:
    try:
        return checkout.composed_document(text)
    except checkout.UnreadableWorkflow as error:
        raise unreadable(name, "it is no YAML quality can read") from error


def parsed(name: str, text: str) -> Document:
    """The parsed workflow; one whose top mapping or `on:` an alias or a merge key could hide the keys of cannot be judged."""
    document = composed(name, text)
    top = checkout.mapping_entries(document.root)
    triggers = top.get("on")
    hidden_at_top = hides_keys(document, document.root)
    hidden_in_triggers = triggers is not None and _hides_events(document, triggers[1])
    if hidden_at_top or hidden_in_triggers:
        raise hidden(name)
    return document


def hides_keys(document: Document, mapping: yaml.Node | None) -> bool:
    """Whether a merge key stands in the mapping, or an alias stands as one of its keys."""
    entries = checkout.mapping_entries(mapping)
    return MERGE_KEY in entries or any(document.is_alias(key) for key, _ in entries.values())


def _hides_events(document: Document, triggers: yaml.Node) -> bool:
    if document.is_alias(triggers):
        return True
    if isinstance(triggers, yaml.SequenceNode):
        return any(document.is_alias(item) for item in triggers.value)
    return hides_keys(document, triggers)


def guarded(name: str, document: Document, node: yaml.Node | None) -> yaml.Node | None:
    """The node, unless an alias or a merge key could hide the script it leads to."""
    if node is not None and (document.is_alias(node) or hides_keys(document, node)):
        raise hidden(name)
    return node


def scalar_text(node: yaml.ScalarNode) -> str:
    """A block scalar is read by its content: the loader keeps its final line breaks, which `>-` would drop."""
    return node.value.rstrip("\n") if node.style in checkout.BLOCK_STYLES else node.value


def line_of(node: yaml.Node) -> int:
    return node.start_mark.line + 1


@dataclass(frozen=True)
class Job:
    """An entry of `jobs`: its id, the node of its id and its mapping."""

    id: str
    key: yaml.ScalarNode
    mapping: yaml.MappingNode


def jobs(name: str, document: Document) -> list[Job]:
    """The entries of `jobs`, which an alias or a merge key could hide, and each of which must be a mapping."""
    top = guarded(name, document, document.root)
    entries = guarded(name, document, checkout.mapping_values(top).get("jobs"))
    if entries is None:
        return []
    if not isinstance(entries, yaml.MappingNode):
        raise unreadable(name, "jobs is not a mapping")
    found = []
    for job_id, (key, job) in checkout.mapping_entries(entries).items():
        if not isinstance(guarded(name, document, job), yaml.MappingNode):
            raise unreadable(name, f"job {job_id} is not a mapping")
        found.append(Job(job_id, key, job))
    return found


def steps(name: str, document: Document, job: Job) -> list[yaml.Node]:
    """The steps of the job, each of which an alias or a merge key could hide."""
    listed = guarded(name, document, checkout.mapping_values(job.mapping).get("steps"))
    return [guarded(name, document, step) for step in listed.value] if isinstance(listed, yaml.SequenceNode) else []


def run_steps(name: str, document: Document) -> Iterator[tuple[yaml.Node, yaml.ScalarNode]]:
    """Every step of every job that has a `run:`, with that `run:`."""
    for job in jobs(name, document):
        for step in steps(name, document, job):
            run = checkout.mapping_values(step).get("run")
            if run is not None:
                yield step, _script(name, document, run)


def _script(name: str, document: Document, run: yaml.Node) -> yaml.ScalarNode:
    if document.is_alias(run):
        raise hidden(name)
    if not isinstance(run, yaml.ScalarNode):
        raise unreadable(name, "run is not a string")
    return run


@dataclass(frozen=True)
class PullRequestRunStep:
    """A `run:` step of a workflow a pull request triggers: the workflow's name, the step's mapping and its script's logical lines."""

    workflow: str
    step: yaml.Node
    lines: tuple[ScriptLine, ...]


def pull_request_run_steps(root: Path, tracked: list[str]) -> Iterator[PullRequestRunStep]:
    """Every `run:` step of every workflow a pull request triggers, its script read line by line; the walk the rules over `run:` scripts share."""
    for name, text in readable_workflows(root, tracked):
        document = parsed(name, text)
        if checkout.pull_request_events(document):
            for step, run in run_steps(name, document):
                yield PullRequestRunStep(name, step, tuple(logical_lines(physical_lines(run))))


def environment_values(name: str, document: Document, variables: Collection[str]) -> Iterator[tuple[str, str, int]]:
    """Each of the `variables` in an `env:` mapping of the workflow, a job or a step, with its text and line; an alias or a merge key that could hide one cannot be judged."""
    owners = [guarded(name, document, document.root)]
    for job in jobs(name, document):
        owners += [job.mapping, *steps(name, document, job)]
    for owner in owners:
        environment = guarded(name, document, checkout.mapping_values(owner).get("env"))
        for variable, (_, value) in checkout.mapping_entries(environment).items():
            if variable in variables and isinstance(guarded(name, document, value), yaml.ScalarNode):
                yield variable, scalar_text(value), line_of(value)


COMMENT_ONLY_LINE = re.compile(r"[ \t]*(?:-[ \t]+)?#")
BLOCK_SCALAR_RUN_LINE = re.compile(r".*\brun:[ \t]*[|>][-+\d]*[ \t]+#")


def _is_mark_carrier(line: str) -> bool:
    """Whether a line holds nothing but a comment, or is a block scalar's `run:` line with a comment."""
    return COMMENT_ONLY_LINE.match(line) is not None or BLOCK_SCALAR_RUN_LINE.match(line) is not None


def _mark_carrier_run(file_lines: list[str], first: int) -> range:
    """The numbers of the unbroken run of mark carriers directly above the line `first`; a block scalar's `run:` line with a comment is its top."""
    top = first
    while top > 1 and _is_mark_carrier(file_lines[top - 2]):
        top -= 1
        if BLOCK_SCALAR_RUN_LINE.match(file_lines[top - 1]):
            break
    return range(top, first)


@dataclass(frozen=True)
class Marker:
    """The comment `# <rule>: <reason>` that clears a rule's findings: on a call's logical line or in the unbroken run of comment-only lines (the run ends at a block scalar's `run:` line that carries a comment) directly above it, and, where the rule allows it, in the column of a step's `-` directly above the step."""

    pattern: re.Pattern[str]
    above_step: bool

    @classmethod
    def of(cls, rule: Rule, above_step: bool) -> Marker:
        return cls(re.compile(rf"#[ \t]*{re.escape(rule.value)}:[^\n]*?[^\W\d_]"), above_step)

    def lines(self, file_lines: list[str]) -> frozenset[int]:
        """The numbers of the file lines that carry the marker."""
        return frozenset(number for number, line in enumerate(file_lines, 1) if self.pattern.search(line))

    def clears(self, file_lines: list[str], marked: frozenset[int], first: int, last: int) -> bool:
        """Whether a mark stands on a line of `first..last`, or in the run of comment lines directly above; a mark trailing another command clears that command only."""
        return any(number in marked for number in range(first, last + 1)) or any(
            number in marked for number in _mark_carrier_run(file_lines, first)
        )

    def per_line_marks(self, file_lines: list[str], marked: frozenset[int], step: yaml.Node, run: yaml.Node) -> frozenset[int]:
        """The marked lines the per-line form reads for the step's `run:`; where the rule has a step form, the comment run above a step whose `run:` stands on the step's own first line after its `-` (`- run: x`), and above a flow step, is read by the step form alone."""
        number = step.start_mark.line
        before_step = file_lines[number][: step.start_mark.column]
        if self.above_step and run.start_mark.line == number and (step.flow_style or before_step.strip()):
            return marked - set(_mark_carrier_run(file_lines, number + 1)) - {number}
        return marked

    def clears_step(self, file_lines: list[str], step: yaml.Node) -> bool:
        """Whether the marker stands directly above the step's first line, its `#` in the column of the `-` on that line; a step whose `-` stands alone on its line has no such column."""
        number = step.start_mark.line
        before_step = file_lines[number][: step.start_mark.column]
        if not self.above_step or number == 0 or before_step.strip() != "-":
            return False
        column = len(before_step) - len(before_step.lstrip())
        above = file_lines[number - 1]
        return not above[:column].strip() and self.pattern.match(above, column) is not None


# A pattern no comment matches: a rule with no marker.
NO_MARKER = Marker(re.compile(r"\b\B"), above_step=False)
