"""The `spec-length` rule: a spec rule that a change adds or edits and that holds more than `WORD_LIMIT` words is `too long`.

A change is what the work tree holds beyond the merge base of `HEAD` and `origin/main`, uncommitted edits included. A rule is
untouched when the base defines its ID with the same words in any file, so a re-wrap or a move touches nothing. Git is asked only
where the tree holds a live rule over the limit, and a clone without that merge base cannot judge.
"""

from collections import defaultdict
from collections.abc import Iterator
from contextlib import suppress
from itertools import chain
from pathlib import Path, PurePosixPath

from quality import checkout, spec_definitions
from quality.config import MARKDOWN_SUFFIX
from quality.output import SEPARATOR
from quality.report import Finding, FindingEvent, RuleResult
from quality.rules import Rule
from quality.spec_definitions import Definition, Markdown

WORD_LIMIT = 40
NO_BASE_FACT = f"spec rule length cannot be judged, HEAD has no merge base with {checkout.REMOTE_DEFAULT_BRANCH}"
FIX = "shorten it, or split it into rules that each have an ID and a citing test"


def too_long_fact(identifier: str, count: int) -> str:
    return SEPARATOR.join((f"spec rule {identifier} has {count} words", f"limit {WORD_LIMIT}"))


def _live(definitions: dict[str, list[Definition]]) -> Iterator[tuple[str, Definition]]:
    return ((identifier, definition) for identifier, defined in definitions.items() for definition in defined if definition.live)


def _base_texts(root: Path, base: str, differences: list[checkout.Difference]) -> dict[str, str]:
    """The text at `base` of each changed Markdown file it holds; one that is not UTF-8 defines nothing."""
    texts: dict[str, str] = {}
    for difference in differences:
        if difference.in_commit and PurePosixPath(difference.path).suffix == MARKDOWN_SUFFIX:
            with suppress(UnicodeDecodeError):
                texts[difference.path] = checkout.blob(root, f"{base}:{difference.path}").decode("utf-8")
    return texts


def check_spec_length(root: Path, markdown: Markdown) -> RuleResult:
    """The live rules over the limit that the base does not define with the same words; no finding and no git where none is over."""
    over = sorted((definition, identifier) for identifier, definition in _live(markdown.definitions) if len(definition.words) > WORD_LIMIT)
    if not over:
        return RuleResult([], 0, False)
    base = checkout.merge_base_with_default_branch(root, NO_BASE_FACT)
    differences = checkout.differences_from(root, base)
    changed = {difference.path for difference in differences}
    unchanged = ((identifier, definition) for identifier, definition in _live(markdown.definitions) if definition.site.path not in changed)
    held: defaultdict[str, set[tuple[str, ...]]] = defaultdict(set)
    for identifier, definition in chain(_live(spec_definitions.define(_base_texts(root, base, differences))), unchanged):
        held[identifier].add(definition.words)
    findings = [
        Finding(Rule.SPEC_LENGTH, definition.site.path, definition.site.line, too_long_fact(identifier, len(definition.words)), FIX, event=FindingEvent.TOO_LONG)
        for definition, identifier in over
        if definition.site.path in changed and definition.words not in held[identifier]
    ]
    return RuleResult(findings, 0, False)
