import subprocess
from pathlib import Path

import pytest

from quality import checkout, cli
from tests.support.door import CHECK_WORD, FIX_WORDS, check_door, door_in
from tests.support.git_repositories import git, make_repo


def bare_repository(build_repo, tmp_path):
    subprocess.run(["git", "init", "-q", "--bare", str(tmp_path / "bare.git")], check=True)
    return tmp_path / "bare.git"


def git_directory(build_repo, tmp_path):
    return build_repo({}) / ".git"


@pytest.mark.parametrize(
    "arrange",
    [
        bare_repository,
        git_directory,
    ],
    ids=["bare repository", "inside .git"],
)
def test_a_folder_git_cannot_read_names_the_folder_and_never_a_traceback(build_repo, tmp_path, capsys, arrange):
    """Git's refusal, whichever its cause, is exit 2 naming the folder the door started in."""
    path = arrange(build_repo, tmp_path)
    code = door_in(path, CHECK_WORD)
    err = capsys.readouterr().err
    assert (code, str(path) in err, "Traceback" in err) == (2, True, False), err


def test_a_working_directory_that_was_removed_is_cannot_judge_not_a_traceback(tmp_path, monkeypatch, capsys):
    """#76 E8, rule 2: a relative PATH cannot be resolved without a working directory."""
    gone = tmp_path / "gone"
    gone.mkdir()
    monkeypatch.chdir(gone)
    gone.rmdir()
    results = [(command, cli.main(command), capsys.readouterr().err) for command in ([CHECK_WORD], [CHECK_WORD, "debt", "--fix"])]
    assert [(code, ". is not a readable git repository: the working directory cannot be read ([Errno 2]" in err, "Traceback" in err) for _, code, err in results] == [(2, True, False)] * 2, results


@pytest.mark.integration
def test_a_machine_without_git_is_cannot_judge_not_a_traceback(tmp_path, monkeypatch, capsys):
    """#76 rule 2: git missing from the PATH names that git did not run."""
    repo = make_repo(tmp_path, {"src/a.py": "x = 1\n"})
    nowhere = tmp_path / "empty-bin"
    nowhere.mkdir()
    monkeypatch.setenv("PATH", str(nowhere))
    code = check_door(repo)
    err = capsys.readouterr().err
    assert (code, ". is not a readable git repository: git did not run ([Errno 2]" in err, "Traceback" in err) == (2, True, False), err


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
    assert door_in(own_top / "src", *FIX_WORDS) == 0
    capsys.readouterr()
    assert ((own_top / "quality/baseline.toml").is_file(), (superproject / "quality").exists()) == (True, False)


def hash_of(repo: Path, content: str) -> str:
    done = subprocess.run(["git", "-C", str(repo), "hash-object", "--stdin"], input=content, capture_output=True, text=True, check=True)
    return done.stdout.strip()


def committed(repo: Path, name: str, content: str | None) -> None:
    """One commit that writes `content` to `name`, or removes the file where it is None."""
    if content is None:
        git(repo, "rm", "-q", name)
    else:
        (repo / name).write_text(content)
        git(repo, "add", name)
    git(repo, "commit", "-q", "--allow-empty", "-m", "step")


def started(repo: Path) -> Path:
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "log.showRoot", "false")
    return repo


def added_then_removed(repo: Path) -> None:
    committed(repo, "f.toml", "one\n")
    committed(repo, "f.toml", None)


def a_merge_alone_introduced_it(repo: Path) -> None:
    committed(repo, "other", "base\n")
    git(repo, "checkout", "-q", "-b", "side")
    committed(repo, "side", "side\n")
    git(repo, "checkout", "-q", "main")
    committed(repo, "main", "main\n")
    git(repo, "merge", "-q", "--no-commit", "--no-ff", "side")
    (repo / "f.toml").write_text("one\n")
    git(repo, "add", "f.toml")
    git(repo, "commit", "-q", "-m", "merge")


def a_side_branch_added_and_removed(repo: Path) -> None:
    committed(repo, "other", "base\n")
    git(repo, "checkout", "-q", "-b", "side")
    committed(repo, "f.toml", "one\n")
    committed(repo, "f.toml", None)
    git(repo, "checkout", "-q", "main")
    committed(repo, "main", "main\n")
    git(repo, "merge", "-q", "--no-ff", "-m", "merge", "side")


def a_branch_a_merge_dropped(repo: Path) -> None:
    committed(repo, "other", "base\n")
    git(repo, "checkout", "-q", "-b", "side")
    committed(repo, "f.toml", "one\n")
    git(repo, "checkout", "-q", "main")
    committed(repo, "main", "main\n")
    git(repo, "merge", "-q", "-s", "ours", "-m", "merge", "side")


def the_root_commit_then_removed(repo: Path) -> None:
    committed(repo, "f.toml", "one\n")
    committed(repo, "other", "base\n")
    committed(repo, "f.toml", None)


@pytest.mark.parametrize(
    ("arrange", "held"),
    [
        (added_then_removed, True),
        (a_merge_alone_introduced_it, True),
        (a_side_branch_added_and_removed, True),
        (a_branch_a_merge_dropped, True),
        (the_root_commit_then_removed, True),
        (lambda repo: None, False),
    ],
    ids=["added then removed", "a merge alone introduced it", "a side branch added and removed", "a branch a merge dropped",
         "the root commit under log.showRoot=false", "no commit"],
)
def test_the_blobs_of_a_name_are_each_version_any_commit_head_reaches_recorded(tmp_path, arrange, held):
    repo = started(tmp_path)
    arrange(repo)
    expected = (hash_of(repo, "one\n"),) if held else ()
    assert checkout.blobs_of(repo, "f.toml") == expected


def test_a_blob_is_the_bytes_of_a_revision(build_repo):
    repo = build_repo({"f.toml": "one\n"})
    assert checkout.blob(repo, ":f.toml") == b"one\n"


def test_a_shallow_clone_is_shallow_and_a_whole_one_is_not(build_repo, tmp_path):
    repo = build_repo({"f.toml": "one\n"})
    committed(repo, "f.toml", "two\n")
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", "--depth", "1", f"file://{repo}", str(clone)], check=True, capture_output=True)
    assert (checkout.is_shallow(clone), checkout.is_shallow(repo)) == (True, False)
