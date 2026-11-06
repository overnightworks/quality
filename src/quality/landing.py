"""The line a merge commit ends with: the pull request body's one `Work-Item:` or `No-Item:` line, in the trunk's trailer form."""

import re
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

from quality.config import ITEM_REFERENCE
from quality.report import CannotJudge

NO_ITEM_KINDS = frozenset({"docs", "fix", "deps"})


class LandingKind(StrEnum):
    """What the merge commit's last line names, by its trailer key: a work item, or an issue-less kind."""

    WORK_ITEM = "Work-Item"
    NO_ITEM = "No-Item"


CLASSIFICATION_LINE = re.compile(rf"(?i)(?P<key>{LandingKind.WORK_ITEM}|{LandingKind.NO_ITEM}):(?P<value>[^\n]*)")
FENCE_OPENING = re.compile(r" {0,3}(?P<run>`{3,}|~{3,})")
FENCE_CLOSING = re.compile(r" {0,3}(?P<run>`{3,}|~{3,})[ \t]*")


class LandingRefused(Exception):
    """The body names no line the trunk takes; the message says why."""


@dataclass(frozen=True)
class Landing:
    """The merge commit's message: the body without its classification line, then that line as the last paragraph;
    `value` is the line's value in the trunk's form, `#<n>` or a kind."""

    kind: LandingKind
    value: str
    message: str


def live_line_indexes(lines: Sequence[str]) -> list[int]:
    """The lines outside fenced code blocks: a fence closes on a run of its own character at least as long, nothing after it."""
    live: list[int] = []
    fence = ""
    for index, line in enumerate(lines):
        if not fence:
            opening = FENCE_OPENING.match(line)
            if opening:
                fence = opening["run"]
            else:
                live.append(index)
        elif (closing := FENCE_CLOSING.fullmatch(line)) and closing["run"][0] == fence[0] and len(closing["run"]) >= len(fence):
            fence = ""
    return live


def classification(key: str, value: str, repository: str) -> tuple[LandingKind, str]:
    """The kind and value in the trunk's trailer form; any other value keeps the door shut, since a trailer cannot be rewritten."""
    if key.lower() == LandingKind.NO_ITEM.lower():
        if value.lower() not in NO_ITEM_KINDS:
            raise LandingRefused(
                f"the pull request body carries {LandingKind.NO_ITEM}: {value}; an issue-less pull request is {', '.join(sorted(NO_ITEM_KINDS))}"
            )
        return LandingKind.NO_ITEM, value.lower()
    if not ITEM_REFERENCE.fullmatch(value):
        raise LandingRefused(f"the pull request body carries {LandingKind.WORK_ITEM}: {value}; a work item reads OWNER/REPO#n or #n")
    named, _, number = value.rpartition("#")
    if named not in ("", repository):
        raise LandingRefused(f"the pull request body names work item {value} of another repository, not of {repository}")
    return LandingKind.WORK_ITEM, f"#{number}"


def landing_of(body: object, repository: str) -> Landing:
    """The landing of the body judged with the head: exactly one classification line outside code blocks. GitHub answers an empty body as null."""
    if body is None:
        body = ""
    if not isinstance(body, str):
        raise CannotJudge("GitHub's answer for the pull request holds no body text")
    lines = body.splitlines()
    found = [(index, match) for index in live_line_indexes(lines) if (match := CLASSIFICATION_LINE.fullmatch(lines[index]))]
    if len(found) != 1:
        raise LandingRefused(
            f"the pull request body carries {len(found) or 'no'} {LandingKind.WORK_ITEM}: or {LandingKind.NO_ITEM}: lines outside code "
            "blocks; the merge commit ends with exactly one"
        )
    index, match = found[0]
    kind, value = classification(match["key"], match["value"].strip(" \t"), repository)
    trailer = f"{kind}: {value}"
    kept = [*lines[:index], *lines[index + 1 :]]
    if 0 < index < len(kept) and not kept[index - 1].strip() and not kept[index].strip():
        del kept[index]
    rest = "\n".join(kept).strip()
    return Landing(kind, value, f"{rest}\n\n{trailer}" if rest else trailer)
