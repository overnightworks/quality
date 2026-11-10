"""The `claude-md` rule, a tracked `CLAUDE.md` that is exactly `@AGENTS.md` beside a tracked `AGENTS.md`, and the
`agents-md` rule, the title, sections and size of the root `AGENTS.md`."""

import re
from collections.abc import Iterator
from pathlib import Path, PurePosixPath

from quality import checkout, repository
from quality.report import CannotJudge, Finding, FindingEvent, Instruction
from quality.rules import Rule

CLAUDE_NESTED_NAME = ".claude/claude.md"
CLAUDE_FILE_NAMES = frozenset({"claude.md", CLAUDE_NESTED_NAME})
CLAUDE_IMPORTS = frozenset({b"@AGENTS.md", b"@AGENTS.md\n"})
AGENTS_FILE = "AGENTS.md"
AGENTS_SECTIONS = ("Commands", "Rules", "Owners", "Documents")
AGENTS_MAX_LINES = 100
AGENTS_MAX_BYTES = 4096
FENCE_MARKERS = ("```", "~~~")
FIRST_SPAN = re.compile(r"`([^`\n]+)`")
type AgentsProblem = tuple[FindingEvent, int, str, str]
NO_REMOTE_FACT = "no GitHub remote names this repository"
NO_REMOTE_STEP = Instruction("add the repository's GitHub remote")
NOT_THE_NAME = "the title is not the repository's name"
MISSING_FIX = "point at a tracked document or delete the line"
SECTION_FIX = f"name it {', '.join(AGENTS_SECTIONS)}, or make it a level 3 heading"


def check_claude_md(root: Path, tracked: list[str]) -> list[Finding]:
    """A tracked CLAUDE.md, in any case, is exactly `@AGENTS.md` beside a tracked AGENTS.md.

    `.claude/CLAUDE.md` is none.

    The findings carry fixed texts, never the file's content.
    """
    findings = []
    for name in (name for name in tracked if name.casefold() in CLAUDE_FILE_NAMES):
        problem = _claude_md_problem(root, name, AGENTS_FILE in tracked)
        if problem is not None:
            event, fact, fix = problem
            findings.append(Finding(Rule.CLAUDE_MD, name, None, fact, fix, event=event))
    return findings


def _claude_md_problem(root: Path, name: str, agents_tracked: bool) -> tuple[FindingEvent, str, str] | None:
    """The event, the fact and the fix for a CLAUDE.md that is not clean; None when clean or gone from the tree."""
    is_link = checkout.judged(repository.is_symlink, root, name)
    if name.casefold() == CLAUDE_NESTED_NAME:
        if not (is_link or checkout.judged(repository.is_on_disk, root, name)):
            return None
        return (FindingEvent.CLAUDE_ONLY, "guidance Codex and Grok never read",
                f"move its lines into {AGENTS_FILE} and delete it")
    if is_link:
        return (FindingEvent.SYMLINK, "not a file holding @AGENTS.md",
                f"replace the link with a file whose only line is @AGENTS.md; {AGENTS_FILE} stays as it is")
    on_disk = checkout.judged(repository.is_on_disk, root, name)
    content = checkout.judged(repository.read_bytes, root, name) if on_disk else None
    if content is None:
        return None
    if content not in CLAUDE_IMPORTS:
        return (FindingEvent.DIVERGES, "not exactly @AGENTS.md, so Claude reads other guidance",
                f"move its own lines into {AGENTS_FILE}, then leave @AGENTS.md as its only line, or delete it")
    if not (agents_tracked and checkout.judged(repository.is_on_disk, root, AGENTS_FILE)):
        return (FindingEvent.NO_AGENTS_FILE, f"imports {AGENTS_FILE}, which the repository does not hold",
                f"add {AGENTS_FILE} with the repository's guidance, or delete this file")
    return None


def check_agents_md(root: Path, tracked: list[str], repositories: list[str]) -> list[Finding]:
    """The root AGENTS.md has a title that is the name of one of the GitHub `repositories` (`owner/name`), then only
    the sections of the document set once and in order, within its size.

    Each line of its Documents section names a tracked file or a directory holding one.
    No repository means no judgment at all, once the file is there to judge.
    The findings carry fixed texts, line numbers, counts, the path of a document line and the name of a repository,
    never other content.
    """
    text = checkout.judged(repository.read_text, root, AGENTS_FILE) if AGENTS_FILE in tracked else None
    if text is None:
        return []
    if not repositories:
        raise CannotJudge(NO_REMOTE_FACT, (NO_REMOTE_STEP,))
    lines = text.removesuffix("\n").split("\n") if text else []
    problems = [
        *_title_problems(lines, [name.partition("/")[2] for name in repositories]),
        *_section_problems(lines[1:]),
        *_document_problems(lines[1:], tracked),
        *_size_problems(len(lines), len(text.encode())),
    ]
    return [Finding(Rule.AGENTS_MD, AGENTS_FILE, line, fact, fix, event=event) for event, line, fact, fix in problems]


def _title_problems(lines: list[str], names: list[str]) -> Iterator[AgentsProblem]:
    """The `title` problem of a line 1 that is no title, or that is none of the repository `names`, case ignored."""
    if not (lines and lines[0].startswith("# ") and lines[0][2:].strip()):
        yield FindingEvent.TITLE, 1, "line 1 is not a title", "make line 1 `# <repository name>`"
    elif lines[0][2:].strip().casefold() not in {name.casefold() for name in names}:
        expected = " or ".join(f"`# {name}`" for name in dict.fromkeys(names))
        yield FindingEvent.TITLE, 1, NOT_THE_NAME, f"make line 1 {expected}"


def _section_problems(body: list[str]) -> Iterator[AgentsProblem]:
    """The section problems of the lines after the title, which is line 1; a heading in a fence is no heading."""
    seen: list[str] = []
    text_reported = False
    past_heading = False
    for number, line, heading, _ in _scan(body):
        past_heading = past_heading or heading is not None
        if heading is None:
            if line.strip() and not (text_reported or past_heading):
                text_reported = True
                yield _section(number, "text before the first section", "move it under a section or delete it")
        elif problem := _heading_problem(heading, number, seen):
            yield problem


def _scan(body: list[str]) -> Iterator[tuple[int, str, str | None, bool]]:
    """The number (the title is line 1), text, heading and fence flag of each line after the title.

    A line in a fence, its marker lines included, has no heading.
    """
    fence = ""
    for number, line in enumerate(body, start=2):
        in_fence = bool(fence)
        fence = _fence_after(fence, line)
        fenced = in_fence or bool(fence)
        yield number, line, None if fenced else _heading(line), fenced


def _document_problems(body: list[str], tracked: list[str]) -> Iterator[AgentsProblem]:
    """A `missing` problem for each document line whose first span is no tracked file or directory with one."""
    names = set(tracked)
    directories = {str(parent) for name in names for parent in PurePosixPath(name).parents[:-1]}
    for number, line in _document_lines(body):
        if (span := FIRST_SPAN.search(line)) and not _is_tracked_path(span[1], names, directories):
            yield FindingEvent.MISSING, number, f"{span[1]} is not tracked", MISSING_FIX


def _document_lines(body: list[str]) -> Iterator[tuple[int, str]]:
    """The number and text of the lines in a `## Documents` section, outside a fence."""
    in_documents = False
    for number, line, heading, fenced in _scan(body):
        if heading is not None:
            in_documents = heading == "Documents"
        elif in_documents and not fenced:
            yield number, line


def _is_tracked_path(path: str, names: set[str], directories: set[str]) -> bool:
    """A tracked file, or a directory with a tracked file below it, in exact case; a directory may end in `/`."""
    return path in names or path.removesuffix("/") in directories


def _heading_problem(heading: str, number: int, seen: list[str]) -> AgentsProblem | None:
    """The problem of a heading among the section headings `seen` before it; a section of the shape joins `seen`."""
    if heading not in AGENTS_SECTIONS:
        return _section(number, "not a section of the shape", SECTION_FIX)
    if heading in seen:
        return _section(number, "appears twice", "keep one section of that name and merge the other into it")
    out_of_order = any(AGENTS_SECTIONS.index(heading) < AGENTS_SECTIONS.index(earlier) for earlier in seen)
    seen.append(heading)
    if out_of_order:
        return _section(number, "stands out of order", f"order the sections {', '.join(AGENTS_SECTIONS)}")
    return None


def _section(number: int, fact: str, fix: str) -> AgentsProblem:
    return FindingEvent.SECTION, number, fact, fix


def _heading(line: str) -> str | None:
    """The text of a `# ` or `## ` heading line, "" for a second title, None for any other line."""
    if line.startswith("## "):
        return line[3:]
    return "" if line.startswith("# ") else None


def _fence_after(fence: str, line: str) -> str:
    """The marker of the fence still open after `line`, "" for none; `fence` is the marker open before it."""
    stripped = line.strip()
    if fence:
        return "" if stripped.startswith(fence) and not stripped.strip(fence[0]) else fence
    if stripped.startswith(FENCE_MARKERS):
        return stripped[: len(stripped) - len(stripped.lstrip(stripped[0]))]
    return ""


def _size_problems(line_count: int, byte_count: int) -> Iterator[AgentsProblem]:
    fix = "cut it; the document set says where removed text goes"
    if line_count > AGENTS_MAX_LINES:
        yield FindingEvent.TOO_LONG, 1, f"{line_count} lines, the limit is {AGENTS_MAX_LINES}", fix
    if byte_count > AGENTS_MAX_BYTES:
        yield FindingEvent.TOO_LONG, 1, f"{byte_count} bytes, the limit is {AGENTS_MAX_BYTES}", fix
