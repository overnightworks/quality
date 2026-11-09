"""The `claude-md` rule, a tracked `CLAUDE.md` that is exactly `@AGENTS.md` beside a tracked `AGENTS.md`, and the
`agents-md` rule, the title, sections and size of the root `AGENTS.md`."""

from collections.abc import Iterator
from pathlib import Path

from quality import checkout, repository
from quality.report import Finding, FindingEvent
from quality.rules import Rule

CLAUDE_NESTED_NAME = ".claude/claude.md"
CLAUDE_FILE_NAMES = frozenset({"claude.md", CLAUDE_NESTED_NAME})
CLAUDE_IMPORTS = frozenset({b"@AGENTS.md", b"@AGENTS.md\n"})
AGENTS_FILE = "AGENTS.md"
AGENTS_SECTIONS = ("Commands", "Rules", "Owners", "Documents")
AGENTS_MAX_LINES = 100
AGENTS_MAX_BYTES = 4096
FENCE_MARKERS = ("```", "~~~")
type AgentsProblem = tuple[FindingEvent, int, str, str]
SECTION_FIX = f"name it {', '.join(AGENTS_SECTIONS)}, or make it a level 3 heading"


def check_claude_md(root: Path, tracked: list[str]) -> list[Finding]:
    """A tracked CLAUDE.md, in any case, is exactly `@AGENTS.md` beside a tracked AGENTS.md; `.claude/CLAUDE.md` is none.

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
    """The event, the fact and the fix for a CLAUDE.md that is not clean; None when it is clean or gone from the tree."""
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


def check_agents_md(root: Path, tracked: list[str]) -> list[Finding]:
    """The root AGENTS.md has a title, then only the sections of the document set once and in order, within its size.

    The findings carry fixed texts, line numbers and counts, never the file's content.
    """
    text = checkout.judged(repository.read_text, root, AGENTS_FILE) if AGENTS_FILE in tracked else None
    if text is None:
        return []
    lines = text.removesuffix("\n").split("\n") if text else []
    problems = [*_title_problems(lines), *_section_problems(lines[1:]), *_size_problems(len(lines), len(text.encode()))]
    return [Finding(Rule.AGENTS_MD, AGENTS_FILE, line, fact, fix, event=event) for event, line, fact, fix in problems]


def _title_problems(lines: list[str]) -> Iterator[AgentsProblem]:
    if not (lines and lines[0].startswith("# ") and lines[0][2:].strip()):
        yield FindingEvent.TITLE, 1, "line 1 is not a title", "make line 1 `# <repository name>`"


def _section_problems(body: list[str]) -> Iterator[AgentsProblem]:
    """The section problems of the lines after the title, which is line 1; a heading in a fence is no heading."""
    fence = ""
    seen: list[str] = []
    text_reported = False
    for number, line in enumerate(body, start=2):
        in_fence = bool(fence)
        fence = _fence_after(fence, line)
        heading = None if in_fence or fence else _heading(line)
        if heading is None:
            if line.strip() and not (text_reported or seen or line.startswith("# ")):
                text_reported = True
                yield _section(number, "text before the first section", "move it under a section or delete it")
        elif heading not in AGENTS_SECTIONS:
            yield _section(number, "not a section of the shape", SECTION_FIX)
        elif heading in seen:
            yield _section(number, "appears twice", "keep one section of that name and merge the other into it")
        else:
            if seen and AGENTS_SECTIONS.index(heading) < max(AGENTS_SECTIONS.index(earlier) for earlier in seen):
                yield _section(number, "stands out of order", f"order the sections {', '.join(AGENTS_SECTIONS)}")
            seen.append(heading)


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
