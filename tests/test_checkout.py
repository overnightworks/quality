import os
import subprocess
from pathlib import Path

import pytest

from quality import cli
from tests.support.check import hygiene_output as run
from tests.support.git_repositories import make_repo


def not_a_directory(tmp_path):
    target = tmp_path / "a-file"
    target.write_text("x")
    return target


@pytest.mark.integration
@pytest.mark.parametrize(
    "bad_path",
    [not_a_directory, lambda tmp_path: tmp_path / "missing", lambda tmp_path: tmp_path / "."],
    ids=["regular-file", "missing-path", "directory-without-git-repository"],
)
def test_a_path_that_is_no_repository_is_exit_2(tmp_path, capsys, bad_path):
    """#31 rule 1 (S8705): a path that is not a readable git repository is exit 2, never clean."""
    code, output = run(capsys, bad_path(tmp_path))
    assert code == 2
    assert "cannot judge" in output


def locked_parent(build_repo, tmp_path):
    (tmp_path / "locked" / "repo").mkdir(parents=True)
    (tmp_path / "locked").chmod(0)
    return tmp_path / "locked" / "repo"


def bare_repository(build_repo, tmp_path):
    subprocess.run(["git", "init", "-q", "--bare", str(tmp_path / "bare.git")], check=True)
    return tmp_path / "bare.git"


def git_directory(build_repo, tmp_path):
    return build_repo({}) / ".git"


@pytest.mark.parametrize(
    "arrange",
    [
        pytest.param(locked_parent, marks=pytest.mark.skipif(os.geteuid() == 0, reason="root passes a mode-0 parent")),
        bare_repository,
        git_directory,
    ],
    ids=["parent without permission", "bare repository", "inside .git"],
)
@pytest.mark.parametrize("command", [["hygiene"], ["baseline", "write"]])
def test_a_path_git_cannot_read_names_the_path_as_given_and_never_a_traceback(
    build_repo, tmp_path, capsys, arrange, command
):
    """#76 rule 2, cell 15: git's refusal, whichever its cause, is exit 2 naming the PATH."""
    path = arrange(build_repo, tmp_path)
    try:
        code = cli.main([*command, str(path)])
    finally:
        if (tmp_path / "locked").exists():
            (tmp_path / "locked").chmod(0o755)
    err = capsys.readouterr().err
    assert (code, f"{path} is not a readable git repository" in err, "Traceback" in err) == (2, True, False), err


@pytest.mark.integration
def test_the_hint_for_a_path_git_refuses_quotes_it(tmp_path, monkeypatch, capsys):
    """#76 rule 2, cell 16: the command the message advises runs as printed, whatever the PATH holds."""
    (tmp_path / "a b").write_text("x")
    monkeypatch.chdir(tmp_path)
    code = cli.main(["hygiene", "a b"])
    err = capsys.readouterr().err
    assert (code, "a b is not a readable git repository: git rev-parse exited 128 (`git -C 'a b' status` says why)" in err) == (2, True), err


def test_a_working_directory_that_was_removed_is_cannot_judge_not_a_traceback(tmp_path, monkeypatch, capsys):
    """#76 E8, rule 2: a relative PATH cannot be resolved without a working directory."""
    gone = tmp_path / "gone"
    gone.mkdir()
    monkeypatch.chdir(gone)
    gone.rmdir()
    results = [(command, cli.main(command), capsys.readouterr().err) for command in (["hygiene", "."], ["baseline", "write", "."])]
    assert [(code, ". is not a readable git repository: the working directory cannot be read ([Errno 2]" in err, "Traceback" in err) for _, code, err in results] == [(2, True, False)] * 2, results


@pytest.mark.integration
def test_a_machine_without_git_is_cannot_judge_not_a_traceback(tmp_path, monkeypatch, capsys):
    """#76 rule 2: git missing from the PATH names that git did not run."""
    repo = make_repo(tmp_path, {"src/a.py": "x = 1\n"})
    nowhere = tmp_path / "empty-bin"
    nowhere.mkdir()
    monkeypatch.setenv("PATH", str(nowhere))
    code = cli.main(["hygiene", str(repo)])
    err = capsys.readouterr().err
    assert (code, f"{repo} is not a readable git repository: git did not run ([Errno 2]" in err, "Traceback" in err) == (2, True, False), err


def make_submodule_path(tmp_path: Path) -> tuple[Path, Path]:
    (tmp_path / "inner").mkdir()
    (tmp_path / "outer").mkdir()
    inner = make_repo(tmp_path / "inner", {"src/a.py": "x = 1\n"})
    outer = make_repo(tmp_path / "outer", {})
    subprocess.run(["git", "-C", str(outer), "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(inner), "sub"], check=True)
    return outer, outer / "sub"


def make_linked_worktree_path(tmp_path: Path) -> tuple[Path, Path]:
    (tmp_path / "main").mkdir()
    main = make_repo(tmp_path / "main", {"src/a.py": "x = 1\n"})
    linked = tmp_path / "linked"
    subprocess.run(["git", "-C", str(main), "worktree", "add", "-q", str(linked)], check=True)
    return main, linked


@pytest.mark.integration
@pytest.mark.parametrize("make", [make_submodule_path, make_linked_worktree_path], ids=["submodule", "linked worktree"])
def test_a_path_in_a_submodule_or_linked_worktree_means_its_own_top(tmp_path, capsys, make):
    """#76 rule 1, cell 19 (E7): the write lands in the work tree the PATH lies in, not in the superproject or main checkout."""
    superproject, own_top = make(tmp_path)
    assert cli.main(["baseline", "write", str(own_top / "src")]) == 0
    capsys.readouterr()
    assert ((own_top / "quality/baseline.toml").is_file(), (superproject / "quality").exists()) == (True, False)


@pytest.mark.integration
@pytest.mark.parametrize("path", ["a\nb", "a\u202eb"])
def test_the_hint_for_a_path_git_refuses_has_no_command_when_the_path_is_unprintable(tmp_path, monkeypatch, capsys, path):
    """#86 rule 1, cell 9 and #228 rule 7: a PATH holding a line feed or a format character gets the hint without a `git -C` command."""
    (tmp_path / path).write_text("x")
    monkeypatch.chdir(tmp_path)
    code = cli.main(["hygiene", path])
    err = capsys.readouterr().err
    assert code == 2
    assert "(`git status` run inside it says why)" in err
    assert "git -C" not in err


