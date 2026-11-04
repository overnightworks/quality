"""The door of `quality check debt --fix`: the frames it prints, what it writes and what it refuses."""

import os
from pathlib import Path

import pytest

from quality import report
from quality.config import CONFIG_PATH
from quality.ratchet import BASELINE_PATH, Key, Metric, load_baseline
from tests.support.architecture import FORBIDDEN, SHOP, contracts_ini
from tests.support.check import copy_block, refusal_row
from tests.support.clones import function as clone_function
from tests.support.config_refusals import check_refusals
from tests.support.door import FIX_WORDS, digest, door_in, fixed, restore_step
from tests.support.generated_files import MANDATORY_JOB, generated_config
from tests.support.git_repositories import git, put, shallow_clone
from tests.support.source_code import branchy_function, long_file, long_function
from tests.support.verdict import verdict_streams


def held_ceiling(value: int = 62, clones: bool = True) -> str:
    return f'[function_lines."src/a.py"]\nlong_one = {value}\n' + ("\n[clones]\n" if clones else "")


def declared_generated(path: str) -> str:
    return f'[gate]\nmandatory_jobs = ["{MANDATORY_JOB}"]\n' + generated_config(path=path)


def test_a_lowered_ceiling_stands_in_the_pictures_frame(build_repo, capsys):
    held = '[ccn."src/cart.py"]\nbranchy = 19\n\n[file_lines]\n"src/orders.py" = 826\n\n[clones]\n'
    repo = build_repo({"src/cart.py": branchy_function(17), "src/orders.py": long_file(812), BASELINE_PATH: held})
    printed = (
        "  ✓ debt          lowered        2 entries · quality/baseline.toml\n"
        "    ✓ src/cart.py:1  lowered  function branchy CCN 19 → 17\n"
        "    ✓ src/orders.py  lowered  file 826 → 812 lines\n"
        "✓ clean · ceiling lowered by 2 entries · 1 area · <1s\n"
    )
    assert fixed(capsys, repo, *FIX_WORDS) == (0, printed, "")
    assert load_baseline((repo / BASELINE_PATH).read_text()).size == {
        Key(Metric.CCN, "src/cart.py", "branchy"): 17,
        Key(Metric.FILE_LINES, "src/orders.py"): 812,
    }


def test_a_first_recording_stands_in_the_pictures_frame(build_repo, capsys):
    repo = build_repo({"src/a.py": long_function(62)})
    printed = (
        "  ✓ debt          recorded       1 entry, 0 clone pairs · quality/baseline.toml is new\n"
        "✓ clean · ceiling recorded · 1 area · <1s\n"
    )
    assert fixed(capsys, repo, *FIX_WORDS) == (0, printed, "")


@pytest.mark.parametrize(
    ("files", "held", "printed", "written"),
    [
        pytest.param(
            {"src/a.py": long_function(71)},
            '[function_lines."src/a.py"]\nlong_one = 64\n\n[clones]\n',
            "  ! debt          1 finding      nothing lowered\n"
            "    ! src/a.py:1  grew  function long_one has 71 lines · ceiling 64\n"
            "      fix: bring it back to 64 lines or below; a raise is a hand edit and a declared loosening\n"
            "! 1 finding in 1 area · <1s\n",
            None,
            id="grew alone",
        ),
        pytest.param(
            {"src/a.py": long_function(62), "src/orders.py": long_file(850)},
            '[function_lines."src/a.py"]\nlong_one = 80\n\n[file_lines]\n"src/orders.py" = 840\n\n[clones]\n',
            "  ! debt          1 finding      1 entry lowered · quality/baseline.toml\n"
            "    ! src/orders.py  grew     file has 850 lines · ceiling 840\n"
            "      fix: bring it back to 840 lines or below; a raise is a hand edit and a declared loosening\n"
            "    ✓ src/a.py:1     lowered  function long_one 80 → 62 lines\n"
            "! 1 finding in 1 area · ceiling lowered by 1 entry · <1s\n",
            '[file_lines]\n"src/orders.py" = 840\n\n[function_lines."src/a.py"]\n"long_one" = 62\n\n[clones]\n',
            id="grew beside a fall",
        ),
    ],
)
def test_what_grew_stays_a_finding_and_what_fell_is_the_only_change(build_repo, capsys, files, held, printed, written):
    repo = build_repo({**files, BASELINE_PATH: held})
    before = (repo / BASELINE_PATH).read_text()
    assert fixed(capsys, repo, *FIX_WORDS) == (1, printed, "")
    assert (repo / BASELINE_PATH).read_text().endswith(before if written is None else written)


def test_nothing_to_lower_leaves_the_files_bytes(build_repo, capsys):
    repo = build_repo({"src/a.py": long_function(62), BASELINE_PATH: "# a comment\n" + held_ceiling()})
    before = (repo / BASELINE_PATH).read_bytes()
    code, out, _ = fixed(capsys, repo, *FIX_WORDS)
    assert (code, out.splitlines()[-1], (repo / BASELINE_PATH).read_bytes()) == (0, "✓ clean · nothing to lower · 1 area · <1s", before)


def test_a_new_entry_is_never_added_to_a_held_ceiling(build_repo, capsys):
    repo = build_repo({"src/a.py": long_function(62), "src/b.py": long_file(850), BASELINE_PATH: held_ceiling()})
    code, out, _ = fixed(capsys, repo, *FIX_WORDS)
    assert (code, "src/b.py" in out, "new debt" in out) == (1, True, True)
    assert "src/b.py" not in (repo / BASELINE_PATH).read_text()


def test_the_fixed_ceiling_is_what_the_check_then_holds(build_repo, capsys):
    repo = build_repo({"src/a.py": long_function(62), BASELINE_PATH: '[function_lines."src/a.py"]\nlong_one = 80\n\n[clones]\n'})
    assert fixed(capsys, repo, *FIX_WORDS)[0] == 0
    code, out, _ = fixed(capsys, repo, "check", "debt")
    assert (code, "can fall" in out) == (0, False)


def test_a_held_pair_the_tree_lost_is_dropped(build_repo, capsys):
    held = '[clones."src/a.py"]\nrun = [["src/gone.py", "run"]]\n'
    repo = build_repo({"src/a.py": "x = 1\n", BASELINE_PATH: held})
    code, out, _ = fixed(capsys, repo, *FIX_WORDS)
    assert (code, "dropped" in out) == (0, True)
    assert load_baseline((repo / BASELINE_PATH).read_text()).clones == frozenset()


def commit_file(repo: Path, name: str, text: str) -> None:
    put(repo / name, text)
    git(repo, "add", "-f", name)
    git(repo, "commit", "-q", "-m", f"write {name}")


def remove_file(repo: Path, name: str) -> None:
    git(repo, "rm", "-q", name)
    git(repo, "commit", "-q", "-m", f"remove {name}")


def sources(build_repo) -> Path:
    repo = build_repo({"src/a.py": long_function(62)})
    git(repo, "branch", "-M", "main")
    return repo


def merged_alone(repo: Path, name: str, text: str) -> None:
    """A merge commit that introduces `name`, which neither parent holds."""
    commit_file(repo, "other.txt", "base\n")
    git(repo, "checkout", "-q", "-b", "side")
    commit_file(repo, "side.txt", "side\n")
    git(repo, "checkout", "-q", "main")
    commit_file(repo, "main.txt", "main\n")
    git(repo, "merge", "-q", "--no-commit", "--no-ff", "side")
    put(repo / name, text)
    git(repo, "add", "-f", name)
    git(repo, "commit", "-q", "-m", "merge")


def removed_from_the_tree(build_repo, text: str) -> Path:
    repo = sources(build_repo)
    commit_file(repo, BASELINE_PATH, text)
    remove_file(repo, BASELINE_PATH)
    return repo


def rm_the_file(build_repo) -> Path:
    repo = sources(build_repo)
    commit_file(repo, BASELINE_PATH, held_ceiling())
    (repo / BASELINE_PATH).unlink()
    return repo


def git_rm_the_file(build_repo) -> Path:
    repo = sources(build_repo)
    commit_file(repo, BASELINE_PATH, held_ceiling())
    git(repo, "rm", "-q", BASELINE_PATH)
    return repo


def a_committed_deletion(build_repo) -> Path:
    return removed_from_the_tree(build_repo, held_ceiling())


def staged_and_never_committed(build_repo) -> Path:
    repo = sources(build_repo)
    put(repo / BASELINE_PATH, held_ceiling())
    git(repo, "add", "-f", BASELINE_PATH)
    (repo / BASELINE_PATH).unlink()
    return repo


def a_merged_side_branch(build_repo) -> Path:
    repo = sources(build_repo)
    git(repo, "checkout", "-q", "-b", "side")
    commit_file(repo, BASELINE_PATH, held_ceiling())
    git(repo, "checkout", "-q", "main")
    commit_file(repo, "main.txt", "main\n")
    git(repo, "merge", "-q", "--no-ff", "-m", "merge", "side")
    remove_file(repo, BASELINE_PATH)
    return repo


def a_branch_a_merge_dropped(build_repo) -> Path:
    repo = sources(build_repo)
    git(repo, "checkout", "-q", "-b", "side")
    commit_file(repo, BASELINE_PATH, held_ceiling())
    git(repo, "checkout", "-q", "main")
    commit_file(repo, "main.txt", "main\n")
    git(repo, "merge", "-q", "-s", "ours", "-m", "merge", "side")
    return repo


def a_merge_that_alone_introduced_it(build_repo) -> Path:
    repo = sources(build_repo)
    merged_alone(repo, BASELINE_PATH, held_ceiling())
    remove_file(repo, BASELINE_PATH)
    return repo


def the_root_commit_then_removed(build_repo) -> Path:
    repo = build_repo({"src/a.py": long_function(62), BASELINE_PATH: held_ceiling()})
    commit_file(repo, "other.txt", "base\n")
    remove_file(repo, BASELINE_PATH)
    return repo


def fix_rows_step() -> str:
    return "fix what the ✗ rows name, then run quality check --fix again"


def refused_pair(next_step: str, found: str = "") -> str:
    return f"error: 1 of 1 area could not check{found}\nnext: {next_step}\n"


@pytest.mark.parametrize(
    "arrange",
    [
        rm_the_file,
        git_rm_the_file,
        a_committed_deletion,
        staged_and_never_committed,
        a_merged_side_branch,
        a_branch_a_merge_dropped,
        a_merge_that_alone_introduced_it,
        the_root_commit_then_removed,
    ],
    ids=["rm", "git rm", "committed deletion", "staged and never committed", "a merged side branch", "a branch a merge dropped",
         "a merge that alone introduced it", "the root commit, then removed"],
)
def test_a_ceiling_git_holds_is_no_first_recording(build_repo, capsys, arrange):
    repo = arrange(build_repo)
    git(repo, "config", "log.showRoot", "false")
    code, out, err = fixed(capsys, repo, *FIX_WORDS)
    assert (code, "  ✗ debt          not fixed      quality/baseline.toml is gone from the tree but git holds it\n" in out) == (2, True)
    assert err == refused_pair(restore_step())
    assert not (repo / BASELINE_PATH).exists()


def lost_by_hand(build_repo) -> Path:
    repo = build_repo({"src/a.py": long_function(62), BASELINE_PATH: held_ceiling()})
    put(repo / BASELINE_PATH, held_ceiling(clones=False))
    return repo


def lost_in_a_commit(build_repo) -> Path:
    repo = sources(build_repo)
    commit_file(repo, BASELINE_PATH, held_ceiling())
    commit_file(repo, BASELINE_PATH, held_ceiling(clones=False))
    return repo


def held_by_the_index_only(build_repo) -> Path:
    repo = sources(build_repo)
    put(repo / BASELINE_PATH, held_ceiling())
    git(repo, "add", "-f", BASELINE_PATH)
    put(repo / BASELINE_PATH, held_ceiling(clones=False))
    return repo


def held_by_a_merge_alone(build_repo) -> Path:
    repo = sources(build_repo)
    merged_alone(repo, BASELINE_PATH, held_ceiling())
    commit_file(repo, BASELINE_PATH, held_ceiling(clones=False))
    return repo


def held_by_the_root_commit(build_repo) -> Path:
    repo = build_repo({"src/a.py": long_function(62), BASELINE_PATH: held_ceiling()})
    commit_file(repo, BASELINE_PATH, held_ceiling(clones=False))
    return repo


@pytest.mark.parametrize(
    "arrange",
    [lost_by_hand, lost_in_a_commit, held_by_the_index_only, held_by_a_merge_alone, held_by_the_root_commit],
    ids=["by hand", "committed", "in the index only", "a table only a merge held", "the root commit, then removed"],
)
def test_a_clones_table_git_holds_is_not_recorded_again(build_repo, capsys, arrange):
    repo = arrange(build_repo)
    git(repo, "config", "log.showRoot", "false")
    before = digest(repo)
    code, out, err = fixed(capsys, repo, *FIX_WORDS)
    assert code == 2
    assert "quality/baseline.toml lost its [clones] table, which git holds" in out
    assert (err, digest(repo)) == (refused_pair(restore_step()), before)


@pytest.mark.parametrize("held", [None, held_ceiling(value=80, clones=False)], ids=["no ceiling", "no table"])
def test_a_shallow_clone_cannot_prove_a_first_recording(build_repo, elsewhere, capsys, held):
    origin = build_repo({"src/a.py": long_function(62), **({} if held is None else {BASELINE_PATH: held})})
    commit_file(origin, "second", "2\n")
    clone = shallow_clone(origin, elsewhere)
    code, out, err = fixed(capsys, clone, *FIX_WORDS)
    assert (code, "  ✗ debt          not fixed      this clone is shallow, so git cannot show the ceiling is new\n" in out) == (2, True)
    assert err.endswith("next: git fetch --unshallow\n")
    assert (clone / BASELINE_PATH).exists() == (held is not None)
    git(clone, "fetch", "-q", "--unshallow")
    assert fixed(capsys, clone, *FIX_WORDS)[0] == 0
    assert load_baseline((clone / BASELINE_PATH).read_text()).size == {Key(Metric.FUNCTION_LINES, "src/a.py", "long_one"): 62}


def test_a_shallow_clone_lowers_a_ceiling_that_holds_its_table(build_repo, elsewhere, capsys):
    origin = build_repo({"src/a.py": long_function(62)})
    commit_file(origin, "second", "2\n")
    commit_file(origin, BASELINE_PATH, held_ceiling(value=80))
    commit_file(origin, "third", "3\n")
    clone = shallow_clone(origin, elsewhere, depth=3)
    assert fixed(capsys, clone, *FIX_WORDS)[0] == 0
    assert load_baseline((clone / BASELINE_PATH).read_text()).size == {Key(Metric.FUNCTION_LINES, "src/a.py", "long_one"): 62}


def an_unlisted_history(build_repo) -> tuple[Path, str]:
    repo = build_repo({"src/a.py": long_function(62)})
    put(repo / BASELINE_PATH, held_ceiling(clones=False))
    return repo, "log"


def a_table_the_index_holds(build_repo) -> tuple[Path, str]:
    repo = build_repo({"src/a.py": long_function(62)})
    put(repo / BASELINE_PATH, held_ceiling())
    git(repo, "add", "-f", BASELINE_PATH)
    put(repo / BASELINE_PATH, held_ceiling(clones=False))
    return repo, "cat-file"


def no_ceiling_anywhere(build_repo) -> tuple[Path, str]:
    return build_repo({"src/a.py": long_function(62)}), "rev-parse --is-shallow-repository"


@pytest.mark.parametrize("arrange", [an_unlisted_history, a_table_the_index_holds, no_ceiling_anywhere], ids=["log", "cat-file", "rev-parse"])
def test_a_failing_git_quotes_nothing(build_repo, fake_git, capsys, arrange):
    repo, command = arrange(build_repo)
    fake_git(f'case " $* " in *" {command} "*) echo "SECRET-OF-GIT" >&2; exit 9;; esac')
    command = command.split()[0]
    code, out, err = fixed(capsys, repo, *FIX_WORDS)
    assert code == 2
    assert (f"git {command} exited 9" in out, "SECRET-OF-GIT" in out + err) == (True, False)


def test_a_restored_ceiling_lowers_and_keeps_what_grew(build_repo, capsys):
    held = '[function_lines."src/a.py"]\nlong_one = 80\n\n[function_lines."src/b.py"]\nlong_one = 64\n\n[clones]\n'
    repo = build_repo({"src/a.py": long_function(62), "src/b.py": long_function(71), BASELINE_PATH: held})
    (repo / BASELINE_PATH).unlink()
    git(repo, "checkout", "--", BASELINE_PATH)
    code, _, _ = fixed(capsys, repo, *FIX_WORDS)
    assert code == 1
    assert load_baseline((repo / BASELINE_PATH).read_text()).size == {
        Key(Metric.FUNCTION_LINES, "src/a.py", "long_one"): 62,
        Key(Metric.FUNCTION_LINES, "src/b.py", "long_one"): 64,
    }


@pytest.mark.integration
def test_a_repository_without_a_commit_takes_a_first_recording(tmp_path, capsys):
    repo = tmp_path / "fresh"
    repo.mkdir()
    git(repo, "init", "-q")
    put(repo / "src/a.py", long_function(62))
    git(repo, "add", "src/a.py")
    code, out, _ = fixed(capsys, repo, *FIX_WORDS)
    assert (code, "recorded" in out) == (0, True)
    assert (repo / BASELINE_PATH).is_file()


def a_version_in_history(content: bytes):
    def arrange(build_repo) -> Path:
        repo = build_repo({"src/a.py": long_function(62)})
        put(repo / BASELINE_PATH, content)
        git(repo, "add", "-f", BASELINE_PATH)
        git(repo, "commit", "-q", "-m", "write the ceiling")
        remove_file(repo, BASELINE_PATH)
        return repo

    return arrange


def a_version_in_the_index(build_repo) -> Path:
    repo = build_repo({"src/a.py": long_function(62)})
    put(repo / BASELINE_PATH, "[clones\n")
    git(repo, "add", "-f", BASELINE_PATH)
    return repo


@pytest.mark.parametrize(
    "arrange",
    [a_version_in_history(b"[clones\n"), a_version_in_history(b"\xff\xfe"), a_version_in_the_index],
    ids=["in history", "not UTF-8", "in the index"],
)
def test_a_version_no_parser_reads_refuses_and_a_clones_table_by_hand_runs_the_fix(build_repo, capsys, arrange):
    repo = arrange(build_repo)
    put(repo / BASELINE_PATH, held_ceiling(clones=False))
    before = digest(repo)
    code, out, err = fixed(capsys, repo, *FIX_WORDS)
    assert (code, "a version of quality/baseline.toml in git cannot be read" in out, digest(repo)) == (2, True, before)
    assert err.endswith(f"next: {restore_step()}\n")
    put(repo / BASELINE_PATH, held_ceiling())
    assert fixed(capsys, repo, *FIX_WORDS)[0] == 0


def tree_of(root: Path) -> dict[str, bytes | str | None]:
    """Every entry under `root` outside `.git`: a file's bytes, a link's target, a folder as None."""
    found: dict[str, bytes | str | None] = {}
    for current, folders, files in os.walk(root):
        folders[:] = [folder for folder in folders if folder != ".git"]
        for name in (*folders, *files):
            path = Path(current) / name
            key = os.fsdecode(path.relative_to(root))
            found[key] = os.readlink(path) if path.is_symlink() else None if path.is_dir() else path.read_bytes()
    return found


def a_ceiling_with(content: bytes):
    def arrange(build_repo, elsewhere) -> Path:
        repo = build_repo({"src/a.py": long_function(62)})
        put(repo / BASELINE_PATH, content)
        git(repo, "add", "-f", BASELINE_PATH)
        return repo

    return arrange


def a_link_out(build_repo, elsewhere) -> Path:
    put(elsewhere / "outside.toml", held_ceiling(clones=False))
    repo = build_repo({"src/a.py": long_function(62)})
    (repo / "quality").mkdir()
    (repo / BASELINE_PATH).symlink_to(elsewhere / "outside.toml")
    git(repo, "add", BASELINE_PATH)
    return repo


def a_directory(build_repo, elsewhere) -> Path:
    return build_repo({"src/a.py": long_function(62), f"{BASELINE_PATH}/inner": held_ceiling()})


def a_file_that_does_not_parse(build_repo, elsewhere) -> Path:
    return build_repo({"src/a.py": long_function(62), "src/broken.py": "def broken(:\n", BASELINE_PATH: held_ceiling(value=80)})


def a_generated_file_python_cannot_parse(build_repo, elsewhere) -> Path:
    return build_repo({
        "src/a.py": long_function(62), "gen/api.py": "def broken(:\n", CONFIG_PATH: declared_generated("gen/api.py"),
        BASELINE_PATH: held_ceiling(value=80),
    })


def a_config_refusal(build_repo, elsewhere) -> Path:
    return build_repo({"src/a.py": long_function(62), CONFIG_PATH: "nope = 1\n", BASELINE_PATH: held_ceiling(value=80)})


def quality_is_a_file(build_repo, elsewhere) -> Path:
    return build_repo({"src/a.py": long_function(62), "quality": "not a folder\n"})


def a_path_that_is_no_utf8(build_repo, elsewhere) -> Path:
    repo = build_repo({"src/a.py": "x = 1\n"})
    put(repo / os.fsdecode(b"src/\xff.py"), long_file(850))
    git(repo, "add", "-A", "-f")
    return repo


@pytest.mark.parametrize(
    ("arrange", "refusal"),
    [
        pytest.param(a_ceiling_with(b"[file_lines\n"), refused_pair(restore_step()), id="no TOML"),
        pytest.param(a_ceiling_with(b"\xff\xfe"), refused_pair(restore_step()), id="not UTF-8"),
        pytest.param(a_ceiling_with(b'[file_lines]\n"src/a.py" = "many"\n'), refused_pair(restore_step()), id="not a count"),
        pytest.param(a_ceiling_with(b"[future]\n"), refused_pair("run the quality release your gate pins"), id="an unknown table"),
        pytest.param(a_link_out, refused_pair(fix_rows_step()), id="a link out"),
        pytest.param(a_directory, refused_pair(fix_rows_step()), id="a directory"),
        pytest.param(a_file_that_does_not_parse, refused_pair(fix_rows_step()), id="a file that does not parse"),
        pytest.param(a_generated_file_python_cannot_parse, refused_pair(fix_rows_step(), " · 1 finding, 1 of them in those areas"), id="a generated file python cannot parse"),
        pytest.param(a_config_refusal, refused_pair(fix_rows_step()), id="a config refusal"),
        pytest.param(quality_is_a_file, refused_pair(fix_rows_step()), id="quality is a file"),
        pytest.param(a_path_that_is_no_utf8, refused_pair(fix_rows_step()), id="a path that is no UTF-8"),
    ],
)
def test_every_refused_fix_writes_nothing_and_names_its_next_step(build_repo, elsewhere, capsys, arrange, refusal):
    repo = arrange(build_repo, elsewhere)
    before = (tree_of(repo), tree_of(elsewhere))
    code, out, err = fixed(capsys, repo, *FIX_WORDS)
    assert (code, "  ✗ debt          not " in out, (tree_of(repo), tree_of(elsewhere))) == (2, True, before)
    assert err == refusal


def a_size_config_and_a_near_copy(build_repo, elsewhere) -> Path:
    near_copies = {"tests/test_a.py": clone_function("run", "a", "b"), "tests/test_b.py": clone_function("check", "c", "d")}
    return build_repo({
        "src/a.py": long_function(62), **near_copies, CONFIG_PATH: generated_config(path="gen/api.py", job=None),
        BASELINE_PATH: held_ceiling(value=80),
    })


def a_generated_file_and_a_grown_entry(build_repo, elsewhere) -> Path:
    return build_repo({
        "src/a.py": long_function(71), "gen/api.py": "def broken(:\n", CONFIG_PATH: declared_generated("gen/api.py"),
        BASELINE_PATH: held_ceiling(value=60),
    })


def a_refused_write_and_an_entry_that_can_fall(build_repo, elsewhere) -> Path:
    repo = build_repo({"src/a.py": long_function(62), BASELINE_PATH: held_ceiling(value=80)})
    (repo / BASELINE_PATH).chmod(0o444)
    return repo


def a_shallow_clone_and_a_grown_entry(build_repo, elsewhere) -> Path:
    return shallow_clone(build_repo({"src/a.py": long_function(71), BASELINE_PATH: held_ceiling(value=60, clones=False)}), elsewhere)


@pytest.mark.parametrize(
    ("arrange", "rows"),
    [
        pytest.param(
            a_size_config_and_a_near_copy,
            ["  ! tests/test_b.py:1  near copy  function check and tests/test_a.py run\n"],
            id="a size config, a near copy",
        ),
        pytest.param(
            a_generated_file_and_a_grown_entry,
            ["  ✗ debt          not checked    gen/api.py is not valid Python (invalid syntax (api.py, line 1))\n",
             "    ! src/a.py:1  grew  function long_one has 71 lines · ceiling 60\n"],
            id="a generated file, a grown entry",
        ),
        pytest.param(
            a_refused_write_and_an_entry_that_can_fall,
            ["  ✗ debt          not fixed      cannot write quality/baseline.toml: [Errno 13] Permission denied\n",
             "    ! src/a.py:1  can fall  function long_one has 62 lines · ceiling 80\n"],
            marks=pytest.mark.skipif(os.geteuid() == 0, reason="root writes a mode-0444 file"),
            id="a refused write, can fall",
        ),
        pytest.param(
            a_shallow_clone_and_a_grown_entry,
            ["  ✗ debt          not fixed      this clone is shallow, so git cannot show the ceiling is new\n",
             "    ! src/a.py:1  grew  function long_one has 71 lines · ceiling 60\n"],
            id="a shallow clone, a grown entry",
        ),
    ],
)
def test_a_refused_fix_keeps_what_each_rule_judged(build_repo, elsewhere, capsys, arrange, rows):
    repo = arrange(build_repo, elsewhere)
    before = tree_of(repo)
    code, out, _ = fixed(capsys, repo, *FIX_WORDS)
    assert (code, [row in out for row in rows], tree_of(repo)) == (2, [True] * len(rows), before)


@pytest.mark.parametrize("refusal", check_refusals(), ids=lambda refusal: refusal.id)
def test_a_config_refusal_keeps_its_fact_and_its_example_apart(build_repo, capsys, refusal):
    repo = build_repo({"src/a.py": long_function(62), BASELINE_PATH: held_ceiling(value=80)})
    if refusal.directory:
        (repo / CONFIG_PATH).mkdir(parents=True)
    else:
        put(repo / CONFIG_PATH, refusal.text)
    code, out, err = fixed(capsys, repo, *FIX_WORDS)
    assert (code, f"{refusal_row('debt')}{refusal.fact}\n" in out) == (2, True)
    if refusal.example is None:
        assert f"next: {report.CONFIG_EXAMPLE_LABEL}:" not in err
    else:
        indented = {f"    {line}" for line in refusal.example}
        assert (err.count(copy_block(refusal.example)), indented & set(out.splitlines())) == (1, set())


def a_compose_file_that_does_not_parse(build_repo, elsewhere) -> Path:
    repo = build_repo({"src/a.py": long_function(62), BASELINE_PATH: held_ceiling(value=80)})
    put(repo / "compose.yaml", "services: [\n")
    git(repo, "add", "compose.yaml")
    return repo


def an_import_contract_config_linked_out(build_repo, elsewhere) -> Path:
    put(elsewhere / "outside.ini", contracts_ini({"guarded": FORBIDDEN}))
    repo = build_repo({**SHOP, "src/a.py": long_function(62), BASELINE_PATH: held_ceiling(value=80)})
    (repo / ".importlinter").symlink_to(elsewhere / "outside.ini")
    git(repo, "add", "-f", ".importlinter")
    return repo


@pytest.mark.parametrize(
    ("areas", "arrange"),
    [(("files", "debt"), a_compose_file_that_does_not_parse), (("debt", "imports"), an_import_contract_config_linked_out)],
    ids=["files", "imports"],
)
def test_another_areas_refusal_leaves_the_fix_standing(build_repo, elsewhere, capsys, areas, arrange):
    repo = arrange(build_repo, elsewhere)
    code, out, _ = fixed(capsys, repo, "check", *areas, "--fix")
    assert code == 2
    assert "  ✓ debt          lowered        1 entry · quality/baseline.toml\n" in out
    assert load_baseline((repo / BASELINE_PATH).read_text()).size == {Key(Metric.FUNCTION_LINES, "src/a.py", "long_one"): 62}


@pytest.mark.parametrize("when", ["before", "after"])
@pytest.mark.parametrize("verbosity", [(), ("-v",)], ids=["quiet", "-v"])
def test_an_interrupted_fix_leaves_the_old_file_or_the_new_one(build_repo, monkeypatch, capsys, when, verbosity):
    repo = build_repo({"src/a.py": long_function(62), BASELINE_PATH: held_ceiling(value=80)})
    before = (repo / BASELINE_PATH).read_bytes()
    real_replace = os.replace

    def interrupted(*arguments, **keywords):
        if when == "before":
            raise KeyboardInterrupt
        real_replace(*arguments, **keywords)
        raise KeyboardInterrupt

    monkeypatch.setattr(os, "replace", interrupted)
    with pytest.raises(KeyboardInterrupt):
        door_in(repo, "check", "debt", "secrets", "--fix", *verbosity)
    assert ((repo / BASELINE_PATH).read_bytes() == before) == (when == "before")
    assert "debt" not in capsys.readouterr().out


@pytest.mark.parametrize("verbosity", [(), ("-v",)], ids=["quiet", "-v"])
def test_under_v_a_fix_keeps_its_write_and_exit(build_repo, capsys, verbosity):
    repo = build_repo({"src/a.py": long_function(62), BASELINE_PATH: held_ceiling(value=80)})
    code, out, _ = fixed(capsys, repo, *FIX_WORDS, *verbosity)
    assert (code, "  ✓ debt          lowered        1 entry · quality/baseline.toml\n" in out) == (0, True)
    assert (repo / BASELINE_PATH).read_text().endswith('[function_lines."src/a.py"]\n"long_one" = 62\n\n[clones]\n')


@pytest.mark.parametrize(
    ("sources", "base", "head", "exit_code"),
    [
        pytest.param({"src/a.py": long_function(62)}, held_ceiling(value=80), '"long_one" = 62\n\n[clones]\n', 0, id="lowered"),
        pytest.param(
            {"src/a.py": long_function(62), "src/orders.py": long_file(850)},
            held_ceiling(value=80) + '\n[file_lines]\n"src/orders.py" = 840\n',
            '[file_lines]\n"src/orders.py" = 840\n\n[function_lines."src/a.py"]\n"long_one" = 62\n\n[clones]\n',
            1,
            id="grew beside a fall",
        ),
        pytest.param({"src/a.py": long_function(62)}, None, '[function_lines."src/a.py"]\n"long_one" = 62\n\n[clones]\n', 0, id="first recording"),
        pytest.param(
            {"src/a.py": "x = 1\n", "tests/test_a.py": clone_function("run", "a", "b"), "tests/test_b.py": clone_function("check", "c", "d")},
            "[file_lines]\n",
            '[clones."tests/test_a.py"]\nrun = [["tests/test_b.py", "check"]]\n',
            0,
            id="clones recorded",
        ),
        pytest.param(
            {"src/a.py": "x = 1\n"},
            '[clones."tests/test_a.py"]\nrun = [["tests/test_b.py", "check"]]\n',
            "[clones]\n",
            0,
            id="a pair dropped",
        ),
        pytest.param(
            {"src/a.py": long_function(62)},
            held_ceiling(62, clones=False) + '\n[file_lines]\n"src/gone.py" = 900\n\n[clones]\n',
            '[function_lines."src/a.py"]\n"long_one" = 62\n\n[clones]\n',
            0,
            id="a gone entry dropped",
        ),
    ],
)
def test_a_fix_passes_the_verdict(pull_request, capsys, sources, base, head, exit_code):
    repo, commit_base = pull_request
    for name, text in sources.items():
        put(repo / name, text)
    commit_base(base)
    assert fixed(capsys, repo, *FIX_WORDS)[0] == exit_code
    written = (repo / BASELINE_PATH).read_text()
    assert (written.endswith(head), "src/gone.py" in written) == (True, False)
    code, out, err = verdict_streams(repo, capsys)
    assert (code, "verdict: green" in out, err) == (0, True, ""), out
