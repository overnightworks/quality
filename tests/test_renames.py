"""#149: a pure rename is no loosening. The verdict and the door read the files git renamed between the base and the head."""

import json
import os
import subprocess

import pytest
from conftest import GIT_IDENTITY, git_pull, head_commit, put
from test_emergency import GATE_CONFIG, ONE_JOB, SONAR_GREEN, FakeGh, changed_nothing, declaring, drive
from test_verdict import verdict_of

from quality import verdict
from quality.config import CONFIG_PATH
from quality.hygiene import CannotJudge
from quality.ratchet import BASELINE_PATH

MOVABLE_FILE = "".join(f"line {number} of a file git can match to its move\n" for number in range(30))
OTHER_FILE = "".join(f"nothing like the movable file, row {number}\n" for number in range(30))
OLD_ENTRY = '[function_lines."src/old.py"]\n"f" = 70\n'


def commit_all(repo, message):
    git = ["git", "-C", str(repo), *GIT_IDENTITY]
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run([*git, "commit", "-q", "-m", message], check=True)


def moved_in_head(
    pull_request, base, head, new_text=MOVABLE_FILE, old="src/old.py", new="src/new.py", head_config=None, base_files=None,
    head_files=None,
):
    """The base commit holds `old` and `base`; the head commit moves it to `new` (holding `new_text`) and holds `head`.

    `base_files` and `head_files` add files to either commit, a head file after the move."""
    repo, commit_base = pull_request
    put(repo / old, MOVABLE_FILE)
    for path, text in (base_files or {}).items():
        put(repo / path, text)
    commit_base(base)
    subprocess.run(["git", "-C", str(repo), "mv", old, new], check=True)
    put(repo / new, new_text)
    put(repo / BASELINE_PATH, head)
    if head_config is not None:
        put(repo / CONFIG_PATH, head_config)
    for path, text in (head_files or {}).items():
        put(repo / path, text)
    commit_all(repo, "head moves the file")


def file_text(name):
    return "".join(f"{name} row {number} that no other file shares\n" for number in range(30))


def moves_in_head(pull_request, base, head, moves):
    """The base commit holds every old path and `base`; the head commit moves each (old, new) and holds `head`."""
    repo, commit_base = pull_request
    for old, _ in moves:
        put(repo / old, file_text(old))
    commit_base(base)
    for old, new in moves:
        subprocess.run(["git", "-C", str(repo), "mv", old, new], check=True)
    put(repo / BASELINE_PATH, head)
    commit_all(repo, "head moves the files")


@pytest.mark.parametrize(
    ("head", "new_text", "code", "expected"),
    [
        (OLD_ENTRY.replace("old", "new"), MOVABLE_FILE, 0, "verdict: green"),
        (OLD_ENTRY.replace("old", "new").replace("70", "60"), MOVABLE_FILE, 0, "verdict: green"),
        (OLD_ENTRY.replace("old", "new").replace("70", "90"), MOVABLE_FILE, 1,
         "size baseline raises function_lines src/new.py f 70 -> 90"),
        (OLD_ENTRY.replace("old", "new").replace('"f"', '"g"'), MOVABLE_FILE, 1,
         "size baseline adds function_lines src/new.py g 70"),
        (OLD_ENTRY.replace("old", "new"), OTHER_FILE, 1, "size baseline adds function_lines src/new.py f 70"),
        (OLD_ENTRY.replace("old", "new"), MOVABLE_FILE + "an edit\n", 0, "verdict: green"),
        (OLD_ENTRY.replace("old", "new") + '[ccn."src/split.py"]\n"h" = 16\n', MOVABLE_FILE, 1, "size baseline adds ccn src/split.py h 16"),
    ],
    ids=["moved unchanged", "moved and lowered", "moved and grown", "moved and renamed symbol", "moved past git's threshold",
         "moved and edited within the threshold", "moved next to a split-off file"],
)
def test_a_pure_rename_is_no_loosening_but_a_raise_or_a_new_symbol_in_it_is(capsys, pull_request, head, new_text, code, expected):
    """#149 R1, R2, R4: git's rename detection gives the moved entry its base path; the raise names the head's path."""
    moved_in_head(pull_request, OLD_ENTRY, head, new_text)
    actual_code, output = verdict_of(pull_request[0], capsys)
    assert (actual_code, expected in output, "Traceback" in output) == (code, True, False)


def pulled(directory, base_baseline, merge_baseline, moves=(), **options):
    """A FakeGh whose pull request has real base and merge commits in a local remote."""
    remote, base_sha, merge_sha = git_pull(directory, base_baseline, merge_baseline, moves)
    return FakeGh(
        base_baseline=base_baseline, merge_baseline=merge_baseline, base_sha=base_sha, merge_sha=merge_sha, remote=remote,
        **options,
    )


NEW_ENTRY = OLD_ENTRY.replace("old", "new")


def entry(path, size, symbol="f", metric="function_lines"):
    return f"[{metric}.{json.dumps(path)}]\n{json.dumps(symbol)} = {size}\n"


@pytest.mark.parametrize(
    ("base", "head", "code", "expected"),
    [
        (entry("src/old.py", 100) + entry("src/new.py", 200), entry("src/new.py", 150), 1,
         "size baseline raises function_lines src/new.py f 100 -> 150"),
        (entry("src/old.py", 100) + entry("src/new.py", 80), entry("src/new.py", 100), 0, "verdict: green"),
        (OLD_ENTRY, NEW_ENTRY + OLD_ENTRY, 1, "size baseline adds function_lines src/new.py f 70"),
        (OLD_ENTRY + entry("src/other.py", 5), NEW_ENTRY + entry("src/other.py", 5) + entry("src/fresh.py", 9), 1,
         "size baseline adds function_lines src/fresh.py f 9"),
    ],
    ids=["a leftover under the target is no allowance", "a lower leftover does not block a pure move",
         "the head keeps the old path so the target is an add", "an unrelated add beside a move is the only red"],
)
def test_a_leftover_does_not_count_and_the_allowance_moves_rather_than_copies(capsys, pull_request, base, head, code, expected):
    """#149 cells 7, 12, 13: rule 2 and rule 1."""
    moved_in_head(pull_request, base, head)
    actual_code, output = verdict_of(pull_request[0], capsys)
    assert (actual_code, expected in output, output.count("loosened (undeclared)")) == (code, True, 0 if code == 0 else 1)


def test_a_moved_allowance_is_not_copied_into_a_later_pull_request(capsys, pull_request, monkeypatch):
    """#149 cell 14: after the move the base holds no `old` entry, so recreating `old` adds it."""
    repo, _ = pull_request
    moved_in_head(pull_request, OLD_ENTRY, NEW_ENTRY)
    monkeypatch.setenv("GATE_BASE_SHA", head_commit(repo))
    put(repo / "src/old.py", OTHER_FILE)
    put(repo / BASELINE_PATH, NEW_ENTRY + OLD_ENTRY)
    commit_all(repo, "recreates old")
    code, output = verdict_of(repo, capsys)
    assert (code, "size baseline adds function_lines src/old.py f 70" in output) == (1, True)


def test_a_move_whose_old_path_is_recreated_in_the_same_pull_request_is_an_add(capsys, pull_request):
    """#149 cell 14b: git reports an edit and an addition, no rename, so the target's entries are adds."""
    moved_in_head(pull_request, OLD_ENTRY, NEW_ENTRY, head_files={"src/old.py": OTHER_FILE})
    code, output = verdict_of(pull_request[0], capsys)
    assert (code, "size baseline adds function_lines src/new.py f 70" in output) == (1, True)


def test_a_swap_in_place_is_two_edits_under_their_own_paths(capsys, pull_request):
    """#149 cell 11: git reports no rename, so only the file that grew is raised."""
    repo, commit_base = pull_request
    put(repo / "src/one.py", MOVABLE_FILE)
    put(repo / "src/two.py", OTHER_FILE)
    commit_base(entry("src/one.py", 70) + entry("src/two.py", 50))
    put(repo / "src/one.py", OTHER_FILE)
    put(repo / "src/two.py", MOVABLE_FILE)
    put(repo / BASELINE_PATH, entry("src/one.py", 50) + entry("src/two.py", 70))
    commit_all(repo, "swap")
    code, output = verdict_of(repo, capsys)
    assert (code, "raises function_lines src/two.py f 50 -> 70" in output, "src/one.py" in output) == (1, True, False)


def test_a_path_with_a_carriage_return_and_one_with_a_newline_each_meet_their_own_predecessor(capsys, pull_request):
    """#149 cell 28: the rename list keeps bytes, so a carriage return is never read as a newline."""
    repo, commit_base = pull_request
    put(repo / "src/o\rld.py", MOVABLE_FILE)
    put(repo / "src/o\nld.py", OTHER_FILE)
    commit_base(entry("src/o\rld.py", 70) + entry("src/o\nld.py", 40))
    for old, new in (("src/o\rld.py", "src/n\rew.py"), ("src/o\nld.py", "src/n\new.py")):
        subprocess.run(["git", "-C", str(repo), "mv", old, new], check=True)
    put(repo / BASELINE_PATH, entry("src/n\rew.py", 70) + entry("src/n\new.py", 40))
    commit_all(repo, "moves both")
    assert verdict_of(repo, capsys) == (0, "verdict: green\n")


STRIP_THE_LIMIT = (
    'n=$#; i=0; while [ $i -lt $n ]; do a=$1; shift; [ "$a" = -l1000 ] || set -- "$@" "$a"; i=$((i+1)); done'
)


@pytest.mark.parametrize(
    ("setting", "limit_flag_kept", "code"),
    [("renames=false", True, 0), ("renames=copies", True, 0), ("renameLimit=1", True, 0), ("renameLimit=1", False, 1)],
    ids=["renames off", "copies", "a rename limit of one", "control: the same limit without -l1000 misses the renames"],
)
def test_a_user_git_config_does_not_change_the_rename_map(
    capsys, pull_request, monkeypatch, fake_git, setting, limit_flag_kept, code
):
    """#149 cell 26 (R4): the explicit flags give git's default map; the control shows the config would otherwise bite."""
    key, value = setting.split("=")
    for name, text in {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": f"diff.{key}", "GIT_CONFIG_VALUE_0": value}.items():
        monkeypatch.setenv(name, text)
    if not limit_flag_kept:
        fake_git(STRIP_THE_LIMIT)
    repo, commit_base = pull_request
    put(repo / "src/one.py", MOVABLE_FILE)
    put(repo / "src/two.py", OTHER_FILE)
    commit_base(entry("src/one.py", 70) + entry("src/two.py", 40))
    for old, new, text in (("one", "uno", MOVABLE_FILE), ("two", "dos", OTHER_FILE)):
        subprocess.run(["git", "-C", str(repo), "mv", f"src/{old}.py", f"src/{new}.py"], check=True)
        put(repo / f"src/{new}.py", text + "an edit\n")
    put(repo / BASELINE_PATH, entry("src/uno.py", 70) + entry("src/dos.py", 40))
    commit_all(repo, "moves and edits both")
    assert verdict_of(repo, capsys)[0] == code


def test_without_a_base_commit_no_renames_are_read(capsys, pull_request, monkeypatch, fake_git, tmp_path):
    """#149 cell 29: a push has no GATE_BASE_SHA; git is never asked for a diff."""
    moved_in_head(pull_request, OLD_ENTRY, NEW_ENTRY)
    monkeypatch.delenv("GATE_BASE_SHA")
    calls = tmp_path / "git-calls"
    fake_git(f'echo "$@" >> "{calls}"')
    assert verdict_of(pull_request[0], capsys) == (0, "verdict: green\n")
    assert not calls.exists() or " diff " not in calls.read_text()


@pytest.mark.parametrize(
    ("listing", "renames"),
    [
        (b"", {}),
        (b"R100\0a.py\0b.py\0M\0c.py\0", {"a.py": "b.py"}),
        (b"C100\0a.py\0b.py\0", {}),
        (b"R100\0a.py\0b.py\0R100\0a.py\0c.py\0", {}),
        (b"R100\0a\rb.py\0c\nd.py\0", {"a\rb.py": "c\nd.py"}),
    ],
    ids=["nothing", "only a rename counts", "a copy carries nothing", "a source named twice carries to neither", "bytes stay bytes"],
)
def test_the_rename_list_reads_only_complete_renames(listing, renames):
    """#149 cell 20: the parser."""
    assert verdict.renames_of(listing) == renames


@pytest.mark.parametrize("listing", [b"R100\0a.py\0", b"R100\0a.py\0b.py", b"M\0", b"R100\0\xff\0b\0"],
                         ids=["a rename without its target", "output cut off", "a record without its path", "not UTF-8"])
def test_a_rename_list_git_did_not_finish_cannot_be_judged(listing):
    """#149 cell 20: fail closed, never an empty map."""
    with pytest.raises(CannotJudge):
        verdict.renames_of(listing)


def test_a_rename_map_git_cannot_list_cannot_be_judged(capsys, pull_request, fake_git):
    """#149 R4: fail-closed, the verdict never guesses that nothing was renamed."""
    moved_in_head(pull_request, OLD_ENTRY, OLD_ENTRY.replace("old", "new"))
    fake_git('case "$*" in *" diff "*) echo boom >&2; exit 1;; esac')
    code, output = verdict_of(pull_request[0], capsys)
    assert (code, "cannot judge - cannot list the files renamed between" in output, "Traceback" in output) == (2, True, False)


MOVE = [("src/old.py", "src/new.py")]
OLD_ENTRY = '[function_lines."src/old.py"]\n"f" = 70\n'
NEW_ENTRY = OLD_ENTRY.replace("old", "new")


def new_entry(size, path="src/new.py"):
    return f'[function_lines."{path}"]\n"f" = {size}\n'


@pytest.mark.parametrize(
    ("base", "head", "options", "code", "expected"),
    [
        (OLD_ENTRY, NEW_ENTRY, {}, 0, "merged."),
        (OLD_ENTRY, new_entry(90), {}, 1, "size baseline raises function_lines src/new.py f 70 -> 90"),
        (new_entry(200, "src/old.py") + new_entry(300), new_entry(250), {}, 1, "size baseline raises function_lines src/new.py f 200 -> 250"),
        (new_entry(100, "src/old.py") + new_entry(80), new_entry(100), {}, 0, "merged."),
        (OLD_ENTRY, new_entry(90), {"checks": SONAR_GREEN, "base_config": ONE_JOB, "merge_config": ONE_JOB + declaring("size baseline raises function_lines src/new.py f 70 -> 90")}, 0, "merged."),
    ],
    ids=["cell 1 a pure move", "cell 3 a raise", "cell 12 a leftover is no allowance", "cell 12 a lower leftover", "cell 22 a declared raise"],
)
def test_the_door_reads_a_move_as_the_verdict_does(capsys, tmp_path, base, head, options, code, expected):
    """#149 cell 23: the same findings and declaration matches, from the pull request's own two commits."""
    gh = pulled(tmp_path, base, head, MOVE, **{"base_config": GATE_CONFIG, "merge_config": GATE_CONFIG, **options})
    assert drive(gh)[0] == code
    assert expected in capsys.readouterr().out
    assert (len(gh.merge_calls) == 1) == (code == 0)


def failing_fetch(tmp_path):
    return pulled(tmp_path, OLD_ENTRY, NEW_ENTRY, MOVE)


def test_a_fetch_that_fails_refuses_before_anything_is_touched(capsys, tmp_path):
    """#149 cell 24: no empty rename map is substituted, so a move is never read as an add by guess."""
    gh = failing_fetch(tmp_path)
    gh.remote = str(tmp_path / "missing.git")
    assert drive(gh)[0] == 1
    output = capsys.readouterr().out
    assert "cannot fetch the pull request's commits to compare renamed files (git exited 128)" in output
    assert "missing.git" not in output
    changed_nothing(gh)
    assert gh.sequence == []


def test_a_fetch_that_needs_a_password_never_asks_for_one(capsys, tmp_path, monkeypatch, stub_server):
    """#149 cell 24: with no usable credentials git refuses; an askpass helper in the environment is never run."""

    def unauthorized(handler):
        handler.send_response(401)
        handler.send_header("WWW-Authenticate", 'Basic realm="x"')
        handler.end_headers()

    origin, heard = stub_server(unauthorized)
    asked = tmp_path / "asked"
    askpass = tmp_path / "askpass"
    askpass.write_text(f"#!/bin/sh\ntouch {asked}\necho secret\n")
    askpass.chmod(0o755)
    for name in ("GIT_ASKPASS", "SSH_ASKPASS"):
        monkeypatch.setenv(name, str(askpass))
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "1")
    gh = failing_fetch(tmp_path)
    gh.remote = f"{origin}/acme/widgets.git"
    no_credentials = tmp_path / "bin"
    no_credentials.mkdir()
    (no_credentials / "gh").write_text("#!/bin/sh\nexit 1\n")
    (no_credentials / "gh").chmod(0o755)
    monkeypatch.setenv("PATH", f"{no_credentials}:{os.environ['PATH']}")
    assert drive(gh)[0] == 1
    assert "cannot fetch the pull request's commits" in capsys.readouterr().out
    assert heard
    assert not asked.exists()
    changed_nothing(gh)


def test_the_door_fetches_exactly_the_pull_requests_base_and_merge_commits(tmp_path, fake_git):
    """#149 cell 25, and the credentials come from gh's protocol only."""
    gh = failing_fetch(tmp_path)
    calls = tmp_path / "git-calls"
    fake_git(f'echo "$@" >> "{calls}"')
    assert drive(gh)[0] == 0
    (fetch,) = [line for line in calls.read_text().splitlines() if " fetch " in line]
    assert fetch.endswith(f"-- {gh.remote} {gh.base_sha} {gh.merge_sha}")
    assert "credential.helper=!gh auth git-credential" in fetch


@pytest.mark.parametrize("fails", [False, True], ids=["after a judgement", "after a failed fetch"])
def test_the_fetched_repository_is_removed(tmp_path, monkeypatch, fails):
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setattr("tempfile.tempdir", str(scratch))
    gh = failing_fetch(tmp_path)
    if fails:
        gh.remote = str(tmp_path / "missing.git")
    drive(gh)
    assert list(scratch.iterdir()) == []


def test_a_fetch_that_does_not_finish_in_time_refuses(capsys, tmp_path, monkeypatch, fake_git):
    monkeypatch.setattr("quality.emergency.FETCH_TIMEOUT_SECONDS", 0.5)
    fake_git('case "$*" in *" fetch "*) exec sleep 30;; esac')
    gh = failing_fetch(tmp_path)
    assert drive(gh)[0] == 1
    assert "git did not finish within 0.5 seconds" in capsys.readouterr().out
    changed_nothing(gh)


CUT_OFF_LIST = "case \"$*\" in *\" diff \"*) printf 'R100\\0a.py\\0'; exit 0;; esac"


def test_a_rename_list_cut_off_is_exit_two_at_the_verdict(capsys, pull_request, fake_git):
    """#149 cell 20: the adapter turns the parser's refusal into exit 2."""
    moved_in_head(pull_request, OLD_ENTRY, NEW_ENTRY)
    fake_git(CUT_OFF_LIST)
    code, output = verdict_of(pull_request[0], capsys)
    assert (code, "cannot judge - git's list of renamed files ended inside a record" in output) == (2, True)


def test_a_rename_list_cut_off_is_a_refusal_at_the_door(capsys, tmp_path, fake_git):
    """#149 cell 20: the door never substitutes an empty map."""
    gh = pulled(tmp_path, OLD_ENTRY, NEW_ENTRY, MOVE)
    fake_git(CUT_OFF_LIST)
    assert drive(gh)[0] == 1
    assert "refused - git's list of renamed files ended inside a record" in capsys.readouterr().out
    changed_nothing(gh)
