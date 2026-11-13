"""The `literal-text` rule: it counts the literal texts of a covered repository's tracked `*.svelte` pages, the words a person
reads that the markup holds itself instead of taking them from a catalog. It only measures: it never has a finding, and a page
it cannot read to its end refuses the rule, because the texts after the unread part would be missed in silence."""

import html
import re
from html.parser import HTMLParser
from pathlib import Path

from quality import catalog_rules, checkout, repository
from quality.report import LiteralExtent, RuleResult, Unparsable

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


def _pages_read(root: Path, tracked: list[str]) -> tuple[int, int]:
    """The literal texts and the pages the tree holds; the first page in the tracked order that cannot be read to its end stops it."""
    names = checkout.on_disk(root, [name for name in tracked if name.endswith(SVELTE_SUFFIX)])
    sources = checkout.read_existing(repository.read_text, root, names)
    texts = 0
    for name, source in sources.items():
        try:
            texts += count_literal_texts(source)
        except UnreadablePage as error:
            raise Unparsable(f"{name} {error}") from error
    return texts, len(sources)


def check_literal_text(root: Path, tracked: list[str]) -> RuleResult | None:
    """The literal texts and pages of a covered repository, measured only; None for a repository that is not covered."""
    if not catalog_rules.declares_catalogs(root, tracked):
        return None
    texts, pages = _pages_read(root, tracked)
    return RuleResult([], LiteralExtent(texts, pages), True)
