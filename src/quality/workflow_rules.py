"""Two rules over the tracked workflows, each reading a closed list of forms and judging anything outside it a finding.

`pr-concurrency`: a workflow a pull request triggers sets a top-level `concurrency` that groups a pull-request run by its
number and cancels only pull-request runs; no expression is read.

`pytest-workers`: every pytest call in a `run:` script passes `-n` with more than zero workers or carries a
`# pytest-workers: <reason>` marker; the script is read as POSIX shell words, never run."""

import re
import shlex
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import yaml

from quality import checkout
from quality.checkout import Document, PullRequestEvent
from quality.report import CannotJudge, Example, Finding, FindingEvent, Instruction, RuleResult
from quality.rules import Rule

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
RUN_EXPRESSION = re.compile(r"\$\{\{.*?\}\}")
DYNAMIC_EXPRESSION = "$EXPRESSION"
PYTEST_WORD = re.compile(r"(?<![\w-])pytest(?![\w-])")
MARKER = re.compile(r"#[ \t]*pytest-workers:[^\n]*?[^\W\d_]")
ASSIGNMENT = re.compile(r"\w+=")
DYNAMIC_COMMAND = re.compile(r"\$[\w{]")
WORKERS_FLAG = re.compile(r"(?:-n|--numprocesses)(?:=(.*))?|-n(.+)", re.DOTALL)
WHOLE_NUMBER = re.compile(r"\d+", re.ASCII)
SEPARATOR_CHARACTERS = frozenset("();|&`")
PREFIX_WORDS = frozenset({"time", "exec", "command", "nohup", "if", "then", "elif", "else", "do", "while", "until", "!", "{"})
PYTEST_COMMANDS = frozenset({"pytest", "py.test"})
INTERPRETER = re.compile(r"python(?:3(?:\.\d+t?)?)?|pypy(?:3(?:\.\d+)?)?")
INTERPRETER_VALUE_FLAGS = frozenset({"-W", "-X", "--check-hash-based-pycs"})
RUNNERS = frozenset({("uv", "run"), ("poetry", "run"), ("pipenv", "run"), ("pdm", "run"), ("hatch", "run"), ("coverage", "run")})
AUTOMATIC_COUNTS = frozenset({"auto", "logical"})
ENV_VALUE_FLAGS = frozenset({"-u", "--unset", "-C", "--chdir", "-a", "--argv0"})
ENV_SPLIT_FLAGS = frozenset({"-S", "--split-string"})
ENV_SPLIT_PREFIXES = ("-S", "--split-string=")
SUDO_VALUE_FLAGS = frozenset({"-u", "-g", "-D", "-C", "-h", "-p", "-r", "-t", "-T", "-U", "-R"})
WRAPPER_VALUE_FLAGS = {"env": ENV_VALUE_FLAGS, "sudo": SUDO_VALUE_FLAGS}
SUBSTITUTION = re.compile(r"\$\(|`")
LONE_DOLLAR = ["$"]

Problem = tuple[FindingEvent, str]


@dataclass(frozen=True)
class ScriptLine:
    """A logical line of a `run:` script without its comments, and the file lines of its first and last physical line."""

    text: str
    first: int
    last: int


def check_pytest_workers(root: Path, tracked: list[str]) -> RuleResult:
    """Every pytest call in a `run:` of a tracked workflow, judged; the extent is how many workflows were read."""
    findings: list[Finding] = []
    read = 0
    for name, text in _readable_workflows(root, tracked):
        read += 1
        document = _composed(name, text)
        marked = frozenset(number for number, line in enumerate(text.split("\n"), 1) if MARKER.search(line))
        for run in _run_values(name, document):
            findings.extend(_script_findings(name, list(_logical_lines(_physical_lines(run))), marked))
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


def _physical_lines(run: yaml.ScalarNode) -> list[tuple[str, int]]:
    """The value line by line, as the shell reads it; a literal block scalar keeps each file line, any other style the first."""
    lines = run.value.split("\n")
    if run.style == "|":
        first = run.start_mark.line + 2
        return [(RUN_EXPRESSION.sub(DYNAMIC_EXPRESSION, line), first + offset) for offset, line in enumerate(lines[: len(lines) - run.value.endswith("\n")])]
    first = run.start_mark.line + (2 if run.style == ">" else 1)
    return [(RUN_EXPRESSION.sub(DYNAMIC_EXPRESSION, line), first) for line in lines]


def _logical_lines(physical: list[tuple[str, int]]) -> Iterator[ScriptLine]:
    """Lines joined at a trailing backslash, each without the comment that ends its physical lines."""
    parts: list[str] = []
    first = 0
    for text, number in physical:
        first = first if parts else number
        code = _without_comment(text)
        parts.append(code.removesuffix("\\"))
        if not code.endswith("\\"):
            yield ScriptLine("".join(parts), first, number)
            parts = []
    if parts:
        yield ScriptLine("".join(parts), first, physical[-1][1])


def _without_comment(line: str) -> str:
    """The line up to a `#` that starts a word outside quotes."""
    quote = ""
    escaped = False
    for index, character in enumerate(line):
        if escaped:
            escaped = False
        elif character == "\\" and quote != "'":
            escaped = True
        elif quote:
            quote = "" if character == quote else quote
        elif character in "'\"":
            quote = character
        elif character == "#" and (index == 0 or line[index - 1].isspace() or line[index - 1] in ";&|()"):
            return line[:index]
    return line


def _script_findings(name: str, lines: list[ScriptLine], marked: frozenset[int]) -> Iterator[Finding]:
    holds_pytest = any(PYTEST_WORD.search(line.text) for line in lines)
    for line in lines:
        if any(number in marked for number in range(line.first - 1, line.last + 1)):
            continue
        for event, fact in _line_problems(line.text, holds_pytest):
            yield Finding(Rule.PYTEST_WORKERS, name, line.first, fact, PYTEST_FIX, event=event)


def _line_problems(text: str, script_holds_pytest: bool) -> Iterator[Problem]:
    try:
        segments = [_with_env_strings_split(words) for words in _segments(text)]
    except ValueError:
        if PYTEST_WORD.search(text):
            yield FindingEvent.INVALID, UNREADABLE_FACT
        return
    for index, words in enumerate(segments):
        command = words[_command_start(words) :]
        if command == LONE_DOLLAR:
            if script_holds_pytest and not (index + 1 < len(segments) and _starts_pytest_call(segments[index + 1])):
                yield FindingEvent.INVALID, RUN_TIME_FACT
            continue
        problem = _segment_problem(command, script_holds_pytest)
        if problem is not None:
            yield problem


def _segments(text: str) -> list[list[str]]:
    """The words of a line, split at `&&`, `||`, `;`, `|`, `&`, `(`, `)` and a backtick; a word that holds a substitution is split again."""
    return [words for words in _split(text) if words]


def _split(text: str) -> list[list[str]]:
    lexer = shlex.shlex(text, posix=True, punctuation_chars="();<>|&`")
    lexer.whitespace_split = True
    lexer.commenters = ""
    segments: list[list[str]] = [[]]
    for word in lexer:
        if word and set(word) <= SEPARATOR_CHARACTERS:
            segments.append([])
        elif SUBSTITUTION.search(word):
            inner = _split(word)
            segments[-1].extend(inner[0])
            segments.extend(inner[1:])
        else:
            segments[-1].append(word)
    return segments


def _starts_pytest_call(words: list[str]) -> bool:
    command = words[_command_start(words) :]
    return bool(command) and _pytest_arguments(command) is not None


def _with_env_strings_split(words: list[str]) -> list[str]:
    """The words with the value of each `env -S` replaced by the words it splits into, placed where the value stood."""
    result: list[str] = []
    index = 0
    while index < len(words):
        result.append(words[index])
        index += 1
        if words[index - 1] == "env":
            index = _copy_env_flags(words, index, result)
    return result


def _copy_env_flags(words: list[str], index: int, result: list[str]) -> int:
    """Copies the flags of an `env` from `index` on, splitting the value of each `-S`; returns the index of its first operand."""
    while index < len(words) and words[index].startswith("-"):
        word = words[index]
        index += 1
        if word in ENV_SPLIT_FLAGS:
            if index < len(words):
                result.extend(shlex.split(words[index]))
            index += 1
        elif (glued := _glued_split_value(word)) is not None:
            result.extend(shlex.split(glued))
        else:
            result.append(word)
            if word in ENV_VALUE_FLAGS and index < len(words):
                result.append(words[index])
                index += 1
    return index


def _glued_split_value(word: str) -> str | None:
    """The value of `-S'…'` or `--split-string=…`, None for any other word."""
    for prefix in ENV_SPLIT_PREFIXES:
        if word.startswith(prefix):
            return word[len(prefix) :]
    return None


def _command_start(words: list[str]) -> int:
    """The index of the command word, after assignments, `env` and `sudo` with their flags and the prefix words."""
    index = 0
    while index < len(words):
        if words[index] in WRAPPER_VALUE_FLAGS:
            index = _after_flags(words, index + 1, WRAPPER_VALUE_FLAGS[words[index]])
        elif words[index] in PREFIX_WORDS or ASSIGNMENT.match(words[index]):
            index += 1
        else:
            break
    return index


def _after_flags(words: list[str], index: int, value_flags: frozenset[str]) -> int:
    """The index past the flags from `index` on, each flag in `value_flags` taking the word after it as its value."""
    while index < len(words) and words[index].startswith("-"):
        index += 2 if words[index] in value_flags else 1
    return index


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
    if INTERPRETER.fullmatch(command):
        return _module_arguments(words[1:])
    if command == "uvx":
        return _runner_arguments(words[1:])
    if (command, *words[1:2]) in RUNNERS:
        return _runner_arguments(words[2:])
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


def _runner_arguments(words: list[str]) -> list[str] | None:
    """The pytest call a runner starts, past its flags and the values of its flags."""
    index = 0
    while index < len(words):
        arguments = _pytest_arguments(words[index:])
        if arguments is not None:
            return arguments
        if not words[index].startswith("-"):
            return None
        index += 1
        if index < len(words) and not words[index].startswith("-") and _pytest_arguments(words[index:]) is None:
            index += 1
    return None


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
