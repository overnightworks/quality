import os
from pathlib import Path

import pytest

from quality.report import FindingEvent
from quality.rules import Rule
from tests.support.check import check_output as run
from tests.support.check import env_row_stands, is_clean, refusal_row, rule_findings
from tests.support.git_repositories import git
from tests.support.github_remote import HTTPS, SCP, SSH_URL, with_github_remote

HEAD = "# guidance\n## Commands\n"
NOT_A_TITLE = "line 1 is not a title"
NOT_THE_NAME = "the title is not the repository's name"
NO_REMOTE = "no GitHub remote names this repository"
NOT_A_SECTION = "not a section of the shape"
TWICE = "appears twice"
OUT_OF_ORDER = "stands out of order"
TEXT_BEFORE = "text before the first section"
TITLE, SECTION, TOO_LONG = FindingEvent.TITLE, FindingEvent.SECTION, FindingEvent.TOO_LONG
MISSING = FindingEvent.MISSING
DOCS = "# guidance\n## Documents\n"
TRACKED_DOCS = {"docs/a.md": "a\n", "docs/b/c.md": "c\n"}


def judged(build_repo, content: str, files: dict[str, str] | None = None) -> list[tuple[FindingEvent, int | None, str]]:
    """The event, line and fact of every `agents-md` finding over a repository tracking `content` as its AGENTS.md."""
    repo = with_github_remote(build_repo({"AGENTS.md": content, **(files or {})}))
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
    code, output = run(capsys, with_github_remote(build_repo({"AGENTS.md": content})))
    assert (code, is_clean(output)) == (0, True), output


@pytest.mark.parametrize(
    "content",
    ["## guidance\n", "#guidance\n", "\n## Commands\n", "﻿# guidance\n", "guidance\n", "", "# \n", "#  \t\n"],
    ids=[
        "level 2", "no space", "blank first line", "byte order mark", "text", "empty file", "nothing after the hash",
        "blanks only",
    ],
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
    ids=[
        "backtick fence", "tilde fence", "longer fence holds a shorter one", "level 3 and 4 headings", "fenced repeat",
    ],
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
    repo = with_github_remote(build_repo({"AGENTS.md": "text\n"}))
    (repo / "AGENTS.md").unlink()
    code, output = run(capsys, repo)
    assert (code, is_clean(output)) == (0, True), output


def test_claude_md_without_agents_md_stays_the_claude_md_finding_alone(build_repo):
    """AGENTS-5."""
    repo = build_repo({"CLAUDE.md": "@AGENTS.md\n"})
    assert (len(rule_findings(repo, Rule.CLAUDE_MD)), rule_findings(repo, Rule.AGENTS_MD)) == (1, [])


def test_no_finding_prints_the_title_or_a_heading(build_repo, capsys):
    """AGENTS-6, AGENTS-12: the marker in the title and in headings stays out of everything printed."""
    marker = "zq7-marker"
    content = f"# {marker} title\n{marker} text\n## {marker} one\n## Rules\n## Commands\n## Rules\n# {marker} two\n"
    repo = with_github_remote(build_repo({"AGENTS.md": content + "x\n" * 100}))
    code, output = run(capsys, repo)
    assert (code, len(rule_findings(repo, Rule.AGENTS_MD)), marker in output) == (1, 7, False), output
    findings = rule_findings(repo, Rule.AGENTS_MD)
    fixes = [finding.fix for finding in findings if finding.event is not TITLE]
    assert [fix for fix in fixes if "`" in fix or "quality" in fix] == []
    assert [finding.fix for finding in findings if finding.event is TITLE] == ["make line 1 `# guidance`"]


def refuse_by_mode(repo: Path, _elsewhere: Path) -> None:
    (repo / "AGENTS.md").chmod(0)


def refuse_by_link(repo: Path, elsewhere: Path) -> None:
    (elsewhere / "outside.md").write_text(HEAD)
    (repo / "AGENTS.md").unlink()
    (repo / "AGENTS.md").symlink_to(elsewhere / "outside.md")


def refuse_by_dangling_link(repo: Path, _elsewhere: Path) -> None:
    (repo / "AGENTS.md").unlink()
    (repo / "AGENTS.md").symlink_to(repo / "missing.md")


def refuse_by_encoding(repo: Path, _elsewhere: Path) -> None:
    (repo / "AGENTS.md").write_bytes(b"# guidance\n\xff\xfe\n")


@pytest.mark.parametrize(
    "refuse",
    [
        pytest.param(
            refuse_by_mode, id="mode 0", marks=pytest.mark.skipif(os.geteuid() == 0, reason="root reads a mode-0 file")
        ),
        pytest.param(refuse_by_link, id="link out of the repository"),
        pytest.param(refuse_by_dangling_link, id="dangling link"),
        pytest.param(refuse_by_encoding, id="text that is not UTF-8"),
    ],
)
def test_agents_md_the_reader_refuses_cannot_be_judged_and_other_rules_still_judge(
    build_repo, capsys, elsewhere, refuse
):
    """AGENTS-7: exit 2 naming the file; the env-file rule still judges."""
    repo = with_github_remote(build_repo({"AGENTS.md": HEAD, ".env": "A=1\n"}))
    refuse(repo, elsewhere)
    code, output = run(capsys, repo)
    assert code == 2, output
    assert refusal_row("files") in output
    assert env_row_stands(output)


def not_tracked(path: str, line: int) -> list[tuple[FindingEvent, int, str]]:
    return [(MISSING, line, f"{path} is not tracked")]


@pytest.mark.parametrize("path", ["docs/a.md", "docs/b/", "docs/b", "docs", "docs/", "AGENTS.md"])
def test_a_tracked_file_or_a_directory_with_a_tracked_file_passes(build_repo, path):
    """AGENTS-9: a file, a directory with or without the trailing slash."""
    assert judged(build_repo, f"{DOCS}- `{path}`: read it\n", TRACKED_DOCS) == []


def test_a_tracked_document_gone_from_disk_passes(build_repo):
    """AGENTS-9: only the tracked listing is used."""
    repo = with_github_remote(build_repo({"AGENTS.md": f"{DOCS}- `docs/a.md`\n", "docs/a.md": "a\n"}))
    (repo / "docs/a.md").unlink()
    assert rule_findings(repo, Rule.AGENTS_MD) == []


@pytest.mark.parametrize(
    "path",
    [
        "docs/none.md", "Docs/a.md", "docs/A.md", "docs/b//", "./docs/a.md", "docs//a.md", "empty/", "docs/a",
        "docs/a.md/", "AGENTS.md/",
    ],
    ids=[
        "untracked", "wrong case directory", "wrong case file", "two slashes", "dot slash", "double slash",
        "directory without a tracked file", "prefix of a file", "nested file with a slash", "top file with a slash",
    ],
)
def test_a_path_no_tracked_file_backs_is_one_missing_finding_at_its_line(build_repo, path):
    """AGENTS-9, AGENTS-10: the path is compared as written, in exact case."""
    repo = with_github_remote(build_repo({"AGENTS.md": f"{DOCS}- `{path}`: read it\n", **TRACKED_DOCS}))
    (repo / "empty").mkdir()
    found = [(f.event, f.line, f.fact) for f in rule_findings(repo, Rule.AGENTS_MD)]
    assert found == not_tracked(path, 3)


def test_a_document_that_exists_only_on_disk_is_missing(build_repo):
    """AGENTS-10: an untracked file on disk does not pass."""
    repo = with_github_remote(build_repo({"AGENTS.md": f"{DOCS}- `docs/x.md`\n", "docs/a.md": "a\n"}))
    (repo / "docs/x.md").write_text("x\n")
    assert [(f.event, f.line, f.fact) for f in rule_findings(repo, Rule.AGENTS_MD)] == not_tracked("docs/x.md", 3)


@pytest.mark.parametrize(
    "path", ["~/x", "../x", "https://example.com/x", "file:///x", "@docs/a.md", "docs/a.md#top"],
    ids=["home", "parent", "https scheme", "file scheme", "at span", "anchor"],
)
def test_every_form_of_a_path_that_is_not_tracked_is_missing(build_repo, path):
    """AGENTS-10: no form is classified; each one fails the tracked-listing test."""
    assert judged(build_repo, f"{DOCS}- `{path}`\n", TRACKED_DOCS) == not_tracked(path, 3)


def refuse_absolute(repo: Path, elsewhere: Path, _home: Path) -> str:
    target = elsewhere / "secret.md"
    target.write_text("x\n")
    target.chmod(0)
    return str(target)


def refuse_home(_repo: Path, _elsewhere: Path, home: Path) -> str:
    target = home / "secret.md"
    target.write_text("x\n")
    target.chmod(0)
    return "~/secret.md"


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads a mode-0 file")
@pytest.mark.parametrize("plant", [refuse_absolute, refuse_home], ids=["absolute path", "home path"])
def test_a_path_outside_the_repository_is_missing_and_opens_nothing(build_repo, capsys, elsewhere, own_home, plant):
    """AGENTS-10, AGENTS-11: a read of the mode-0 file would end the run with exit 2."""
    repo = with_github_remote(build_repo({"AGENTS.md": HEAD}))
    path = plant(repo, elsewhere, own_home)
    (repo / "AGENTS.md").write_text(f"{DOCS}- `{path}`\n")
    code, output = run(capsys, repo)
    found = [(f.event, f.line, f.fact) for f in rule_findings(repo, Rule.AGENTS_MD)]
    assert (code, found) == (1, not_tracked(path, 3)), output


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("- `docs/a.md` read when `nothing/here` or `ci.yml` runs\n", []),
        ("- read when `docs/a.md` changes\n- no span here\n", []),
        ("- `nope.md` and `docs/a.md`\n", not_tracked("nope.md", 3)),
        ("- `docs/a.md` and `nope.md`\n", []),
        ("- `docs/a.md`\n- `x/y.md`\n\n- `z.md`\n", not_tracked("x/y.md", 4) + not_tracked("z.md", 6)),
        ("- one ` backtick only\n", []),
    ],
    ids=["later spans", "span not first word", "bad first span", "good first span", "several lines", "lone backtick"],
)
def test_only_the_first_span_of_a_line_is_judged(build_repo, content, expected):
    """AGENTS-8."""
    assert judged(build_repo, DOCS + content, TRACKED_DOCS) == expected


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("# guidance\n## Commands\n- `nope`\n## Rules\n- `nope`\n## Owners\n- `nope`\n", []),
        (DOCS + "```\n`nope.md`\n```\n", []),
        (DOCS + "~~~\n`nope.md`\n~~~\n`nope.md`\n", not_tracked("nope.md", 6)),
        (DOCS + "### Sub\n`nope.md`\n", not_tracked("nope.md", 4)),
        (DOCS + "## Documents\n`nope.md`\n", not_tracked("nope.md", 4)),
        ("# guidance\n## Documents\n## Owners\n`nope.md`\n", []),
        ("# guidance\n## Documents\n# Second\n`nope.md`\n", []),
        ("# guidance\n## Documents\n## Documents\n`nope.md`\n", not_tracked("nope.md", 4)),
        ("# `nope.md`\n## Commands\n- `nope.md`\n", []),
        (DOCS, []),
        ("# guidance\n## Documents\ntext only\n", []),
        ("# guidance\n", []),
    ],
    ids=[
        "other sections", "fenced span", "span after a fence", "level 3 heading", "second Documents section",
        "after the next section", "after a second title", "repeated section", "title and commands", "empty section",
        "no span", "no section",
    ],
)
def test_only_lines_of_a_documents_section_outside_a_fence_are_judged(build_repo, content, expected):
    """AGENTS-8."""
    assert [(e, n, f) for e, n, f in judged(build_repo, content, TRACKED_DOCS) if e is MISSING] == expected


def test_a_missing_finding_prints_the_path_and_nothing_else_of_the_line(build_repo, capsys):
    """AGENTS-6: the path text of the span, never the words around it."""
    marker = "zq7-marker"
    repo = with_github_remote(build_repo({"AGENTS.md": f"{DOCS}- `nope.md`: read {marker} when `{marker}.yml` runs\n"}))
    code, output = run(capsys, repo)
    assert (code, "nope.md is not tracked" in output, marker in output) == (1, True, False), output


def titled(build_repo, title: str, *remotes: str, form: str = HTTPS) -> Path:
    """A repository tracking an AGENTS.md titled `title`, with one GitHub remote for each of `remotes`."""
    repo = build_repo({"AGENTS.md": f"# {title}\n"})
    for number, repository in enumerate(remotes):
        with_github_remote(repo, repository, form, remote=f"remote{number}")
    return repo


def title_findings(repo: Path) -> list[tuple[FindingEvent, int | None, str, str]]:
    return [(f.event, f.line, f.fact, f.fix) for f in rule_findings(repo, Rule.AGENTS_MD)]


@pytest.mark.parametrize(
    ("title", "repository"),
    [
        ("guidance", "acme/guidance"), ("Guidance", "acme/guidance"), ("guidance", "ACME/Guidance"),
        ("guidance  ", "a/guidance"),
    ],
    ids=["same", "title in upper case", "remote in upper case", "blanks after the title"],
)
def test_a_title_equal_to_the_remote_s_repository_name_in_any_case_is_clean(build_repo, title, repository):
    """AGENTS-12."""
    assert title_findings(titled(build_repo, title, repository)) == []


@pytest.mark.parametrize("title", ["tooling", "guidance-tools", "guid", "acme/guidance", "acme guidance"])
def test_a_title_that_is_another_name_is_one_title_finding_naming_the_expected_name(build_repo, title):
    """AGENTS-12: equality, never containment; the fix names the repository, not the title."""
    found = title_findings(titled(build_repo, title, "acme/guidance"))
    assert found == [(TITLE, 1, NOT_THE_NAME, "make line 1 `# guidance`")]


@pytest.mark.parametrize("title", ["fork", "upstream"])
def test_a_title_matching_either_of_two_remotes_is_clean(build_repo, title):
    """AGENTS-12: fork and upstream."""
    assert title_findings(titled(build_repo, title, "me/fork", "acme/upstream")) == []


def test_a_title_matching_neither_of_two_remotes_is_one_finding_naming_both(build_repo):
    """AGENTS-12."""
    found = title_findings(titled(build_repo, "other", "me/fork", "acme/upstream"))
    assert found == [(TITLE, 1, NOT_THE_NAME, "make line 1 `# fork` or `# upstream`")]


@pytest.mark.parametrize(
    "content", ["## guidance\n", "guidance\n", "# \n", ""], ids=["level 2", "text", "blank title", "empty"]
)
def test_a_first_line_that_is_no_title_gets_no_name_finding(build_repo, content):
    """AGENTS-12: only AGENTS-1's finding, with a remote present."""
    repo = with_github_remote(build_repo({"AGENTS.md": content}), "acme/other")
    assert title_findings(repo) == [(TITLE, 1, NOT_A_TITLE, "make line 1 `# <repository name>`")]


@pytest.mark.parametrize("form", [HTTPS, SSH_URL, SCP], ids=["https", "ssh scheme", "scp"])
def test_each_form_of_a_github_remote_names_the_repository(build_repo, form):
    """AGENTS-13."""
    assert title_findings(titled(build_repo, "guidance", "acme/guidance", form=form)) == []


@pytest.mark.parametrize(
    "remote",
    [
        None, "/srv/git/acme/guidance.git", "https://gitlab.com/acme/guidance",
        "https://github.com/acme/guidance/tree/main",
    ],
    ids=["none", "local path", "another host", "a path below the repository"],
)
def test_without_a_github_remote_the_whole_rule_cannot_judge_and_other_rules_still_judge(build_repo, capsys, remote):
    """AGENTS-13, CHECK-4: exit 2, no finding even for a bad shape, the other rules judge."""
    repo = build_repo({"AGENTS.md": "## guidance\n## Setup\n", ".env": "A=1\n"})
    if remote:
        git(repo, "remote", "add", "origin", remote)
    code, output = run(capsys, repo)
    assert code == 2, output
    assert f"{refusal_row('files')}{NO_REMOTE}" in output
    assert "add the repository's GitHub remote" in output
    assert (rule_findings(repo, Rule.AGENTS_MD), env_row_stands(output)) == ([], True)


def test_once_a_remote_counts_the_shape_findings_show(build_repo):
    """AGENTS-13: the refusal of the previous test is the whole rule's and nothing else."""
    repo = with_github_remote(build_repo({"AGENTS.md": "## guidance\n## Setup\n"}))
    assert [event for event, *_ in title_findings(repo)] == [TITLE, SECTION]


@pytest.mark.parametrize("gone", [False, True], ids=["absent", "tracked and gone from disk"])
def test_without_the_file_no_remote_is_clean(build_repo, capsys, gone):
    """AGENTS-13, AGENTS-5: the file is read before the remote is asked for."""
    repo = build_repo({"AGENTS.md": "text\n"} if gone else {})
    if gone:
        (repo / "AGENTS.md").unlink()
    code, output = run(capsys, repo)
    assert (code, is_clean(output)) == (0, True), output


TOKEN = "s3cret-token"
CREDENTIAL_URL = f"https://user:{TOKEN}@github.com/acme/guidance"


def test_a_remote_with_credentials_counts_as_no_remote_and_is_never_printed(build_repo, capsys):
    """AGENTS-14."""
    repo = build_repo({"AGENTS.md": "# guidance\n"})
    git(repo, "remote", "add", "origin", CREDENTIAL_URL)
    code, output = run(capsys, repo)
    assert (code, TOKEN in output, NO_REMOTE in output) == (2, False, True), output


def test_a_finding_names_the_valid_remote_beside_one_with_credentials_and_prints_no_url(build_repo, capsys):
    """AGENTS-14, AGENTS-6."""
    repo = titled(build_repo, "other", "acme/guidance")
    git(repo, "remote", "add", "extra", CREDENTIAL_URL)
    code, output = run(capsys, repo)
    assert title_findings(repo) == [(TITLE, 1, NOT_THE_NAME, "make line 1 `# guidance`")]
    assert (code, TOKEN in output, "github.com" in output) == (1, False, False), output
