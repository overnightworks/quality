"""The `spec-citation` rule: spec IDs no test cites, cited IDs no spec defines, and IDs defined twice. The rule only reports,
as one `open` note under the debt area.

A Markdown list item that opens with `ID:` defines a live ID and `ID (retired).` a retired one; the first cell of a table row
under such an item defines one too. A test cites the IDs at the head of its Python docstring or of its `it(` and `test(` title.
"""

import ast
import re
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from quality import clones, repository
from quality.markdown_fences import live_line_indexes
from quality.output import counted
from quality.ratchet import Language, language_of
from quality.report import Note, NoteEvent, RuleResult
from quality.typescript_tokens import Kind, ScriptSyntaxError, Token, tokenize

NOTE_PATH = "."
MARKDOWN_SUFFIX = ".md"
BYTE_ORDER_MARK = "\ufeff"
ID = r"[A-Z][A-Z0-9]*(?:-[A-Z][A-Z0-9]*)*-(?a:\d+)"
ID_PATTERN = re.compile(ID)
DEFINING_ITEM = re.compile(rf"\s*(?:[-*+]|\d+[.)])\s+(?P<id>{ID})(?::|(?P<retired> \(retired\)\.))")
HEAD = re.compile(rf"\s*(?:Spec:\s*)?{ID}(?:\s*(?:,(?:\s*and\b)?|\band\b)\s*{ID})*")
SCRIPT_TEST_CALLS = frozenset({"it", "test"})
SCRIPT_LANGUAGES = frozenset({Language.TYPESCRIPT, Language.JAVASCRIPT})


@dataclass(frozen=True)
class Definitions:
    """The IDs of the Markdown files, in the order they are defined; a retired ID is defined too."""

    live: frozenset[str]
    ids: Counter[str]


def _first_cell(line: str) -> str:
    return line.strip().removeprefix("|").split("|", 1)[0].strip()


def _definitions_of(text: str) -> Iterator[tuple[str, bool]]:
    """Each (ID, live) a Markdown text defines: a list item as the rules say, a table row while a rule's table is open."""
    under_rule = False
    lines = text.removeprefix(BYTE_ORDER_MARK).splitlines()
    for line in (lines[index] for index in live_line_indexes(lines)):
        stripped = line.strip()
        if not stripped:
            continue
        if item := DEFINING_ITEM.match(line):
            under_rule = True
            yield item["id"], item["retired"] is None
        elif stripped.startswith("|"):
            if under_rule and ID_PATTERN.fullmatch(cell := _first_cell(line)):
                yield cell, True
        elif not line[0].isspace():
            under_rule = False


def _define(texts: dict[str, str]) -> Definitions:
    live: set[str] = set()
    ids: Counter[str] = Counter()
    for text in texts.values():
        for identifier, is_live in _definitions_of(text):
            ids[identifier] += 1
            if is_live:
                live.add(identifier)
    return Definitions(frozenset(live), ids)


def _head_ids(text: str) -> list[str]:
    """The IDs at the start of a docstring or a title, after an optional `Spec:`."""
    head = HEAD.match(text)
    return ID_PATTERN.findall(head[0]) if head else []


def _python_citations(sources: dict[str, bytes]) -> Iterator[str]:
    for _, tree in clones.python_test_trees(sources):
        for node in ast.walk(tree):
            if clones.is_test_function(node):
                yield from _head_ids(ast.get_docstring(node) or "")


def _script_titles(tokens: list[Token]) -> Iterator[str]:
    """The title of each bare `it(` or `test(` call whose first argument is a string or a template without a hole."""
    for before, name, opening, title in zip([None, *tokens], tokens, tokens[1:], tokens[2:], strict=False):
        is_bare = before is None or before.value != "."
        if is_bare and name.kind is Kind.IDENTIFIER and name.value in SCRIPT_TEST_CALLS and opening.value == "(" and title.kind is Kind.STRING:
            yield title.value


def _is_script_test(path: str) -> bool:
    language = language_of(path)
    return language in SCRIPT_LANGUAGES and language.is_test_file(PurePosixPath(path))


def _script_citations(sources: dict[str, bytes]) -> tuple[list[str], list[str]]:
    """The cited IDs of the TypeScript and JavaScript test files, and the files that cannot be read."""
    cited: list[str] = []
    unread: list[str] = []
    for path in (path for path in sources if _is_script_test(path)):
        try:
            tokens = tokenize(sources[path].decode("utf-8"))
        except (UnicodeDecodeError, ScriptSyntaxError):
            unread.append(path)
        else:
            cited.extend(identifier for title in _script_titles(tokens) for identifier in _head_ids(title))
    return cited, unread


def _read_markdown(root: Path, tracked: list[str]) -> tuple[dict[str, str], list[str]]:
    """The tracked Markdown files the tree holds as text, and those that cannot be read or are not UTF-8."""
    texts: dict[str, str] = {}
    unread: list[str] = []
    for name in (name for name in tracked if PurePosixPath(name).suffix == MARKDOWN_SUFFIX):
        try:
            content = repository.read_bytes(root, name) if repository.is_on_disk(root, name) else None
            if content is not None:
                texts[name] = content.decode("utf-8")
        except (repository.RepositoryFileError, UnicodeDecodeError):
            unread.append(name)
    return texts, unread


def _note_facts(defined: Definitions, cited: set[str], unread: list[str]) -> list[str]:
    uncited = len(defined.live - cited)
    undefined = len(cited - defined.live)
    twice = sum(count > 1 for count in defined.ids.values())
    facts = [
        f"{counted(uncited, 'spec ID')} uncited" if uncited else "",
        f"{counted(undefined, 'cited ID')} undefined or retired" if undefined else "",
        f"{counted(twice, 'spec ID')} defined twice" if twice else "",
        f"{counted(len(unread), 'file')} not read: {', '.join(sorted(unread))}" if unread else "",
    ]
    return [fact for fact in facts if fact]


def check_spec_citations(root: Path, tracked: list[str], sources: dict[str, bytes]) -> RuleResult:
    """The note with the counts, a count of zero left out and no note when all are zero; no finding, no extent.

    A tracked Markdown file that cannot be read or is not UTF-8 and a script test file the lexer refuses are named in the note, so the rule never
    changes an exit code; `sources` that cannot be read refuse the area before this rule runs."""
    texts, unread = _read_markdown(root, tracked)
    script_cited, unread_scripts = _script_citations(sources)
    cited = {*_python_citations(sources), *script_cited}
    facts = _note_facts(_define(texts), cited, [*unread, *unread_scripts])
    notes = (Note(NOTE_PATH, NoteEvent.OPEN, " · ".join(facts)),) if facts else ()
    return RuleResult([], 0, False, notes)
