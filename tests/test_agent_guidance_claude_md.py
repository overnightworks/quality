import os
import subprocess
from pathlib import Path

import pytest

from quality.report import FindingEvent
from quality.rules import Rule
from tests.support.check import check_output as run
from tests.support.check import env_row_stands, is_clean, refusal_row, refusals, rows_of, rule_findings
from tests.support.layout import assert_prints_as_kind, rule_ids_in

GUIDANCE = {"AGENTS.md": "# guidance\n"}
IMPORT = "@AGENTS.md\n"


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
        "no CLAUDE.md beside AGENTS.md",
        "neither file",
        "exact import with newline",
        "exact import without newline",
        "nested CLAUDE.md is not judged",
    ],
)
def test_claude_md_that_is_absent_or_exactly_the_import_is_clean(build_repo, capsys, tracked, links):
    """CLAUDE-2, CLAUDE-4, CLAUDE-8."""
    code, output = run(capsys, claude_repo(build_repo, tracked, links))
    assert (code, is_clean(output)) == (0, True), output


@pytest.mark.parametrize(
    ("tracked", "links", "name", "event"),
    [
        ({**GUIDANCE, "CLAUDE.md": "@AGENTS.md\r\n"}, {}, "CLAUDE.md", FindingEvent.DIVERGES),
        ({**GUIDANCE, "CLAUDE.md": ""}, {}, "CLAUDE.md", FindingEvent.DIVERGES),
        ({**GUIDANCE, "CLAUDE.md": " @AGENTS.md\n"}, {}, "CLAUDE.md", FindingEvent.DIVERGES),
        ({**GUIDANCE, "CLAUDE.md": "@AGENTS.md\n\n"}, {}, "CLAUDE.md", FindingEvent.DIVERGES),
        ({**GUIDANCE, "CLAUDE.md": "\ufeff@AGENTS.md\n"}, {}, "CLAUDE.md", FindingEvent.DIVERGES),
        ({**GUIDANCE, "CLAUDE.md": "@AGENTS.md\nuse pnpm\n"}, {}, "CLAUDE.md", FindingEvent.DIVERGES),
        ({**GUIDANCE, "claude.md": "own rules\n"}, {}, "claude.md", FindingEvent.DIVERGES),
        ({**GUIDANCE, ".claude/CLAUDE.md": IMPORT}, {}, ".claude/CLAUDE.md", FindingEvent.CLAUDE_ONLY),
        (GUIDANCE, {"CLAUDE.md": "AGENTS.md"}, "CLAUDE.md", FindingEvent.SYMLINK),
        (GUIDANCE, {"CLAUDE.md": "missing.md"}, "CLAUDE.md", FindingEvent.SYMLINK),
        ({"CLAUDE.md": IMPORT}, {}, "CLAUDE.md", FindingEvent.NO_AGENTS_FILE),
        ({"CLAUDE.md": IMPORT, "agents.md": "x\n"}, {}, "CLAUDE.md", FindingEvent.NO_AGENTS_FILE),
    ],
    ids=[
        "windows line ending",
        "empty",
        "leading space",
        "blank line after",
        "byte order mark",
        "second line",
        "lowercase name is named as it is",
        ".claude/CLAUDE.md even holding the import",
        "symlink to AGENTS.md",
        "dangling symlink",
        "AGENTS.md absent",
        "only agents.md",
    ],
)
def test_claude_md_that_is_not_the_import_is_one_finding_of_its_kind_at_the_file(build_repo, capsys, tracked, links, name, event):
    """CLAUDE-1, CLAUDE-3, CLAUDE-5, CLAUDE-6, CLAUDE-7, CLAUDE-9, CLAUDE-11, O12: one finding of its kind at the file,
    its fact never names the file, and its fix is never a command.
    """
    finding = assert_prints_as_kind(capsys, claude_repo(build_repo, tracked, links), Rule.CLAUDE_MD, event)
    assert (finding.path, name in finding.fact, "git " in finding.fix, rule_ids_in(finding.fix)) == (name, False, False, set())


def test_claude_md_finding_never_prints_the_files_content(build_repo, capsys):
    """CLAUDE-10: a token-like string or bytes that are not UTF-8 stay out of the output."""
    repo = build_repo({**GUIDANCE, "CLAUDE.md": "token = hunter2-secret-value\n"})
    (repo / "claude.md").write_bytes(b"\xff\xfe secret-bytes\n")
    subprocess.run(["git", "-C", str(repo), "add", "--", "claude.md"], check=True)
    code, output = run(capsys, repo)
    assert (code, len(rule_findings(repo, Rule.CLAUDE_MD)), "hunter2" in output, "secret-bytes" in output) == (1, 2, False, False), output


def test_claude_md_two_wrong_files_are_two_findings(build_repo, capsys):
    """One finding per wrong file."""
    repo = build_repo({**GUIDANCE, "CLAUDE.md": "own\n", ".claude/CLAUDE.md": "own\n"})
    code, output = run(capsys, repo)
    findings = rule_findings(repo, Rule.CLAUDE_MD)
    assert (code, [len(rows_of(output, finding)) for finding in findings]) == (1, [1, 1]), output


def test_claude_md_counts_only_once_tracked_and_only_while_on_disk(build_repo, capsys):
    """CLAUDE-1, CLAUDE-12: an untracked file is not judged until `git add`; a tracked one gone from disk is skipped."""
    repo = build_repo(GUIDANCE)
    (repo / "CLAUDE.md").write_text("own rules\n")
    untracked = run(capsys, repo)
    subprocess.run(["git", "-C", str(repo), "add", "--", "CLAUDE.md"], check=True)
    tracked = run(capsys, repo)
    (finding,) = rule_findings(repo, Rule.CLAUDE_MD)
    (repo / "CLAUDE.md").unlink()
    deleted = run(capsys, repo)
    assert (untracked[0], is_clean(untracked[1])) == (0, True), untracked
    assert (tracked[0], len(rows_of(tracked[1], finding))) == (1, 1), tracked
    assert (deleted[0], is_clean(deleted[1])) == (0, True), deleted


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads a mode-0 file, so no permission is refused")
def test_unreadable_claude_md_cannot_be_judged_and_other_rules_still_judge(build_repo, capsys):
    """CLAUDE-13, CHECK-4: exit 2 naming the file; the env-file rule still judges."""
    repo = build_repo({**GUIDANCE, "CLAUDE.md": IMPORT, ".env": "A=1\n"})
    (repo / "CLAUDE.md").chmod(0)
    code, output = run(capsys, repo)
    assert code == 2, output
    assert f"{refusal_row('files')}cannot read CLAUDE.md" in output
    assert env_row_stands(output)


@pytest.mark.skipif(os.geteuid() == 0, reason="root reaches a mode-0 directory, so no permission is refused")
@pytest.mark.parametrize(("name", "event"), [("CLAUDE.md", FindingEvent.SYMLINK), (".claude/CLAUDE.md", FindingEvent.CLAUDE_ONLY)])
def test_claude_md_link_to_an_unreachable_target_is_the_link_finding(build_repo, capsys, elsewhere, name, event):
    """CLAUDE-5, CLAUDE-6: a link is judged from lstat alone, so a target the filesystem refuses changes nothing."""
    locked = elsewhere / "locked"
    locked.mkdir()
    (locked / "target").write_text("own\n")
    repo = claude_repo(build_repo, GUIDANCE, {name: str(locked / "target")})
    locked.chmod(0)
    try:
        code, output = run(capsys, repo)
    finally:
        locked.chmod(0o700)
    (finding,) = rule_findings(repo, Rule.CLAUDE_MD)
    assert (code, finding.path, finding.event, len(rows_of(output, finding))) == (1, name, event, 1), output
    assert not refusals(repo)


def test_claude_md_from_a_subdirectory_is_judged_as_from_the_top(build_repo, capsys):
    """CHECK-1: the verdict does not depend on where the run starts."""
    repo = build_repo({**GUIDANCE, "CLAUDE.md": "own\n", "src/a.py": ""})
    from_subdirectory = run(capsys, repo / "src")
    assert from_subdirectory == run(capsys, repo)
    (finding,) = rule_findings(repo, Rule.CLAUDE_MD)
    assert (from_subdirectory[0], len(rows_of(from_subdirectory[1], finding))) == (1, 1), from_subdirectory
