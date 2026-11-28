"""The `spec-citation` rule: spec IDs no test cites, cited IDs no spec defines, and IDs defined twice. Without a
`[spec_citations]` table in the baseline the rule only reports, as one `open` note under the debt area; with it, a new uncited
ID, an undefined or retired citation and a duplicate definition are findings, and the table holds the uncited IDs of the day
it was recorded.

`quality.spec_definitions` reads the IDs the Markdown defines. A test cites the IDs at the head of its Python docstring or of
its `it(` and `test(` title.

Two declarations of `quality/config.toml` leave IDs to another repository's judgement: a foreign prefix exempts the citations of
IDs the tree defines nowhere, and an implemented document exempts its own IDs from needing a citation here.
"""

import ast
import re
from collections.abc import Collection, Iterator
from dataclasses import dataclass

from quality import baseline_file, clones, suites
from quality.config import FOREIGN_PREFIX_HEADING, IMPLEMENTED_DOCUMENT_HEADING, AllowLists
from quality.config_entries import CONFIG_PATH, delete_entry_fix
from quality.output import SEPARATOR, counted
from quality.report import CannotJudge, Finding, FindingEvent, Note, NoteEvent, RuleResult
from quality.rules import Rule
from quality.spec_definitions import ID, ID_PATTERN, Definition, Markdown, Site

NOTE_PATH = "."
HEAD = re.compile(rf"\s*(?:Spec:\s*)?{ID}(?:\s*(?:,(?:\s*and\b)?|\band\b)\s*{ID})*")


def prefix_of(identifier: str) -> str:
    """An ID's part before its last number."""
    return identifier.rsplit("-", 1)[0]


@dataclass(frozen=True)
class Tree:
    """What a repository's Markdown files define and its tests cite, in file and line order, and the files that could not
    be read; a retired ID is defined too. `foreign` holds the prefixes and `implemented` the document paths the config
    declares."""

    definitions: dict[str, list[Definition]]
    citations: dict[str, list[Site]]
    unread: list[str]
    foreign: frozenset[str] = frozenset()
    implemented: frozenset[str] = frozenset()

    @property
    def live(self) -> frozenset[str]:
        return frozenset(identifier for identifier, defined in self.definitions.items() if any(each.live for each in defined))

    @property
    def prefix_sites(self) -> dict[str, Site]:
        """Each prefix the tree defines, live or retired, with its earliest definition."""
        sites: dict[str, Site] = {}
        for identifier, defined in self.definitions.items():
            earliest = min(each.site for each in defined)
            sites[prefix_of(identifier)] = min(earliest, sites.get(prefix_of(identifier), earliest))
        return sites

    @property
    def foreign_citations(self) -> frozenset[str]:
        """The cited IDs of a declared prefix the tree defines nowhere; their owner judges them, so a declaration the tree
        itself contradicts exempts nothing."""
        exempt = self.foreign - self.prefix_sites.keys()
        return frozenset(identifier for identifier in self.citations if prefix_of(identifier) in exempt)

    @property
    def left_to_implementers(self) -> frozenset[str]:
        """The live IDs whose first definition stands in a declared document."""
        return frozenset(identifier for identifier in self.live if self.definitions[identifier][0].site.path in self.implemented)

    @property
    def uncited(self) -> frozenset[str]:
        """The live IDs no test cites and no declared document leaves to its implementers."""
        return self.live - self.citations.keys() - self.left_to_implementers

    @property
    def undefined_or_retired(self) -> frozenset[str]:
        """The cited IDs that are not live and no owner judges."""
        return self.citations.keys() - self.live - self.foreign_citations

    @property
    def conflicting_prefixes(self) -> dict[str, Site]:
        """The declared prefixes the tree defines, each with its earliest definition."""
        return {prefix: site for prefix, site in sorted(self.prefix_sites.items()) if prefix in self.foreign}

    @property
    def unused_prefixes(self) -> list[str]:
        """The declared prefixes the tree does not define that no citation carries."""
        cited = {prefix_of(identifier) for identifier in self.citations}
        return sorted(self.foreign - self.prefix_sites.keys() - cited)

    @property
    def unused_documents(self) -> list[str]:
        """The declared documents that hold no live ID's first definition."""
        holding = {self.definitions[identifier][0].site.path for identifier in self.live}
        return sorted(self.implemented - holding)

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
        if identifier in self.left_to_implementers:
            return f"spec ID {identifier} is left to its implementers"
        return f"spec ID {identifier} is no longer defined"


def _head_ids(text: str) -> list[str]:
    """The IDs at the start of a docstring or a title, after an optional `Spec:`."""
    head = HEAD.match(text)
    return ID_PATTERN.findall(head[0]) if head else []


def _python_citations(sources: dict[str, bytes]) -> Iterator[tuple[str, Site]]:
    for path, tree in clones.python_test_trees(sources):
        for node in ast.walk(tree):
            if clones.is_test_function(node):
                yield from ((identifier, Site(path, node.lineno)) for identifier in _head_ids(ast.get_docstring(node) or ""))


def _script_citations(tracked: list[str], sources: dict[str, bytes]) -> tuple[list[tuple[str, Site]], list[str]]:
    """The citations of the tracked TypeScript and JavaScript test files, and the files that are absent from the tree,
    not UTF-8 or refused by the lexer."""
    read, unread = suites.read_script_tests(tracked, sources)
    cited = [(identifier, Site(path, title.line)) for path, tokens in read.items() for title in suites.script_titles(tokens) for identifier in _head_ids(title.value)]
    return cited, unread


def read_tree(markdown: Markdown, tracked: list[str], sources: dict[str, bytes], declared: AllowLists) -> Tree:
    """The definitions and citations of the tree under the config's declarations; a file that cannot be read is named,
    never guessed."""
    script_cited, unread_scripts = _script_citations(tracked, sources)
    citations: dict[str, list[Site]] = {}
    for identifier, site in sorted({*_python_citations(sources), *script_cited}, key=lambda each: (each[1], each[0])):
        citations.setdefault(identifier, []).append(site)
    return Tree(
        markdown.definitions, citations, [*markdown.unread, *unread_scripts],
        frozenset(foreign.prefix for foreign in declared.foreign_prefixes),
        frozenset(document.path for document in declared.implemented_documents),
    )


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


def _finding(site: tuple[str, int | None], event: FindingEvent, fact: str, fix: str) -> Finding:
    path, line = site
    return Finding(Rule.SPEC_CITATION, path, line, fact, fix, event=event)


def _citation_finding(identifier: str, site: Site, defined: Collection[str]) -> Finding:
    if identifier in defined:
        return _finding(site, FindingEvent.RETIRED, f"{identifier} is cited and retired", "cite a live ID")
    return _finding(site, FindingEvent.UNDEFINED, f"{identifier} is cited and defined nowhere", "define it in a spec, or cite another ID")


def _declaration_findings(tree: Tree) -> list[Finding]:
    """The declared prefixes the tree defines itself or no citation carries, and the declared documents that define no live ID."""
    conflicts = [
        _finding(
            (CONFIG_PATH, None), FindingEvent.CONFLICT, f"{FOREIGN_PREFIX_HEADING} prefix {prefix} is defined here, first at {site}",
            f"{delete_entry_fix(FOREIGN_PREFIX_HEADING)}, or give the local IDs a prefix of their own",
        )
        for prefix, site in tree.conflicting_prefixes.items()
    ]
    unused_prefixes = [
        _finding((CONFIG_PATH, None), FindingEvent.UNUSED, f"{FOREIGN_PREFIX_HEADING} prefix {prefix} matches no citation", delete_entry_fix(FOREIGN_PREFIX_HEADING))
        for prefix in tree.unused_prefixes
    ]
    unused_documents = [
        _finding(
            (path, None), FindingEvent.UNUSED, f"{IMPLEMENTED_DOCUMENT_HEADING} matches no tracked file that defines a live spec ID",
            delete_entry_fix(IMPLEMENTED_DOCUMENT_HEADING),
        )
        for path in tree.unused_documents
    ]
    return [*conflicts, *unused_prefixes, *unused_documents]


def _findings(tree: Tree, held: frozenset[str]) -> list[Finding]:
    """The new uncited IDs, the undefined and retired citations, the later definitions of an ID, the held IDs that can fall
    and the declarations that conflict or match nothing."""
    uncited = [
        _finding(tree.definitions[identifier][0].site, FindingEvent.UNCITED, f"spec ID {identifier} is cited by no test", "cite it at the head of a test")
        for identifier in sorted(tree.uncited - held, key=lambda each: tree.definitions[each][0].site)
    ]
    cited = [
        _citation_finding(identifier, site, tree.definitions.keys())
        for identifier in sorted(tree.undefined_or_retired)
        for site in tree.citations[identifier]
    ]
    twice = [
        _finding(
            later.site, FindingEvent.DUPLICATE,
            f"spec ID {identifier} is defined again, first at {tree.definitions[identifier][0].site}", "define each spec ID once",
        )
        for identifier in tree.repeated
        for later in tree.definitions[identifier][1:]
    ]
    can_fall = [
        _finding((baseline_file.BASELINE_PATH, None), FindingEvent.CAN_FALL, reason, baseline_file.FIX_COMMAND)
        for identifier in sorted(held)
        if (reason := tree.fall_reason(identifier))
    ]
    return [*uncited, *sorted(cited, key=lambda each: (each.path, each.line)), *twice, *can_fall, *_declaration_findings(tree)]


def check_spec_citations(
    markdown: Markdown, tracked: list[str], sources: dict[str, bytes], baseline: baseline_file.Baseline | None, declared: AllowLists
) -> RuleResult:
    """Without a `[spec_citations]` table the note with the counts, a count of zero left out and no note when all are zero;
    no finding, no extent. With it the findings, an extent of the held IDs and no note.

    A tracked Markdown file that cannot be read or is not UTF-8 and a script test file the lexer refuses are named in the note,
    so the report-only rule never changes an exit code; with the table they make the rule cannot judge. `sources` that cannot
    be read refuse the area before this rule runs."""
    tree = read_tree(markdown, tracked, sources, declared)
    held = None if baseline is None else baseline.spec_citations
    if held is None:
        return RuleResult([], 0, False, _note(tree))
    require_read(tree)
    return RuleResult(_findings(tree, held), len(held), False)
