"""The `claude-md` rule: a tracked `CLAUDE.md` is exactly `@AGENTS.md` beside a tracked `AGENTS.md`."""

from pathlib import Path

from quality import checkout, repository
from quality.report import Finding
from quality.rules import Rule

CLAUDE_NESTED_NAME = ".claude/claude.md"
CLAUDE_FILE_NAMES = frozenset({"claude.md", CLAUDE_NESTED_NAME})
CLAUDE_IMPORTS = frozenset({b"@AGENTS.md", b"@AGENTS.md\n"})
AGENTS_FILE = "AGENTS.md"


def check_claude_md(root: Path, tracked: list[str]) -> list[Finding]:
    """A tracked CLAUDE.md, in any case, is exactly `@AGENTS.md` beside a tracked AGENTS.md; `.claude/CLAUDE.md` is none.

    The findings carry fixed texts and the file's name, never its content.
    """
    findings = []
    for name in (name for name in tracked if name.casefold() in CLAUDE_FILE_NAMES):
        problem = _claude_md_problem(root, name, AGENTS_FILE in tracked)
        if problem is not None:
            findings.append(Finding(Rule.CLAUDE_MD, name, None, *problem))
    return findings


def _claude_md_problem(root: Path, name: str, agents_tracked: bool) -> tuple[str, str] | None:
    """The reason and the fix for a CLAUDE.md that is not clean; None when it is clean or gone from the tree."""
    is_link = checkout.judged(repository.is_symlink, root, name)
    if name.casefold() == CLAUDE_NESTED_NAME:
        if not (is_link or checkout.judged(repository.is_on_disk, root, name)):
            return None
        return (f"{name} gives Claude guidance Codex and Grok never read.",
                f"move its lines into {AGENTS_FILE} and delete {name}.")
    if is_link:
        return (f"{name} is a symlink, not a file holding @AGENTS.md.",
                f"replace the link with a file whose only line is @AGENTS.md; {AGENTS_FILE} stays as it is.")
    on_disk = checkout.judged(repository.is_on_disk, root, name)
    content = checkout.judged(repository.read_bytes, root, name) if on_disk else None
    if content is None:
        return None
    if content not in CLAUDE_IMPORTS:
        return (f"{name} is not exactly @AGENTS.md, so Claude reads other guidance than Codex and Grok.",
                f"move its own lines into {AGENTS_FILE}, then leave @AGENTS.md as its only line, or delete it.")
    if not (agents_tracked and checkout.judged(repository.is_on_disk, root, AGENTS_FILE)):
        return (f"{name} imports {AGENTS_FILE}, which the repository does not hold.",
                f"add {AGENTS_FILE} with the repository's guidance, or delete {name}.")
    return None
