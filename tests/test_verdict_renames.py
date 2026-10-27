"""#149: a pure rename is no loosening. The verdict and the door read the files git renamed between the base and the head."""

import os
import signal
import subprocess
import sys
import time

import pytest

from quality import cli, emergency, verdict
from quality.hygiene import CannotJudge
from quality.ratchet import BASELINE_PATH, Baseline, Key, Metric, Renames, render_baseline
from tests.support.emergency import GATE_CONFIG, ONE_JOB, SONAR_GREEN, changed_nothing, declaring, drive
from tests.support.git_repositories import head_commit, put
from tests.support.processes import process_is_gone
from tests.support.source_code import long_function
from tests.support.verdict import verdict_of
from tests.support.verdict_renames import MOVABLE_FILE, OLD_ENTRY, commit_all, entry, moved_in_head, pulled

OTHER_FILE = "".join(f"nothing like the movable file, row {number}\n" for number in range(30))


def metric_baseline(metric, path, size):
    return render_baseline(Baseline({Key(Metric(metric), path, "" if metric == "file_lines" else "f"): size}, None))


def metric_label(metric, path):
    return f"{metric} {path}" if metric == "file_lines" else f"{metric} {path} f"


@pytest.mark.parametrize("metric", ["file_lines", "function_lines", "ccn"])
@pytest.mark.parametrize(
    ("change", "code"), [(0, 0), (-1, 0), (1, 1)], ids=["equal", "lower", "raised"],
)
def test_a_moved_entry_is_held_against_its_predecessor_for_every_metric(capsys, pull_request, metric, change, code):
    """#149 cells 1-3, R1: equal and lower are green, a raise names the head path and the base's number."""
    size = {"file_lines": 900, "function_lines": 70, "ccn": 20}[metric]
    moved_in_head(
        pull_request, metric_baseline(metric, "src/old.py", size), metric_baseline(metric, "src/new.py", size + change)
    )
    actual_code, output = verdict_of(pull_request[0], capsys)
    assert (actual_code, "Traceback" in output) == (code, False)
    assert (f"size baseline raises {metric_label(metric, 'src/new.py')} {size} -> {size + 1}" in output) == (change == 1)


@pytest.mark.parametrize(
    ("head", "new_text", "expected"),
    [
        (OLD_ENTRY.replace("old", "new").replace('"f"', '"g"'), MOVABLE_FILE,
         "size baseline adds function_lines src/new.py g 70"),
        (OLD_ENTRY.replace("old", "new") + '[ccn."src/new.py"]\n"f" = 16\n', MOVABLE_FILE,
         "size baseline adds ccn src/new.py f 16"),
        (OLD_ENTRY.replace("old", "new"), OTHER_FILE, "size baseline adds function_lines src/new.py f 70"),
    ],
    ids=["moved and renamed symbol", "an existing moved symbol gaining a metric", "moved past git's threshold"],
)
def test_a_new_symbol_or_metric_or_an_undetected_rename_is_an_add(capsys, pull_request, head, new_text, expected):
    """#149 cells 5, 6, 10, R2, R4: git pairs the files, but only the base's own symbols and metrics carry."""
    moved_in_head(pull_request, OLD_ENTRY, head, new_text)
    code, output = verdict_of(pull_request[0], capsys)
    assert (code, expected in output) == (1, True)


def test_a_moved_file_edited_within_the_threshold_still_meets_its_predecessor(capsys, pull_request):
    """#149 cell 9, R4."""
    moved_in_head(pull_request, OLD_ENTRY, OLD_ENTRY.replace("old", "new"), MOVABLE_FILE + "an edit\n")
    assert verdict_of(pull_request[0], capsys) == (0, "verdict: green\n")


def test_a_file_split_off_beside_a_move_has_its_entries_as_adds(capsys, pull_request):
    """#149 cell 8: git pairs the moved half with its predecessor; the split-off file is new."""
    head = OLD_ENTRY.replace("old", "new") + '[ccn."src/split.py"]\n"h" = 16\n'
    moved_in_head(pull_request, OLD_ENTRY, head, head_files={"src/split.py": OTHER_FILE})
    code, output = verdict_of(pull_request[0], capsys)
    assert (code, "size baseline adds ccn src/split.py h 16" in output, "src/new.py" in output) == (1, True, False)


def test_a_commit_that_renames_the_baseline_file_removes_it(capsys, pull_request):
    """#149 cell 19: moved to another name, the baseline is gone from its path, as when deleted."""
    repo, commit_base = pull_request
    commit_base(OLD_ENTRY)
    subprocess.run(["git", "-C", str(repo), "mv", BASELINE_PATH, "quality/baseline-moved.toml"], check=True)
    commit_all(repo, "renames the baseline")
    code, output = verdict_of(repo, capsys)
    assert (code, f"loosened (undeclared): {BASELINE_PATH} removed" in output) == (1, True)


def test_the_baseline_write_after_a_move_still_names_the_moved_entries_as_adds(build_repo, capsys):
    """#149 cell 27: the write is informational and unmapped."""
    repo = build_repo({"src/old.py": long_function(70), BASELINE_PATH: entry("src/old.py", 70, "long_one")})
    subprocess.run(["git", "-C", str(repo), "mv", "src/old.py", "src/new.py"], check=True)
    assert cli.main(["baseline", "write", str(repo)]) == 0
    assert "baseline: adds function_lines src/new.py long_one 70" in capsys.readouterr().out


NEW_ENTRY = OLD_ENTRY.replace("old", "new")


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


UNNAMEABLE_SOURCE = os.fsdecode(b"legacy/\xff.txt")
STALE_LEFTOVER = entry("src/new.py", 200)
RAISED_LEFTOVER = entry("src/new.py", 150)


def test_a_rename_from_a_path_that_is_not_utf8_still_clears_its_target(capsys, pull_request):
    """#149 rule 2, gate B1: no baseline key names the source, but the stale entry at the target never counts."""
    moved_in_head(pull_request, STALE_LEFTOVER, RAISED_LEFTOVER, old=UNNAMEABLE_SOURCE)
    code, output = verdict_of(pull_request[0], capsys)
    assert (code, "size baseline adds function_lines src/new.py f 150" in output) == (1, True)


def test_the_door_refuses_a_rename_from_a_path_that_is_not_utf8_as_the_verdict_does(capsys, tmp_path):
    """#149 rule 2, gate B1: the door reads the same pair of commits."""
    gh = pulled(
        tmp_path, STALE_LEFTOVER, RAISED_LEFTOVER, [(UNNAMEABLE_SOURCE, "src/new.py")],
        base_config=GATE_CONFIG, merge_config=GATE_CONFIG,
    )
    assert drive(gh)[0] == 1
    assert "size baseline adds function_lines src/new.py f 150" in capsys.readouterr().out
    assert gh.merge_calls == []


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
        (b"", Renames({}, frozenset())),
        (b"R100\0a.py\0b.py\0M\0c.py\0", Renames({"a.py": "b.py"}, frozenset({"b.py"}))),
        (b"C100\0a.py\0b.py\0", Renames({}, frozenset())),
        (b"R100\0a\rb.py\0c\nd.py\0", Renames({"a\rb.py": "c\nd.py"}, frozenset({"c\nd.py"}))),
        (b"M\0caf\xe9.txt\0A\0\xff\0D\0\xfe\0", Renames({}, frozenset())),
        (b"R100\0\xff\0b.py\0R100\0a.py\0caf\xe9.py\0R100\0c.py\0d.py\0",
         Renames({"c.py": "d.py"}, frozenset({"b.py", "d.py"}))),
    ],
    ids=["nothing", "only a rename counts", "a copy carries nothing", "bytes stay bytes",
         "an edit, an addition and a deletion are never decoded", "a source that is not UTF-8 still names its target; a target that is not UTF-8 is skipped"],
)
def test_the_rename_list_reads_only_complete_renames(listing, renames):
    """#149 cell 20: the parser."""
    assert verdict.renames_of(listing) == renames


@pytest.mark.parametrize(
    "listing",
    [
        b"R100\0a.py\0", b"R100\0a.py\0b.py", b"M\0",
        b"R100\0a.py\0b.py\0R100\0a.py\0c.py\0",
        b"R100\0a.py\0b.py\0R100\0a.py\0caf\xe9.py\0",
        b"R100\0\xff\0b.py\0R100\0\xff\0c.py\0",
    ],
    ids=["a rename without its target", "output cut off", "a record without its path", "a source renamed twice",
         "a source renamed twice, one target not UTF-8", "a source that is not UTF-8 renamed twice"],
)
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


@pytest.mark.parametrize(
    ("stalls", "refusal"),
    [("fetch", "git did not finish within 0.5 seconds"), ("diff", "cannot list the files git renamed between")],
)
def test_a_step_that_does_not_finish_in_time_refuses_and_leaves_no_child(
    capsys, tmp_path, monkeypatch, fake_git, stalls, refusal
):
    """#149 fetch boundary: the fetch and the diff share the bound, and the whole process group dies with it."""
    monkeypatch.setattr("quality.emergency.FETCH_TIMEOUT_SECONDS", 0.5)
    child = tmp_path / "child-pid"
    gh = failing_fetch(tmp_path)
    fake_git(f'case "$*" in *" {stalls} "*) sleep 300 & echo $! > "{child}"; wait;; esac')
    assert drive(gh)[0] == 1
    assert refusal in capsys.readouterr().out
    assert process_is_gone(int(child.read_text()))
    changed_nothing(gh)


def test_git_output_of_a_failed_rename_list_never_reaches_the_operator(capsys, tmp_path, fake_git):
    """#149 fetch boundary: the door refuses in its own words."""
    gh = failing_fetch(tmp_path)
    fake_git('case "$*" in *" diff "*) echo STDERR_SENTINEL >&2; exit 1;; esac')
    assert drive(gh)[0] == 1
    output = capsys.readouterr().out
    assert ("refused - cannot list the files git renamed" in output, "STDERR_SENTINEL" in output) == (True, False)
    changed_nothing(gh)


def test_inherited_git_routing_and_configuration_do_not_reach_the_diff(capsys, tmp_path, monkeypatch):
    """#149 fetch boundary: the diff runs in the isolated environment of the fetch."""
    gh = failing_fetch(tmp_path)
    for name, value in {
        "GIT_DIR": str(tmp_path / "elsewhere"), "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "diff.orderFile",
        "GIT_CONFIG_VALUE_0": "/dev/stdin",
    }.items():
        monkeypatch.setenv(name, value)
    assert drive(gh)[0] == 0
    assert "merged." in capsys.readouterr().out


@pytest.mark.parametrize(
    ("environment", "expected"),
    [
        ({"GH_CONFIG_DIR": "/g", "XDG_CONFIG_HOME": "/x", "HOME": "/h"}, "/g"),
        ({"XDG_CONFIG_HOME": "/x", "HOME": "/h"}, "/x/gh"),
        ({"HOME": "/h"}, "/h/.config/gh"),
    ],
    ids=["GH_CONFIG_DIR", "XDG_CONFIG_HOME", "home"],
)
def test_gh_keeps_the_login_it_would_use_without_the_changed_home(environment, expected):
    assert emergency.gh_configuration_directory(environment) == expected


def unauthorized(handler):
    handler.send_response(401)
    handler.send_header("WWW-Authenticate", 'Basic realm="x"')
    handler.end_headers()


def test_a_netrc_in_the_home_is_never_sent_and_gh_finds_its_login_from_the_empty_home(
    capsys, tmp_path, monkeypatch, stub_server
):
    """#149 fetch boundary: gh is the only credential source, and its helper still sees the real configuration."""
    origin, heard = stub_server(unauthorized)
    home = tmp_path / "home"
    home.mkdir()
    (home / ".netrc").write_text("machine 127.0.0.1 login probe password netrc-secret\n")
    helper_saw = tmp_path / "helper-saw"
    bin_directory = tmp_path / "bin"
    bin_directory.mkdir()
    (bin_directory / "gh").write_text(f'#!/bin/sh\necho "$GH_CONFIG_DIR $HOME" > "{helper_saw}"\nexit 1\n')
    (bin_directory / "gh").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_directory}:{os.environ['PATH']}")
    monkeypatch.setenv("HOME", str(home))
    for name in ("GH_CONFIG_DIR", "XDG_CONFIG_HOME"):
        monkeypatch.delenv(name, raising=False)
    gh = failing_fetch(tmp_path)
    gh.remote = f"{origin}/acme/widgets.git"
    assert drive(gh)[0] == 1
    assert heard
    assert all("Authorization" not in headers for headers in heard)
    configuration, helper_home = helper_saw.read_text().split()
    assert (configuration, helper_home != str(home)) == (str(home / ".config" / "gh"), True)


SIGTERM_SCRIPT = """
import os, socket
from quality import emergency
server = socket.socket()
server.bind(("127.0.0.1", 0))
server.listen(5)
print(server.getsockname()[1], flush=True)
emergency.read_renames_of_pull(
    f"http://127.0.0.1:{server.getsockname()[1]}/x.git", "a" * 40, "b" * 40, {"PATH": os.environ["PATH"]}
)
"""


def test_a_sigterm_during_the_fetch_leaves_no_repository_behind(tmp_path):
    """#149 fetch boundary: the repository is removed on interruption too, through the engines' scratch directory."""
    scratch_parent = tmp_path / "scratch-parent"
    scratch_parent.mkdir()
    door = subprocess.Popen(
        [sys.executable, "-c", SIGTERM_SCRIPT], env=os.environ | {"TMPDIR": str(scratch_parent)},
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    try:
        door.stdout.readline()
        deadline = time.monotonic() + 30
        while not list(scratch_parent.glob("*/repository/HEAD")):
            assert time.monotonic() < deadline, "the door never created its repository"
            time.sleep(0.05)
        door.terminate()
        assert door.wait(timeout=30) == 128 + signal.SIGTERM
    finally:
        door.kill()
        door.communicate()
    assert list(scratch_parent.iterdir()) == []


NON_UTF8_PATH = os.fsdecode(b"assets/caf\xe9.txt")


@pytest.mark.parametrize("baseline", [None, OLD_ENTRY], ids=["without baselines", "with baselines"])
def test_a_pull_request_that_edits_a_path_that_is_not_utf8_is_judged_as_before(capsys, pull_request, baseline):
    """#149 R4: a file git does not pair counts as new; the rename list never decodes an edit's path."""
    repo, commit_base = pull_request
    put(repo / NON_UTF8_PATH, "one\n")
    commit_base(baseline)
    put(repo / NON_UTF8_PATH, "two\n")
    commit_all(repo, "edits the file")
    assert verdict_of(repo, capsys) == (0, "verdict: green\n")


@pytest.mark.parametrize(
    ("base", "head"), [(None, OLD_ENTRY), (OLD_ENTRY, None)], ids=["no base baseline", "no head baseline"]
)
def test_the_diff_is_asked_for_only_when_both_sides_hold_a_baseline(capsys, pull_request, fake_git, tmp_path, base, head):
    """#149: one decider for both callers; any other pair is judged without renames."""
    repo, commit_base = pull_request
    put(repo / "src/old.py", MOVABLE_FILE)
    commit_base(base)
    put(repo / BASELINE_PATH, head)
    commit_all(repo, "head")
    calls = tmp_path / "git-calls"
    fake_git(f'echo "$@" >> "{calls}"')
    verdict_of(repo, capsys)
    assert " diff " not in f" {calls.read_text()} "


UNFINISHED_LISTS = [
    ("R100\\0a.py\\0", "git's list of renamed files ended inside a record"),
    ("R100\\0a.py\\0b.py\\0R100\\0a.py\\0c.py\\0", "git listed one source as renamed twice"),
]


def listing_script(printed):
    return f"case \"$*\" in *\" diff \"*) printf '{printed}'; exit 0;; esac"


@pytest.mark.parametrize(("printed", "cause"), UNFINISHED_LISTS, ids=["cut off", "a source renamed twice"])
def test_a_rename_list_git_did_not_finish_is_exit_two_at_the_verdict(capsys, pull_request, fake_git, printed, cause):
    """#149 cell 20: the adapter turns the parser's refusal into exit 2."""
    moved_in_head(pull_request, OLD_ENTRY, NEW_ENTRY)
    fake_git(listing_script(printed))
    code, output = verdict_of(pull_request[0], capsys)
    assert (code, f"cannot judge - {cause}" in output) == (2, True)


@pytest.mark.parametrize(("printed", "cause"), UNFINISHED_LISTS, ids=["cut off", "a source renamed twice"])
def test_a_rename_list_git_did_not_finish_is_a_refusal_at_the_door(capsys, tmp_path, fake_git, printed, cause):
    """#149 cell 20: the door never substitutes an empty map."""
    gh = pulled(tmp_path, OLD_ENTRY, NEW_ENTRY, MOVE)
    fake_git(listing_script(printed))
    assert drive(gh)[0] == 1
    assert "refused - cannot list the files git renamed between the pull request's commits" in capsys.readouterr().out
    changed_nothing(gh)
