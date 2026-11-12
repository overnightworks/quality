"""Three rules over the tracked workflows, each reading a closed list of forms and judging anything outside it a finding.

`pr-concurrency`: a workflow a pull request triggers sets a top-level `concurrency` that groups a pull-request run by its
number and cancels only pull-request runs; no expression is read.

`pytest-workers`: every pytest call in a `run:` script passes `-n` with more than zero workers or carries a
`# pytest-workers: <reason>` marker; the script is read as POSIX shell words, never run.

`job-timeout`: every job of a workflow a pull request or a push to main triggers, and of the workflows those call through a
local `uses:`, declares `timeout-minutes` as a plain number of at most 15, or of the whole minutes its baseline entry holds."""

import math
import re
from collections.abc import Iterator
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path, PurePosixPath

import yaml

from quality import checkout
from quality.baseline_file import BASELINE_PATH, Baseline, JobKey
from quality.checkout import Document, PullRequestEvent
from quality.report import CannotJudge, Example, Finding, FindingEvent, Instruction, RuleResult
from quality.rules import Rule
from quality.run_script import (
    LONE_DOLLAR,
    ScriptLine,
    command_start,
    logical_lines,
    physical_lines,
    segments,
    with_env_strings_split,
)

EXPRESSION = re.compile(r"\$\{\{(.*?)\}\}", re.DOTALL)
OPERATORS = re.compile(r" ?(\|\||==|!=) ?")
FALLBACK = re.compile(r"github\.(?:sha|ref_name|head_ref|ref)(?!\w)")
MERGE_KEY = "<<"
BLOCK_STYLES = ("|", ">")
GROUP_KEY = "group"
FLAG_KEY = "cancel-in-progress"
FIX_LABEL = "add this at the top level of the workflow, beside the on key"
NO_GROUP_FACT = "no group"
ASCII_SPACES = re.compile(r"[ \t\r\n]+")


def _expression(body: str) -> str:
    return f"${{{{ {body} }}}}"


WORKFLOW_PART = _expression("github.workflow")
PULL_REQUEST_OR_RUN = _expression("github.event.pull_request.number || github.run_id")
GROUP = f"{WORKFLOW_PART}-{PULL_REQUEST_OR_RUN}"
GROUP_BY_EVENT = f"{WORKFLOW_PART}-{_expression('github.event_name')}-{PULL_REQUEST_OR_RUN}"
FLAG = _expression("github.event.pull_request.number != null")
FLAG_BY_EVENT_NAME = _expression("github.event_name == 'pull_request'")
LISTED_GROUPS = frozenset({GROUP, GROUP_BY_EVENT})


@dataclass(frozen=True)
class Entry:
    """A `concurrency` value: its text, and the line of the key it stands under."""

    text: str
    line: int


def check_pr_concurrency(root: Path, tracked: list[str]) -> RuleResult:
    """Every tracked workflow a pull request triggers, judged; the extent is how many there are."""
    findings: list[Finding] = []
    roots = 0
    for name, text in _readable_workflows(root, tracked):
        document = _document(name, text)
        events = checkout.pull_request_events(document)
        if events:
            roots += 1
            findings.extend(_concurrency_findings(name, document, events))
    return RuleResult(findings, roots, False)


def _readable_workflows(root: Path, tracked: list[str]) -> Iterator[tuple[str, str]]:
    """Each tracked workflow with its text; one gone from the tree is skipped and one the reader refuses cannot be judged."""
    for name, text in checkout.workflow_texts(root, tracked).items():
        if text is checkout.Unread.REFUSED:
            raise _unreadable(name, "the file cannot be read")
        if text is not checkout.Unread.ABSENT:
            yield name, text


def _unreadable(name: str, reason: str) -> CannotJudge:
    return CannotJudge(f"cannot read {name}: {reason}", (Instruction(f"fix {name}"),))


def _composed(name: str, text: str) -> Document:
    try:
        return checkout.composed_document(text)
    except checkout.UnreadableWorkflow as error:
        raise _unreadable(name, "it is no YAML quality can read") from error


def _document(name: str, text: str) -> Document:
    """The parsed workflow; one whose top mapping or `on:` an alias or a merge key could hide the keys of cannot be judged."""
    document = _composed(name, text)
    top = checkout.mapping_entries(document.root)
    triggers = top.get("on")
    hidden_at_top = _hides_keys(document, document.root)
    hidden_in_triggers = triggers is not None and _hides_events(document, triggers[1])
    if hidden_at_top or hidden_in_triggers:
        raise _hidden(name)
    return document


def _hidden(name: str) -> CannotJudge:
    return _unreadable(name, "an alias or a merge key can hide what the rule reads")


def _hides_keys(document: Document, mapping: yaml.Node | None) -> bool:
    """Whether a merge key stands in the mapping, or an alias stands as one of its keys."""
    entries = checkout.mapping_entries(mapping)
    return MERGE_KEY in entries or any(document.is_alias(key) for key, _ in entries.values())


def _hides_events(document: Document, triggers: yaml.Node) -> bool:
    if document.is_alias(triggers):
        return True
    if isinstance(triggers, yaml.SequenceNode):
        return any(document.is_alias(item) for item in triggers.value)
    return _hides_keys(document, triggers)


def _concurrency_findings(name: str, document: Document, events: frozenset[PullRequestEvent]) -> Iterator[Finding]:
    """The findings of a pull-request workflow's top-level `concurrency`."""
    top = checkout.mapping_entries(document.root)
    fix = _fix(events)

    def finding(event: FindingEvent, line: int | None, fact: str) -> Finding:
        return Finding(Rule.PR_CONCURRENCY, name, line, fact, fix, event=event)

    if "concurrency" not in top:
        yield finding(FindingEvent.MISSING, None, "no concurrency")
        return
    key, node = top["concurrency"]
    group, flag = _group_and_flag(name, document, key, node)
    if group is None:
        yield finding(FindingEvent.SHARED_GROUP, _line(key), NO_GROUP_FACT)
    elif _normalised(group.text) not in LISTED_GROUPS:
        yield finding(FindingEvent.SHARED_GROUP, group.line, _group_fact(group))
    if flag is None:
        yield finding(FindingEvent.MISSING, None, "no cancel-in-progress")
    elif _normalised(flag.text) not in _listed_flags(events):
        yield finding(FindingEvent.CANCELS_MAIN, flag.line, "cancel-in-progress is not a listed form")


def _group_and_flag(name: str, document: Document, key: yaml.ScalarNode, node: yaml.Node) -> tuple[Entry | None, Entry | None]:
    """The `group` and `cancel-in-progress` of a `concurrency`; a scalar is a group with no flag."""
    if document.is_alias(node) or _hides_keys(document, node):
        raise _hidden(name)
    if isinstance(node, yaml.ScalarNode):
        return Entry(_scalar_text(node), _line(key)), None
    if not isinstance(node, yaml.MappingNode):
        raise _unreadable(name, "concurrency is neither a mapping nor a string")
    entries = checkout.mapping_entries(node)
    return _entry(name, document, entries.get(GROUP_KEY)), _entry(name, document, entries.get(FLAG_KEY))


def _entry(name: str, document: Document, pair: tuple[yaml.ScalarNode, yaml.Node] | None) -> Entry | None:
    if pair is None:
        return None
    key, value = pair
    if document.is_alias(value):
        raise _hidden(name)
    if not isinstance(value, yaml.ScalarNode):
        raise _unreadable(name, f"{key.value} is not a string")
    return Entry(_scalar_text(value), _line(key))


def _scalar_text(node: yaml.ScalarNode) -> str:
    """A block scalar is read by its content: the loader keeps its final line breaks, which `>-` would drop."""
    return node.value.rstrip("\n") if node.style in BLOCK_STYLES else node.value


def _line(node: yaml.Node) -> int:
    return node.start_mark.line + 1


def _group_fact(group: Entry) -> str:
    fact = "group is not a listed form"
    fallback = FALLBACK.search(group.text)
    return fact if fallback is None else f"{fact} (falls back to {fallback[0]})"


def _listed_flags(events: frozenset[PullRequestEvent]) -> frozenset[str]:
    """The flag by event name leaves the runs of any other pull-request event uncancelled."""
    return frozenset({FLAG, FLAG_BY_EVENT_NAME}) if events == {PullRequestEvent.PULL_REQUEST} else frozenset({FLAG})


def _normalised(value: str) -> str:
    """The value with each `${{ }}` body trimmed, its whitespace runs one space and its operators set off by one space."""
    return EXPRESSION.sub(lambda expression: _expression(_normalised_body(expression[1])), value)


def _normalised_body(body: str) -> str:
    return OPERATORS.sub(r" \1 ", ASCII_SPACES.sub(" ", body)).strip(" ")


def _fix(events: frozenset[PullRequestEvent]) -> Example:
    """The canonical block, with the group that tells a combined workflow's events apart."""
    group = GROUP_BY_EVENT if len(events) > 1 else GROUP
    return Example(FIX_LABEL, ("concurrency:", f"  group: {group}", f"  cancel-in-progress: {FLAG}"))


PYTEST_FIX = "pass -n auto, or explain on the line above: # pytest-workers: <reason>"
WITHOUT_WORKERS_FACT = "pytest runs without -n"
ZERO_WORKERS_FACT = "pytest runs with -n 0"
BAD_COUNT_FACT = "-n count is not a number above zero or auto"
UNREADABLE_FACT = "command cannot be read to its end"
RUN_TIME_FACT = "command is built at run time, pytest -n cannot be read"
PYTEST_WORD = re.compile(r"(?<![\w-])pytest(?![\w-])")
MARKER = re.compile(r"#[ \t]*pytest-workers:[^\n]*?[^\W\d_]")
DYNAMIC_COMMAND = re.compile(r"\$[\w{]")
WORKERS_FLAG = re.compile(r"(?:-n|--numprocesses)(?:=(.*))?|-n(.+)", re.DOTALL)
WHOLE_NUMBER = re.compile(r"\d+", re.ASCII)
PYTEST_COMMANDS = frozenset({"pytest", "py.test"})
PYTHON_INTERPRETER = re.compile(r"python(?:3(?:\.\d+t?)?)?")
PYPY_INTERPRETER = re.compile(r"pypy(?:3(?:\.\d+)?)?")
INTERPRETERS = (PYTHON_INTERPRETER, PYPY_INTERPRETER)
INTERPRETER_VALUE_FLAGS = frozenset({"-W", "-X", "--check-hash-based-pycs"})
RUNNERS = frozenset({("uv", "run"), ("poetry", "run"), ("pipenv", "run"), ("pdm", "run"), ("hatch", "run"), ("coverage", "run")})
AUTOMATIC_COUNTS = frozenset({"auto", "logical"})

Problem = tuple[FindingEvent, str]


def check_pytest_workers(root: Path, tracked: list[str]) -> RuleResult:
    """Every pytest call in a `run:` of a tracked workflow, judged; the extent is how many workflows were read."""
    findings: list[Finding] = []
    read = 0
    for name, text in _readable_workflows(root, tracked):
        read += 1
        document = _composed(name, text)
        marked = frozenset(number for number, line in enumerate(text.split("\n"), 1) if MARKER.search(line))
        for run in _run_values(name, document):
            findings.extend(_script_findings(name, list(logical_lines(physical_lines(run))), marked))
    return RuleResult(findings, read, False)


def _guarded(name: str, document: Document, node: yaml.Node | None) -> yaml.Node | None:
    """The node, unless an alias or a merge key could hide the script it leads to."""
    if node is not None and (document.is_alias(node) or _hides_keys(document, node)):
        raise _hidden(name)
    return node


def _run_values(name: str, document: Document) -> Iterator[yaml.ScalarNode]:
    """The `run:` of every step of every job."""
    top = _guarded(name, document, document.root)
    jobs = _guarded(name, document, checkout.mapping_values(top).get("jobs"))
    for job in checkout.mapping_values(jobs).values():
        steps = checkout.mapping_values(_guarded(name, document, job)).get("steps")
        for step in _guarded(name, document, steps).value if isinstance(steps, yaml.SequenceNode) else ():
            run = checkout.mapping_values(_guarded(name, document, step)).get("run")
            if run is not None:
                yield _script(name, document, run)


def _script(name: str, document: Document, run: yaml.Node) -> yaml.ScalarNode:
    if document.is_alias(run):
        raise _hidden(name)
    if not isinstance(run, yaml.ScalarNode):
        raise _unreadable(name, "run is not a string")
    return run


def _script_findings(name: str, lines: list[ScriptLine], marked: frozenset[int]) -> Iterator[Finding]:
    holds_pytest = any(PYTEST_WORD.search(line.text) for line in lines)
    for line in lines:
        if any(number in marked for number in range(line.first - 1, line.last + 1)):
            continue
        for event, fact in _line_problems(line.text, holds_pytest):
            yield Finding(Rule.PYTEST_WORKERS, name, line.first, fact, PYTEST_FIX, event=event)


def _line_problems(text: str, script_holds_pytest: bool) -> Iterator[Problem]:
    try:
        segment_words = [with_env_strings_split(words) for words in segments(text)]
    except ValueError:
        if PYTEST_WORD.search(text):
            yield FindingEvent.INVALID, UNREADABLE_FACT
        return
    for index, words in enumerate(segment_words):
        command = words[command_start(words) :]
        if command == LONE_DOLLAR:
            if script_holds_pytest and not (index + 1 < len(segment_words) and _starts_pytest_call(segment_words[index + 1])):
                yield FindingEvent.INVALID, RUN_TIME_FACT
            continue
        problem = _segment_problem(command, script_holds_pytest)
        if problem is not None:
            yield problem


def _starts_pytest_call(words: list[str]) -> bool:
    command = words[command_start(words) :]
    return bool(command) and _pytest_arguments(command) is not None


def _segment_problem(words: list[str], script_holds_pytest: bool) -> Problem | None:
    if not words:
        return None
    arguments = _pytest_arguments(words)
    if arguments is not None:
        return _workers_problem(arguments)
    if script_holds_pytest and (words[0] == "eval" or DYNAMIC_COMMAND.match(words[0])):
        return FindingEvent.INVALID, RUN_TIME_FACT
    return None


def _pytest_arguments(words: list[str]) -> list[str] | None:
    """The words after `pytest` when `words` start a pytest call, else None."""
    command = PurePosixPath(words[0]).name
    if command in PYTEST_COMMANDS:
        return words[1:]
    if any(interpreter.fullmatch(command) for interpreter in INTERPRETERS):
        return _module_arguments(words[1:])
    if command == "uvx":
        return _runner_arguments(words[1:], strips_version=True)
    if (command, *words[1:2]) in RUNNERS:
        return _runner_arguments(words[2:], strips_version=False)
    return None


def _module_arguments(words: list[str]) -> list[str] | None:
    """The words after `-m pytest`, when only flags stand before the `-m`; a flag of `INTERPRETER_VALUE_FLAGS` takes a value."""
    index = 0
    while index < len(words):
        word = words[index]
        if word == "-m":
            return words[index + 2 :] if words[index + 1 : index + 2] == ["pytest"] else None
        if not word.startswith("-"):
            return None
        index += 2 if word in INTERPRETER_VALUE_FLAGS else 1
    return None


def _runner_arguments(words: list[str], strips_version: bool) -> list[str] | None:
    """The pytest call a runner starts, past its flags and the values of its flags; `uvx` reads its tool word without a trailing `@version`."""
    index = 0
    while index < len(words):
        if (arguments := _pytest_arguments(_words_from(words, index, strips_version))) is not None:
            return arguments
        if not words[index].startswith("-"):
            return None
        index += 1
        if (
            index < len(words)
            and not words[index].startswith("-")
            and _pytest_arguments(_words_from(words, index, strips_version)) is None
        ):
            index += 1
    return None


def _words_from(words: list[str], index: int, strips_version: bool) -> list[str]:
    """The words from `index` on, the word at `index` without a trailing `@version` when a `uvx` runner reads it as its tool word."""
    if not strips_version:
        return words[index:]
    return [words[index].partition("@")[0], *words[index + 1 :]]


def _workers_problem(arguments: list[str]) -> Problem | None:
    counts = _worker_counts(arguments)
    if not counts:
        return FindingEvent.SERIAL, WITHOUT_WORKERS_FACT
    count = counts[-1]
    if count in AUTOMATIC_COUNTS:
        return None
    if count is not None and WHOLE_NUMBER.fullmatch(count):
        return None if count.strip("0") else (FindingEvent.SERIAL, ZERO_WORKERS_FACT)
    return FindingEvent.INVALID, BAD_COUNT_FACT


def _worker_counts(arguments: list[str]) -> list[str | None]:
    """The value of each readable workers flag before the first `--`, None for one with no value; an abbreviated or glued flag is not read."""
    if "--" in arguments:
        arguments = arguments[: arguments.index("--")]
    counts: list[str | None] = []
    index = 0
    while index < len(arguments):
        flag = WORKERS_FLAG.fullmatch(arguments[index])
        index += 1
        if flag is None:
            continue
        count = flag[1] if flag[1] is not None else flag[2]
        if count is None:
            count = arguments[index] if index < len(arguments) else None
            index += 1
        counts.append(count)
    return counts


MAXIMUM_MINUTES = 15
TIMEOUT_KEY = "timeout-minutes"
MINUTES = re.compile(r"\d+(?:\.\d+)?", re.ASCII)
SET_TIMEOUT_FIX = f"set {TIMEOUT_KEY} to a number of at most {MAXIMUM_MINUTES}"
LOWER_TIMEOUT_FIX = f"lower {TIMEOUT_KEY} to {MAXIMUM_MINUTES} or less"
LOWER_ENTRY_FIX = f"delete or lower the entry in {BASELINE_PATH} by hand"

UNREADABLE_LIMIT = frozenset({FindingEvent.MISSING, FindingEvent.INVALID})

Job = tuple[str, yaml.ScalarNode, yaml.MappingNode]


def check_job_timeouts(root: Path, tracked: list[str], baseline: Baseline | None) -> RuleResult:
    """Every job of a workflow a pull request or a push to main triggers, and of the local workflows those call, judged
    against the baseline's job limits; the extent is how many jobs there are."""
    parsed = {name: _root_document(name, text) for name, text in _readable_workflows(root, tracked)}
    documents = {name: parsed.get(name) for name in tracked if checkout.is_workflow(PurePosixPath(name))}
    roots = frozenset(name for name, document in parsed.items() if _is_root(name, document))
    held = (baseline.job_minutes if baseline else None) or {}
    findings: list[Finding] = []
    readable: dict[JobKey, bool] = {}
    for name in _reached(documents, roots):
        jobs = [job for job in _jobs(name, documents[name]) if "uses" not in checkout.mapping_values(job[2])]
        for job in jobs:
            key = JobKey(name, job[0])
            job_findings = list(_limit_findings(name, documents[name], job, held.get(key)))
            readable[key] = not any(finding.event in UNREADABLE_LIMIT for finding in job_findings)
            findings.extend(job_findings)
    findings.extend(_unheld_entries(held, readable))
    return RuleResult(findings, len(readable), False)


def _unheld_entries(held: dict[JobKey, int], readable: dict[JobKey, bool]) -> Iterator[Finding]:
    """Each entry that can fall with no limit to measure: its job is gone, or its job has a valid limit and the entry
    is no debt. An entry of a job without a readable limit stands."""
    for key in sorted(held):
        if key not in readable:
            reason = "is gone"
        elif readable[key] and held[key] <= MAXIMUM_MINUTES:
            reason = "has no debt"
        else:
            continue
        fact = f"job {key.job} {reason} · ceiling {held[key]}"
        yield Finding(Rule.JOB_TIMEOUT, key.path, None, fact, LOWER_ENTRY_FIX, event=FindingEvent.CAN_FALL)


def _root_document(name: str, text: str) -> Document:
    """The parsed workflow; one whose `push` filters an alias or a merge key could hide cannot be judged."""
    document = _document(name, text)
    if _hides_push(document, checkout.mapping_values(document.root).get("on")):
        raise _hidden(name)
    return document


def _hides_push(document: Document, triggers: yaml.Node | None) -> bool:
    push = checkout.mapping_values(triggers).get("push")
    if push is None:
        return False
    if document.is_alias(push) or _hides_keys(document, push):
        return True
    filters = checkout.mapping_values(push)
    return any(_hides_items(document, filters[key]) for key in checkout.PUSH_FILTER_KEYS if key in filters)


def _hides_items(document: Document, node: yaml.Node) -> bool:
    items = node.value if isinstance(node, yaml.SequenceNode) else []
    return document.is_alias(node) or any(document.is_alias(item) for item in items)


def _is_root(name: str, document: Document) -> bool:
    try:
        pushes = checkout.pushes_to_main(document)
    except checkout.UnreadableTrigger as error:
        raise _unreadable(name, error.reason) from error
    return pushes or bool(checkout.pull_request_events(document))


def _reached(documents: dict[str, Document | None], roots: frozenset[str]) -> list[str]:
    try:
        return checkout.reached_workflows(documents, roots, lambda name: _called_files(name, documents[name]))
    except checkout.UnfollowableCall as error:
        raise _unreadable(error.caller, f"it calls {error.uses}, which {error.because}") from error


def _called_files(name: str, document: Document) -> list[str]:
    calls = (_guarded(name, document, checkout.mapping_values(job).get("uses")) for _, _, job in _jobs(name, document))
    return [call.value for call in calls if isinstance(call, yaml.ScalarNode)]


def _jobs(name: str, document: Document) -> list[Job]:
    """The entries of `jobs`, which an alias or a merge key could hide, and each of which must be a mapping."""
    top = _guarded(name, document, document.root)
    jobs = _guarded(name, document, checkout.mapping_values(top).get("jobs"))
    if jobs is None:
        return []
    if not isinstance(jobs, yaml.MappingNode):
        raise _unreadable(name, "jobs is not a mapping")
    entries = []
    for job_id, (key, job) in checkout.mapping_entries(jobs).items():
        if not isinstance(_guarded(name, document, job), yaml.MappingNode):
            raise _unreadable(name, f"job {job_id} is not a mapping")
        entries.append((job_id, key, job))
    return entries


def _limit_findings(name: str, document: Document, job: Job, entry: int | None) -> Iterator[Finding]:
    """The job's finding against its baseline entry; an entry of at most the maximum is no debt and reads as none."""
    job_id, id_key, mapping = job

    def finding(event: FindingEvent, line: int, fact: str, fix: str) -> Finding:
        return Finding(Rule.JOB_TIMEOUT, name, line, fact, fix, event=event)

    limit = checkout.mapping_entries(mapping).get(TIMEOUT_KEY)
    if limit is None:
        yield finding(FindingEvent.MISSING, _line(id_key), f"job {job_id} has no {TIMEOUT_KEY}", SET_TIMEOUT_FIX)
        return
    key, value = limit
    minutes = _minutes(_guarded(name, document, value))
    if minutes is None:
        fact = f"job {job_id} has a {TIMEOUT_KEY} that is not a number above zero"
        yield finding(FindingEvent.INVALID, _line(key), fact, SET_TIMEOUT_FIX)
        return
    ceiling = entry if entry is not None and entry > MAXIMUM_MINUTES else None
    debt = _debt_minutes(minutes)
    given = f"job {job_id} has {TIMEOUT_KEY} {value.value}"
    if ceiling is None and debt > MAXIMUM_MINUTES:
        yield finding(FindingEvent.NEW_DEBT, _line(key), f"{given} · limit {MAXIMUM_MINUTES}", LOWER_TIMEOUT_FIX)
    elif ceiling is not None and debt > ceiling:
        yield finding(FindingEvent.GREW, _line(key), f"{given} · ceiling {ceiling}", f"bring it back to {ceiling} or below")
    elif ceiling is not None and debt < ceiling:
        yield finding(FindingEvent.CAN_FALL, _line(key), f"{given} · ceiling {ceiling}", LOWER_ENTRY_FIX)


def _debt_minutes(limit: Decimal) -> int:
    """The whole minutes a limit counts as debt: rounded up, so 17.5 is held by an entry of 18."""
    return math.ceil(limit)


def _minutes(value: yaml.Node | None) -> Decimal | None:
    """The limit a plain scalar spells as a decimal number above zero, else None."""
    if not isinstance(value, yaml.ScalarNode) or value.style or not MINUTES.fullmatch(value.value):
        return None
    return Decimal(value.value) or None
