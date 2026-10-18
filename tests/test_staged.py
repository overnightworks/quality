import subprocess
import sys
from pathlib import Path

import pytest
from conftest import FAKE_KEY, GIT_IDENTITY, OTHER_KEY, allow_config

from quality import cli

KEY_LINE = f"aws_access_key_id = {FAKE_KEY}\n"


def git(repo: Path, *arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", str(repo), *GIT_IDENTITY, *arguments], check=check, capture_output=True, text=True)


def stage(repo: Path, files: dict[str, str]) -> None:
    for name, content in files.items():
        (repo / name).parent.mkdir(parents=True, exist_ok=True)
        (repo / name).write_text(content)
    git(repo, "add", "-f", *files)


def scan_staged(repo: Path, engines: Path) -> int:
    return cli.main(["scan-staged", "--engines", str(engines), str(repo)])


@pytest.fixture
def unborn_repo(tmp_path) -> Path:
    repo = tmp_path / "unborn"
    git(tmp_path, "init", "-q", str(repo))
    return repo


@pytest.mark.engine
def test_a_staged_key_is_red_and_its_value_is_not_printed(build_repo, installed_engines, capsys):
    """#25 rule 1: the staged change is judged; the output names the hash, never the value."""
    repo = build_repo({"README": "hello\n"})
    stage(repo, {"src/a.py": KEY_LINE})
    assert scan_staged(repo, installed_engines) == 1
    out = capsys.readouterr().out
    assert "secret src/a.py:1 - gitleaks rule aws-access-token matched in the staged change (sha256 " in out
    assert FAKE_KEY not in out


@pytest.mark.engine
@pytest.mark.parametrize("where", ["unstaged edit", "untracked file", "only in HEAD", "removed from HEAD"])
def test_a_key_that_is_not_in_the_staged_change_is_green(build_repo, installed_engines, where):
    """#25 rule 1: a key only in the work tree, or only in HEAD, is not what the next commit adds."""
    repo = build_repo({"kept.txt": KEY_LINE} if where.endswith("HEAD") else {"kept.txt": "clean\n"})
    if where == "unstaged edit":
        (repo / "kept.txt").write_text(KEY_LINE)
    if where == "untracked file":
        (repo / "new.txt").write_text(KEY_LINE)
    if where == "removed from HEAD":
        git(repo, "rm", "-q", "kept.txt")
    assert scan_staged(repo, installed_engines) == 0


@pytest.mark.engine
def test_a_repository_without_head_is_scanned(unborn_repo, installed_engines):
    """#25 rule 1: no HEAD is an empty parent, so the whole index is the change."""
    stage(unborn_repo, {"clean.txt": "ok\n"})
    assert scan_staged(unborn_repo, installed_engines) == 0
    stage(unborn_repo, {"config.txt": KEY_LINE})
    assert scan_staged(unborn_repo, installed_engines) == 1


@pytest.mark.engine
@pytest.mark.parametrize(
    ("name", "content"),
    [
        (".gitleaksignore", "config.txt:aws-access-token:1\n"),
        (".gitattributes", "config.txt -diff\n"),
        (".gitleaks.toml", "[allowlist]\nregexes = ['AKIA.*']\n"),
    ],
)
def test_a_staged_ignore_attribute_or_config_file_changes_nothing(build_repo, installed_engines, name, content):
    """#25 rule 1: the scratch work tree is empty, so no file of the scanned repository steers gitleaks or git."""
    repo = build_repo({"README": "hello\n"})
    stage(repo, {name: content, "config.txt": KEY_LINE})
    assert scan_staged(repo, installed_engines) == 1


@pytest.mark.engine
def test_a_gitleaks_allow_comment_changes_nothing(build_repo, installed_engines):
    """#25 rule 1: an inline gitleaks:allow comment is ignored, the staged key is still red."""
    repo = build_repo({"README": "hello\n"})
    stage(repo, {"config.txt": f"aws_access_key_id = {FAKE_KEY}  # gitleaks:allow\n"})
    assert scan_staged(repo, installed_engines) == 1


@pytest.mark.engine
def test_the_live_repositorys_info_attributes_cannot_hide_a_staged_key(build_repo, installed_engines):
    """#25 rule 1 (quality#21 follow-up): git reads $GIT_DIR/info/attributes whatever the environment says; the scratch repository has none."""
    repo = build_repo({"README": "hello\n"})
    (repo / ".git" / "info").mkdir(exist_ok=True)
    (repo / ".git" / "info" / "attributes").write_text("k.txt -diff\n")
    stage(repo, {"k.txt": KEY_LINE})
    assert scan_staged(repo, installed_engines) == 1


@pytest.mark.engine
@pytest.mark.parametrize("staged_config", [True, False], ids=["allowance staged", "allowance only in work tree"])
def test_the_allow_list_is_the_one_of_the_staged_tree(build_repo, installed_engines, staged_config):
    """#25 rule 1.4: an allowed value is green when the staged tree allows it; a work-tree-only file allows nothing."""
    repo = build_repo({"README": "hello\n"})
    config = allow_config(("aws-access-token", "config.txt", FAKE_KEY, "test key"))
    stage(repo, {"config.txt": KEY_LINE})
    if staged_config:
        stage(repo, {"quality/config.toml": config})
    else:
        (repo / "quality").mkdir()
        (repo / "quality" / "config.toml").write_text(config)
    assert scan_staged(repo, installed_engines) == (0 if staged_config else 1)


@pytest.mark.engine
@pytest.mark.parametrize(
    ("staged_change", "exit_code", "expected_line"),
    [
        (
            f"# one\n# two\nrenamed_key = {FAKE_KEY}\n",
            0,
            "allowed config.txt:3 - gitleaks rule aws-access-token allowed: test key",
        ),
        (f"aws_access_key_id = {OTHER_KEY}\n", 1, None),
    ],
    ids=["allowed value moved to another line of its file", "allowed value replaced by another key"],
)
def test_an_allowance_follows_the_value_in_the_staged_scan(
    build_repo, installed_engines, capsys, staged_change, exit_code, expected_line
):
    """#25 rule 1.4, #24 rule 1: the staged diff matches by rule, file and value hash, not by line."""
    config = allow_config(("aws-access-token", "config.txt", FAKE_KEY, "test key"))
    repo = build_repo({"config.txt": KEY_LINE, "quality/config.toml": config})
    stage(repo, {"config.txt": staged_change})
    assert scan_staged(repo, installed_engines) == exit_code
    if expected_line is not None:
        assert expected_line in capsys.readouterr().out


@pytest.fixture
def refusing_hook(build_repo, installed_engines):
    """A repository whose pre-commit hook runs `quality scan-staged`, with a committed clean file."""
    repo = build_repo({"tracked.txt": "clean\n"})
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.write_text(
        f"#!/bin/sh\nexec {sys.executable} -m quality.cli scan-staged . --engines {installed_engines}\n"
    )
    hook.chmod(0o755)
    return repo


def commit_attempt(repo: Path, how: str) -> subprocess.CompletedProcess[str]:
    if how == "plain":
        stage(repo, {"tracked.txt": KEY_LINE})
        return git(repo, "commit", "-q", "-m", "x", check=False)
    (repo / "tracked.txt").write_text(KEY_LINE)  # tracked and modified, not staged
    arguments = {"commit -a": ["commit", "-q", "-a", "-m", "x"], "commit <path>": ["commit", "-q", "-m", "x", "tracked.txt"]}
    return git(repo, *arguments[how], check=False)


@pytest.mark.engine
@pytest.mark.parametrize("how", ["plain", "commit -a", "commit <path>"])
def test_a_real_commit_is_refused_through_the_hook(refusing_hook, how):
    """#25 rule 1: git's own index for the commit (relative or absolute GIT_INDEX_FILE) is the one scanned."""
    before = git(refusing_hook, "rev-parse", "HEAD").stdout
    attempt = commit_attempt(refusing_hook, how)
    assert attempt.returncode != 0
    assert "matched in the staged change" in attempt.stdout + attempt.stderr
    assert git(refusing_hook, "rev-parse", "HEAD").stdout == before


@pytest.mark.engine
def test_a_commit_from_a_linked_worktree_is_refused_through_the_hook(refusing_hook, tmp_path):
    """#25 rule 1: a linked worktree commits with GIT_DIR and an absolute GIT_INDEX_FILE set."""
    linked = tmp_path / "linked"
    git(refusing_hook, "worktree", "add", "-q", "-b", "side", str(linked))
    stage(linked, {"tracked.txt": KEY_LINE})
    attempt = git(linked, "commit", "-q", "-m", "x", check=False)
    assert attempt.returncode != 0
    assert "matched in the staged change" in attempt.stdout + attempt.stderr


@pytest.mark.engine
def test_a_clean_commit_goes_through_the_hook(refusing_hook):
    """#25 rule 1: a commit with nothing to find passes the hook."""
    stage(refusing_hook, {"tracked.txt": "still clean\n"})
    assert git(refusing_hook, "commit", "-q", "-m", "x", check=False).returncode == 0


@pytest.mark.engine
def test_scan_staged_leaves_the_repository_as_it_found_it(build_repo, installed_engines):
    """#25 rule 1.1: write-tree only writes tree objects, so status is unchanged by the scan."""
    repo = build_repo({"README": "hello\n"})
    stage(repo, {"config.txt": KEY_LINE})
    before = git(repo, "status", "--porcelain=v2", "--branch").stdout
    scan_staged(repo, installed_engines)
    assert git(repo, "status", "--porcelain=v2", "--branch").stdout == before


@pytest.mark.engine
def test_an_unmerged_index_is_exit_2(build_repo, installed_engines, capsys):
    """#25 rule 4: an index with unmerged entries is exit 2, never clean."""
    repo = build_repo({"f.txt": "base\n"})
    git(repo, "checkout", "-q", "-b", "other")
    stage(repo, {"f.txt": "other\n"})
    git(repo, "commit", "-q", "-m", "other")
    git(repo, "checkout", "-q", "-")
    stage(repo, {"f.txt": "main\n"})
    git(repo, "commit", "-q", "-m", "main")
    git(repo, "merge", "other", check=False)
    assert scan_staged(repo, installed_engines) == 2
    assert capsys.readouterr().err.startswith("scan-staged: cannot judge - git write-tree failed")


@pytest.mark.engine
def test_a_path_that_is_not_a_repository_is_exit_2(tmp_path, installed_engines, capsys):
    """#25 rule 4: a path that is not a repository is exit 2."""
    assert scan_staged(tmp_path, installed_engines) == 2
    assert capsys.readouterr().err.startswith("scan-staged: cannot judge - ")


@pytest.mark.engine
def test_a_missing_engine_is_exit_2(build_repo, tmp_path, capsys):
    """#25 rule 4: a missing engine is exit 2."""
    assert scan_staged(build_repo({}), tmp_path / "no-engines") == 2
    assert capsys.readouterr().err.startswith("scan-staged: cannot judge - ")


@pytest.mark.engine
@pytest.mark.parametrize(
    ("staged_config", "exit_code"),
    [
        ('[gate]\nlegacy = "fail"\nwhatever = 1\n[future]\nkey = 1\n', 0),
        ('[secrets]\nallow = [{ rule = "r" }]\n[gate]\nlegacy = "fail"\n', 2),
    ],
    ids=["unknown gate keys and tables are ignored", "a malformed allowance is still refused"],
)
def test_the_staged_scan_reads_only_the_secrets_table(build_repo, installed_engines, staged_config, exit_code):
    """#40 rule 1: only [secrets] of the staged config is validated."""
    repo = build_repo({"README": "hello\n"})
    stage(repo, {"quality/config.toml": staged_config})
    assert scan_staged(repo, installed_engines) == exit_code


@pytest.mark.engine
def test_an_unreadable_allow_list_is_exit_2(build_repo, installed_engines, capsys):
    """#25 rule 4: a staged allow list that cannot be read is exit 2."""
    repo = build_repo({"README": "hello\n"})
    stage(repo, {"quality/config.toml": "not [valid toml"})
    assert scan_staged(repo, installed_engines) == 2
    assert "quality/config.toml" in capsys.readouterr().err


@pytest.mark.engine
@pytest.mark.parametrize(
    ("failing", "expected"),
    [
        ("ls-tree", "cannot look for quality/config.toml in the staged tree"),
        ("show", "quality/config.toml cannot be read from the staged tree"),
    ],
)
def test_git_failing_on_the_staged_allow_list_is_exit_2_in_its_words(
    build_repo, installed_engines, fake_git, capsys, failing, expected
):
    """#25 rule 4: git that cannot read the staged allow list is exit 2, and the message says which read failed."""
    repo = build_repo({"README": "hello\n"})
    stage(repo, {"quality/config.toml": "[secrets]\n"})
    fake_git(f'case " $* " in *" {failing} "*) echo "{failing} broke" >&2; exit 1;; esac')
    assert scan_staged(repo, installed_engines) == 2
    assert f"scan-staged: cannot judge - {expected}: {failing} broke" in capsys.readouterr().err


@pytest.mark.engine
@pytest.mark.parametrize("kind", ["a missing path", "a regular file"])
def test_a_path_that_is_not_a_directory_is_exit_2_and_named(kind, tmp_path, installed_engines, capsys):
    """#25 rule 4: a path that is no directory is exit 2, and git's own error names the path (#58 rule 3)."""
    path = tmp_path / "not-a-directory"
    if kind == "a regular file":
        path.write_text("content\n")
    assert scan_staged(path, installed_engines) == 2
    assert str(path) in capsys.readouterr().err


@pytest.mark.engine
def test_a_repository_path_that_starts_with_a_dash_is_scanned_as_a_path(
    tmp_path, installed_engines, monkeypatch, capsys
):
    """#25 rule 4: `-C` reached by a relative path is a repository, never a git option, so its staged key is red."""
    git(tmp_path, "init", "-q", "--", "-C")
    stage(tmp_path / "-C", {"config.txt": KEY_LINE})
    monkeypatch.chdir(tmp_path)
    assert cli.main(["scan-staged", "--engines", str(installed_engines), "--", "-C"]) == 1
