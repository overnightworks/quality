"""The door of `quality check secrets --staged`: the frame it prints, what it counts, and how each refusal looks."""

import os
import subprocess
from pathlib import Path

import pytest

from quality import config, scanners
from quality.report import printable
from quality.rules import Area
from tests.support.check import refusal_row, scanner_rows
from tests.support.config_refusals import CONFIG, all_refusals
from tests.support.door import CHECK_WORD, MODULE_DOOR, STAGED_WORDS, door_environment, door_in, staged_door
from tests.support.git_repositories import git, stage
from tests.support.scanners import OUTPUTS, fake_gitleaks, flagging, lacking, vetting
from tests.support.secrets import FAKE_KEY, allow_config


def clean_row(files: str) -> str:
    return f"  ✓ secrets       clean          staged change · {files}\n✓ clean · 1 area · <1s\n"


def refused_pair_for_missing_scanner() -> str:
    return f"error: 1 of 1 area could not check\nnext: {scanners.install_command()}\n"


def stage_line() -> str:
    return "stage the fix, then run quality check secrets --staged again"


def refused_pair() -> str:
    return "error: 1 of 1 area could not check\nnext: fix what the ✗ rows name, then run quality check secrets --staged again\n"


def a_finding_scan() -> str:
    return f'cp "{OUTPUTS / "gitleaks-finding.json"}" "$report"; exit 7'


def test_a_clean_staged_change_stands_in_the_pictures_frame(build_repo, capsys):
    repo = build_repo({"README.md": "x\n"})
    stage(repo, {"docs/b.md": "b\n", "docs/c.md": "c\n"})
    code = staged_door(repo, clock=lambda: 0.0)
    printed = capsys.readouterr()
    assert (code, printed.out, printed.err) == (0, clean_row("2 files"), "")


def test_a_staged_finding_stands_in_the_pictures_frame(build_repo, served_scanners, capsys):
    repo = build_repo({"README.md": "x\n"})
    stage(repo, {"docs/b.md": "b\n"})
    fake_gitleaks(served_scanners, on_scan=a_finding_scan())
    code = staged_door(repo, clock=lambda: 0.0)
    printed = capsys.readouterr()
    lines = printed.out.splitlines()
    assert (code, lines[0], lines[-1], printed.err) == (
        1, "  ! secrets       1 finding      staged change · 1 file", "! 1 finding in 1 area · <1s", "",
    )


@pytest.mark.parametrize(
    ("committed", "change", "counted"),
    [
        pytest.param({"a": "1\n", "b": "1\n", "c": "1\n"}, lambda repo: stage(repo, {"a": "2\n"}), "1 file", id="an edit among three tracked files"),
        pytest.param({"a": "1\n"}, lambda repo: stage(repo, {"b": "1\n", "c": "1\n"}), "2 files", id="two additions"),
        pytest.param({"a": "1\n", "b": "1\n"}, lambda repo: git(repo, "rm", "-q", "a"), "1 file", id="a deletion"),
        pytest.param({"a": "1 and more text\n" * 20}, lambda repo: git(repo, "mv", "a", "z"), "1 file", id="a rename"),
        pytest.param({"a": "1\n"}, lambda repo: stage(repo, {"line\nbreak.txt": "1\n"}), "1 file", id="a name with a newline"),
        pytest.param({"a": "1\n"}, lambda repo: stage(repo, {os.fsdecode(b"caf\xe9.txt"): "1\n"}), "1 file", id="a name that is no UTF-8"),
        pytest.param({"a": "1\n"}, lambda repo: None, "0 files", id="nothing"),
        pytest.param(None, lambda repo: stage(repo, {"a": "1\n"}), "1 file", id="no commit yet"),
    ],
)
def test_a_staged_run_counts_what_the_next_commit_changes(build_repo, tmp_path, capsys, committed, change, counted):
    if committed is None:
        git(tmp_path, "init", "-q")
    repo = tmp_path if committed is None else build_repo(committed)
    change(repo)
    assert staged_door(repo, clock=lambda: 0.0) == 0
    assert capsys.readouterr().out == clean_row(counted)


def test_a_staged_run_prints_no_unused_row(build_repo, capsys):
    repo = build_repo({config.CONFIG_PATH: allow_config(("aws-access-token", "docs/gone.txt", FAKE_KEY, "a stale entry"))})
    stage(repo, {"docs/b.md": "b\n"})
    assert staged_door(repo, clock=lambda: 0.0) == 0
    assert capsys.readouterr().out == clean_row("1 file")


def merge_conflict(repo: Path) -> None:
    git(repo, "checkout", "-q", "-b", "other")
    stage(repo, {"f.txt": "other\n"})
    git(repo, "commit", "-q", "-m", "other")
    git(repo, "checkout", "-q", "-")
    stage(repo, {"f.txt": "main\n"})
    git(repo, "commit", "-q", "-m", "main")
    git(repo, "merge", "other", check=False)


def stage_a_config_link(repo: Path) -> None:
    (repo / config.CONFIG_PATH).parent.mkdir()
    (repo / config.CONFIG_PATH).symlink_to("elsewhere.toml")
    git(repo, "add", config.CONFIG_PATH)


@pytest.mark.parametrize(
    ("arrange", "fact", "pair"),
    [
        pytest.param(
            lambda repo, served, use_mise, fake_git: use_mise(lacking(served, scanners.GITLEAKS)),
            "mise could not locate gitleaks", refused_pair_for_missing_scanner(), id="a scanner missing",
        ),
        pytest.param(lambda repo, served, use_mise, fake_git: fake_gitleaks(served, on_scan="exit 3"), "gitleaks exited 3 without writing its report", refused_pair(), id="gitleaks exits 3"),
        pytest.param(
            lambda repo, served, use_mise, fake_git: fake_gitleaks(served, on_scan="printf '[]\\342\\202' > \"$report\"; exit 7"),
            "cannot read the gitleaks report: not UTF-8 at byte", refused_pair(), id="a report that is not UTF-8",
        ),
        pytest.param(
            lambda repo, served, use_mise, fake_git: fake_gitleaks(served, on_scan="printf '[{' > \"$report\"; exit 7"),
            "cannot read the gitleaks report: JSONDecodeError(", refused_pair(), id="a report that is no JSON",
        ),
        pytest.param(
            lambda repo, served, use_mise, fake_git: fake_git('[ "$1" = "--version" ] && echo "git version 2.40.1" && exit 0'),
            "git 2.40.1 cannot hide .gitattributes from the gitleaks scan, it needs git 2.41", refused_pair(), id="git below the floor",
        ),
        pytest.param(
            lambda repo, served, use_mise, fake_git: stage_a_config_link(repo),
            "quality/config.toml cannot be read from the staged tree: not a regular file", refused_pair(), id="a staged config that is a link",
        ),
        pytest.param(lambda repo, served, use_mise, fake_git: merge_conflict(repo), "git write-tree failed", refused_pair(), id="an unmerged index"),
    ],
)
def test_every_refused_staged_run_is_its_row_and_the_pair(build_repo, served_scanners, use_mise, fake_git, capsys, arrange, fact, pair):
    repo = build_repo({"f.txt": "base\n"})
    arrange(repo, served_scanners, use_mise, fake_git)
    code = staged_door(repo, clock=lambda: 0.0)
    printed = capsys.readouterr()
    (row,) = printed.out.splitlines()
    assert (code, row.startswith(refusal_row("secrets")), fact in row, str(repo) in row, printed.err) == (2, True, True, False, pair)


STAGED_EXAMPLE_REFUSALS = [refusal for refusal in all_refusals() if "staged" in refusal.loaders and refusal.text is not None]


@pytest.mark.parametrize("refusal", STAGED_EXAMPLE_REFUSALS, ids=[refusal.id for refusal in STAGED_EXAMPLE_REFUSALS])
def test_a_staged_config_refusal_keeps_its_fact_and_its_example_apart(build_repo, capsys, refusal):
    repo = build_repo({"README.md": "x\n"})
    stage(repo, {CONFIG: refusal.text})
    code = staged_door(repo, clock=lambda: 0.0)
    printed = capsys.readouterr()
    lines = printed.err.splitlines()
    assert (code, printed.out, lines[0]) == (2, f"{refusal_row('secrets')}{printable(refusal.fact)}\n", "error: 1 of 1 area could not check")
    if refusal.example is None:
        assert lines[1:] == [f"next: {stage_line()}"]
    else:
        steps = [line.strip() for line in lines[2:]]
        assert (lines[1], steps) == ("next:", ["write it in quality/config.toml like this:", *map(printable, refusal.example), stage_line()])


def test_a_staged_config_is_read_from_the_tree_the_commit_records(build_repo, capsys):
    repo = build_repo({"README.md": "x\n"})
    stage(repo, {config.CONFIG_PATH: "[secrets]\nallow = [{ rule = \"r\" }]\n"})
    (repo / config.CONFIG_PATH).write_text(allow_config())
    assert staged_door(repo) == 2
    git(repo, "add", config.CONFIG_PATH)
    capsys.readouterr()
    assert staged_door(repo) == 0


@pytest.mark.integration
def test_a_staged_run_outside_a_repository_is_the_start_failure(elsewhere, capsys):
    code = staged_door(elsewhere)
    printed = capsys.readouterr()
    assert (code, printed.out) == (2, "")
    first, second = printed.err.splitlines()
    assert (first.startswith("error: not inside a git work tree · "), second) == (True, "next: run quality check secrets --staged inside a repository")


@pytest.mark.parametrize("words", [("--staged", "secrets"), ("secrets", "--staged"), ("secrets", "secrets", "--staged")], ids=["first", "last", "repeated"])
def test_the_staged_flag_may_stand_before_or_after_its_area(build_repo, capsys, words):
    repo = build_repo({"README.md": "x\n"})
    code = door_in(repo, CHECK_WORD, *words, clock=lambda: 0.0)
    assert (code, capsys.readouterr().out) == (0, clean_row("0 files"))


@pytest.mark.parametrize("verbose", [True, False], ids=["with -v", "without -v"])
def test_under_v_gitleaks_stands_under_the_staged_row(build_repo, served_scanners, capsys, verbose):
    repo = build_repo({"README.md": "x\n"})
    stage(repo, {"docs/b.md": "b\n"})
    fake_gitleaks(served_scanners, on_scan='echo "one line" >&2\n' + vetting(flagging(FAKE_KEY)))
    assert staged_door(repo, *(("-v",) if verbose else ()), clock=lambda: 0.0) == 0
    printed = capsys.readouterr().out
    assert scanner_rows(printed, Area.SECRETS) == ([(scanners.GITLEAKS, "0", ("one line",))] if verbose else [])
    assert (served_scanners / "gitleaks.vetted").exists() == verbose


@pytest.mark.integration
@pytest.mark.parametrize("outcome", ["clean", "finding", "refused", "refused, stderr left"])
def test_a_staged_run_keeps_the_exit_when_its_reader_left(build_repo, served_scanners, use_mise, tmp_path, outcome):
    repo = build_repo({"README.md": "x\n"})
    stage(repo, {"docs/b.md": "b\n"})
    if outcome == "finding":
        fake_gitleaks(served_scanners, on_scan=a_finding_scan())
    if outcome.startswith("refused"):
        use_mise(lacking(served_scanners, scanners.GITLEAKS))
    expected = {"clean": 0, "finding": 1, "refused": 2, "refused, stderr left": 2}[outcome]
    read_end, write_end = os.pipe()
    os.close(read_end)
    try:
        with (tmp_path / "kept").open("w", encoding="utf-8") as kept:
            left_on_stdout = outcome != "refused, stderr left"
            done = subprocess.run(
                [*MODULE_DOOR, *STAGED_WORDS], cwd=repo, env=door_environment(), timeout=60, check=False,
                stdout=write_end if left_on_stdout else kept, stderr=kept if left_on_stdout else write_end,
            )
    finally:
        os.close(write_end)
    kept_text = (tmp_path / "kept").read_text(encoding="utf-8")
    if left_on_stdout:
        expected_kept = refused_pair_for_missing_scanner() if outcome == "refused" else ""
        assert (done.returncode, kept_text) == (expected, expected_kept)
    else:
        assert (done.returncode, refusal_row("secrets") in kept_text) == (expected, True)
