"""Four rules over the tracked workflows, each reading a closed list of forms and judging anything outside it a finding.

`pr-concurrency`: a workflow a pull request triggers sets a top-level `concurrency` that groups a pull-request run by its
number and cancels only pull-request runs; no expression is read.

`pytest-workers`: every pytest call that runs tests in a `run:` script passes `-n` with more than zero workers or carries a
`# pytest-workers: <reason>` marker; the script is read as POSIX shell words, never run.

`runtime-install`: no `run:` script installs through an operating-system package manager or pipes a download into a
shell, unless the step carries a `# runtime-install: <reason>` marker on the line above; the script is read by the same
reader as `pytest-workers`.

`job-timeout`: every job of a workflow a pull request or a push to main triggers, and of the workflows those call through a
local `uses:`, declares `timeout-minutes` as a plain number of at most 15, or of the whole minutes its baseline entry holds."""

import math
import re
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, replace
from decimal import Decimal
from enum import Enum, auto
from pathlib import Path, PurePosixPath

import yaml

from quality import checkout
from quality.baseline_file import FIX_COMMAND, Baseline, JobKey, JobMinutes
from quality.checkout import Document, PullRequestEvent
from quality.output import SEPARATOR
from quality.pytest_options import WORKERS_NAMES, Option, pytest_arguments, running_options
from quality.ratchet import CeilingFix
from quality.report import CannotJudge, Example, Finding, FindingEvent, Note, NoteEvent, RuleResult
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
from quality.workflow_document import (
    Job,
    Marker,
    composed,
    guarded,
    hidden,
    hides_keys,
    jobs,
    line_of,
    parsed,
    readable,
    readable_workflows,
    run_steps,
    scalar_text,
    unreadable,
)

EXPRESSION = re.compile(r"\$\{\{(.*?)\}\}", re.DOTALL)
OPERATORS = re.compile(r" ?(\|\||==|!=) ?")
FALLBACK = re.compile(r"github\.(?:sha|ref_name|head_ref|ref)(?!\w)")
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
    for name, text in readable_workflows(root, tracked):
        document = parsed(name, text)
        events = checkout.pull_request_events(document)
        if events:
            roots += 1
            findings.extend(_concurrency_findings(name, document, events))
    return RuleResult(findings, roots, False)


def pull_request_workflows(texts: Mapping[str, str | checkout.Unread]) -> list[str]:
    """The paths of the workflows a pull request triggers among `texts` (`checkout.workflow_texts`' mapping), in its order."""
    return [name for name, text in readable(texts) if checkout.pull_request_events(parsed(name, text))]


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
        yield finding(FindingEvent.SHARED_GROUP, line_of(key), NO_GROUP_FACT)
    elif _normalised(group.text) not in LISTED_GROUPS:
        yield finding(FindingEvent.SHARED_GROUP, group.line, _group_fact(group))
    if flag is None:
        yield finding(FindingEvent.MISSING, None, "no cancel-in-progress")
    elif _normalised(flag.text) not in _listed_flags(events):
        yield finding(FindingEvent.CANCELS_MAIN, flag.line, "cancel-in-progress is not a listed form")


def _group_and_flag(name: str, document: Document, key: yaml.ScalarNode, node: yaml.Node) -> tuple[Entry | None, Entry | None]:
    """The `group` and `cancel-in-progress` of a `concurrency`; a scalar is a group with no flag."""
    if document.is_alias(node) or hides_keys(document, node):
        raise hidden(name)
    if isinstance(node, yaml.ScalarNode):
        return Entry(scalar_text(node), line_of(key)), None
    if not isinstance(node, yaml.MappingNode):
        raise unreadable(name, "concurrency is neither a mapping nor a string")
    entries = checkout.mapping_entries(node)
    return _entry(name, document, entries.get(GROUP_KEY)), _entry(name, document, entries.get(FLAG_KEY))


def _entry(name: str, document: Document, pair: tuple[yaml.ScalarNode, yaml.Node] | None) -> Entry | None:
    if pair is None:
        return None
    key, value = pair
    if document.is_alias(value):
        raise hidden(name)
    if not isinstance(value, yaml.ScalarNode):
        raise unreadable(name, f"{key.value} is not a string")
    return Entry(scalar_text(value), line_of(key))


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
WHOLE_NUMBER = re.compile(r"\d+", re.ASCII)
AUTOMATIC_COUNTS = frozenset({"auto", "logical"})
Problem = tuple[FindingEvent, str]


@dataclass(frozen=True)
class ScriptRule:
    """What one rule over the `run:` scripts brings: its marker, its reading of a line and its fix."""

    rule: Rule
    marker: Marker
    reading: LineReading[Problem]
    fix: str


def _pytest_problem(parts: Sequence[Segment], index: int) -> Problem | None:
    called = running_options(parts[index])
    return None if called is None else _workers_problem(called)


PYTEST_RULE = ScriptRule(
    Rule.PYTEST_WORKERS,
    Marker.of(Rule.PYTEST_WORKERS, above_step=False),
    LineReading(
        judge=_pytest_problem,
        starts_call=lambda command: pytest_arguments(command) is not None,
        holds_hidden_word=lambda text: bool(PYTEST_WORD.search(text)),
        unreadable=(FindingEvent.INVALID, UNREADABLE_FACT),
        built_at_run_time=(FindingEvent.INVALID, RUN_TIME_FACT),
    ),
    PYTEST_FIX,
)


def check_pytest_workers(root: Path, tracked: list[str]) -> RuleResult:
    """Every pytest call in a `run:` of a tracked workflow, judged; the extent is how many workflows were read."""
    return judge_scripts(PYTEST_RULE, root, tracked)


def judge_scripts(script_rule: ScriptRule, root: Path, tracked: list[str]) -> RuleResult:
    """Every `run:` step of every tracked workflow, read line by line under the rule; the extent is how many workflows were read."""
    findings: list[Finding] = []
    read = 0
    for name, text in readable_workflows(root, tracked):
        read += 1
        file_lines = text.split("\n")
        marked = script_rule.marker.lines(file_lines)
        for step, run in run_steps(name, composed(name, text)):
            if not script_rule.marker.clears_step(file_lines, step):
                lines = list(logical_lines(physical_lines(run)))
                marks = script_rule.marker.per_line_marks(file_lines, marked, step, run)
                try:
                    findings.extend(_script_findings(script_rule, name, lines, file_lines, marks))
                except CannotJudge as error:
                    raise unreadable(name, error.reason.fact) from error
    return RuleResult(findings, read, False)


def _script_findings(script_rule: ScriptRule, name: str, lines: list[ScriptLine], file_lines: list[str], marked: frozenset[int]) -> Iterator[Finding]:
    reading = script_rule.reading
    holds_hidden_word = reading.holds_hidden_word("\n".join(line.text for line in lines))
    for line in lines:
        if script_rule.marker.clears(file_lines, marked, line.first, line.last):
            continue
        for event, fact in line_problems(line.text, reading, holds_hidden_word):
            yield Finding(script_rule.rule, name, line.first, fact, script_rule.fix, event=event)


def _workers_problem(called: Sequence[Option]) -> Problem | None:
    counts = [option.value for option in called if option.name in WORKERS_NAMES]
    if not counts:
        return FindingEvent.SERIAL, WITHOUT_WORKERS_FACT
    count = counts[-1]
    if count in AUTOMATIC_COUNTS:
        return None
    if WHOLE_NUMBER.fullmatch(count):
        return None if count.strip("0") else (FindingEvent.SERIAL, ZERO_WORKERS_FACT)
    return FindingEvent.INVALID, BAD_COUNT_FACT


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
    return judge_scripts(INSTALL_RULE, root, tracked)


MAXIMUM_MINUTES = 15
TIMEOUT_KEY = "timeout-minutes"
MINUTES = re.compile(r"\d+(?:\.\d+)?", re.ASCII)
SET_TIMEOUT_FIX = f"set {TIMEOUT_KEY} to a number of at most {MAXIMUM_MINUTES}"
LOWER_TIMEOUT_FIX = f"lower {TIMEOUT_KEY} to {MAXIMUM_MINUTES} or less"
RECORD_TIMEOUT_FIX = f"{LOWER_TIMEOUT_FIX}, or run {FIX_COMMAND} to record it"


class Unmeasured(Enum):
    """Why a held entry has no limit to be measured against."""

    UNREADABLE = auto()
    ABSENT = auto()


Limits = dict[JobKey, int | Unmeasured]


class Fall(Enum):
    """What a held entry does against its job: it stands, falls to the job's limit, which is debt, or goes."""

    STANDS = auto()
    LOWERS = auto()
    LIMIT_IS_NO_DEBT = auto()
    ENTRY_IS_NO_DEBT = auto()
    JOB_IS_GONE = auto()


def _fall_of(entry: int, limit: int | Unmeasured) -> Fall:
    """The one decision whether a held entry can fall, for the rule's findings and the fix alike; a job whose limit
    cannot be read leaves its entry standing."""
    if limit is Unmeasured.UNREADABLE:
        return Fall.STANDS
    if entry <= MAXIMUM_MINUTES:
        return Fall.ENTRY_IS_NO_DEBT
    if limit is Unmeasured.ABSENT:
        return Fall.JOB_IS_GONE
    if limit >= entry:
        return Fall.STANDS
    return Fall.LOWERS if limit > MAXIMUM_MINUTES else Fall.LIMIT_IS_NO_DEBT


def check_job_timeouts(root: Path, tracked: list[str], baseline: Baseline | None) -> RuleResult:
    """Every job of a workflow a pull request or a push to main triggers, and of the local workflows those call, judged
    against the baseline's job limits; the extent is the number of jobs the held `[job_minutes]` table holds."""
    table = baseline.job_minutes if baseline else None
    held = table or {}
    debt = {key: minutes for key, minutes in held.items() if minutes > MAXIMUM_MINUTES}
    new_debt_fix = RECORD_TIMEOUT_FIX if table is None else LOWER_TIMEOUT_FIX
    judged = _judged_jobs(root, tracked)
    findings: list[Finding] = []
    for name, document, job in judged:
        findings.extend(_limit_findings(name, document, job, debt.get(JobKey(name, job.id)), new_debt_fix))
    findings.extend(_unmeasured_entries(held, _limits(judged)))
    return RuleResult(findings, len(held), False)


def lower_job_entries(root: Path, tracked: list[str], held: Baseline) -> CeilingFix:
    """The held job entries lowered to their jobs' rounded-up limits and dropped where they are no debt or their job is
    gone, from the jobs the rule judges; an entry whose job's limit cannot be read stands, and nothing rises or enters.
    A table with no entry reads no workflow."""
    entries = held.job_minutes
    if not entries:
        return CeilingFix(held, ())
    limits = _limits(_judged_jobs(root, tracked))
    kept, changes = dict(entries), []
    for key in sorted(entries):
        fall = _entry_fall(key, entries[key], limits.get(key, Unmeasured.ABSENT))
        if fall is not None:
            note, lowered = fall
            changes.append(note)
            if lowered is None:
                del kept[key]
            else:
                kept[key] = lowered
    return CeilingFix(replace(held, job_minutes=kept), tuple(changes))


def recordable_job_limits(root: Path, tracked: list[str]) -> JobMinutes:
    """The jobs the rule judges whose limit is debt, each at its rounded-up limit: what a table recorded now holds.
    No job where a workflow the rule must read cannot be read, because a table once held never gains an entry and so
    could not be completed; the rule reports that file itself."""
    try:
        limits = _limits(_judged_jobs(root, tracked))
    except CannotJudge:
        return {}
    return {key: limit for key, limit in limits.items() if isinstance(limit, int) and limit > MAXIMUM_MINUTES}


def _entry_fall(key: JobKey, entry: int, limit: int | Unmeasured) -> tuple[Note, int | None] | None:
    """The note of an entry that falls and the value it falls to, None where it goes; None where it stands."""
    match _fall_of(entry, limit):
        case Fall.STANDS:
            return None
        case Fall.LOWERS:
            return Note(key.path, NoteEvent.LOWERED, f"job {key.job} {entry} → {limit} minutes"), limit
        case Fall.LIMIT_IS_NO_DEBT:
            fall = f"job {key.job} {entry} → {limit} minutes"
            return Note(key.path, NoteEvent.DROPPED, SEPARATOR.join((fall, f"debt from {MAXIMUM_MINUTES + 1} minutes"))), None
        case Fall.ENTRY_IS_NO_DEBT:
            return Note(key.path, NoteEvent.DROPPED, f"job {key.job} has no debt · was {entry} minutes"), None
        case Fall.JOB_IS_GONE:
            return Note(key.path, NoteEvent.DROPPED, f"job {key.job} is gone · was {entry} minutes"), None


def _limits(judged: list[tuple[str, Document, Job]]) -> Limits:
    return {JobKey(name, job.id): _job_debt(name, document, job) for name, document, job in judged}


def _judged_jobs(root: Path, tracked: list[str]) -> list[tuple[str, Document, Job]]:
    """Each job the rule judges with its file's document: the jobs of the roots and of what they call that are no call."""
    read = {name: _root_document(name, text) for name, text in readable_workflows(root, tracked)}
    documents = {name: read.get(name) for name in tracked if checkout.is_workflow(PurePosixPath(name))}
    roots = frozenset(name for name, document in read.items() if _is_root(name, document))
    return [
        (name, documents[name], job)
        for name in _reached(documents, roots)
        for job in jobs(name, documents[name])
        if "uses" not in checkout.mapping_values(job.mapping)
    ]


def _unmeasured_entries(held: JobMinutes, limits: Limits) -> Iterator[Finding]:
    """Each entry that can fall with no limit to measure: an entry that is no debt, unless its job is there with a limit
    that cannot be read, and a debt entry whose job is gone."""
    reasons = {Fall.ENTRY_IS_NO_DEBT: "has no debt", Fall.JOB_IS_GONE: "is gone"}
    for key in sorted(held):
        reason = reasons.get(_fall_of(held[key], limits.get(key, Unmeasured.ABSENT)))
        if reason is not None:
            fact = f"job {key.job} {reason} · ceiling {held[key]}"
            yield Finding(Rule.JOB_TIMEOUT, key.path, None, fact, FIX_COMMAND, event=FindingEvent.CAN_FALL)


def _root_document(name: str, text: str) -> Document:
    """The parsed workflow; one whose `push` filters an alias or a merge key could hide cannot be judged."""
    document = parsed(name, text)
    if _hides_push(document, checkout.mapping_values(document.root).get("on")):
        raise hidden(name)
    return document


def _hides_push(document: Document, triggers: yaml.Node | None) -> bool:
    push = checkout.mapping_values(triggers).get("push")
    if push is None:
        return False
    if document.is_alias(push) or hides_keys(document, push):
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
        raise unreadable(name, error.reason) from error
    return pushes or bool(checkout.pull_request_events(document))


def _reached(documents: dict[str, Document | None], roots: frozenset[str]) -> list[str]:
    try:
        return checkout.reached_workflows(documents, roots, lambda name: _called_files(name, documents[name]))
    except checkout.UnfollowableCall as error:
        raise unreadable(error.caller, f"it calls {error.uses}, which {error.because}") from error


def _called_files(name: str, document: Document) -> list[str]:
    calls = (guarded(name, document, checkout.mapping_values(job.mapping).get("uses")) for job in jobs(name, document))
    return [call.value for call in calls if isinstance(call, yaml.ScalarNode)]


def _limit_findings(name: str, document: Document, job: Job, ceiling: int | None, new_debt_fix: str) -> Iterator[Finding]:
    """The job's finding against the debt entry that holds it, if any; a job with no entry that is debt is new debt,
    whose fix is `new_debt_fix`."""

    def finding(event: FindingEvent, line: int, fact: str, fix: str) -> Finding:
        return Finding(Rule.JOB_TIMEOUT, name, line, fact, fix, event=event)

    limit, minutes = _read_limit(name, document, job.mapping)
    if limit is None:
        yield finding(FindingEvent.MISSING, line_of(job.key), f"job {job.id} has no {TIMEOUT_KEY}", SET_TIMEOUT_FIX)
        return
    key, value = limit
    if minutes is None:
        fact = f"job {job.id} has a {TIMEOUT_KEY} that is not a number above zero"
        yield finding(FindingEvent.INVALID, line_of(key), fact, SET_TIMEOUT_FIX)
        return
    debt = _debt_minutes(minutes)
    given = f"job {job.id} has {TIMEOUT_KEY} {value.value}"
    if ceiling is None and debt > MAXIMUM_MINUTES:
        yield finding(FindingEvent.NEW_DEBT, line_of(key), f"{given} · limit {MAXIMUM_MINUTES}", new_debt_fix)
    elif ceiling is not None and debt > ceiling:
        yield finding(FindingEvent.GREW, line_of(key), f"{given} · ceiling {ceiling}", f"bring it back to {ceiling} or below")
    elif ceiling is not None and _fall_of(ceiling, debt) is not Fall.STANDS:
        yield finding(FindingEvent.CAN_FALL, line_of(key), f"{given} · ceiling {ceiling}", FIX_COMMAND)


def _read_limit(name: str, document: Document, mapping: yaml.MappingNode) -> tuple[tuple[yaml.Node, yaml.Node] | None, Decimal | None]:
    """The job's `timeout-minutes` key and value, and the minutes the value spells, each None where there is none."""
    limit = checkout.mapping_entries(mapping).get(TIMEOUT_KEY)
    return limit, None if limit is None else _minutes(guarded(name, document, limit[1]))


def _job_debt(name: str, document: Document, job: Job) -> int | Unmeasured:
    """The whole minutes the job's limit counts as debt, unmeasured where the limit is missing or invalid."""
    minutes = _read_limit(name, document, job.mapping)[1]
    return Unmeasured.UNREADABLE if minutes is None else _debt_minutes(minutes)


def _debt_minutes(limit: Decimal) -> int:
    """The whole minutes a limit counts as debt: rounded up, so 17.5 is held by an entry of 18."""
    return math.ceil(limit)


def _minutes(value: yaml.Node | None) -> Decimal | None:
    """The limit a plain scalar spells as a decimal number above zero, else None."""
    if not isinstance(value, yaml.ScalarNode) or value.style or not MINUTES.fullmatch(value.value):
        return None
    return Decimal(value.value) or None


def job_names(root: Path, tracked: list[str]) -> frozenset[str]:
    """The names the run lists for the jobs of every tracked workflow: a job's `name:` when it is a plain string, else its id.
    A `name:` that holds an expression is no name the run lists, so it adds none."""
    names: set[str] = set()
    for name, text in readable_workflows(root, tracked):
        document = parsed(name, text)
        for job in jobs(name, document):
            label = guarded(name, document, checkout.mapping_values(job.mapping).get("name"))
            if label is None:
                names.add(job.id)
            elif isinstance(label, yaml.ScalarNode) and label.value and "${{" not in label.value:
                names.add(label.value)
    return frozenset(names)
