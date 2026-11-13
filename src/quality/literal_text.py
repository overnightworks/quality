"""The `literal-text` rule: it counts the literal texts of a covered repository's tracked `*.svelte` pages, the words a person
reads that the markup holds itself instead of taking them from a catalog. Without a `[literal_text]` table in the ceiling it
only measures; with one, a page above its count or new with text is a finding. A page it cannot read to its end refuses the
rule, because the texts after the unread part would be missed in silence."""

import html
import re
from collections.abc import Callable
from dataclasses import replace
from html.parser import HTMLParser
from pathlib import Path

from quality import catalog_rules, checkout, repository
from quality.baseline_file import FIX_COMMAND, Baseline, LiteralTexts, PageKey
from quality.output import SEPARATOR, counted
from quality.ratchet import CeilingFix
from quality.report import Finding, FindingEvent, LiteralExtent, Note, NoteEvent, RuleResult, Unparsable
from quality.rules import Rule

SVELTE_SUFFIX = ".svelte"
CODE_BLOCK = re.compile(r"<(script|style)(?:\s[^>]*)?>.*?</\1\s*>", re.DOTALL | re.IGNORECASE)
LOGIC_MARKS = frozenset("#:/@")
QUOTES = frozenset("'\"`")
READ_ATTRIBUTES = frozenset(
    {"title", "placeholder", "aria-label", "alt", "label", "aria-description", "aria-roledescription", "aria-valuetext"}
)
LABELLED_INPUT_TYPES = frozenset({"submit", "button", "reset"})
CODE_TAGS = frozenset({"script", "style"})
RAW_TEXT_TAGS = CODE_TAGS | {"textarea", "title", "iframe", "xmp", "noembed", "noframes"}
UNCLOSED_HOLE = "has a { that is never closed, so the markup after it cannot be read"
UNCLOSED_ELEMENT = "has a <{tag}> that is opened and never closed, so the markup after it cannot be read"
NEW_DEBT_FIX = "read each text through the generated module and keep it in the catalog"
TEXT = "literal text"


class UnreadablePage(ValueError):
    """A page whose markup cannot be read to its end; the message says what is wrong, not where."""


def _is_text(candidate: str) -> bool:
    return any(character.isalpha() for character in candidate)


def _value_is_read(tag: str, attributes: dict[str, str | None]) -> bool:
    """A submit, button or reset control's value is its label; a plain input's value is what a person typed."""
    kind = attributes.get("type")
    if tag == "input":
        return kind in LABELLED_INPUT_TYPES
    return tag == "button" and kind in (None, "submit")


def _read_attributes(tag: str, attributes: dict[str, str | None]) -> frozenset[str]:
    names = set(READ_ATTRIBUTES)
    if _value_is_read(tag, attributes):
        names.add("value")
    if tag == "meta" and attributes.get("name") == "description":
        names.add("content")
    return frozenset(names)


class _Scanner(HTMLParser):
    """Counts the text nodes and the read attribute values; the body of a script or style block is code."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.texts = 0
        self.open_raw_text_tag: str | None = None

    def handle_data(self, data: str) -> None:
        if self.open_raw_text_tag not in CODE_TAGS and _is_text(data.strip()):
            self.texts += 1

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        values = (attributes.get(name) for name in _read_attributes(tag, attributes))
        self.texts += sum(1 for value in values if value is not None and _is_text(value.strip()))
        if tag in RAW_TEXT_TAGS:
            self.open_raw_text_tag = tag

    def handle_endtag(self, tag: str) -> None:
        if tag == self.open_raw_text_tag:
            self.open_raw_text_tag = None


def _end_of_quoted(source: str, opening: int) -> int:
    """The index of the quote that closes the string opened at `opening`, or -1."""
    index = opening + 1
    while index < len(source):
        if source[index] == "\\":
            index += 2
        elif source[index] == source[opening]:
            return index
        else:
            index += 1
    return -1


def _closing_brace(source: str, opening: int) -> int:
    """The index of the `}` that closes the hole opened at `opening`, or -1."""
    depth = 0
    index = opening
    while index < len(source):
        if source[index] in QUOTES:
            index = _end_of_quoted(source, index)
            if index == -1:
                return -1
        elif source[index] == "{":
            depth += 1
        elif source[index] == "}":
            depth -= 1
            if depth == 0:
                return index
        index += 1
    return -1


def _string_literal(expression: str) -> str:
    """The text of an expression that is one string literal (a template literal without a substitution too), else nothing."""
    stripped = expression.strip()
    if not stripped or stripped[0] not in QUOTES or _end_of_quoted(stripped, 0) != len(stripped) - 1:
        return ""
    literal = stripped[1:-1]
    return "" if stripped[0] == "`" and "${" in literal else literal


def _folded_hole(expression: str) -> str:
    return "" if expression[:1] in LOGIC_MARKS else _string_literal(expression)


def _folded_markup(source: str) -> str:
    """The page as HTML: its code blocks dropped and each `{...}` hole folded to the literal it shows, quoted when it is an
    attribute's whole value."""
    markup = CODE_BLOCK.sub("", source)
    parts: list[str] = []
    position = 0
    while (opening := markup.find("{", position)) != -1:
        closing = _closing_brace(markup, opening)
        if closing == -1:
            raise UnreadablePage(UNCLOSED_HOLE)
        parts.append(markup[position:opening])
        shown = html.escape(_folded_hole(markup[opening + 1 : closing]))
        parts.append(f'"{shown}"' if markup[:opening].rstrip().endswith("=") else shown)
        position = closing + 1
    parts.append(markup[position:])
    return "".join(parts)


def count_literal_texts(source: str) -> int:
    """The literal texts of one page's source; an `UnreadablePage` when a hole or a raw-text element is never closed."""
    scanner = _Scanner()
    scanner.feed(_folded_markup(source))
    scanner.close()
    if scanner.open_raw_text_tag is not None:
        raise UnreadablePage(UNCLOSED_ELEMENT.format(tag=scanner.open_raw_text_tag))
    return scanner.texts


def _page_counts(root: Path, tracked: list[str]) -> dict[str, int]:
    """The literal texts of each page the tree holds; the first page in the tracked order that cannot be read to its end stops it."""
    names = checkout.on_disk(root, [name for name in tracked if name.endswith(SVELTE_SUFFIX)])
    counts: dict[str, int] = {}
    for name, source in checkout.read_existing(repository.read_text, root, names).items():
        try:
            counts[name] = count_literal_texts(source)
        except UnreadablePage as error:
            raise Unparsable(f"{name} {error}") from error
    return counts


def _covered_counts(root: Path, tracked: list[str]) -> dict[str, int] | None:
    """The page counts of a covered repository, None for one that is not covered."""
    return _page_counts(root, tracked) if catalog_rules.declares_catalogs(root, tracked) else None


def table_words(texts: int, pages: int) -> str:
    """What a `[literal_text]` table holds, as a row names it."""
    return f"{counted(texts, TEXT)} on {counted(pages, 'page')}"


def recordable_counts(root: Path, tracked: list[str]) -> LiteralTexts | None:
    """What a table recorded now holds: each page with text at its count; None for a repository that is not covered."""
    counts = _covered_counts(root, tracked)
    return None if counts is None else {PageKey(name): count for name, count in counts.items() if count}


def lower_literal_entries(root: Path, tracked: list[str], held: Baseline) -> CeilingFix:
    """The held page counts lowered to what their pages measure and dropped where a page measures none or is gone;
    nothing rises or enters. A page the tree tracks but the disk lacks keeps its entry. A table with no entry, or a
    repository that is not covered, reads no page."""
    entries = held.literal_texts
    counts = _covered_counts(root, tracked) if entries else None
    if counts is None:
        return CeilingFix(held, ())
    kept, changes = dict(entries), []
    for key in sorted(entries):
        ceiling = entries[key]
        if key.path not in tracked:
            changes.append(Note(key.path, NoteEvent.DROPPED, SEPARATOR.join(("file is gone", f"was {counted(ceiling, TEXT)}"))))
            del kept[key]
        elif (measured := counts.get(key.path, ceiling)) < ceiling:
            fall = f"{ceiling} → {measured} literal texts"
            changes.append(Note(key.path, NoteEvent.LOWERED if measured else NoteEvent.DROPPED, fall))
            if measured:
                kept[key] = measured
            else:
                del kept[key]
    return CeilingFix(replace(held, literal_texts=kept), tuple(changes))


def _findings(counts: dict[str, int], table: LiteralTexts, tracked: frozenset[str]) -> list[Finding]:
    """The pages against the table: a page above its entry or new with text, then the entries that can fall."""
    findings = []
    for name, count in sorted(counts.items()):
        held = table.get(PageKey(name))
        if held is None and count:
            findings.append(_finding(name, count, FindingEvent.NEW_DEBT, "ceiling none", NEW_DEBT_FIX))
        elif held is not None and count > held:
            fix = f"bring it back to {counted(held, TEXT)} or fewer"
            findings.append(_finding(name, count, FindingEvent.GREW, f"ceiling {held}", fix))
        elif held is not None and count < held:
            findings.append(_finding(name, count, FindingEvent.CAN_FALL, f"ceiling {held}", FIX_COMMAND))
    for key in sorted(table):
        if key.path not in tracked:
            fact = SEPARATOR.join(("file is gone", f"ceiling {table[key]}"))
            findings.append(Finding(Rule.LITERAL_TEXT, key.path, None, fact, FIX_COMMAND, event=FindingEvent.CAN_FALL))
    return findings


def _finding(name: str, count: int, event: FindingEvent, ceiling: str, fix: str) -> Finding:
    return Finding(Rule.LITERAL_TEXT, name, None, SEPARATOR.join((counted(count, TEXT), ceiling)), fix, event=event)


def check_literal_text(root: Path, tracked: list[str], read_ceiling: Callable[[], Baseline | None]) -> RuleResult | None:
    """A covered repository's pages against its `[literal_text]` table; without the table they are measured only, and the
    extent counts the texts and files read, with the table the texts and pages it holds. None for a repository that is
    not covered, whose ceiling is never read for this rule."""
    counts = _covered_counts(root, tracked)
    if counts is None:
        return None
    baseline = read_ceiling()
    table = baseline.literal_texts if baseline else None
    if table is None:
        return RuleResult([], LiteralExtent(sum(counts.values()), len(counts)), True)
    return RuleResult(_findings(counts, table, frozenset(tracked)), LiteralExtent(sum(table.values()), len(table)), False)
