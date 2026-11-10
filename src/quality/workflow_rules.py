"""The `pr-concurrency` rule: a workflow a pull request triggers sets a top-level `concurrency` that groups a pull-request
run by its number and cancels only pull-request runs. The `group` and `cancel-in-progress` values are compared with a
closed list of forms; no expression is read, and a form outside the list is a finding."""

import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import yaml

from quality import checkout
from quality.checkout import Document, PullRequestEvent
from quality.report import CannotJudge, Example, Finding, FindingEvent, Instruction, RuleResult
from quality.rules import Rule

EXPRESSION = re.compile(r"\$\{\{(.*?)\}\}", re.DOTALL)
OPERATORS = re.compile(r"\s*(\|\||==|!=)\s*")
FALLBACK = re.compile(r"github\.(?:sha|ref_name|head_ref|ref)(?!\w)")
MERGE_KEY = "<<"
GROUP_KEY = "group"
FLAG_KEY = "cancel-in-progress"
FIX_LABEL = "add this at the top level of the workflow, beside on:"


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
    for name, text in checkout.workflow_texts(root, tracked).items():
        if text is checkout.Unread.ABSENT:
            continue
        if text is checkout.Unread.REFUSED:
            raise _unreadable(name, "the file cannot be read")
        document = _document(name, text)
        events = checkout.pull_request_events(document)
        if events:
            roots += 1
            findings.extend(_concurrency_findings(name, document, events))
    return RuleResult(findings, roots, False)


def _unreadable(name: str, reason: str) -> CannotJudge:
    return CannotJudge(f"cannot read {name}: {reason}", (Instruction(f"fix {name}"),))


def _document(name: str, text: str) -> Document:
    """The parsed workflow; one whose top mapping or `on:` an alias or a merge key could hide the keys of cannot be judged."""
    try:
        document = checkout.composed_document(text)
    except (yaml.YAMLError, RecursionError, ValueError) as error:
        raise _unreadable(name, "it is no YAML quality can read") from error
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
    if group is None or _normalised(group.text) not in LISTED_GROUPS:
        yield finding(FindingEvent.SHARED_GROUP, None if group is None else group.line, _group_fact(group))
    if flag is None:
        yield finding(FindingEvent.MISSING, None, "no cancel-in-progress")
    elif _normalised(flag.text) not in _listed_flags(events):
        yield finding(FindingEvent.CANCELS_MAIN, flag.line, "cancel-in-progress is not a listed form")


def _group_and_flag(name: str, document: Document, key: yaml.ScalarNode, node: yaml.Node) -> tuple[Entry | None, Entry | None]:
    """The `group` and `cancel-in-progress` of a `concurrency`; a scalar is a group with no flag."""
    if document.is_alias(node) or _hides_keys(document, node):
        raise _hidden(name)
    if isinstance(node, yaml.ScalarNode):
        return Entry(node.value, _line(key)), None
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
    return Entry(value.value, _line(key))


def _line(node: yaml.Node) -> int:
    return node.start_mark.line + 1


def _group_fact(group: Entry | None) -> str:
    fact = "group is not a listed form"
    fallback = None if group is None else FALLBACK.search(group.text)
    return fact if fallback is None else f"{fact} (falls back to {fallback[0]})"


def _listed_flags(events: frozenset[PullRequestEvent]) -> frozenset[str]:
    """The flag by event name leaves the runs of any other pull-request event uncancelled."""
    return frozenset({FLAG, FLAG_BY_EVENT_NAME}) if events == {PullRequestEvent.PULL_REQUEST} else frozenset({FLAG})


def _normalised(value: str) -> str:
    """The value with each `${{ }}` body trimmed, its whitespace runs one space and its operators set off by one space."""
    return EXPRESSION.sub(lambda expression: _expression(_normalised_body(expression[1])), value)


def _normalised_body(body: str) -> str:
    return OPERATORS.sub(r" \1 ", " ".join(body.split())).strip()


def _fix(events: frozenset[PullRequestEvent]) -> Example:
    """The canonical block, with the group that tells a combined workflow's events apart."""
    group = GROUP_BY_EVENT if len(events) > 1 else GROUP
    return Example(FIX_LABEL, ("concurrency:", f"  group: {group}", f"  cancel-in-progress: {FLAG}"))
