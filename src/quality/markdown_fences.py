"""The Markdown rule for fenced code blocks, which every reader of Markdown lines shares."""

import re
from collections.abc import Sequence

FENCE_OPENING = re.compile(r" {0,3}(?P<run>`{3,}|~{3,})")
FENCE_CLOSING = re.compile(r" {0,3}(?P<run>`{3,}|~{3,})[ \t]*")


def live_line_indexes(lines: Sequence[str]) -> list[int]:
    """The lines outside fenced code blocks, fence lines excluded: a fence closes on a run of its own character at least as long, nothing after it."""
    live: list[int] = []
    fence = ""
    for index, line in enumerate(lines):
        if not fence:
            opening = FENCE_OPENING.match(line)
            if opening:
                fence = opening["run"]
            else:
                live.append(index)
        elif (
            (closing := FENCE_CLOSING.fullmatch(line))
            and closing["run"][0] == fence[0]
            and len(closing["run"]) >= len(fence)
        ):
            fence = ""
    return live
