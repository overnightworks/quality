import os
import subprocess
from pathlib import Path

import pytest

from tests.support.check import hygiene_output as run
from tests.support.check import is_clean, refusal_row, refusals

ENV_FINDING = "env-file .env - "


GUIDANCE = {"AGENTS.md": "# guidance\n"}
IMPORT = "@AGENTS.md\n"
MISMATCH_REASON = "{name} is not exactly @AGENTS.md, so Claude reads other guidance than Codex and Grok."
MISMATCH_FIX = "move its own lines into AGENTS.md, then leave @AGENTS.md as its only line, or delete it."
NESTED_REASON = ".claude/CLAUDE.md gives Claude guidance Codex and Grok never read."
NESTED_FIX = "move its lines into AGENTS.md and delete .claude/CLAUDE.md."
LINK_REASON = "CLAUDE.md is a symlink, not a file holding @AGENTS.md."
LINK_FIX = "replace the link with a file whose only line is @AGENTS.md; AGENTS.md stays as it is."
MISSING_IMPORT_REASON = "CLAUDE.md imports AGENTS.md, which the repository does not hold."
MISSING_IMPORT_FIX = "add AGENTS.md with the repository's guidance, or delete CLAUDE.md."


def claude_repo(build_repo, tracked: dict[str, str], links: dict[str, str] | None = None) -> Path:
    """A repository tracking `tracked` and, as symbolic links, `links` (name to target)."""
    repo = build_repo(tracked)
    for name, target in (links or {}).items():
        (repo / name).parent.mkdir(exist_ok=True)
        (repo / name).symlink_to(target)
        subprocess.run(["git", "-C", str(repo), "add", "--", name], check=True)
    return repo


@pytest.mark.parametrize(
    ("tracked", "links"),
    [
        (GUIDANCE, {}),
        ({}, {}),
        ({**GUIDANCE, "CLAUDE.md": IMPORT}, {}),
        ({**GUIDANCE, "CLAUDE.md": "@AGENTS.md"}, {}),
        ({**GUIDANCE, "docs/CLAUDE.md": "own rules\n"}, {}),
    ],
    ids=[
        "#95 E3 cell 1 no CLAUDE.md beside AGENTS.md",
        "#95 E16 cell 2 neither file",
        "#95 E11 cell 3 exact import with newline",
        "#95 E11 cell 4 exact import without newline",
        "#95 E5 cell 13 nested CLAUDE.md is not judged",
    ],
)
def test_claude_md_that_is_absent_or_exactly_the_import_is_clean(build_repo, capsys, tracked, links):
    code, output = run(capsys, claude_repo(build_repo, tracked, links))
    assert (code, is_clean(output)) == (0, True), output


@pytest.mark.parametrize(
    ("tracked", "links", "name", "reason", "fix"),
    [
        ({**GUIDANCE, "CLAUDE.md": "@AGENTS.md\r\n"}, {}, "CLAUDE.md", MISMATCH_REASON, MISMATCH_FIX),
        ({**GUIDANCE, "CLAUDE.md": ""}, {}, "CLAUDE.md", MISMATCH_REASON, MISMATCH_FIX),
        ({**GUIDANCE, "CLAUDE.md": " @AGENTS.md\n"}, {}, "CLAUDE.md", MISMATCH_REASON, MISMATCH_FIX),
        ({**GUIDANCE, "CLAUDE.md": "@AGENTS.md\n\n"}, {}, "CLAUDE.md", MISMATCH_REASON, MISMATCH_FIX),
        ({**GUIDANCE, "CLAUDE.md": "﻿@AGENTS.md\n"}, {}, "CLAUDE.md", MISMATCH_REASON, MISMATCH_FIX),
        ({**GUIDANCE, "CLAUDE.md": "@AGENTS.md\nuse pnpm\n"}, {}, "CLAUDE.md", MISMATCH_REASON, MISMATCH_FIX),
        ({**GUIDANCE, "claude.md": "own rules\n"}, {}, "claude.md", MISMATCH_REASON, MISMATCH_FIX),
        ({**GUIDANCE, ".claude/CLAUDE.md": IMPORT}, {}, ".claude/CLAUDE.md", NESTED_REASON, NESTED_FIX),
        (GUIDANCE, {"CLAUDE.md": "AGENTS.md"}, "CLAUDE.md", LINK_REASON, LINK_FIX),
        (GUIDANCE, {"CLAUDE.md": "missing.md"}, "CLAUDE.md", LINK_REASON, LINK_FIX),
        ({"CLAUDE.md": IMPORT}, {}, "CLAUDE.md", MISSING_IMPORT_REASON, MISSING_IMPORT_FIX),
        ({"CLAUDE.md": IMPORT, "agents.md": "x\n"}, {}, "CLAUDE.md", MISSING_IMPORT_REASON, MISSING_IMPORT_FIX),
    ],
    ids=[
        "#95 E12 cell 5 windows line ending",
        "#95 E13 cell 6 empty",
        "#95 E13 cell 6 leading space",
        "#95 E13 cell 6 blank line after",
        "#95 E13 cell 6 byte order mark",
        "#95 E13 cell 6 second line",
        "#95 E7 cell 11 lowercase name is named as it is",
        "#95 E6 cell 12 .claude/CLAUDE.md even holding the import",
        "#95 E8 cell 9 symlink to AGENTS.md",
        "#95 E8 cell 10 dangling symlink",
        "#95 E4 cell 16 AGENTS.md absent",
        "#95 E4 cell 16 only agents.md",
    ],
)
def test_claude_md_that_is_not_the_import_is_one_finding_naming_the_file(build_repo, capsys, tracked, links, name, reason, fix):
    """#95 E1/E17/E20: the reason and the fix are fixed prose, and the fix is never a command."""
    code, output = run(capsys, claude_repo(build_repo, tracked, links))
    assert code == 1, output
    assert f"claude-md {name} - {reason.format(name=name)}\n  Fix: {fix}\n" in output
    assert output.count("claude-md ") == 1


def test_claude_md_finding_never_prints_the_files_content(build_repo, capsys):
    """#95 E18 cell 7 and E13 cell 8: a token-like string or bytes that are not UTF-8 stay out of the output."""
    repo = build_repo({**GUIDANCE, "CLAUDE.md": "token = hunter2-secret-value\n"})
    (repo / "claude.md").write_bytes(b"\xff\xfe secret-bytes\n")
    subprocess.run(["git", "-C", str(repo), "add", "--", "claude.md"], check=True)
    code, output = run(capsys, repo)
    assert (code, output.count("claude-md "), "hunter2" in output, "secret-bytes" in output) == (1, 2, False, False), output


def test_claude_md_two_wrong_files_are_two_findings(build_repo, capsys):
    """#95 E22 cell 20: one finding per wrong file."""
    repo = build_repo({**GUIDANCE, "CLAUDE.md": "own\n", ".claude/CLAUDE.md": "own\n"})
    code, output = run(capsys, repo)
    assert (code, output.count("claude-md ")) == (1, 2), output


def test_claude_md_counts_only_once_tracked_and_only_while_on_disk(build_repo, capsys):
    """#95 E9 cells 14 and 15: an untracked file is not judged until `git add`; a tracked one gone from disk is skipped."""
    repo = build_repo(GUIDANCE)
    (repo / "CLAUDE.md").write_text("own rules\n")
    untracked = run(capsys, repo)
    subprocess.run(["git", "-C", str(repo), "add", "--", "CLAUDE.md"], check=True)
    tracked = run(capsys, repo)
    (repo / "CLAUDE.md").unlink()
    deleted = run(capsys, repo)
    assert (untracked[0], is_clean(untracked[1])) == (0, True), untracked
    assert (tracked[0], "claude-md CLAUDE.md - " in tracked[1]) == (1, True), tracked
    assert (deleted[0], is_clean(deleted[1])) == (0, True), deleted


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads a mode-0 file, so no permission is refused")
def test_unreadable_claude_md_cannot_be_judged_and_other_rules_still_judge(build_repo, capsys):
    """#95 E14 cell 17: exit 2 naming the file; the env-file rule still judges."""
    repo = build_repo({**GUIDANCE, "CLAUDE.md": IMPORT, ".env": "A=1\n"})
    (repo / "CLAUDE.md").chmod(0)
    code, output = run(capsys, repo)
    assert code == 2, output
    assert f"{refusal_row('files')}cannot read CLAUDE.md" in output
    assert ENV_FINDING in output


@pytest.mark.skipif(os.geteuid() == 0, reason="root reaches a mode-0 directory, so no permission is refused")
@pytest.mark.parametrize(("name", "reason"), [("CLAUDE.md", LINK_REASON), (".claude/CLAUDE.md", NESTED_REASON)])
def test_claude_md_link_to_an_unreachable_target_is_the_link_finding(build_repo, capsys, elsewhere, name, reason):
    """#95 E8: a link is judged from lstat alone, so a target the filesystem refuses changes nothing."""
    locked = elsewhere / "locked"
    locked.mkdir()
    (locked / "target").write_text("own\n")
    repo = claude_repo(build_repo, GUIDANCE, {name: str(locked / "target")})
    locked.chmod(0)
    try:
        code, output = run(capsys, repo)
    finally:
        locked.chmod(0o700)
    assert code == 1, output
    assert f"claude-md {name} - {reason}" in output
    assert not refusals(repo)


def test_claude_md_from_a_subdirectory_is_judged_as_from_the_top(build_repo, capsys):
    """#95 cell 18: the verdict does not depend on where the run starts."""
    repo = build_repo({**GUIDANCE, "CLAUDE.md": "own\n", "src/a.py": ""})
    from_subdirectory = run(capsys, repo / "src")
    assert from_subdirectory == run(capsys, repo)
    assert from_subdirectory[0] == 1, from_subdirectory
    assert "claude-md CLAUDE.md - " in from_subdirectory[1]


def test_claude_md_is_named_in_run_order(build_repo, capsys):
    """#95 E15 cell 19: the clean line names the rule after import-contract."""
    plain = run(capsys, build_repo({**GUIDANCE, "CLAUDE.md": IMPORT}))
    assert (plain[0], is_clean(plain[1])) == (0, True), plain
