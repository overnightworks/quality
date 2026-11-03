"""The door of `quality check debt --fix`: the frames it prints, what it writes and what it refuses."""

import hashlib
import os
import subprocess
from pathlib import Path

import pytest

from quality import repository
from quality.ratchet import BASELINE_PATH, Key, Metric, load_baseline
from tests.support.door import FIX_WORDS, door_in
from tests.support.git_repositories import git, put
from tests.support.source_code import branchy_function, long_file, long_function

RESTORE_STEP = "restore quality/baseline.toml from git, or repair it by hand"
ENTRY = '[function_lines."src/a.py"]\nlong_one = 62\n'


def fixed(capsys, repo: Path, *words: str) -> tuple[int, str, str]:
    code = door_in(repo, *words, clock=lambda: 0.0)
    printed = capsys.readouterr()
    return code, printed.out, printed.err


def digest(repo: Path) -> str:
    return hashlib.sha256((repo / BASELINE_PATH).read_bytes()).hexdigest()


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


def test_what_grew_stays_a_finding_and_the_file_stays_as_it_was(build_repo, capsys):
    repo = build_repo({"src/a.py": long_function(71), BASELINE_PATH: '[function_lines."src/a.py"]\nlong_one = 64\n\n[clones]\n'})
    before = digest(repo)
    printed = (
        "  ! debt          1 finding      nothing lowered\n"
        "    ! src/a.py:1  grew  function long_one has 71 lines · ceiling 64\n"
        "      fix: bring it back to 64 lines or below; a raise is a hand edit and a declared loosening\n"
        "! 1 finding in 1 area · <1s\n"
    )
    assert fixed(capsys, repo, *FIX_WORDS) == (1, printed, "")
    assert digest(repo) == before


def test_nothing_to_lower_leaves_the_files_bytes(build_repo, capsys):
    repo = build_repo({"src/a.py": long_function(62), BASELINE_PATH: "# a comment\n" + ENTRY + "\n[clones]\n"})
    before = (repo / BASELINE_PATH).read_bytes()
    code, out, _ = fixed(capsys, repo, *FIX_WORDS)
    assert (code, out.splitlines()[-1], (repo / BASELINE_PATH).read_bytes()) == (0, "✓ clean · nothing to lower · 1 area · <1s", before)


@pytest.mark.parametrize(
    "held",
    ['[file_lines]\n"src/gone.py" = 900\n', '[function_lines."src/gone.py"]\nlong_one = 90\n'],
    ids=["file", "function"],
)
def test_a_gone_entry_stands_as_it_stands(build_repo, capsys, held):
    repo = build_repo({"src/a.py": "x = 1\n", BASELINE_PATH: held + "\n[clones]\n"})
    before = (repo / BASELINE_PATH).read_bytes()
    assert fixed(capsys, repo, *FIX_WORDS)[0] == 0
    assert (repo / BASELINE_PATH).read_bytes() == before


def test_a_new_entry_is_never_added_to_a_held_ceiling(build_repo, capsys):
    repo = build_repo({"src/a.py": long_function(62), "src/b.py": long_file(850), BASELINE_PATH: ENTRY + "\n[clones]\n"})
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


def remove_the_file(repo: Path) -> None:
    (repo / BASELINE_PATH).unlink()


def remove_from_the_index(repo: Path) -> None:
    git(repo, "rm", "-q", BASELINE_PATH)


def commit_the_removal(repo: Path) -> None:
    git(repo, "rm", "-q", BASELINE_PATH)
    git(repo, "commit", "-q", "-m", "remove the ceiling")


@pytest.mark.parametrize("arrange", [remove_the_file, remove_from_the_index, commit_the_removal], ids=["rm", "git rm", "committed deletion"])
def test_a_ceiling_git_holds_is_no_first_recording(build_repo, capsys, arrange):
    repo = build_repo({"src/a.py": long_function(62), BASELINE_PATH: ENTRY + "\n[clones]\n"})
    arrange(repo)
    code, out, err = fixed(capsys, repo, *FIX_WORDS)
    assert (code, "  ✗ debt          not fixed      quality/baseline.toml is gone from the tree but git holds it\n" in out) == (2, True)
    assert err == f"error: 1 of 1 area could not check\nnext: {RESTORE_STEP}\n"
    assert not (repo / BASELINE_PATH).exists()


def test_a_clones_table_git_holds_is_not_recorded_again(build_repo, capsys):
    repo = build_repo({"src/a.py": long_function(62), BASELINE_PATH: ENTRY + "\n[clones]\n"})
    (repo / BASELINE_PATH).write_text(ENTRY)
    before = digest(repo)
    code, out, err = fixed(capsys, repo, *FIX_WORDS)
    assert code == 2
    assert "quality/baseline.toml lost its [clones] table, which git holds" in out
    assert (err, digest(repo)) == (f"error: 1 of 1 area could not check\nnext: {RESTORE_STEP}\n", before)


def test_a_shallow_clone_cannot_prove_a_first_recording(build_repo, tmp_path, capsys):
    origin = build_repo({"src/a.py": long_function(62)})
    (origin / "second").write_text("2\n")
    git(origin, "add", "second")
    git(origin, "commit", "-q", "-m", "second")
    clone = tmp_path / "shallow"
    subprocess.run(["git", "clone", "-q", "--depth", "1", f"file://{origin}", str(clone)], check=True, capture_output=True)
    code, out, err = fixed(capsys, clone, *FIX_WORDS)
    assert (code, "this clone is shallow, so git cannot show the ceiling is new" in out) == (2, True)
    assert err == "error: 1 of 1 area could not check\nnext: git fetch --unshallow\n"
    assert not (clone / BASELINE_PATH).exists()


@pytest.mark.parametrize("held", ["[file_lines\n", '[file_lines]\n"src/a.py" = "many"\n', "[future]\n"], ids=["no TOML", "not a count", "an unknown table"])
def test_a_refused_fix_writes_nothing(build_repo, capsys, held):
    repo = build_repo({"src/a.py": long_function(62), BASELINE_PATH: held})
    before = digest(repo)
    code, out, _ = fixed(capsys, repo, *FIX_WORDS)
    assert (code, "  ✗ debt          not checked" in out, digest(repo)) == (2, True, before)


def test_another_areas_refusal_leaves_the_fix_standing(build_repo, capsys):
    repo = build_repo({"src/a.py": long_function(62), BASELINE_PATH: '[function_lines."src/a.py"]\nlong_one = 80\n\n[clones]\n'})
    (repo / "compose.yaml").write_text("services: [\n")
    git(repo, "add", "compose.yaml")
    code, out, _ = fixed(capsys, repo, "check", "files", "debt", "--fix")
    assert code == 2
    assert "  ✓ debt          lowered        1 entry · quality/baseline.toml\n" in out
    assert load_baseline((repo / BASELINE_PATH).read_text()).size == {Key(Metric.FUNCTION_LINES, "src/a.py", "long_one"): 62}


def test_a_moved_file_stays_new_debt(build_repo, capsys):
    repo = build_repo({"src/old.py": long_function(70), BASELINE_PATH: '[function_lines."src/old.py"]\nlong_one = 70\n\n[clones]\n'})
    git(repo, "mv", "src/old.py", "src/new.py")
    code, out, _ = fixed(capsys, repo, *FIX_WORDS)
    assert (code, "src/new.py:1  new debt" in out) == (1, True)
    assert load_baseline((repo / BASELINE_PATH).read_text()).size == {Key(Metric.FUNCTION_LINES, "src/old.py", "long_one"): 70}


def test_a_failing_git_quotes_nothing(build_repo, fake_git, capsys):
    repo = build_repo({"src/a.py": long_function(62)})
    fake_git('case " $* " in *" log "*) echo "SECRET-OF-GIT" >&2; exit 9;; esac')
    (repo / "quality").mkdir()
    (repo / BASELINE_PATH).write_text(ENTRY)
    code, out, err = fixed(capsys, repo, *FIX_WORDS)
    assert code == 2
    assert ("git log exited 9" in out, "SECRET-OF-GIT" in out + err) == (True, False)


def test_a_ceiling_staged_and_never_committed_is_held_by_git(build_repo, capsys):
    repo = build_repo({"src/a.py": long_function(62)})
    (repo / BASELINE_PATH).parent.mkdir()
    (repo / BASELINE_PATH).write_text(ENTRY + "\n[clones]\n")
    git(repo, "add", BASELINE_PATH)
    (repo / BASELINE_PATH).unlink()
    code, out, _ = fixed(capsys, repo, *FIX_WORDS)
    assert (code, "quality/baseline.toml is gone from the tree but git holds it" in out) == (2, True)
    assert not (repo / BASELINE_PATH).exists()


def test_a_restored_ceiling_lowers_and_keeps_what_grew(build_repo, capsys):
    held = '[function_lines."src/a.py"]\nlong_one = 80\n\n[function_lines."src/b.py"]\nlong_one = 64\n\n[clones]\n'
    repo = build_repo({"src/a.py": long_function(62), "src/b.py": long_function(71), BASELINE_PATH: held})
    remove_the_file(repo)
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


@pytest.mark.parametrize("held", ["[clones\n", "\xff\xfe"], ids=["in history", "not UTF-8"])
def test_a_version_no_parser_reads_refuses_and_a_clones_table_by_hand_runs_the_fix(build_repo, capsys, held):
    repo = build_repo({"src/a.py": long_function(62), BASELINE_PATH: held})
    git(repo, "rm", "-q", "--cached", BASELINE_PATH)
    (repo / BASELINE_PATH).write_text(ENTRY)
    git(repo, "commit", "-q", "-m", "drop")
    before = digest(repo)
    code, out, err = fixed(capsys, repo, *FIX_WORDS)
    assert (code, "a version of quality/baseline.toml in git cannot be read" in out, digest(repo)) == (2, True, before)
    assert err.endswith(f"next: {RESTORE_STEP}\n")
    (repo / BASELINE_PATH).write_text(ENTRY + "\n[clones]\n")
    assert fixed(capsys, repo, *FIX_WORDS)[0] == 0


def test_a_shallow_clone_lowers_a_ceiling_that_holds_its_table(build_repo, tmp_path, capsys):
    origin = build_repo({"src/a.py": long_function(62), BASELINE_PATH: '[function_lines."src/a.py"]\nlong_one = 80\n\n[clones]\n'})
    clone = tmp_path / "shallow"
    subprocess.run(["git", "clone", "-q", "--depth", "1", f"file://{origin}", str(clone)], check=True, capture_output=True)
    assert fixed(capsys, clone, *FIX_WORDS)[0] == 0
    assert load_baseline((clone / BASELINE_PATH).read_text()).size == {Key(Metric.FUNCTION_LINES, "src/a.py", "long_one"): 62}


def test_a_ceiling_a_link_points_out_of_the_repository_is_refused_and_writes_nothing(build_repo, capsys, tmp_path):
    outside = tmp_path / "outside.toml"
    outside.write_text(ENTRY)
    repo = build_repo({"src/a.py": long_function(62)})
    (repo / "quality").mkdir()
    (repo / BASELINE_PATH).symlink_to(outside)
    git(repo, "add", BASELINE_PATH)
    code, out, _ = fixed(capsys, repo, *FIX_WORDS)
    assert (code, "  ✗ debt" in out, outside.read_text()) == (2, True, ENTRY)


def test_a_refused_write_keeps_what_each_rule_judged(build_repo, monkeypatch, capsys):
    repo = build_repo({"src/a.py": long_function(62), BASELINE_PATH: '[function_lines."src/a.py"]\nlong_one = 80\n\n[clones]\n'})

    def refuse(root, name, text):
        raise repository.RepositoryFileError(f"cannot write {name}: [Errno 13] Permission denied")

    monkeypatch.setattr(repository, "write_text", refuse)
    code, out, _ = fixed(capsys, repo, *FIX_WORDS)
    assert code == 2
    assert "  ✗ debt          not fixed      cannot write quality/baseline.toml: [Errno 13] Permission denied" in out
    assert "can fall" in out


@pytest.mark.parametrize("when", ["before", "after"])
def test_an_interrupted_fix_leaves_the_old_file_or_the_new_one(build_repo, monkeypatch, capsys, when):
    repo = build_repo({"src/a.py": long_function(62), BASELINE_PATH: '[function_lines."src/a.py"]\nlong_one = 80\n\n[clones]\n'})
    before = (repo / BASELINE_PATH).read_bytes()
    real_replace = os.replace

    def interrupted(*arguments, **keywords):
        if when == "before":
            raise KeyboardInterrupt
        real_replace(*arguments, **keywords)
        raise KeyboardInterrupt

    monkeypatch.setattr(os, "replace", interrupted)
    with pytest.raises(KeyboardInterrupt):
        door_in(repo, *FIX_WORDS)
    assert ((repo / BASELINE_PATH).read_bytes() == before) == (when == "before")
    assert "debt" not in capsys.readouterr().out
