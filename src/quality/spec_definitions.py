"""The spec rules a repository's Markdown files define, which `spec-citation` and `spec-length` both judge.

A Markdown list item that opens with `ID:` defines a live ID and `ID (retired).` a retired one; the first cell of a table row
under such an item, or of a table whose header's first cell is `ID`, defines one too. A live rule's text is what follows its
ID: the rest of the item's first line and each later line indented deeper than the item's marker, a blank line included, or the
row's cells after the first. A nested item or a row that defines an ID is a rule of its own, and a fenced block, a table line
that defines no ID and a retired rule's text belong to no rule. A word is a whitespace-separated token with a letter or a digit.
"""

import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import NamedTuple

from quality import repository
from quality.config import ID_PREFIX, MARKDOWN_SUFFIX
from quality.markdown_fences import live_line_indexes

BYTE_ORDER_MARK = "\ufeff"
ID = rf"{ID_PREFIX}-(?a:\d+)"
ID_PATTERN = re.compile(ID)
ID_HEADER = "ID"
DEFINING_ITEM = re.compile(rf"\s*(?:[-*+]|(?a:\d+)[.)])\s+(?P<id>{ID})(?::|(?P<retired> \(retired\)\.))")


class Site(NamedTuple):
    """A line of a file: where an ID is defined or a test cites it."""

    path: str
    line: int

    def __str__(self) -> str:
        return f"{self.path}:{self.line}"


class Definition(NamedTuple):
    """One definition of an ID: where it stands, whether it is live, and the words of its text, none for a retired ID."""

    site: Site
    live: bool
    words: tuple[str, ...]


@dataclass
class _OpenRule:
    identifier: str
    line: int
    live: bool
    indent: int
    words: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Markdown:
    """What a repository's tracked Markdown files define, in file and line order, and the files that could not be read."""

    definitions: dict[str, list[Definition]]
    unread: list[str]


def _words(text: str) -> list[str]:
    return [token for token in text.split() if any(character.isalnum() for character in token)]


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip())


def _first_cell(line: str) -> str:
    return line.strip().removeprefix("|").split("|", 1)[0].strip()


def _cells_after_the_first(line: str) -> str:
    return line.strip().removeprefix("|").partition("|")[2]


def _rules_of(text: str) -> list[_OpenRule]:
    """Each rule a Markdown text defines: a list item as the rules say, a table row while a rule's table is open and in every
    table whose header's first cell is exactly `ID`."""
    under_rule = False
    id_table = False
    last_row = -2
    rules: list[_OpenRule] = []
    standing: list[_OpenRule] = []
    lines = text.removeprefix(BYTE_ORDER_MARK).splitlines()
    for index in live_line_indexes(lines):
        line = lines[index]
        stripped = line.strip()
        if not stripped:
            continue
        indent = _indent(line)
        while standing and standing[-1].indent >= indent:
            standing.pop()
        if item := DEFINING_ITEM.match(line):
            under_rule = True
            rules.append(_OpenRule(item["id"], index + 1, item["retired"] is None, indent))
            if rules[-1].live:
                rules[-1].words.extend(_words(line[item.end():]))
            standing.append(rules[-1])
        elif stripped.startswith("|"):
            cell = _first_cell(line)
            if index != last_row + 1:
                id_table = cell == ID_HEADER
            last_row = index
            if (under_rule or id_table) and ID_PATTERN.fullmatch(cell):
                rules.append(_OpenRule(cell, index + 1, True, indent, _words(_cells_after_the_first(line))))
        elif standing:
            if standing[-1].live:
                standing[-1].words.extend(_words(stripped))
        elif not line[0].isspace():
            under_rule = False
    return rules


def define(texts: dict[str, str]) -> dict[str, list[Definition]]:
    """The definitions of each ID in the Markdown `texts` (file name to text), in file and line order."""
    definitions: dict[str, list[Definition]] = {}
    for path, text in texts.items():
        for rule in _rules_of(text):
            definitions.setdefault(rule.identifier, []).append(Definition(Site(path, rule.line), rule.live, tuple(rule.words)))
    return definitions


def is_spec_file(name: str) -> bool:
    return PurePosixPath(name).suffix == MARKDOWN_SUFFIX


def spec_text(content: bytes) -> str | None:
    """The text of a spec file's bytes; one that is not UTF-8 defines nothing, so None."""
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError:
        return None


def read_markdown(root: Path, tracked: list[str]) -> Markdown:
    """The definitions of the tracked Markdown files the tree holds as text; those that are absent from it, cannot be read or are
    not UTF-8 are named, never guessed."""
    texts: dict[str, str] = {}
    unread: list[str] = []
    for name in filter(is_spec_file, tracked):
        try:
            content = repository.read_bytes(root, name) if repository.is_on_disk(root, name) else None
        except repository.RepositoryFileError:
            content = None
        text = None if content is None else spec_text(content)
        if text is None:
            unread.append(name)
        else:
            texts[name] = text
    return Markdown(define(texts), unread)
