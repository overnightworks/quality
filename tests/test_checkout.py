import os
import shutil
import subprocess
from pathlib import Path

import pytest

from quality import checkout, cli
from quality.report import Instruction, Reason
from tests.support.door import CHECK_WORD, FIX_WORDS, check_door, door_in
from tests.support.git_repositories import git, head_commit, make_repo
from tests.support.github_remote import with_github_remote


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
    """CLOSING-26: a bare repository and the inside of `.git` end in exit 2 that names the folder, never a traceback."""
    path = arrange(build_repo, tmp_path)
    code = door_in(path, CHECK_WORD)
    err = capsys.readouterr().err
    assert (code, str(path) in err, "Traceback" in err) == (2, True, False), err


def test_a_working_directory_that_was_removed_is_cannot_judge_not_a_traceback(tmp_path, monkeypatch, capsys):
    """CLOSING-22, CLOSING-26: a working directory that was removed ends `check` and `check --fix` in exit 2 with the
    error, never a traceback."""
    gone = tmp_path / "gone"
    gone.mkdir()
    monkeypatch.chdir(gone)
    gone.rmdir()
    results = [(command, cli.main(command), capsys.readouterr().err) for command in ([CHECK_WORD], [CHECK_WORD, "debt", "--fix"])]
    assert [(code, ". is not a readable git repository: the working directory cannot be read ([Errno 2]" in err, "Traceback" in err) for _, code, err in results] == [(2, True, False)] * 2, results


@pytest.mark.integration
def test_a_machine_without_git_is_cannot_judge_not_a_traceback(tmp_path, monkeypatch, capsys):
    """CLOSING-22, CLOSING-26: git missing from the PATH ends in exit 2 that names that git did not run, never a
    traceback."""
    repo = make_repo(tmp_path, {"src/a.py": "x = 1\n"})
    nowhere = tmp_path / "empty-bin"
    nowhere.mkdir()
    monkeypatch.setenv("PATH", str(nowhere))
    code = check_door(repo)
    err = capsys.readouterr().err
    assert (code, ". is not a readable git repository: git did not run ([Errno 2]" in err, "Traceback" in err) == (2, True, False), err


@pytest.mark.integration
def test_a_git_that_refuses_the_index_listing_after_the_run_started_names_one_step_on_next(build_repo, tmp_path, monkeypatch, capsys):
    """The files and code rows carry the fact; the `git -C` command stands once, on `next:`."""
    repo = build_repo({"src/a.py": "x = 1\n"})
    real_git = shutil.which("git")
    wrapper = tmp_path / "bin" / "git"
    wrapper.parent.mkdir()
    wrapper.write_text(f'#!/bin/sh\ncase "$*" in *"ls-files -z -s"*) exit 3;; esac\nexec {real_git} "$@"\n')
    wrapper.chmod(0o755)
    monkeypatch.setenv("PATH", f"{wrapper.parent}{os.pathsep}{os.environ['PATH']}")
    code = check_door(repo)
    captured = capsys.readouterr()
    command = "git -C . status"
    rows = [line for line in captured.out.splitlines() if line.startswith("  ✗")]
    assert (code, len(rows) >= 2, captured.err.count(command), captured.out.count(command)) == (2, True, 1, 0), captured
    assert all("git ls-files exited 3" in row or "reasons" in row for row in rows), rows


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
    """CHECK-1, CHECK-2: the write lands in the work tree the PATH lies in, not in the superproject or main checkout."""
    superproject, own_top = make(tmp_path)
    assert door_in(own_top / "src", *FIX_WORDS) == 0
    capsys.readouterr()
    assert ((own_top / "quality/baseline.toml").is_file(), (superproject / "quality").exists()) == (True, False)


def hash_of(repo: Path, content: str) -> str:
    done = subprocess.run(["git", "-C", str(repo), "hash-object", "--stdin"], input=content, capture_output=True, text=True, check=True, timeout=60)
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


@pytest.mark.integration
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


@pytest.mark.integration
def test_a_shallow_clone_is_shallow_and_a_whole_one_is_not(build_repo, tmp_path):
    repo = build_repo({"f.toml": "one\n"})
    committed(repo, "f.toml", "two\n")
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", "--depth", "1", f"file://{repo}", str(clone)], check=True, capture_output=True, timeout=60)
    assert (checkout.is_shallow(clone), checkout.is_shallow(repo)) == (True, False)


def a_root_commit(repo: Path) -> checkout.Change:
    committed(repo, "f.toml", "one\n")
    return checkout.Change(head_commit(repo), has_parent=False)


def a_later_commit(repo: Path) -> checkout.Change:
    committed(repo, "f.toml", "one\n")
    committed(repo, "f.toml", "two\n")
    change = checkout.Change(head_commit(repo), has_parent=True)
    committed(repo, "other", "base\n")
    return change


def a_merged_side_branch(repo: Path) -> checkout.Change:
    committed(repo, "other", "base\n")
    git(repo, "checkout", "-q", "-b", "side")
    committed(repo, "f.toml", "one\n")
    change = checkout.Change(head_commit(repo), has_parent=True)
    git(repo, "checkout", "-q", "main")
    committed(repo, "main", "main\n")
    git(repo, "merge", "-q", "--no-ff", "-m", "merge", "side")
    return change


def no_commit(repo: Path) -> None:
    return None


def never_changed(repo: Path) -> None:
    committed(repo, "other", "base\n")


def under_log_follow(repo: Path) -> checkout.Change:
    git(repo, "config", "log.follow", "true")
    return a_root_commit(repo)


@pytest.mark.integration
@pytest.mark.parametrize(
    "arrange",
    [a_root_commit, a_later_commit, a_merged_side_branch, no_commit, never_changed, under_log_follow],
    ids=["root commit", "a later commit", "a merged side branch", "no commit", "never changed", "under log.follow"],
)
def test_the_last_change_of_a_name_is_the_newest_commit_head_reaches_that_changed_it(tmp_path, arrange):
    repo = started(tmp_path)
    expected = arrange(repo)
    assert checkout.last_change(repo, "f.toml") == expected


@pytest.mark.integration
@pytest.mark.parametrize(
    ("move", "listed"),
    [
        (lambda repo: git(repo, "mv", "old.txt", "new.txt"), b"R100\0old.txt\0new.txt\0"),
        (lambda repo: (repo / "old.txt").rename(repo / "new.txt"), b""),
    ],
    ids=["git mv", "plain mv"],
)
def test_the_renames_since_a_commit_are_what_the_index_would_record(build_repo, move, listed):
    repo = build_repo({"old.txt": "".join(f"row {number} of a file git can match to its move\n" for number in range(30))})
    move(repo)
    assert checkout.renamed_since(repo, head_commit(repo)) == listed


@pytest.mark.parametrize("workflow_file", ["gate.yml", "codeql.yml"])
@pytest.mark.parametrize("prefix", ["./", "$/"])
def test_a_local_reference_to_the_gates_own_file_is_a_call_without_a_ref(prefix, workflow_file):
    """HELP-7, FLEET-8: `./` and `$/` both name the repository's own copy."""
    uses = f"{prefix}.github/workflows/{workflow_file}"
    assert checkout.calls_of(uses, workflow_file, ".github/workflows/ci.yml", None) == [checkout.Call(None, ".github/workflows/ci.yml", None)]


def test_the_gates_owner_and_repository_names_are_read_whatever_their_case():
    """FLEET-11: GitHub reads `uses:` owner and repository case-insensitively."""
    uses = "OvernightWorks/Quality/.github/workflows/gate.yml@v1"
    assert checkout.calls_of(uses, "gate.yml", ".github/workflows/ci.yml", "v1.0.0") == [checkout.Call("v1", ".github/workflows/ci.yml", "v1.0.0")]


def gate_uses(ref: str) -> str:
    return f"{checkout.GATE_REPOSITORY}/.github/workflows/gate.yml@{ref}"


def jobs(*uses_lines: str) -> str:
    """A workflow whose jobs each call what one line names, the line written after `uses:` as it stands."""
    return "on: push\njobs:\n" + "".join(f"  job{number}:\n    uses: {line}\n" for number, line in enumerate(uses_lines))


def gate_calls_of(repo: Path, name: str) -> tuple[checkout.Call, ...]:
    return checkout.read_workflows(repo, [name]).gate


@pytest.mark.parametrize(
    ("line", "ref", "release"),
    [
        pytest.param("{uses} # v0.10.0", "{commit}", "v0.10.0", id="one space before the comment"),
        pytest.param("{uses}  # v0.10.0", "{commit}", "v0.10.0", id="two spaces before the comment"),
        pytest.param("{uses} #v0.10.0", "{commit}", "v0.10.0", id="no space after the hash"),
        pytest.param("{uses} # v0.10.0   ", "{commit}", "v0.10.0", id="trailing spaces"),
        pytest.param('"{uses}" # v0.10.0', "{commit}", "v0.10.0", id="a quoted scalar"),
        pytest.param("{upper} # v0.10.0", "{upper_commit}", "v0.10.0", id="upper-case hex"),
        pytest.param("{uses}\n      continued # v0.10.0", "{commit} continued", "v0.10.0", id="the last line of a scalar that goes on"),
    ],
)
def test_a_gate_call_is_its_ref_its_file_and_the_release_its_line_names(build_repo, line, ref, release):
    """HELP-5: the release of a gate call is the tag the comment after its `uses:` value names, in each form written."""
    commit = "5e" * 20
    words = {"uses": gate_uses(commit), "upper": gate_uses(commit.upper()), "commit": commit, "upper_commit": commit.upper()}
    repo = build_repo({".github/workflows/ci.yml": jobs(line.format(**words))})
    assert gate_calls_of(repo, ".github/workflows/ci.yml") == (checkout.Call(ref.format(**words), ".github/workflows/ci.yml", release),)


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("on: push\njobs:\n  gate:\n    uses: {uses}\n    # v0.10.0\n", id="on the next line"),
        pytest.param('on: push\njobs:\n  gate:\n    uses: "{uses} # v0.10.0"\n', id="inside a quoted value"),
        pytest.param("on: push\njobs:\n  gate:\n    uses: {uses} # pinned at v0.10.0\n", id="after other words"),
        pytest.param("on: push\njobs:\n  gate:\n    uses: {uses} # v0.10.0 (old)\n", id="words after the tag"),
        pytest.param("on: push\njobs:\n  gate:\n    uses: {uses} # v1.2\n", id="no release tag"),
        pytest.param("on: push\njobs:\n  gate:\n    uses: |\n      {uses}\n    # v0.10.0\n", id="the line after a block scalar"),
    ],
)
def test_a_comment_elsewhere_names_no_release(build_repo, text):
    """HELP-5: a comment anywhere but after the value on its line names no release."""
    repo = build_repo({".github/workflows/ci.yml": text.format(uses=gate_uses("5e" * 20))})
    assert [call.release for call in gate_calls_of(repo, ".github/workflows/ci.yml")] == [None]


@pytest.mark.integration
@pytest.mark.parametrize(
    ("url", "repositories"),
    [
        pytest.param("https://github.com/acme/tool", ["acme/tool"], id="https"),
        pytest.param("https://github.com/acme/tool.git", ["acme/tool"], id="https with .git"),
        pytest.param("https://github.com/acme/tool/", ["acme/tool"], id="https with a trailing slash"),
        pytest.param("ssh://git@github.com/acme/tool.git", ["acme/tool"], id="ssh"),
        pytest.param("git@github.com:acme/tool.git", ["acme/tool"], id="scp"),
        pytest.param("git@github.com:acme/tool", ["acme/tool"], id="scp without .git"),
        pytest.param("https://GitHub.com/ACME/Tool", ["ACME/Tool"], id="any case, kept as configured"),
        pytest.param("https://gitlab.com/acme/tool", [], id="another host"),
        pytest.param("https://github.com/acme/tool/tree/main", [], id="a path below the repository"),
        pytest.param("/srv/git/acme/tool.git", [], id="a local path"),
        pytest.param("https://user:dummy-token@github.com/acme/tool", [], id="a URL with credentials"),
    ],
)
def test_a_remote_names_a_github_repository_in_git_s_three_url_forms(tmp_path, url, repositories):
    """HELP-7, AGENTS-13: the three forms in any case, and no other host, path or URL with credentials."""
    repo = make_repo(tmp_path, {})
    git(repo, "remote", "add", "origin", url)
    assert checkout.github_repositories(repo) == repositories


@pytest.mark.integration
def test_every_configured_remote_names_its_repository_and_none_names_a_repository_without_remotes(tmp_path):
    """AGENTS-13: a fork beside its upstream, and a repository with no remote."""
    repo = make_repo(tmp_path, {})
    assert checkout.github_repositories(repo) == []
    with_github_remote(repo, "me/tool", remote="fork")
    with_github_remote(repo, "acme/tool", remote="upstream")
    assert sorted(checkout.github_repositories(repo)) == ["acme/tool", "me/tool"]


def unknown(fact: str, step: str) -> checkout.UnknownGatePin:
    return checkout.UnknownGatePin(Reason(fact, (Instruction(step),)))


def not_pinned(workflow: str) -> checkout.UnknownGatePin:
    return unknown(f"the gate pin in {workflow} is not @<commit sha> # v<tag>", "pin the gate as docs/gate.md shows: @<commit sha> # v<tag>")


def unreadable(workflow: str) -> checkout.UnknownGatePin:
    return unknown(f"cannot read {workflow}, so the gate pin is unknown", f"fix {workflow}")


@pytest.mark.integration
@pytest.mark.parametrize(
    ("files", "remotes", "settings", "deleted", "expected"),
    [
        pytest.param({"ci.yml": "on: push\njobs:\n  build:\n    runs-on: ubuntu-latest\n"}, (), (), (), lambda commit: checkout.NoGatePin(), id="no gate call"),
        pytest.param(
            {"ci.yml": "on: push\njobs:\n  build:\n    runs-on: ubuntu-latest\n    steps:\n      - uses: {pin}\n"}, (), (), (),
            lambda commit: checkout.NoGatePin(), id="a step-level uses",
        ),
        pytest.param(
            {"ci.yml": "{own}"}, ("https://github.com/{gate}",), (), (), lambda commit: checkout.OwnGate(), id="quality's checkout, its own gate.yml",
        ),
        pytest.param(
            {"ci.yml": "{own}"}, ("git@github.com:{gate}.git",), (), (), lambda commit: checkout.OwnGate(), id="quality's checkout by an ssh remote",
        ),
        pytest.param(
            {"ci.yml": "{own_and_pin}"}, ("https://github.com/{gate}",), (), (), lambda commit: checkout.OwnGate(),
            id="quality's checkout, own and remote",
        ),
        pytest.param(
            {"ci.yml": "{own}"}, ("https://github.com/acme/shop",), (), (), lambda commit: checkout.NoGatePin(),
            id="an unrelated repository's own gate.yml",
        ),
        pytest.param(
            {"ci.yml": "{own_and_pin}"}, ("https://github.com/acme/shop",), (), (),
            lambda commit: checkout.GatePin(commit, "v0.10.0", (".github/workflows/ci.yml",)), id="an unrelated own gate.yml beside a remote pin",
        ),
        pytest.param(
            {"ci.yml": "{own}"}, ("https://github.com/someone/quality",), (), (), lambda commit: checkout.NoGatePin(), id="a fork's own gate.yml",
        ),
        pytest.param({"ci.yml": "{own}"}, (), (), (), lambda commit: checkout.NoGatePin(), id="no remote, own gate.yml"),
        pytest.param(
            {"ci.yml": "{own}"}, ("https://github.com/{gate}",), (("url./srv/mirror/quality.insteadOf", "https://github.com/{gate}"),), (),
            lambda commit: checkout.OwnGate(), id="a remote rewritten by insteadOf",
        ),
        pytest.param(
            {"ci.yml": "{pinned}"}, (), (), (), lambda commit: checkout.GatePin(commit, "v0.10.0", (".github/workflows/ci.yml",)), id="one pin",
        ),
        pytest.param(
            {"nightly.yml": "{pinned}", "ci.yml": "{pinned}"}, (), (), (),
            lambda commit: checkout.GatePin(commit, "v0.10.0", (".github/workflows/ci.yml", ".github/workflows/nightly.yml")),
            id="one pin in two files",
        ),
        pytest.param(
            {"ci.yml": "{pinned}", "nightly.yml": "{upper_pinned}"}, (), (), (),
            lambda commit: checkout.GatePin(commit, "v0.10.0", (".github/workflows/ci.yml", ".github/workflows/nightly.yml")),
            id="one pin in upper and lower case",
        ),
        pytest.param(
            {"ci.yml": "{pinned}", "nightly.yml": "{older}"}, (), (), (),
            lambda commit: unknown(
                "the workflows pin v0.9.2 in .github/workflows/nightly.yml and v0.10.0 in .github/workflows/ci.yml", "pin one release in every workflow"
            ),
            id="two pins",
        ),
        pytest.param({"ci.yml": "{branch}"}, (), (), (), lambda commit: not_pinned(".github/workflows/ci.yml"), id="a branch ref"),
        pytest.param({"ci.yml": "{bare}"}, (), (), (), lambda commit: not_pinned(".github/workflows/ci.yml"), id="no comment"),
        pytest.param(
            {"bad.yml": "jobs: [\n"}, (), (), (), lambda commit: unreadable(".github/workflows/bad.yml"), id="a workflow YAML cannot read",
        ),
        pytest.param(
            {"bad.yml": "jobs: [\n", "ci.yml": "{own}"}, ("https://github.com/{gate}",), (), (),
            lambda commit: unreadable(".github/workflows/bad.yml"), id="quality's checkout beside a workflow YAML cannot read",
        ),
        pytest.param({"ci.yml": "{pinned}"}, (), (), ("ci.yml",), lambda commit: unreadable(".github/workflows/ci.yml"), id="a workflow deleted from disk"),
        pytest.param(
            {"ci.yml": "{aliased}"}, (), (), (),
            lambda commit: unknown(
                "the workflows pin v0.9.2 in .github/workflows/ci.yml and v0.10.0 in .github/workflows/ci.yml", "pin one release in every workflow"
            ),
            id="an alias beside its anchor names its own release",
        ),
        pytest.param(
            {"ci.yml": "{block_aliased}"}, (), (), (), lambda commit: checkout.GatePin(commit, "v0.9.2", (".github/workflows/ci.yml",)),
            id="an alias of a block scalar names its own release",
        ),
    ],
)
def test_the_gate_pin_of_a_checkout_is_one_state(build_repo, files, remotes, settings, deleted, expected):
    """PIN-8, PIN-9, HELP-5, HELP-7: the tracked workflows pin no gate, quality's own, one release, or an unknown pin
    with its reason."""
    commit, older = "5e" * 20, "4d" * 20
    words = {
        "gate": checkout.GATE_REPOSITORY,
        "pin": gate_uses(commit),
        "own": jobs("./.github/workflows/gate.yml"),
        "own_and_pin": jobs("$/.github/workflows/gate.yml", f"{gate_uses(commit)} # v0.10.0"),
        "pinned": jobs(f"{gate_uses(commit)} # v0.10.0"),
        "upper_pinned": jobs(f"{gate_uses(commit.upper())} # v0.10.0"),
        "aliased": jobs(f"&pin {gate_uses(commit)} # v0.10.0", "*pin # v0.9.2"),
        "block_aliased": f"on: push\nx-pin: &pin |-\n  {gate_uses(commit)}\njobs:\n  job0:\n    uses: *pin # v0.9.2\n",
        "older": jobs(f"{gate_uses(older)} # v0.9.2"),
        "branch": jobs(f"{gate_uses('main')} # v0.10.0"),
        "bare": jobs(gate_uses(commit)),
    }
    repo = build_repo({f".github/workflows/{name}": text.format(**words) for name, text in files.items()})
    for number, url in enumerate(remotes):
        git(repo, "remote", "add", f"remote{number}", url.format(**words))
    for key, value in settings:
        git(repo, "config", key, value.format(**words))
    for name in deleted:
        (repo / ".github/workflows" / name).unlink()
    assert checkout.gate_pin(repo) == expected(commit)


@pytest.mark.integration
@pytest.mark.parametrize(
    ("remote", "is_quality"), [(f"https://github.com/{checkout.GATE_REPOSITORY}", True), ("https://github.com/acme/shop", False)],
    ids=["quality", "another repository"],
)
@pytest.mark.parametrize(
    "uses_lines",
    [
        pytest.param(("./.github/workflows/gate.yml",), id="its own gate.yml"),
        pytest.param(("$/.github/workflows/gate.yml", f"{gate_uses('5e' * 20)} # v0.10.0"), id="its own gate.yml beside a pin"),
        pytest.param((f"{gate_uses('5e' * 20)} # v0.10.0",), id="a pin"),
    ],
)
def test_the_gate_pin_of_workflow_texts_is_the_gate_pin_of_the_same_files(build_repo, remote, is_quality, uses_lines):
    texts = {".github/workflows/ci.yml": jobs(*uses_lines)}
    repo = build_repo(texts)
    git(repo, "remote", "add", "origin", remote)
    assert checkout.gate_pin_of(texts, is_quality) == checkout.gate_pin(repo)


@pytest.mark.parametrize("unread", list(checkout.Unread))
def test_a_workflow_without_a_text_is_the_unknown_pin(unread):
    """PIN-9, HELP-5: a workflow without a text makes the gate pin unknown."""
    texts = {".github/workflows/ci.yml": jobs(f"{gate_uses('5e' * 20)} # v0.10.0"), ".github/workflows/nightly.yml": unread}
    assert checkout.gate_pin_of(texts, False) == unreadable(".github/workflows/nightly.yml")


PULL_REQUEST, TARGET, REVIEW = checkout.PullRequestEvent


def pull_request_events(text: str) -> frozenset[checkout.PullRequestEvent]:
    return checkout.pull_request_events(checkout.composed_document(text))


@pytest.mark.parametrize(
    "text",
    ["x: &a [*a]\n", "concurrency: &c\n  group: g\n  again: *c\n", "jobs: [\n", 'name: "\\U00110000"\n'],
    ids=["a sequence that aliases itself", "a mapping that aliases itself", "a flow sequence left open", "an escape past the last code point"],
)
def test_a_workflow_text_the_loader_refuses_is_listed_as_unreadable(text):
    """PIN-8: a text the loader refuses, a recursive alias among them, is unreadable and never read through a partial copy."""
    assert checkout.workflows_of({".github/workflows/w.yml": text}).unreadable == (".github/workflows/w.yml",)


@pytest.mark.parametrize(
    ("on", "events"),
    [
        ("on: pull_request\n", {PULL_REQUEST}),
        ("on: pull_request_target\n", {TARGET}),
        ("on: pull_request_review\n", {REVIEW}),
        ("on: [push, pull_request]\n", {PULL_REQUEST}),
        ("on:\n  - push\n  - pull_request_review\n", {REVIEW}),
        ("on:\n  pull_request:\n    branches: ['releases/**', '!old']\n", {PULL_REQUEST}),
        ("on:\n  pull_request_target:\n    types: [opened]\n", {TARGET}),
        ("on:\n  push:\n  pull_request:\n  pull_request_review:\n", {PULL_REQUEST, REVIEW}),
        ("on:\n  pull_request:\n  pull_request_target:\n  pull_request_review:\n", {PULL_REQUEST, TARGET, REVIEW}),
    ],
    ids=[
        "string", "string target", "string review", "list", "list review", "mapping with branch globs",
        "mapping with types", "mapping of two", "mapping of three",
    ],
)
def test_a_workflow_is_a_pull_request_root_for_each_of_the_three_events_in_any_shape_of_on(on, events):
    """CONCURRENCY-1: the event as the string, in a list or as a key of the mapping; the mapping's values are not read."""
    assert pull_request_events(f"name: w\n{on}jobs: {{}}\n") == events


@pytest.mark.parametrize(
    "on",
    [
        "",
        "on: push\n",
        "on:\n  push:\n    branches: [main]\n",
        "on:\n  push:\n    branches-ignore: [main]\n",
        "on:\n  push:\n    tags: ['v*']\n",
        "on:\n  schedule:\n    - cron: '1 1 * * *'\n",
        "on: workflow_dispatch\n",
        "on: merge_group\n",
        "on: workflow_call\n",
        "on:\n  workflow_call:\n    inputs: {}\n",
        "on:\n  push:\n    branches: [pull_request]\n",
        "on: pull_requests\n",
    ],
    ids=[
        "no on", "push", "push to main", "push ignoring main", "tags only", "schedule", "dispatch", "merge group",
        "call", "call with inputs", "event name only as a value", "another word",
    ],
)
def test_a_workflow_without_one_of_the_three_events_is_no_pull_request_root(on):
    """CONCURRENCY-1."""
    assert pull_request_events(f"name: w\n{on}jobs: {{}}\n") == frozenset()
