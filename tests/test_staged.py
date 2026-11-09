import io
import os
import subprocess
import sys
from pathlib import Path

import pytest

from quality import config, staged
from quality.report import FindingEvent, NoteEvent
from quality.rules import Area
from tests.support.check import (
    CopyLine,
    FindingLine,
    area_block,
    assert_block_prints,
    assert_finding_vocabulary,
    decode_block,
)
from tests.support.door import CHECK_WORD, STAGED_WORDS, door_in, staged_door
from tests.support.git_repositories import GIT_IDENTITY, git, head_commit, stage
from tests.support.secrets import FAKE_KEY, HOSTILE_FILE, OTHER_KEY, allow_config
from tests.support.terminal import Terminal, without_sgr

KEY_LINE = f"aws_access_key_id = {FAKE_KEY}\n"


@pytest.fixture
def unborn_repo(tmp_path) -> Path:
    repo = tmp_path / "unborn"
    git(tmp_path, "init", "-q", str(repo))
    return repo


@pytest.mark.scanner
@pytest.mark.parametrize(("stream_kind", "painted"), [(Terminal, True), (io.StringIO, False)])
def test_a_staged_row_is_painted_only_on_a_terminal(build_repo, installed_scanners, capsys, monkeypatch, stream_kind, painted):
    repo = build_repo({"README": "hello\n"})
    stage(repo, {"src/a.py": KEY_LINE})
    stream = stream_kind()
    monkeypatch.setattr(sys, "stdout", stream)
    code = staged_door(repo, environment={"TERM": "xterm"}, clock=lambda: 0.0)
    lines = without_sgr(stream.getvalue()).splitlines()
    assert (code, "\x1b" in stream.getvalue(), FAKE_KEY in stream.getvalue()) == (1, painted, False)
    assert (lines[-1], capsys.readouterr().err) == ("! 1 finding in 1 area · <1s", "")


@pytest.mark.scanner
def test_a_staged_door_prints_its_finding_row_and_verdict_whole_at_a_narrow_terminal(build_repo, installed_scanners, window, capsys, monkeypatch):
    repo = build_repo({"README": "hello\n"})
    stage(repo, {"src/a.py": KEY_LINE})
    terminal = window(20)
    monkeypatch.setattr(sys, "stdout", terminal.stream)
    staged_door(repo, environment={"TERM": "xterm"}, clock=lambda: 0.0)
    (finding,) = staged.scan_change(repo).findings
    printed = without_sgr(terminal.read())
    assert f"    ! src/a.py:1  secret  {finding.fact}" in printed.splitlines()
    assert (printed.splitlines()[-1], FAKE_KEY in printed, capsys.readouterr().err) == ("! 1 finding in 1 area · <1s", False, "")


@pytest.mark.scanner
def test_a_staged_key_is_one_secret_row_with_its_entry_and_its_value_is_not_printed(build_repo, installed_scanners, capsys):
    """#25 rule 1, O32: the staged change is judged; the row names no commit, the entry follows, the value is never printed."""
    repo = build_repo({"README": "hello\n"})
    stage(repo, {"src/a.py": KEY_LINE})
    assert staged_door(repo) == 1
    printed = capsys.readouterr().out
    result = staged.scan_change(repo)
    assert_block_prints(area_block(printed, Area.SECRETS), result.findings, list(result.notes))
    (finding,) = result.findings
    assert (finding.location, finding.event) == ("src/a.py:1", FindingEvent.SECRET)
    assert head_commit(repo)[:8] not in finding.fact
    assert_finding_vocabulary(finding)
    assert FAKE_KEY not in printed


@pytest.mark.scanner
@pytest.mark.parametrize("where", ["unstaged edit", "untracked file", "only in HEAD", "removed from HEAD"])
def test_a_key_that_is_not_in_the_staged_change_is_green(build_repo, installed_scanners, where):
    """STAGED-1: a key only in the work tree, or only in HEAD, is not part of the change the next commit records."""
    repo = build_repo({"kept.txt": KEY_LINE} if where.endswith("HEAD") else {"kept.txt": "clean\n"})
    if where == "unstaged edit":
        (repo / "kept.txt").write_text(KEY_LINE)
    if where == "untracked file":
        (repo / "new.txt").write_text(KEY_LINE)
    if where == "removed from HEAD":
        git(repo, "rm", "-q", "kept.txt")
    assert staged_door(repo) == 0


@pytest.mark.scanner
def test_a_repository_without_head_is_scanned(unborn_repo, installed_scanners):
    """STAGED-2: with no HEAD the whole index is the change."""
    stage(unborn_repo, {"clean.txt": "ok\n"})
    assert staged_door(unborn_repo) == 0
    stage(unborn_repo, {"config.txt": KEY_LINE})
    assert staged_door(unborn_repo) == 1


@pytest.mark.scanner
@pytest.mark.parametrize(
    ("name", "content"),
    [
        (".gitleaksignore", "config.txt:aws-access-token:1\n"),
        (".gitattributes", "config.txt -diff\n"),
        (".gitleaks.toml", "[allowlist]\nregexes = ['AKIA.*']\n"),
    ],
)
def test_a_staged_ignore_attribute_or_config_file_changes_nothing(build_repo, installed_scanners, name, content):
    """STAGED-6: the scratch work tree is empty, so no staged ignore, attributes or config file steers the scan."""
    repo = build_repo({"README": "hello\n"})
    stage(repo, {name: content, "config.txt": KEY_LINE})
    assert staged_door(repo) == 1


@pytest.mark.scanner
def test_a_gitleaks_allow_comment_changes_nothing(build_repo, installed_scanners):
    """STAGED-6: an inline gitleaks:allow comment is ignored, so the staged key is still red."""
    repo = build_repo({"README": "hello\n"})
    stage(repo, {"config.txt": f"aws_access_key_id = {FAKE_KEY}  # gitleaks:allow\n"})
    assert staged_door(repo) == 1


@pytest.mark.scanner
def test_the_live_repositorys_info_attributes_cannot_hide_a_staged_key(build_repo, installed_scanners):
    """STAGED-6: the live repository's info/attributes cannot hide a staged key: the scratch repository has none."""
    repo = build_repo({"README": "hello\n"})
    (repo / ".git" / "info").mkdir(exist_ok=True)
    (repo / ".git" / "info" / "attributes").write_text("k.txt -diff\n")
    stage(repo, {"k.txt": KEY_LINE})
    assert staged_door(repo) == 1


@pytest.mark.scanner
@pytest.mark.parametrize("staged_config", [True, False], ids=["allowance staged", "allowance only in work tree"])
def test_the_allow_list_is_the_one_of_the_staged_tree(build_repo, installed_scanners, staged_config):
    """STAGED-7: an allowed value is green when the staged tree allows it; a work-tree-only file allows nothing."""
    repo = build_repo({"README": "hello\n"})
    config = allow_config(("aws-access-token", "config.txt", FAKE_KEY, "test key"))
    stage(repo, {"config.txt": KEY_LINE})
    if staged_config:
        stage(repo, {"quality/config.toml": config})
    else:
        (repo / "quality").mkdir()
        (repo / "quality" / "config.toml").write_text(config)
    assert staged_door(repo) == (0 if staged_config else 1)


@pytest.mark.scanner
@pytest.mark.parametrize(
    ("staged_change", "exit_code", "expected_line"),
    [
        (
            f"# one\n# two\nrenamed_key = {FAKE_KEY}\n",
            0,
            "config.txt:3",
        ),
        (f"aws_access_key_id = {OTHER_KEY}\n", 1, None),
    ],
    ids=["allowed value moved to another line of its file", "allowed value replaced by another key"],
)
def test_an_allowance_follows_the_value_in_the_staged_scan(
    build_repo, installed_scanners, capsys, staged_change, exit_code, expected_line
):
    """#25 rule 1.4, #24 rule 1: the staged diff matches by rule, file and value hash, not by line."""
    config = allow_config(("aws-access-token", "config.txt", FAKE_KEY, "test key"))
    repo = build_repo({"config.txt": KEY_LINE, "quality/config.toml": config})
    stage(repo, {"config.txt": staged_change})
    assert staged_door(repo) == exit_code
    streams = capsys.readouterr()
    result = staged.scan_change(repo)
    allowed = [note for note in result.notes if note.event is NoteEvent.ALLOWED]
    assert [note.location for note in allowed] == ([] if expected_line is None else [expected_line])
    assert all(("aws-access-token" in note.fact, "test key" in note.fact) == (True, True) for note in allowed)
    assert_block_prints(area_block(streams.out, Area.SECRETS), result.findings, result.notes)
    printed = streams.out + streams.err
    assert [key in printed for key in (FAKE_KEY, OTHER_KEY)] == [False, False]


@pytest.fixture
def refusing_hook(build_repo, installed_scanners):
    """A repository whose pre-commit hook runs the staged scan, with a committed clean file."""
    repo = build_repo({"tracked.txt": "clean\n"})
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.write_text(f"#!/bin/sh\nexec {sys.executable} -m quality.cli {' '.join(STAGED_WORDS)}\n")
    hook.chmod(0o755)
    return repo


def commit_attempt(repo: Path, how: str) -> subprocess.CompletedProcess[str]:
    if how == "plain":
        stage(repo, {"tracked.txt": KEY_LINE})
        return git(repo, "commit", "-q", "-m", "x", check=False)
    (repo / "tracked.txt").write_text(KEY_LINE)  # tracked and modified, not staged
    arguments = {"commit -a": ["commit", "-q", "-a", "-m", "x"], "commit <path>": ["commit", "-q", "-m", "x", "tracked.txt"]}
    return git(repo, *arguments[how], check=False)


def secret_rows_of(attempt: subprocess.CompletedProcess[str]) -> list[tuple[str, FindingEvent]]:
    """The finding rows a refused commit printed, each with its location."""
    block = decode_block((attempt.stdout + attempt.stderr).splitlines())
    return [(line.location, line.row.event) for line in block if isinstance(line, FindingLine)]


@pytest.mark.scanner
@pytest.mark.parametrize("how", ["plain", "commit -a", "commit <path>"])
def test_a_real_commit_is_refused_through_the_hook(refusing_hook, how):
    """STAGED-3, STAGED-5: a hook refuses the real commit, plain, with -a or with a path, and HEAD stays put."""
    before = git(refusing_hook, "rev-parse", "HEAD").stdout
    attempt = commit_attempt(refusing_hook, how)
    assert attempt.returncode != 0
    assert secret_rows_of(attempt) == [("tracked.txt:1", FindingEvent.SECRET)]
    assert git(refusing_hook, "rev-parse", "HEAD").stdout == before


@pytest.mark.scanner
def test_a_commit_from_a_linked_worktree_is_refused_through_the_hook(refusing_hook, tmp_path):
    """STAGED-5: a linked worktree, which commits with GIT_DIR and an absolute GIT_INDEX_FILE, refuses its key."""
    linked = tmp_path / "linked"
    git(refusing_hook, "worktree", "add", "-q", "-b", "side", str(linked))
    stage(linked, {"tracked.txt": KEY_LINE})
    attempt = git(linked, "commit", "-q", "-m", "x", check=False)
    assert attempt.returncode != 0
    assert secret_rows_of(attempt) == [("tracked.txt:1", FindingEvent.SECRET)]


@pytest.mark.scanner
def test_a_clean_commit_goes_through_the_hook(refusing_hook):
    """STAGED-3: a commit with nothing to find passes the hook."""
    stage(refusing_hook, {"tracked.txt": "still clean\n"})
    assert git(refusing_hook, "commit", "-q", "-m", "x", check=False).returncode == 0


@pytest.mark.scanner
def test_a_staged_run_leaves_the_repository_as_it_found_it(build_repo, installed_scanners):
    """A staged scan leaves the repository's status as it found it."""
    repo = build_repo({"README": "hello\n"})
    stage(repo, {"config.txt": KEY_LINE})
    before = git(repo, "status", "--porcelain=v2", "--branch").stdout
    staged_door(repo)
    assert git(repo, "status", "--porcelain=v2", "--branch").stdout == before


@pytest.mark.scanner
def test_an_unmerged_index_is_exit_2(build_repo, installed_scanners, capsys):
    """#25 rule 4: an index with unmerged entries is exit 2, never clean."""
    repo = build_repo({"f.txt": "base\n"})
    git(repo, "checkout", "-q", "-b", "other")
    stage(repo, {"f.txt": "other\n"})
    git(repo, "commit", "-q", "-m", "other")
    git(repo, "checkout", "-q", "-")
    stage(repo, {"f.txt": "main\n"})
    git(repo, "commit", "-q", "-m", "main")
    git(repo, "merge", "other", check=False)
    assert staged_door(repo) == 2
    assert "git write-tree failed" in capsys.readouterr().out


@pytest.mark.scanner
@pytest.mark.parametrize(
    ("staged_config", "exit_code"),
    [
        ('[gate]\nlegacy = "fail"\nwhatever = 1\n[future]\nkey = 1\n', 0),
        ('[secrets]\nallow = [{ rule = "r" }]\n[gate]\nlegacy = "fail"\n', 2),
    ],
    ids=["unknown gate keys and tables are ignored", "a malformed allowance is still refused"],
)
def test_the_staged_scan_reads_only_the_secrets_table(build_repo, installed_scanners, staged_config, exit_code):
    """VERDICT-58: only [secrets] of the staged config is validated: other tables and keys are ignored, a malformed
    allowance is still refused."""
    repo = build_repo({"README": "hello\n"})
    stage(repo, {"quality/config.toml": staged_config})
    assert staged_door(repo) == exit_code


@pytest.mark.scanner
def test_an_unreadable_allow_list_is_exit_2(build_repo, installed_scanners, capsys):
    """#25 rule 4: a staged allow list that cannot be read is exit 2."""
    repo = build_repo({"README": "hello\n"})
    stage(repo, {"quality/config.toml": "not [valid toml"})
    assert staged_door(repo) == 2
    assert "quality/config.toml" in capsys.readouterr().out


@pytest.mark.scanner
@pytest.mark.parametrize(
    ("failing", "expected"),
    [
        ("ls-tree", "cannot look for quality/config.toml in the staged tree"),
        ("show", "quality/config.toml cannot be read from the staged tree"),
    ],
)
def test_git_failing_on_the_staged_allow_list_is_exit_2_in_its_words(
    build_repo, installed_scanners, fake_git, capsys, failing, expected
):
    """#25 rule 4: git that cannot read the staged allow list is exit 2, and the message says which read failed."""
    repo = build_repo({"README": "hello\n"})
    stage(repo, {"quality/config.toml": "[secrets]\n"})
    fake_git(f'case " $* " in *" {failing} "*) echo "{failing} broke" >&2; exit 1;; esac')
    assert staged_door(repo) == 2
    assert f"{expected}: git exited 1" in capsys.readouterr().out


@pytest.mark.scanner
def test_a_repository_folder_that_starts_with_a_dash_is_scanned_as_a_folder(tmp_path, installed_scanners):
    """A folder named `-C` is a repository, never a git option, so its staged key is red."""
    git(tmp_path, "init", "-q", "--", "-C")
    stage(tmp_path / "-C", {"config.txt": KEY_LINE})
    assert door_in(tmp_path / "-C", *STAGED_WORDS) == 1


@pytest.mark.scanner
@pytest.mark.parametrize("name", ["src/a.py", HOSTILE_FILE], ids=["plain-path", "esc-quote-and-bidi-path"])
def test_the_printed_entry_with_a_reason_allows_the_staged_key_with_head_unchanged(build_repo, installed_scanners, capsys, name):
    """The printed lines with a reason, staged beside the key, allow exactly it; nothing is committed."""
    repo = build_repo({"README": "hello\n"})
    stage(repo, {name: KEY_LINE})
    before = head_commit(repo)
    assert staged_door(repo) == 1
    first = capsys.readouterr()
    (finding,) = staged.scan_change(repo).findings
    entry = [line.copy for line in area_block(first.out, Area.SECRETS) if isinstance(line, CopyLine)]
    stage(repo, {config.CONFIG_PATH: '[gate]\nmandatory_jobs = ["checks"]\n' + "\n".join(entry) + '\nreason = "scenario reason"\n'})
    assert staged_door(repo) == 0
    second = capsys.readouterr()
    result = staged.scan_change(repo)
    assert (head_commit(repo), result.findings, [(note.location, note.event) for note in result.notes]) == (
        before, [], [(finding.location, NoteEvent.ALLOWED)],
    )
    assert FAKE_KEY not in first.out + first.err + second.out + second.err


@pytest.mark.scanner
def test_a_staged_run_in_a_subfolder_judges_the_index_the_environment_names(build_repo, installed_scanners, monkeypatch, capsys):
    """STAGED-3, STAGED-4: a hook below the work-tree top, with a relative git directory, an explicit work tree and a
    relative alternate index, judges that index."""
    repo = build_repo({"docs/README": "hello\n"})
    (repo / "docs" / "key.txt").write_text(KEY_LINE)
    alternate_index = repo / ".git" / "alternate-index"
    subprocess.run(
        ["git", "-C", str(repo), *GIT_IDENTITY, "add", "docs/key.txt"], check=True, capture_output=True,
        env=os.environ | {"GIT_INDEX_FILE": str(alternate_index)}, timeout=60,
    )
    monkeypatch.setenv("GIT_DIR", "../.git")
    monkeypatch.setenv("GIT_WORK_TREE", str(repo))
    monkeypatch.setenv("GIT_INDEX_FILE", "../.git/alternate-index")
    assert door_in(repo / "docs", *STAGED_WORDS) == 1
    printed = capsys.readouterr().out
    rows = [(line.location, line.row.event) for line in area_block(printed, Area.SECRETS) if isinstance(line, FindingLine)]
    assert rows == [("docs/key.txt:1", FindingEvent.SECRET)]


@pytest.mark.scanner
@pytest.mark.parametrize("change", ["another file", "the allowed line removed"])
def test_a_commit_that_touches_no_allowed_line_prints_no_unused_row(build_repo, installed_scanners, capsys, change):
    """One change cannot tell a stale entry from one the history still needs, so only the whole-history scan reports it."""
    entry = ("aws-access-token", "docs/key.txt", FAKE_KEY, "a test key")
    repo = build_repo({"docs/key.txt": KEY_LINE, config.CONFIG_PATH: allow_config(entry)})
    stage(repo, {"docs/key.txt": "removed\n"} if change == "the allowed line removed" else {"docs/other.md": "text\n"})
    assert staged_door(repo) == 0
    staged_out = capsys.readouterr().out
    git(repo, "commit", "-q", "-m", "next")
    assert door_in(repo, CHECK_WORD, "secrets") == 0
    whole_out = capsys.readouterr().out
    assert ("unused" in staged_out, "unused" in whole_out, "1 allowed" in whole_out) == (False, False, True)
