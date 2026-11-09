import os
from pathlib import Path

import pytest

from quality.report import FindingEvent
from quality.rules import Rule
from tests.support.check import check_output as run
from tests.support.check import env_row_stands, is_clean, refusal_row, rule_findings

HEAD = "# guidance\n## Commands\n"
NOT_A_TITLE = "line 1 is not a title"
NOT_A_SECTION = "not a section of the shape"
TWICE = "appears twice"
OUT_OF_ORDER = "stands out of order"
TEXT_BEFORE = "text before the first section"
TITLE, SECTION, TOO_LONG = FindingEvent.TITLE, FindingEvent.SECTION, FindingEvent.TOO_LONG


def judged(build_repo, content: str) -> list[tuple[FindingEvent, int | None, str]]:
    """The event, line and fact of every `agents-md` finding over a repository tracking `content` as its AGENTS.md."""
    repo = build_repo({"AGENTS.md": content})
    return [(finding.event, finding.line, finding.fact) for finding in rule_findings(repo, Rule.AGENTS_MD)]


def of_size(lines: int | None = None, size: int | None = None, ending: str = "\n") -> str:
    """A conforming file of exactly `lines` lines (the last one ended by `ending`) or `size` bytes."""
    if size is not None:
        return HEAD + "a" * (size - len(HEAD) - 1) + "\n"
    return HEAD + "x\n" * (lines - 3) + "x" + ending


@pytest.mark.parametrize(
    "content",
    [
        HEAD,
        "# guidance\n",
        "# guidance\n\n\n## Commands\n- run it\n\n## Rules\n\n## Owners\n\n## Documents\n",
        "# guidance\n## Rules\n## Documents\n",
        "# guidance\n## Documents\n",
        "# guidance",
    ],
    ids=["one section", "title alone", "four sections", "two sections in order", "last section alone", "no line feed"],
)
def test_conforming_agents_md_is_clean(build_repo, capsys, content):
    """AGENTS-1, AGENTS-2, AGENTS-3: a title, blank lines, and the sections in order, each at most once, are clean."""
    code, output = run(capsys, build_repo({"AGENTS.md": content}))
    assert (code, is_clean(output)) == (0, True), output


@pytest.mark.parametrize(
    "content",
    ["## guidance\n", "#guidance\n", "\n## Commands\n", "﻿# guidance\n", "guidance\n", "", "# \n", "#  \t\n"],
    ids=["level 2", "no space", "blank first line", "byte order mark", "text", "empty file", "nothing after the hash", "blanks only"],
)
def test_a_first_line_that_is_no_title_is_one_title_finding_at_line_1(build_repo, content):
    """AGENTS-1."""
    assert judged(build_repo, content) == [(TITLE, 1, NOT_A_TITLE)]


@pytest.mark.parametrize(
    ("content", "line"),
    [
        ("# guidance\ntext\n## Commands\n", 2),
        ("# guidance\n\n\nfirst\nsecond\n## Commands\n", 4),
        ("# guidance\ntext\n", 2),
        ("# guidance\n```\n## Commands\n```\n", 2),
    ],
    ids=["right below the title", "after blank lines", "no section at all", "a fence before the first section"],
)
def test_text_before_the_first_section_is_one_section_finding_at_its_first_line(build_repo, content, line):
    """AGENTS-2."""
    assert judged(build_repo, content) == [(SECTION, line, TEXT_BEFORE)]


@pytest.mark.parametrize(
    ("content", "line", "fact"),
    [
        (HEAD + "## Setup\n", 3, NOT_A_SECTION),
        (HEAD + "## commands\n", 3, NOT_A_SECTION),
        (HEAD + "## Commands \n", 3, NOT_A_SECTION),
        ("# guidance\n## Commands\n## Commands\n", 3, TWICE),
        (HEAD + "## Rules\n## Rules\n", 4, TWICE),
        (HEAD + "## Owners\n## Rules\n", 4, OUT_OF_ORDER),
        ("# guidance\n## Documents\n## Commands\n", 3, OUT_OF_ORDER),
        (HEAD + "# second\n", 3, NOT_A_SECTION),
        ("# guidance\n# second\n", 2, NOT_A_SECTION),
        ("# guidance\n## Setup\ntext\nmore\n", 2, NOT_A_SECTION),
        ("# guidance\n## Commands \ntext\n", 2, NOT_A_SECTION),
        ("# guidance\n# second\ntext\n", 2, NOT_A_SECTION),
    ],
    ids=[
        "another name", "another case", "trailing space", "repeat of the last", "repeat after another", "out of order",
        "first after last", "second title", "second title before any section",
        "text under an unknown first heading", "text under a misspelled first heading", "text under a second title",
    ],
)
def test_a_wrong_heading_is_one_section_finding_at_its_line(build_repo, content, line, fact):
    """AGENTS-3: an unknown, repeated or out-of-order section and a second title."""
    assert judged(build_repo, content) == [(SECTION, line, fact)]


@pytest.mark.parametrize(
    "content",
    [
        HEAD + "```\n## Setup\n# Second\n```\n",
        HEAD + "~~~\n## Setup\n~~~\n",
        HEAD + "````\n```\n## Setup\n```\n````\n",
        HEAD + "### Setup\n#### Deeper\n",
        HEAD + "```\n## Commands\n```\n## Rules\n",
    ],
    ids=["backtick fence", "tilde fence", "longer fence holds a shorter one", "level 3 and 4 headings", "fenced repeat"],
)
def test_headings_in_a_fence_and_below_level_2_are_no_sections(build_repo, content):
    """AGENTS-3."""
    assert judged(build_repo, content) == []


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        (of_size(lines=100), []),
        (of_size(lines=100, ending=""), []),
        (of_size(lines=101), [(TOO_LONG, 1, "101 lines, the limit is 100")]),
        (of_size(lines=101, ending=""), [(TOO_LONG, 1, "101 lines, the limit is 100")]),
        (of_size(size=4096), []),
        (of_size(size=4097), [(TOO_LONG, 1, "4097 bytes, the limit is 4096")]),
        (HEAD + "x\r" * 200, []),
        (
            HEAD + ("a" * 50 + "\n") * 99,
            [(TOO_LONG, 1, "101 lines, the limit is 100"), (TOO_LONG, 1, "5072 bytes, the limit is 4096")],
        ),
    ],
    ids=[
        "100 lines", "100 lines without a final line feed", "101 lines", "101 lines without a final line feed",
        "4096 bytes", "4097 bytes", "carriage returns do not separate lines", "both limits",
    ],
)
def test_size_limits_are_100_lines_and_4096_bytes_each_one_finding_at_line_1(build_repo, content, expected):
    """AGENTS-4."""
    assert judged(build_repo, content) == expected


def test_absent_agents_md_is_clean(build_repo, capsys):
    """AGENTS-5."""
    code, output = run(capsys, build_repo({}))
    assert (code, is_clean(output)) == (0, True), output


def test_tracked_agents_md_gone_from_disk_is_skipped(build_repo, capsys):
    """AGENTS-5."""
    repo = build_repo({"AGENTS.md": "text\n"})
    (repo / "AGENTS.md").unlink()
    code, output = run(capsys, repo)
    assert (code, is_clean(output)) == (0, True), output


def test_claude_md_without_agents_md_stays_the_claude_md_finding_alone(build_repo):
    """AGENTS-5."""
    repo = build_repo({"CLAUDE.md": "@AGENTS.md\n"})
    assert (len(rule_findings(repo, Rule.CLAUDE_MD)), rule_findings(repo, Rule.AGENTS_MD)) == (1, [])


def test_no_finding_prints_the_title_or_a_heading(build_repo, capsys):
    """AGENTS-6: the marker in the title and in headings stays out of everything printed."""
    marker = "zq7-marker"
    content = f"# {marker} title\n{marker} text\n## {marker} one\n## Rules\n## Commands\n## Rules\n# {marker} two\n"
    repo = build_repo({"AGENTS.md": content + "x\n" * 100})
    code, output = run(capsys, repo)
    assert (code, len(rule_findings(repo, Rule.AGENTS_MD)), marker in output) == (1, 6, False), output


def refuse_by_mode(repo: Path, _elsewhere: Path) -> None:
    (repo / "AGENTS.md").chmod(0)


def refuse_by_link(repo: Path, elsewhere: Path) -> None:
    (elsewhere / "outside.md").write_text(HEAD)
    (repo / "AGENTS.md").unlink()
    (repo / "AGENTS.md").symlink_to(elsewhere / "outside.md")


@pytest.mark.parametrize(
    "refuse",
    [
        pytest.param(
            refuse_by_mode, id="mode 0", marks=pytest.mark.skipif(os.geteuid() == 0, reason="root reads a mode-0 file")
        ),
        pytest.param(refuse_by_link, id="link out of the repository"),
    ],
)
def test_agents_md_the_reader_refuses_cannot_be_judged_and_other_rules_still_judge(build_repo, capsys, elsewhere, refuse):
    """AGENTS-7: exit 2 naming the file; the env-file rule still judges."""
    repo = build_repo({"AGENTS.md": HEAD, ".env": "A=1\n"})
    refuse(repo, elsewhere)
    code, output = run(capsys, repo)
    assert code == 2, output
    assert refusal_row("files") in output
    assert env_row_stands(output)
