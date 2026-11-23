"""The `spec-citation` rule: spec IDs no test cites, cited IDs no spec defines, and IDs defined twice. Without a
`[spec_citations]` table in the baseline the rule only reports, as one `open` note under the debt area; with it, a new uncited
ID, an undefined or retired citation and a duplicate definition are findings, and the table holds the uncited IDs of the day
it was recorded.

A Markdown list item that opens with `ID:` defines a live ID and `ID (retired).` a retired one; the first cell of a table row
under such an item defines one too. A test cites the IDs at the head of its Python docstring or of its `it(` and `test(` title.
"""

import ast
import re
from collections.abc import Collection, Iterator
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import NamedTuple

from quality import baseline_file, clones, repository
from quality.markdown_fences import live_line_indexes
from quality.output import SEPARATOR, counted
from quality.ratchet import Language, language_of
from quality.report import CannotJudge, Finding, FindingEvent, Note, NoteEvent, RuleResult
from quality.rules import Rule
from quality.typescript_tokens import Kind, ScriptSyntaxError, Token, tokenize

NOTE_PATH = "."
MARKDOWN_SUFFIX = ".md"
BYTE_ORDER_MARK = "\ufeff"
ID = r"[A-Z][A-Z0-9]*(?:-[A-Z][A-Z0-9]*)*-(?a:\d+)"
ID_PATTERN = re.compile(ID)
DEFINING_ITEM = re.compile(rf"\s*(?:[-*+]|(?a:\d+)[.)])\s+(?P<id>{ID})(?::|(?P<retired> \(retired\)\.))")
HEAD = re.compile(rf"\s*(?:Spec:\s*)?{ID}(?:\s*(?:,(?:\s*and\b)?|\band\b)\s*{ID})*")
SCRIPT_TEST_CALLS = frozenset({"it", "test"})
SCRIPT_LANGUAGES = frozenset({Language.TYPESCRIPT, Language.JAVASCRIPT})


class Site(NamedTuple):
    """A line of a file: where an ID is defined or a test cites it."""

    path: str
    line: int

    def __str__(self) -> str:
        return f"{self.path}:{self.line}"


class Definition(NamedTuple):
    site: Site
    live: bool


@dataclass(frozen=True)
class Tree:
    """What a repository's Markdown files define and its tests cite, in file and line order, and the files that could not
    be read; a retired ID is defined too."""

    definitions: dict[str, list[Definition]]
    citations: dict[str, list[Site]]
    unread: list[str]

    @property
    def live(self) -> frozenset[str]:
        return frozenset(identifier for identifier, defined in self.definitions.items() if any(each.live for each in defined))

    @property
    def uncited(self) -> frozenset[str]:
        """The live IDs no test cites."""
        return self.live - self.citations.keys()

    @property
    def undefined_or_retired(self) -> frozenset[str]:
        """The cited IDs that are not live."""
        return self.citations.keys() - self.live

    @property
    def repeated(self) -> list[str]:
        """The IDs defined more than once."""
        return sorted(identifier for identifier, defined in self.definitions.items() if len(defined) > 1)

    def recordable(self) -> frozenset[str] | None:
        """The IDs `--fix` records as the table: the uncited ones of a tree that defines a live ID, cites none undefined or
        retired, defines none twice and has no unread file; None for any other tree."""
        clean = bool(self.live) and not (self.undefined_or_retired or self.repeated or self.unread)
        return self.uncited if clean else None

    def fall_reason(self, identifier: str) -> str | None:
        """Why a held ID no longer needs its entry, None while it does."""
        if identifier in self.uncited:
            return None
        if identifier in self.citations and identifier in self.live:
            return f"spec ID {identifier} is cited"
        if identifier in self.definitions and identifier not in self.live:
            return f"spec ID {identifier} is retired"
        return f"spec ID {identifier} is no longer defined"


def _first_cell(line: str) -> str:
    return line.strip().removeprefix("|").split("|", 1)[0].strip()


def _definitions_of(text: str) -> Iterator[tuple[str, int, bool]]:
    """Each (ID, line, live) a Markdown text defines: a list item as the rules say, a table row while a rule's table is open."""
    under_rule = False
    lines = text.removeprefix(BYTE_ORDER_MARK).splitlines()
    for index in live_line_indexes(lines):
        line = lines[index]
        stripped = line.strip()
        if not stripped:
            continue
        if item := DEFINING_ITEM.match(line):
            under_rule = True
            yield item["id"], index + 1, item["retired"] is None
        elif stripped.startswith("|"):
            if under_rule and ID_PATTERN.fullmatch(cell := _first_cell(line)):
                yield cell, index + 1, True
        elif not line[0].isspace():
            under_rule = False


def _define(texts: dict[str, str]) -> dict[str, list[Definition]]:
    definitions: dict[str, list[Definition]] = {}
    for path, text in texts.items():
        for identifier, line, is_live in _definitions_of(text):
            definitions.setdefault(identifier, []).append(Definition(Site(path, line), is_live))
    return definitions


def _head_ids(text: str) -> list[str]:
    """The IDs at the start of a docstring or a title, after an optional `Spec:`."""
    head = HEAD.match(text)
    return ID_PATTERN.findall(head[0]) if head else []


def _python_citations(sources: dict[str, bytes]) -> Iterator[tuple[str, Site]]:
    for path, tree in clones.python_test_trees(sources):
        for node in ast.walk(tree):
            if clones.is_test_function(node):
                yield from ((identifier, Site(path, node.lineno)) for identifier in _head_ids(ast.get_docstring(node) or ""))


def _script_titles(tokens: list[Token]) -> Iterator[Token]:
    """The title of each bare `it(` or `test(` call whose first argument is a string or a template without a hole."""
    for before, name, opening, title in zip([None, *tokens], tokens, tokens[1:], tokens[2:], strict=False):
        is_bare = before is None or before.value != "."
        if is_bare and name.kind is Kind.IDENTIFIER and name.value in SCRIPT_TEST_CALLS and opening.value == "(" and title.kind is Kind.STRING:
            yield title


def _is_script_test(path: str) -> bool:
    language = language_of(path)
    return language in SCRIPT_LANGUAGES and language.is_test_file(PurePosixPath(path))


def _script_citations(sources: dict[str, bytes]) -> tuple[list[tuple[str, Site]], list[str]]:
    """The citations of the TypeScript and JavaScript test files, and the files that cannot be read."""
    cited: list[tuple[str, Site]] = []
    unread: list[str] = []
    for path in (path for path in sources if _is_script_test(path)):
        try:
            tokens = tokenize(sources[path].decode("utf-8"))
        except (UnicodeDecodeError, ScriptSyntaxError):
            unread.append(path)
        else:
            cited.extend((identifier, Site(path, title.line)) for title in _script_titles(tokens) for identifier in _head_ids(title.value))
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


def read_tree(root: Path, tracked: list[str], sources: dict[str, bytes]) -> Tree:
    """The definitions and citations of the tree; a file that cannot be read is named, never guessed."""
    texts, unread = _read_markdown(root, tracked)
    script_cited, unread_scripts = _script_citations(sources)
    citations: dict[str, list[Site]] = {}
    for identifier, site in sorted({*_python_citations(sources), *script_cited}, key=lambda each: (each[1], each[0])):
        citations.setdefault(identifier, []).append(site)
    return Tree(_define(texts), citations, [*unread, *unread_scripts])


def _unread_fact(unread: list[str]) -> str:
    return f"{counted(len(unread), 'file')} not read: {', '.join(sorted(unread))}"


def require_read(tree: Tree) -> None:
    """With the table every file can hide a definition or a citation, so an unread one is not judged around."""
    if tree.unread:
        raise CannotJudge(f"spec IDs cannot be judged, {_unread_fact(tree.unread)}")


def _note(tree: Tree) -> tuple[Note, ...]:
    facts = [
        f"{counted(len(tree.uncited), 'spec ID')} uncited" if tree.uncited else "",
        f"{counted(len(tree.undefined_or_retired), 'cited ID')} undefined or retired" if tree.undefined_or_retired else "",
        f"{counted(len(tree.repeated), 'spec ID')} defined twice" if tree.repeated else "",
        _unread_fact(tree.unread) if tree.unread else "",
    ]
    return (Note(NOTE_PATH, NoteEvent.OPEN, SEPARATOR.join(fact for fact in facts if fact)),) if any(facts) else ()


def _finding(site: Site | None, event: FindingEvent, fact: str, fix: str) -> Finding:
    path, line = (baseline_file.BASELINE_PATH, None) if site is None else site
    return Finding(Rule.SPEC_CITATION, path, line, fact, fix, event=event)


def _citation_finding(identifier: str, site: Site, defined: Collection[str]) -> Finding:
    if identifier in defined:
        return _finding(site, FindingEvent.RETIRED, f"{identifier} is cited and retired", "cite a live ID")
    return _finding(site, FindingEvent.UNDEFINED, f"{identifier} is cited and defined nowhere", "define it in a spec, or cite another ID")


def _findings(tree: Tree, held: frozenset[str]) -> list[Finding]:
    """The new uncited IDs, the undefined and retired citations, the later definitions of an ID and the held IDs that can fall."""
    uncited = [
        _finding(tree.definitions[identifier][0].site, FindingEvent.UNCITED, f"spec ID {identifier} is cited by no test", "cite it at the head of a test")
        for identifier in sorted(tree.uncited - held, key=lambda each: tree.definitions[each][0].site)
    ]
    cited = [
        _citation_finding(identifier, site, tree.definitions.keys())
        for identifier in tree.undefined_or_retired
        for site in tree.citations[identifier]
    ]
    twice = [
        _finding(later.site, FindingEvent.DUPLICATE, f"spec ID {identifier} is defined again, first at {first.site}", "define each spec ID once")
        for identifier in tree.repeated
        for first, *later_ones in [tree.definitions[identifier]]
        for later in later_ones
    ]
    can_fall = [
        _finding(None, FindingEvent.CAN_FALL, reason, baseline_file.FIX_COMMAND)
        for identifier in sorted(held)
        if (reason := tree.fall_reason(identifier))
    ]
    return [*uncited, *sorted(cited, key=lambda each: (each.path, each.line)), *twice, *can_fall]


def check_spec_citations(root: Path, tracked: list[str], sources: dict[str, bytes], baseline: baseline_file.Baseline | None) -> RuleResult:
    """Without a `[spec_citations]` table the note with the counts, a count of zero left out and no note when all are zero;
    no finding, no extent. With it the findings, an extent of the held IDs and no note.

    A tracked Markdown file that cannot be read or is not UTF-8 and a script test file the lexer refuses are named in the note,
    so the report-only rule never changes an exit code; with the table they make the rule cannot judge. `sources` that cannot
    be read refuse the area before this rule runs."""
    tree = read_tree(root, tracked, sources)
    held = None if baseline is None else baseline.spec_citations
    if held is None:
        return RuleResult([], 0, False, _note(tree))
    require_read(tree)
    return RuleResult(_findings(tree, held), len(held), False)
