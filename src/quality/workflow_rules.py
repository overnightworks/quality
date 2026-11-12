"""Four rules over the tracked workflows, each reading a closed list of forms and judging anything outside it a finding.

`pr-concurrency`: a workflow a pull request triggers sets a top-level `concurrency` that groups a pull-request run by its
number and cancels only pull-request runs; no expression is read.

`pytest-workers`: every pytest call in a `run:` script passes `-n` with more than zero workers or carries a
`# pytest-workers: <reason>` marker; the script is read as POSIX shell words, never run.

`runtime-install`: no `run:` script installs through an operating-system package manager or pipes a download into a
shell, unless the step carries a `# runtime-install: <reason>` marker on the line above; the script is read by the same
reader as `pytest-workers`.

`job-timeout`: every job of a workflow a pull request or a push to main triggers, and of the workflows those call through a
local `uses:`, declares `timeout-minutes` as a plain number of at most 15, or of the whole minutes its baseline entry holds."""

import math
import re
from collections.abc import Iterator, Sequence
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
    C_STRING_OPENER,
    LONE_DOLLAR,
    PIPES,
    SHELLS,
    LineReading,
    ScriptLine,
    Segment,
    Tool,
    command_words,
    line_problems,
    logical_lines,
    physical_lines,
    tool_arguments,
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
WORKERS_FLAG = re.compile(r"(?:-n|--numprocesses)(?:=(.*))?|-n(.+)", re.DOTALL)
WHOLE_NUMBER = re.compile(r"\d+", re.ASCII)
PYTEST = Tool(commands=frozenset({"pytest", "py.test"}), module="pytest")
AUTOMATIC_COUNTS = frozenset({"auto", "logical"})

Problem = tuple[FindingEvent, str]


@dataclass(frozen=True)
class Marker:
    """The comment `# <rule>: <reason>` that clears a rule's findings: on a call's logical line or directly above its first line, and, where the rule allows it, in the column of a step's `-` directly above the step."""

    pattern: re.Pattern[str]
    above_step: bool

    @classmethod
    def of(cls, rule: Rule, above_step: bool) -> Marker:
        return cls(re.compile(rf"#[ \t]*{re.escape(rule.value)}:[^\n]*?[^\W\d_]"), above_step)

    def lines(self, file_lines: list[str]) -> frozenset[int]:
        """The numbers of the file lines that carry the marker."""
        return frozenset(number for number, line in enumerate(file_lines, 1) if self.pattern.search(line))

    def per_line_marks(self, file_lines: list[str], marked: frozenset[int], step: yaml.Node, run: yaml.Node) -> frozenset[int]:
        """The marked lines the per-line form reads for the step's `run:`; where the rule has a step form, the line above a step whose `run:` stands on the step's own first line after its `-` (`- run: x`), and the line above a flow step, is read by the step form alone."""
        number = step.start_mark.line
        before_step = file_lines[number][: step.start_mark.column]
        if self.above_step and run.start_mark.line == number and (step.flow_style or before_step.strip()):
            return marked - {number}
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


@dataclass(frozen=True)
class ScriptRule:
    """What one rule over the `run:` scripts brings: its marker, its reading of a line and its fix."""

    rule: Rule
    marker: Marker
    reading: LineReading[Problem]
    fix: str


def _pytest_arguments(command: Sequence[str]) -> tuple[str, ...] | None:
    return tool_arguments(command, PYTEST)


def _pytest_problem(parts: Sequence[Segment], index: int) -> Problem | None:
    arguments = _pytest_arguments(command_words(parts[index]))
    return None if arguments is None else _workers_problem(arguments)


PYTEST_RULE = ScriptRule(
    Rule.PYTEST_WORKERS,
    Marker.of(Rule.PYTEST_WORKERS, above_step=False),
    LineReading(
        judge=_pytest_problem,
        starts_call=lambda command: _pytest_arguments(command) is not None,
        holds_hidden_word=lambda text: bool(PYTEST_WORD.search(text)),
        unreadable=(FindingEvent.INVALID, UNREADABLE_FACT),
        built_at_run_time=(FindingEvent.INVALID, RUN_TIME_FACT),
    ),
    PYTEST_FIX,
)


def check_pytest_workers(root: Path, tracked: list[str]) -> RuleResult:
    """Every pytest call in a `run:` of a tracked workflow, judged; the extent is how many workflows were read."""
    return _judge_scripts(PYTEST_RULE, root, tracked)


def _judge_scripts(script_rule: ScriptRule, root: Path, tracked: list[str]) -> RuleResult:
    """Every `run:` step of every tracked workflow, read line by line under the rule; the extent is how many workflows were read."""
    findings: list[Finding] = []
    read = 0
    for name, text in _readable_workflows(root, tracked):
        read += 1
        file_lines = text.split("\n")
        marked = script_rule.marker.lines(file_lines)
        for step, run in _run_steps(name, _composed(name, text)):
            if not script_rule.marker.clears_step(file_lines, step):
                lines = list(logical_lines(physical_lines(run)))
                findings.extend(_script_findings(script_rule, name, lines, script_rule.marker.per_line_marks(file_lines, marked, step, run)))
    return RuleResult(findings, read, False)


def _guarded(name: str, document: Document, node: yaml.Node | None) -> yaml.Node | None:
    """The node, unless an alias or a merge key could hide the script it leads to."""
    if node is not None and (document.is_alias(node) or _hides_keys(document, node)):
        raise _hidden(name)
    return node


def _run_steps(name: str, document: Document) -> Iterator[tuple[yaml.Node, yaml.ScalarNode]]:
    """Every step of every job that has a `run:`, with that `run:`."""
    top = _guarded(name, document, document.root)
    jobs = _guarded(name, document, checkout.mapping_values(top).get("jobs"))
    for job in checkout.mapping_values(jobs).values():
        steps = checkout.mapping_values(_guarded(name, document, job)).get("steps")
        for step in _guarded(name, document, steps).value if isinstance(steps, yaml.SequenceNode) else ():
            run = checkout.mapping_values(_guarded(name, document, step)).get("run")
            if run is not None:
                yield step, _script(name, document, run)


@dataclass(frozen=True)
class PullRequestRunStep:
    """A `run:` step of a workflow a pull request triggers: the workflow's name, the step's mapping and its script's logical lines."""

    workflow: str
    step: yaml.Node
    lines: tuple[ScriptLine, ...]


def pull_request_run_steps(root: Path, tracked: list[str]) -> Iterator[PullRequestRunStep]:
    """Every `run:` step of every workflow a pull request triggers, its script read line by line; the walk the rules over `run:` scripts share."""
    for name, text in _readable_workflows(root, tracked):
        document = _document(name, text)
        if checkout.pull_request_events(document):
            for step, run in _run_steps(name, document):
                yield PullRequestRunStep(name, step, tuple(logical_lines(physical_lines(run))))


def _script(name: str, document: Document, run: yaml.Node) -> yaml.ScalarNode:
    if document.is_alias(run):
        raise _hidden(name)
    if not isinstance(run, yaml.ScalarNode):
        raise _unreadable(name, "run is not a string")
    return run


def _script_findings(script_rule: ScriptRule, name: str, lines: list[ScriptLine], marked: frozenset[int]) -> Iterator[Finding]:
    reading = script_rule.reading
    holds_hidden_word = reading.holds_hidden_word("\n".join(line.text for line in lines))
    for line in lines:
        if any(number in marked for number in range(line.first - 1, line.last + 1)):
            continue
        for event, fact in line_problems(line.text, reading, holds_hidden_word):
            yield Finding(script_rule.rule, name, line.first, fact, script_rule.fix, event=event)


def _workers_problem(arguments: Sequence[str]) -> Problem | None:
    counts = _worker_counts(arguments)
    if not counts:
        return FindingEvent.SERIAL, WITHOUT_WORKERS_FACT
    count = counts[-1]
    if count in AUTOMATIC_COUNTS:
        return None
    if count is not None and WHOLE_NUMBER.fullmatch(count):
        return None if count.strip("0") else (FindingEvent.SERIAL, ZERO_WORKERS_FACT)
    return FindingEvent.INVALID, BAD_COUNT_FACT


def _worker_counts(arguments: Sequence[str]) -> list[str | None]:
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


INSTALL_FIX = (
    "take the tool from the lockfile, the runner image or an image pinned by digest, "
    "or explain on the line above: # runtime-install: <reason>"
)
DOWNLOAD_FACT = "a download runs as a shell script"
INSTALL_RUN_TIME_FACT = "command is built at run time, an install cannot be read"
APT_INSTALLS = frozenset({"install", "reinstall", "satisfy", "build-dep", "upgrade", "dist-upgrade", "full-upgrade"})
DNF_INSTALLS = frozenset({"install", "reinstall", "localinstall", "groupinstall", "upgrade", "update"})
INSTALL_WORDS = {
    "apt-get": APT_INSTALLS,
    "apt": APT_INSTALLS,
    "dpkg": frozenset({"-i", "--install"}),
    "dnf": DNF_INSTALLS,
    "yum": DNF_INSTALLS,
    "apk": frozenset({"add", "upgrade"}),
    "brew": frozenset({"install", "reinstall", "bundle", "upgrade"}),
    "snap": frozenset({"install", "refresh"}),
    "pacman": frozenset({"-S", "-Sy", "-Syu", "--sync", "-U"}),
    "zypper": frozenset({"install", "in", "update", "up", "dist-upgrade", "dup"}),
    "choco": frozenset({"install", "upgrade"}),
    "winget": frozenset({"install", "upgrade"}),
    "port": frozenset({"install", "upgrade"}),
}
PLAYWRIGHT = Tool(commands=frozenset({"playwright"}), module="playwright")
PLAYWRIGHT_DEPENDENCY_WORDS = frozenset({"--with-deps", "install-deps"})
HIDDEN_INSTALL_WORDS = {**INSTALL_WORDS, "playwright": PLAYWRIGHT_DEPENDENCY_WORDS}
CRUDE_WORD_BREAKS = re.compile(r"[\s;|&()<>/\"'`$=]+")
DOWNLOADERS = frozenset({"curl", "wget"})
PROCESS_SUBSTITUTION = "<("


def _holds_install_word(text: str) -> bool:
    """Whether the text holds a package manager's name and one of that manager's install words, each as a word of its own."""
    words = frozenset(CRUDE_WORD_BREAKS.split(text))
    return any(manager in words and not install_words.isdisjoint(words) for manager, install_words in HIDDEN_INSTALL_WORDS.items())


def _install_fact(command: Sequence[str]) -> str | None:
    """`<manager> <install word>` when the command is an install of the closed table, else None."""
    name = PurePosixPath(command[0]).name if command else ""
    if name in INSTALL_WORDS:
        matched = next((word for word in command[1:] if word in INSTALL_WORDS[name]), None)
        return None if matched is None else f"{name} {matched}"
    arguments = tool_arguments(command, PLAYWRIGHT)
    if arguments is None:
        return None
    if "install-deps" in arguments:
        return "playwright install-deps"
    return "playwright install --with-deps" if {"install", "--with-deps"} <= set(arguments) else None


def _names_one_of(command: Sequence[str], names: frozenset[str]) -> bool:
    return bool(command) and PurePosixPath(command[0]).name in names


def _is_download_into_shell(parts: Sequence[Segment], index: int) -> bool:
    """A shell fed by a pipeline that starts or continues with a downloader, a shell that runs a process substitution of a downloader, or the `$` of a shell's `-c` string that is a command substitution of a downloader."""
    command = command_words(parts[index])
    if _names_one_of(command, SHELLS):
        return _is_piped_from_downloader(parts, index) or _runs_substitution_of_downloader(command)
    following = parts[index + 1 :][:1]
    return (
        parts[index].opened_by == C_STRING_OPENER
        and command == LONE_DOLLAR
        and any(part.opened_by == "(" and _names_one_of(command_words(part), DOWNLOADERS) for part in following)
    )


def _is_piped_from_downloader(parts: Sequence[Segment], index: int) -> bool:
    """Whether a segment of the pipeline of the segment at `index` is a downloader; the segments of a shell's `-c` string stand in a pipeline of their own."""
    depth = parts[index].depth
    while parts[index].opened_by in PIPES and index > 0:
        index -= 1
        while parts[index].depth > depth:
            index -= 1
        if _names_one_of(command_words(parts[index]), DOWNLOADERS):
            return True
    return False


def _runs_substitution_of_downloader(command: Sequence[str]) -> bool:
    return PROCESS_SUBSTITUTION in command and _names_one_of(command[command.index(PROCESS_SUBSTITUTION) + 1 :], DOWNLOADERS)


def _install_problem(parts: Sequence[Segment], index: int) -> Problem | None:
    fact = _install_fact(command_words(parts[index]))
    if fact is None and _is_download_into_shell(parts, index):
        fact = DOWNLOAD_FACT
    return None if fact is None else (FindingEvent.INSTALLS, fact)


INSTALL_RULE = ScriptRule(
    Rule.RUNTIME_INSTALL,
    Marker.of(Rule.RUNTIME_INSTALL, above_step=True),
    LineReading(
        judge=_install_problem,
        starts_call=lambda command: _install_fact(command) is not None,
        holds_hidden_word=_holds_install_word,
        unreadable=(FindingEvent.INVALID, UNREADABLE_FACT),
        built_at_run_time=(FindingEvent.INVALID, INSTALL_RUN_TIME_FACT),
    ),
    INSTALL_FIX,
)


def check_runtime_install(root: Path, tracked: list[str]) -> RuleResult:
    """Every install and every download run as a shell in a `run:` of a tracked workflow, judged; the extent is how many workflows were read."""
    return _judge_scripts(INSTALL_RULE, root, tracked)


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
    debt, no_debt = _split_entries(held)
    findings: list[Finding] = []
    readable: dict[JobKey, bool] = {}
    for name in _reached(documents, roots):
        jobs = [job for job in _jobs(name, documents[name]) if "uses" not in checkout.mapping_values(job[2])]
        for job in jobs:
            key = JobKey(name, job[0])
            job_findings = list(_limit_findings(name, documents[name], job, debt.get(key)))
            readable[key] = not any(finding.event in UNREADABLE_LIMIT for finding in job_findings)
            findings.extend(job_findings)
    findings.extend(_unheld_entries(debt, no_debt, readable))
    return RuleResult(findings, len(readable), False)


def _split_entries(held: dict[JobKey, int]) -> tuple[dict[JobKey, int], dict[JobKey, int]]:
    """The entries above the maximum, which are debt, and the entries of at most the maximum, which are none."""
    debt = {key: minutes for key, minutes in held.items() if minutes > MAXIMUM_MINUTES}
    return debt, {key: minutes for key, minutes in held.items() if key not in debt}


def _unheld_entries(debt: dict[JobKey, int], no_debt: dict[JobKey, int], readable: dict[JobKey, bool]) -> Iterator[Finding]:
    """Each entry that can fall with no limit to measure: a debt entry whose job is gone, and an entry that is no debt
    unless its job is there with a limit that cannot be read, which makes the entry stand."""
    reasons = {key: ("is gone", minutes) for key, minutes in debt.items() if key not in readable}
    reasons |= {key: ("has no debt", minutes) for key, minutes in no_debt.items() if readable.get(key, True)}
    for key in sorted(reasons):
        reason, minutes = reasons[key]
        fact = f"job {key.job} {reason} · ceiling {minutes}"
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


def _limit_findings(name: str, document: Document, job: Job, ceiling: int | None) -> Iterator[Finding]:
    """The job's finding against the debt entry that holds it, if any."""
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
