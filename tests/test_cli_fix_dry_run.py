"""The door of `quality check debt --fix --dry-run`: the frames it prints, the exit it ends in and the nothing it writes."""

import contextlib
import os
import re
from pathlib import Path

import pytest

from quality.config import CONFIG_PATH
from quality.ratchet import BASELINE_PATH
from tests.support.clones import function as clone_function
from tests.support.door import DRY_RUN_WORDS, FIX_WORDS, door_in, fixed, restore_step, tree_of
from tests.support.generated_files import gate_declaring
from tests.support.git_repositories import git, put, shallow_clone
from tests.support.source_code import branchy_function, long_file, long_function
from tests.support.terminal import Terminal, without_sgr


def ceiling(table: str) -> str:
    return table + "\n[clones]\n"


def untouched(repo: Path) -> tuple[dict[str, bytes | str | None], list[tuple[int, int] | None]]:
    """Everything outside `.git`, and the identity and modification time of the ceiling and of its folder."""
    stamps = []
    for path in (repo / BASELINE_PATH, (repo / BASELINE_PATH).parent):
        stamps.append((path.stat().st_ino, path.stat().st_mtime_ns) if path.exists() else None)
    return tree_of(repo), stamps


def holding(files: dict[str, str]):
    return lambda build_repo: build_repo(files)


def moved_with_git(build_repo) -> Path:
    held = ceiling('[function_lines."src/old.py"]\nlong_one = 70\n')
    repo = build_repo({"src/old.py": long_function(70), BASELINE_PATH: held})
    git(repo, "mv", "src/old.py", "src/new.py")
    return repo


def a_stale_target(build_repo) -> Path:
    held = ceiling('[function_lines."src/old.py"]\nlong_one = 100\n\n[function_lines."src/new.py"]\nlong_one = 200\n')
    repo = build_repo({"src/old.py": long_function(100), BASELINE_PATH: held})
    git(repo, "mv", "src/old.py", "src/new.py")
    put(repo / "src/new.py", long_function(150))
    return repo


def a_move_beside_a_grown_entry(build_repo) -> Path:
    held = ceiling('[function_lines."src/old.py"]\nlong_one = 70\n\n[file_lines]\n"src/other.py" = 810\n')
    repo = build_repo({"src/old.py": long_function(70), "src/other.py": long_file(850), BASELINE_PATH: held})
    git(repo, "mv", "src/old.py", "src/new.py")
    return repo


@pytest.mark.parametrize(
    ("arrange", "printed"),
    [
        pytest.param(
            holding({"src/a.py": long_function(62), BASELINE_PATH: ceiling('[function_lines."src/a.py"]\nlong_one = 80\n')}),
            "  ~ debt          1 to lower     quality/baseline.toml\n"
            "    ~ src/a.py:1  lower  function long_one 80 → 62 lines\n"
            "~ dry run · 1 ceiling entry would fall · nothing written · <1s\n",
            id="lowered",
        ),
        pytest.param(
            holding({"src/a.py": long_file(790), BASELINE_PATH: ceiling('[file_lines]\n"src/a.py" = 805\n')}),
            "  ~ debt          1 to lower     quality/baseline.toml\n"
            "    ~ src/a.py  drop  file 805 → 790 lines · debt from 800 lines\n"
            "~ dry run · 1 ceiling entry would fall · nothing written · <1s\n",
            id="below the threshold",
        ),
        pytest.param(
            holding({"src/a.py": "x = 1\n", BASELINE_PATH: ceiling('[file_lines]\n"src/legacy.py" = 802\n')}),
            "  ~ debt          1 to lower     quality/baseline.toml\n"
            "    ~ src/legacy.py  drop  file is gone · was 802 lines\n"
            "~ dry run · 1 ceiling entry would fall · nothing written · <1s\n",
            id="a gone file",
        ),
        pytest.param(
            holding({"src/a.py": "x = 1\n", BASELINE_PATH: ceiling('[ccn."src/legacy.py"]\nbranchy = 19\n')}),
            "  ~ debt          1 to lower     quality/baseline.toml\n"
            "    ~ src/legacy.py  drop  function branchy is gone · was CCN 19\n"
            "~ dry run · 1 ceiling entry would fall · nothing written · <1s\n",
            id="a gone CCN entry",
        ),
        pytest.param(
            holding({
                "gen/api.py": long_function(70), CONFIG_PATH: gate_declaring("gen/api.py"),
                BASELINE_PATH: ceiling('[function_lines."gen/api.py"]\nlong_one = 70\n'),
            }),
            "  ~ debt          1 to lower     quality/baseline.toml\n"
            "    ~ gen/api.py  drop  function long_one is left out as generated · was 70 lines\n"
            "~ dry run · 1 ceiling entry would fall · nothing written · <1s\n",
            id="a generated file's function",
        ),
        pytest.param(
            holding({"src/a.py": "x = 1\n", BASELINE_PATH: '[clones."src/a.py"]\nrun = [["src/legacy.py", "run"]]\n'}),
            "  ~ debt          1 to lower     quality/baseline.toml\n"
            "    ~ src/a.py  drop  pair src/a.py run <-> src/legacy.py run is gone\n"
            "~ dry run · 1 ceiling entry would fall · nothing written · <1s\n",
            id="a pair the tree lost",
        ),
        pytest.param(
            moved_with_git,
            "  ~ debt          1 to move      quality/baseline.toml\n"
            "    ~ src/new.py  move  function long_one 70 lines from src/old.py\n"
            "~ dry run · 1 ceiling entry would move · nothing written · <1s\n",
            id="moved",
        ),
        pytest.param(
            a_stale_target,
            "  ! debt          1 finding      1 to lower · quality/baseline.toml\n"
            "    ! src/new.py:1  grew  function long_one has 150 lines · ceiling 100\n"
            "      fix: bring it back to 100 lines or below; a raise is a hand edit and a declared loosening\n"
            "    ~ src/new.py    move  function long_one 100 lines from src/old.py\n"
            "    ~ src/new.py    drop  function long_one 200 lines · left from before the move\n"
            "! 1 finding in 1 area · 1 ceiling entry would fall · nothing written · <1s\n",
            id="the stale target",
        ),
        pytest.param(
            a_move_beside_a_grown_entry,
            "  ! debt          1 finding      1 to move · quality/baseline.toml\n"
            "    ! src/other.py  grew  file has 850 lines · ceiling 810\n"
            "      fix: bring it back to 810 lines or below; a raise is a hand edit and a declared loosening\n"
            "    ~ src/new.py    move  function long_one 70 lines from src/old.py\n"
            "! 1 finding in 1 area · 1 ceiling entry would move · nothing written · <1s\n",
            id="a move beside a finding",
        ),
        pytest.param(
            holding({
                "src/a.py": "x = 1\n", BASELINE_PATH: "[file_lines]\n",
                "tests/test_a.py": clone_function("run", "a", "b"), "tests/test_b.py": clone_function("check", "c", "d"),
            }),
            "  ~ debt          to record      1 clone pair · quality/baseline.toml\n"
            "    ~ quality/baseline.toml  record  1 clone pair\n"
            "~ dry run · ceiling would be recorded · nothing written · <1s\n",
            id="a clones table",
        ),
        pytest.param(
            holding({"src/a.py": long_function(62)}),
            "  ~ debt          to record      1 entry, 0 clone pairs · quality/baseline.toml would be new\n"
            "~ dry run · ceiling would be recorded · nothing written · <1s\n",
            id="a first recording",
        ),
    ],
)
def test_a_dry_run_names_each_change_and_writes_nothing(build_repo, capsys, arrange, printed):
    repo = arrange(build_repo)
    before = untouched(repo)
    assert (*fixed(capsys, repo, *DRY_RUN_WORDS), untouched(repo)) == (1, printed, "", before)
    fixed(capsys, repo, *FIX_WORDS)
    assert untouched(repo) != before


def the_pictures_state(build_repo) -> Path:
    """A function that shrank, a file that shrank and a file that is gone."""
    held = ceiling('[ccn."src/cart.py"]\nbranchy = 19\n\n[file_lines]\n"src/orders.py" = 826\n"src/legacy.py" = 802\n')
    return build_repo({"src/cart.py": branchy_function(17), "src/orders.py": long_file(812), BASELINE_PATH: held})


def test_a_dry_run_stands_in_the_pictures_frame_and_the_fix_after_it_lowers(build_repo, capsys):
    repo = the_pictures_state(build_repo)
    planned = (
        "  ~ debt          3 to lower     quality/baseline.toml\n"
        "    ~ src/cart.py:1  lower  function branchy CCN 19 → 17\n"
        "    ~ src/orders.py  lower  file 826 → 812 lines\n"
        "    ~ src/legacy.py  drop   file is gone · was 802 lines\n"
        "~ dry run · 3 ceiling entries would fall · nothing written · <1s\n"
    )
    done = (
        "  ✓ debt          lowered        3 entries · quality/baseline.toml\n"
        "    ✓ src/cart.py:1  lowered  function branchy CCN 19 → 17\n"
        "    ✓ src/orders.py  lowered  file 826 → 812 lines\n"
        "    ✓ src/legacy.py  dropped  file is gone\n"
        "✓ clean · ceiling lowered by 3 entries · 1 area · <1s\n"
    )
    assert fixed(capsys, repo, *DRY_RUN_WORDS) == (1, planned, "")
    assert fixed(capsys, repo, *FIX_WORDS) == (0, done, "")


@pytest.mark.parametrize("words", [DRY_RUN_WORDS, FIX_WORDS], ids=["dry run", "fix"])
def test_a_dry_run_with_nothing_to_change_is_clean(build_repo, capsys, words):
    repo = build_repo({"src/a.py": long_function(62), BASELINE_PATH: ceiling('[function_lines."src/a.py"]\nlong_one = 62\n')})
    before = untouched(repo)
    clean = "  ✓ debt          clean          ceiling held · 1 entry, 0 clone pairs\n✓ clean · nothing to lower · 1 area · <1s\n"
    assert (*fixed(capsys, repo, *words), untouched(repo)) == (0, clean, "", before)


def a_shallow_clone(build_repo, elsewhere) -> Path:
    origin = build_repo({"src/a.py": long_function(62)})
    put(origin / "second", "2\n")
    git(origin, "add", "second")
    git(origin, "commit", "-q", "-m", "second")
    return shallow_clone(origin, elsewhere)


def a_ceiling_git_holds(build_repo, elsewhere) -> Path:
    repo = build_repo({"src/a.py": long_function(62), BASELINE_PATH: ceiling('[function_lines."src/a.py"]\nlong_one = 80\n')})
    (repo / BASELINE_PATH).unlink()
    return repo


def a_ceiling_that_is_no_toml(build_repo, elsewhere) -> Path:
    return build_repo({"src/a.py": long_function(62), BASELINE_PATH: "[file_lines\n"})


def a_file_that_does_not_parse(build_repo, elsewhere) -> Path:
    return build_repo({"src/a.py": long_function(62), "src/broken.py": "def broken(:\n", BASELINE_PATH: ceiling("[file_lines]\n")})


def a_clones_table_git_holds(build_repo, elsewhere) -> Path:
    repo = build_repo({"src/a.py": "x = 1\n", BASELINE_PATH: ceiling("[file_lines]\n")})
    put(repo / BASELINE_PATH, "[file_lines]\n")
    return repo


def a_shallow_clone_whose_ceiling_has_no_table(build_repo, elsewhere) -> Path:
    origin = build_repo({"src/a.py": "x = 1\n", BASELINE_PATH: "[file_lines]\n"})
    put(origin / "second", "2\n")
    git(origin, "add", "second")
    git(origin, "commit", "-q", "-m", "second")
    return shallow_clone(origin, elsewhere)


def a_shallow_clone_whose_ceiling_was_last_changed_at_its_root(build_repo, elsewhere) -> Path:
    origin = build_repo({"src/a.py": "x = 1\n", BASELINE_PATH: ceiling("[file_lines]\n")})
    put(origin / "second", "2\n")
    git(origin, "add", "second")
    git(origin, "commit", "-q", "-m", "second")
    return shallow_clone(origin, elsewhere)


def a_path_no_ceiling_can_hold(build_repo, elsewhere) -> Path:
    return build_repo({"src/a.py": "x = 1\n", os.fsdecode(b"src/\xff.py"): long_file(850)})


def a_config_refusal(build_repo, elsewhere) -> Path:
    return build_repo({"src/a.py": long_function(62), CONFIG_PATH: "nope = 1\n", BASELINE_PATH: ceiling("[file_lines]\n")})


@pytest.mark.parametrize(
    ("arrange", "step"),
    [
        pytest.param(a_shallow_clone, "git fetch --unshallow", id="shallow"),
        pytest.param(a_ceiling_git_holds, restore_step(), id="the ceiling git holds"),
        pytest.param(a_ceiling_that_is_no_toml, restore_step(), id="no TOML"),
        pytest.param(a_clones_table_git_holds, restore_step(), id="a clones table git holds"),
        pytest.param(a_shallow_clone_whose_ceiling_has_no_table, "git fetch --unshallow", id="shallow, a ceiling without a table"),
        pytest.param(a_shallow_clone_whose_ceiling_was_last_changed_at_its_root, "git fetch --unshallow", id="shallow, the lineage"),
        pytest.param(a_file_that_does_not_parse, "fix what the ✗ rows name, then run quality check --fix --dry-run again", id="a file that does not parse"),
        pytest.param(a_path_no_ceiling_can_hold, "fix what the ✗ rows name, then run quality check --fix --dry-run again", id="a path no ceiling can hold"),
        pytest.param(a_config_refusal, "fix what the ✗ rows name, then run quality check --fix --dry-run again", id="a config refusal"),
    ],
)
def test_a_dry_run_refuses_where_the_fix_refuses_before_writing(build_repo, elsewhere, capsys, arrange, step):
    repo = arrange(build_repo, elsewhere)
    before = untouched(repo)
    code, out, err = fixed(capsys, repo, *DRY_RUN_WORDS)
    assert (code, err, untouched(repo)) == (2, f"error: 1 of 1 area could not check\nnext: {step}\n", before)
    refused = fixed(capsys, repo, *FIX_WORDS)
    assert (refused[0], refused[1]) == (2, out)


@pytest.mark.parametrize(
    ("arrange", "printed", "refusal"),
    [
        pytest.param(
            holding({"src/a.py": long_function(62), BASELINE_PATH: ceiling('[function_lines."src/a.py"]\nlong_one = 80\n')}),
            "  ~ debt          1 to lower     quality/baseline.toml\n"
            "    ~ src/a.py:1  lower  function long_one 80 → 62 lines\n"
            "~ dry run · 1 ceiling entry would fall · nothing written · <1s\n",
            "[Errno 13]",
            marks=pytest.mark.skipif(os.geteuid() == 0, reason="root writes a mode-0444 file"),
            id="a read-only ceiling and folder",
        ),
        pytest.param(
            holding({"src/a.py": long_function(62), "quality": "not a folder\n"}),
            "  ~ debt          to record      1 entry, 0 clone pairs · quality/baseline.toml would be new\n"
            "~ dry run · ceiling would be recorded · nothing written · <1s\n",
            "[Errno 17]",
            id="quality is a file",
        ),
    ],
)
def test_a_dry_run_attempts_no_write(build_repo, capsys, request, arrange, printed, refusal):
    repo = arrange(build_repo)
    if (repo / BASELINE_PATH).exists():
        (repo / BASELINE_PATH).chmod(0o444)
        (repo / BASELINE_PATH).parent.chmod(0o555)
        request.addfinalizer(lambda: (repo / BASELINE_PATH).parent.chmod(0o755))
    before = untouched(repo)
    assert (*fixed(capsys, repo, *DRY_RUN_WORDS), untouched(repo)) == (1, printed, "", before)
    code, out, _ = fixed(capsys, repo, *FIX_WORDS)
    assert (code, refusal in out, untouched(repo)) == (2, True, before)


def test_another_areas_refusal_leaves_the_dry_run_standing(build_repo, capsys):
    repo = build_repo({"src/a.py": long_function(62), BASELINE_PATH: ceiling('[function_lines."src/a.py"]\nlong_one = 80\n')})
    put(repo / "compose.yaml", "services: [\n")
    git(repo, "add", "compose.yaml")
    before = untouched(repo)
    code, out, err = fixed(capsys, repo, "check", "files", "debt", "--fix", "--dry-run")
    assert (code, "  ~ debt          1 to lower     quality/baseline.toml\n" in out, untouched(repo)) == (2, True, before)
    assert err.endswith("next: fix what the ✗ rows name, then run quality check --fix --dry-run again\n")


def test_at_a_terminal_a_dry_run_paints_its_glyphs_and_keeps_the_plain_words(build_repo, capsys):
    repo = build_repo({"src/a.py": long_function(62), BASELINE_PATH: ceiling('[function_lines."src/a.py"]\nlong_one = 80\n')})
    piped = fixed(capsys, repo, *DRY_RUN_WORDS)
    stdout = Terminal()
    with contextlib.redirect_stdout(stdout):
        code = door_in(repo, *DRY_RUN_WORDS, clock=lambda: 0.0, environment={"TERM": "xterm"})
    foreground_colour_before_the_glyph = re.search(r"\x1b\[3[0-7]m~", stdout.getvalue())
    assert (foreground_colour_before_the_glyph is not None, (code, without_sgr(stdout.getvalue()), "")) == (True, piped)


def test_a_dry_run_beside_a_grown_entry_with_nothing_to_change_says_so_and_writes_nothing(build_repo, capsys):
    repo = build_repo({"src/a.py": long_function(150), BASELINE_PATH: ceiling('[function_lines."src/a.py"]\nlong_one = 100\n')})
    before = untouched(repo)
    printed = (
        "  ! debt          1 finding      nothing to lower\n"
        "    ! src/a.py:1  grew  function long_one has 150 lines · ceiling 100\n"
        "      fix: bring it back to 100 lines or below; a raise is a hand edit and a declared loosening\n"
        "! 1 finding in 1 area · <1s\n"
    )
    assert (*fixed(capsys, repo, *DRY_RUN_WORDS), untouched(repo)) == (1, printed, "", before)
