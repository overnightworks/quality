"""`quality check debt --fix` follows a rename the index records since the ceiling's last commit."""

from pathlib import Path

import pytest

from quality.ratchet import BASELINE_PATH
from tests.support.clones import function as clone_function
from tests.support.door import CHECK_WORD, FIX_WORDS, digest, door_in, fixed, restore_step
from tests.support.git_repositories import git, head_commit, put, shallow_clone
from tests.support.source_code import long_function
from tests.support.verdict import verdict_streams
from tests.support.verdict_renames import commit_all


def ceiling(*entries: tuple[str, int]) -> str:
    """A ceiling holding `long_one` of each (path, value) and no clone pair."""
    tables = "".join(f'[function_lines."{path}"]\nlong_one = {value}\n\n' for path, value in entries)
    return f"{tables}[clones]\n"


def checked(capsys, repo: Path) -> tuple[int, str]:
    code = door_in(repo, CHECK_WORD, "debt", clock=lambda: 0.0)
    return code, capsys.readouterr().out


def based(pull_request, files: dict[str, str], held: str) -> Path:
    """The pull request's base commit holds `files` and the ceiling `held`."""
    repo, commit_base = pull_request
    for name, text in files.items():
        put(repo / name, text)
    commit_base(held)
    return repo


def old_file_held(pull_request) -> Path:
    return based(pull_request, {"src/old.py": long_function(70)}, ceiling(("src/old.py", 70)))


def moved_with_git(pull_request) -> Path:
    repo = old_file_held(pull_request)
    git(repo, "mv", "src/old.py", "src/new.py")
    return repo


def moved_and_committed(pull_request) -> Path:
    repo = moved_with_git(pull_request)
    commit_all(repo, "move")
    return repo


def moved_and_staged_with_add_all(pull_request) -> Path:
    repo = old_file_held(pull_request)
    (repo / "src/old.py").rename(repo / "src/new.py")
    git(repo, "add", "-A")
    return repo


def moved_and_shrunk(pull_request) -> Path:
    repo = moved_with_git(pull_request)
    put(repo / "src/new.py", long_function(65))
    return repo


def a_directory_moved(pull_request) -> Path:
    repo = based(pull_request, {"src/pkg/old.py": long_function(70)}, ceiling(("src/pkg/old.py", 70)))
    git(repo, "mv", "src/pkg", "src/lib")
    return repo


def moved_twice_and_committed(pull_request) -> Path:
    repo = based(pull_request, {"src/a.py": long_function(70)}, ceiling(("src/a.py", 70)))
    for source, target in (("a", "b"), ("b", "c")):
        git(repo, "mv", f"src/{source}.py", f"src/{target}.py")
        commit_all(repo, f"move {source}")
    return repo


def fixed_and_committed_between_two_moves(pull_request) -> Path:
    repo = based(pull_request, {"src/a.py": long_function(70)}, ceiling(("src/a.py", 70)))
    git(repo, "mv", "src/a.py", "src/b.py")
    door_in(repo, *FIX_WORDS, clock=lambda: 0.0)
    commit_all(repo, "first move")
    git(repo, "mv", "src/b.py", "src/c.py")
    return repo


def a_clone_pair_moved(pull_request) -> Path:
    files = {"tests/test_a.py": clone_function("run", "a", "b"), "tests/test_b.py": clone_function("check", "c", "d")}
    held = '[clones."tests/test_a.py"]\nrun = [["tests/test_b.py", "check"]]\n'
    repo = based(pull_request, files, held)
    git(repo, "mv", "tests/test_b.py", "tests/test_c.py")
    return repo


def the_ceiling_changed_on_a_merged_side_branch(pull_request) -> Path:
    repo = based(pull_request, {"src/old.py": long_function(70)}, ceiling(("src/old.py", 80)))
    trunk = git(repo, "branch", "--show-current").stdout.strip()
    git(repo, "checkout", "-q", "-b", "side")
    put(repo / BASELINE_PATH, ceiling(("src/old.py", 70)))
    commit_all(repo, "lower on the side branch")
    git(repo, "checkout", "-q", trunk)
    put(repo / "main.txt", "main\n")
    commit_all(repo, "main moves on")
    git(repo, "merge", "-q", "--no-ff", "-m", "merge", "side")
    git(repo, "mv", "src/old.py", "src/new.py")
    return repo


def moved_where_git_renames_nothing_by_default(pull_request) -> Path:
    repo = old_file_held(pull_request)
    git(repo, "config", "diff.renames", "false")
    git(repo, "mv", "src/old.py", "src/new.py")
    return repo


def written_as(path: str, value: int = 70) -> str:
    return f'[function_lines."{path}"]\n"long_one" = {value}\n\n[clones]\n'


def moved_note(target: str, source: str) -> str:
    return f"{target}  moved  function long_one 70 lines from {source}\n"


@pytest.mark.parametrize(
    ("arrange", "written", "note"),
    [
        pytest.param(
            moved_with_git, written_as("src/new.py"), moved_note("src/new.py", "src/old.py"), id="git mv, uncommitted"
        ),
        pytest.param(
            moved_and_committed, written_as("src/new.py"), moved_note("src/new.py", "src/old.py"), id="committed move"
        ),
        pytest.param(
            moved_and_staged_with_add_all,
            written_as("src/new.py"),
            moved_note("src/new.py", "src/old.py"),
            id="staged with git add -A",
        ),
        pytest.param(
            moved_and_shrunk,
            written_as("src/new.py", 65),
            "src/new.py    moved    function long_one 70 lines from src/old.py\n",
            id="moved and shrunk",
        ),
        pytest.param(
            a_directory_moved,
            written_as("src/lib/old.py"),
            moved_note("src/lib/old.py", "src/pkg/old.py"),
            id="a directory move",
        ),
        pytest.param(
            moved_twice_and_committed,
            written_as("src/c.py"),
            moved_note("src/c.py", "src/a.py"),
            id="successive committed moves",
        ),
        pytest.param(
            fixed_and_committed_between_two_moves,
            written_as("src/c.py"),
            moved_note("src/c.py", "src/b.py"),
            id="fixed and committed between two moves",
        ),
        pytest.param(
            a_clone_pair_moved,
            '[clones."tests/test_a.py"]\nrun = [["tests/test_c.py", "check"]]\n',
            "tests/test_a.py  moved  pair tests/test_a.py run <-> tests/test_c.py check"
            " · was tests/test_a.py run <-> tests/test_b.py check\n",
            id="a moved clone pair",
        ),
        pytest.param(
            the_ceiling_changed_on_a_merged_side_branch,
            written_as("src/new.py"),
            moved_note("src/new.py", "src/old.py"),
            id="the ceiling last changed on a merged side branch",
        ),
        pytest.param(
            moved_where_git_renames_nothing_by_default,
            written_as("src/new.py"),
            moved_note("src/new.py", "src/old.py"),
            id="diff.renames off",
        ),
    ],
)
def test_a_fixed_move_passes_check_and_the_verdict(pull_request, capsys, arrange, written, note):
    repo = arrange(pull_request)
    capsys.readouterr()
    code, fix_out, _ = fixed(capsys, repo, *FIX_WORDS)
    assert (code, (repo / BASELINE_PATH).read_text().endswith(written), note in fix_out) == (0, True, True), fix_out
    assert checked(capsys, repo)[0] == 0
    commit_all(repo, "fixed")
    verdict_code, verdict_out, verdict_err = verdict_streams(repo, capsys)
    assert (verdict_code, "✓ green" in verdict_out, verdict_err) == (0, True, ""), verdict_out


def test_a_moved_entry_stands_in_the_pictures_frame(pull_request, capsys):
    repo = moved_with_git(pull_request)
    printed = (
        "  ✓ debt          moved          1 entry · quality/baseline.toml\n"
        "    ✓ src/new.py  moved  function long_one 70 lines from src/old.py\n"
        "✓ clean · ceiling moved 1 entry · 1 area · <1s\n"
    )
    assert fixed(capsys, repo, *FIX_WORDS) == (0, printed, "")


def grown_stale_target(pull_request) -> Path:
    return stale_target_repo(pull_request, 150)


def stale_target_repo(pull_request, lines: int) -> Path:
    held = ceiling(("src/old.py", 100), ("src/new.py", 200))
    repo = based(pull_request, {"src/old.py": long_function(100)}, held)
    git(repo, "mv", "src/old.py", "src/new.py")
    put(repo / "src/new.py", long_function(lines))
    return repo


@pytest.mark.parametrize(
    ("lines", "fix_out", "check_out", "written"),
    [
        pytest.param(
            150,
            (
                1,
                (
                    "  ! debt          1 finding      1 entry lowered · quality/baseline.toml\n"
                    "    ! src/new.py:1  grew     function long_one has 150 lines · ceiling 100\n"
                    "      fix: bring it back to 100 lines or below; a raise is a hand edit and a declared loosening\n"
                    "    ✓ src/new.py    moved    function long_one 100 lines from src/old.py\n"
                    "    ✓ src/new.py    dropped  function long_one 200 lines · left from before the move\n"
                    "! 1 finding in 1 area · ceiling lowered by 1 entry · <1s\n"
                ),
            ),
            (
                1,
                (
                    "  ! debt          1 finding\n"
                    "    ! src/new.py:1  grew  function long_one has 150 lines · ceiling 100\n"
                    "      fix: bring it back to 100 lines or below\n"
                    "! 1 finding in 1 area · <1s\n"
                ),
            ),
            written_as("src/new.py", 100),
            id="grew to 150",
        ),
        pytest.param(
            90,
            (
                0,
                (
                    "  ✓ debt          lowered        2 entries · quality/baseline.toml\n"
                    "    ✓ src/new.py    moved    function long_one 100 lines from src/old.py\n"
                    "    ✓ src/new.py    dropped  function long_one 200 lines · left from before the move\n"
                    "    ✓ src/new.py:1  lowered  function long_one 100 → 90 lines\n"
                    "✓ clean · ceiling lowered by 2 entries · 1 area · <1s\n"
                ),
            ),
            (0, "  ✓ debt          clean          ceiling held · 1 entry, 0 clone pairs\n✓ clean · 1 area · <1s\n"),
            written_as("src/new.py", 90),
            id="shrunk to 90",
        ),
    ],
)
def test_the_stale_target_case(pull_request, capsys, lines, fix_out, check_out, written):
    repo = stale_target_repo(pull_request, lines)
    code, out, err = fixed(capsys, repo, *FIX_WORDS)
    assert ((code, out), err, (repo / BASELINE_PATH).read_text().endswith(written)) == (fix_out, "", True)
    assert checked(capsys, repo) == check_out
    commit_all(repo, "fixed")
    verdict_code, verdict_out, verdict_err = verdict_streams(repo, capsys)
    assert (verdict_code, "✓ green" in verdict_out, verdict_err) == (0, True, ""), verdict_out


@pytest.mark.parametrize(
    "arrange",
    [moved_with_git, grown_stale_target],
    ids=["after a move", "after the stale-target case"],
)
def test_a_second_fix_changes_nothing(pull_request, capsys, arrange):
    repo = arrange(pull_request)
    first = fixed(capsys, repo, *FIX_WORDS)
    written = digest(repo)
    second = fixed(capsys, repo, *FIX_WORDS)
    assert (digest(repo), second[0]) == (written, first[0])


def moved_twice_with_the_first_fix_uncommitted(pull_request) -> Path:
    repo = based(pull_request, {"src/a.py": long_function(70)}, ceiling(("src/a.py", 70)))
    git(repo, "mv", "src/a.py", "src/b.py")
    door_in(repo, *FIX_WORDS, clock=lambda: 0.0)
    git(repo, "mv", "src/b.py", "src/c.py")
    return repo


def raised_by_hand_under_a_renamed_file(pull_request) -> Path:
    repo = based(pull_request, {"src/a.py": long_function(70)}, ceiling(("src/a.py", 70)))
    put(repo / BASELINE_PATH, ceiling(("src/a.py", 80)))
    git(repo, "mv", "src/a.py", "src/c.py")
    return repo


@pytest.mark.parametrize(
    ("arrange", "left_over"),
    [
        pytest.param(
            moved_twice_with_the_first_fix_uncommitted, "src/b.py", id="moved twice, the first fix uncommitted"
        ),
        pytest.param(raised_by_hand_under_a_renamed_file, "src/a.py", id="an uncommitted entry under a renamed file"),
    ],
)
def test_an_entry_git_cannot_place_refuses_and_a_restored_ceiling_is_followed(pull_request, capsys, arrange, left_over):
    repo = arrange(pull_request)
    capsys.readouterr()
    before = digest(repo)
    code, out, err = fixed(capsys, repo, *FIX_WORDS)
    assert (code, "  ✗ debt          not fixed      " in out, left_over in out, digest(repo)) == (2, True, True, before)
    assert err == f"error: 1 of 1 area could not check · 2 findings, 2 of them in those areas\nnext: {restore_step()}\n"
    git(repo, "checkout", "HEAD", "--", BASELINE_PATH)
    code, out, _ = fixed(capsys, repo, *FIX_WORDS)
    assert (code, "src/c.py  moved  function long_one 70 lines from src/a.py" in out) == (0, True)


def test_a_stale_committed_entry_for_a_deleted_file_does_not_stop_a_move_from_being_followed(pull_request, capsys):
    repo = based(pull_request, {"src/old.py": long_function(70)}, ceiling(("src/gone.py", 90), ("src/old.py", 70)))
    git(repo, "mv", "src/old.py", "src/new.py")
    code, out, _ = fixed(capsys, repo, *FIX_WORDS)
    held = (repo / BASELINE_PATH).read_text()
    printed = " ".join(out.split())
    moved, dropped = " ".join(moved_note("src/new.py", "src/old.py").split()), "src/gone.py dropped function long_one is gone"
    assert (code, moved in printed, dropped in printed, "src/gone.py" in held) == (0, True, True, False)


def test_a_ceiling_committed_in_the_root_commit_of_a_whole_repository_is_followed(build_repo, capsys):
    repo = build_repo({"src/old.py": long_function(70), BASELINE_PATH: ceiling(("src/old.py", 70))})
    git(repo, "mv", "src/old.py", "src/new.py")
    code, out, _ = fixed(capsys, repo, *FIX_WORDS)
    assert (code, moved_note("src/new.py", "src/old.py") in out) == (0, True)


@pytest.mark.parametrize(
    ("depth", "fillers", "refused"),
    [pytest.param(1, 0, True, id="depth 1"), pytest.param(3, 1, False, id="deep enough")],
)
def test_a_shallow_clone_cannot_show_the_ceilings_last_commit(build_repo, elsewhere, capsys, depth, fillers, refused):
    origin = build_repo({"src/old.py": long_function(70)})
    for number in range(fillers):
        put(origin / f"filler{number}.txt", f"{number}\n")
        commit_all(origin, f"filler {number}")
    put(origin / BASELINE_PATH, ceiling(("src/old.py", 70)))
    commit_all(origin, "ceiling")
    put(origin / "last.txt", "last\n")
    commit_all(origin, "last")
    clone = shallow_clone(origin, elsewhere, depth)
    git(clone, "mv", "src/old.py", "src/new.py")
    before = digest(clone)
    code, _, err = fixed(capsys, clone, *FIX_WORDS)
    assert (code, err.endswith("next: git fetch --unshallow\n"), digest(clone) == before) == (
        2 if refused else 0,
        refused,
        refused,
    )


@pytest.mark.parametrize(
    ("move", "expected"),
    [
        pytest.param(lambda repo: (repo / "src/old.py").rename(repo / "src/new.py"), (0, False, False), id="plain mv"),
        pytest.param(
            lambda repo: ((repo / "src/old.py").rename(repo / "src/new.py"), git(repo, "add", "src/new.py")),
            (1, False, True),
            id="mv and add the new file",
        ),
    ],
)
def test_a_move_the_index_does_not_record_is_not_followed(pull_request, capsys, move, expected):
    repo = old_file_held(pull_request)
    move(repo)
    before = digest(repo)
    code, out, _ = fixed(capsys, repo, *FIX_WORDS)
    assert ((code, "moved" in out, "new debt" in out), digest(repo)) == (expected, before)


def test_no_committed_ceiling_follows_nothing(pull_request, capsys):
    repo, _ = pull_request
    put(repo / "src/old.py", long_function(70))
    commit_all(repo, "files")
    put(repo / BASELINE_PATH, ceiling(("src/old.py", 70)))
    git(repo, "add", "-f", BASELINE_PATH)
    git(repo, "mv", "src/old.py", "src/new.py")
    before = digest(repo)
    code, out, _ = fixed(capsys, repo, *FIX_WORDS)
    printed = " ".join(out.split())
    assert (code, digest(repo) == before, "moved" in out, "dropped function long_one is gone" in printed, "new debt" in out) == (1, False, False, True, True)
    assert "src/old.py" not in (repo / BASELINE_PATH).read_text()


def test_a_ceiling_whose_last_commit_does_not_parse_refuses(pull_request, capsys):
    repo = based(pull_request, {"src/old.py": long_function(70)}, "[function_lines\n")
    put(repo / BASELINE_PATH, ceiling(("src/old.py", 70)))
    before = digest(repo)
    code, out, err = fixed(capsys, repo, *FIX_WORDS)
    commit = head_commit(repo)
    assert (code, digest(repo)) == (2, before)
    assert f"the ceiling at its last commit {commit}: quality/baseline.toml is not valid TOML" in out
    assert err == (
        "error: 1 of 1 area could not check\n"
        "next: commit the repaired quality/baseline.toml, then run quality check --fix again\n"
    )


@pytest.mark.parametrize(
    ("command", "said"),
    [
        pytest.param("rev-list", "git rev-list exited 9", id="rev-list"),
        pytest.param("diff", "git diff exited 9", id="diff"),
        pytest.param("ls-tree", ": git exited 9", id="ls-tree"),
        pytest.param("show", ": git exited 9", id="show"),
    ],
)
def test_a_failing_git_quotes_nothing(pull_request, fake_git, capsys, command, said):
    repo = moved_with_git(pull_request)
    fake_git(f'case " $* " in *" {command} "*) echo "SECRET-OF-GIT" >&2; exit 9;; esac')
    code, out, err = fixed(capsys, repo, *FIX_WORDS)
    assert (code, said in out, "SECRET-OF-GIT" in out + err) == (2, True, False)


def printing_long_one(lines: int) -> str:
    return "def long_one():\n" + "".join(f"    print({number})\n" for number in range(lines - 1))


def moved_beside_a_grown_file(pull_request) -> Path:
    held = ceiling(("src/old.py", 70), ("src/other.py", 70))
    repo = based(pull_request, {"src/old.py": long_function(70), "src/other.py": printing_long_one(70)}, held)
    git(repo, "mv", "src/old.py", "src/new.py")
    put(repo / "src/other.py", printing_long_one(80))
    return repo


def test_a_move_beside_a_finding_is_named_in_the_debt_row(pull_request, capsys):
    code, out, err = fixed(capsys, moved_beside_a_grown_file(pull_request), *FIX_WORDS)
    assert (code, err) == (1, "")
    assert "  ! debt          1 finding      1 entry moved · quality/baseline.toml\n" in out
