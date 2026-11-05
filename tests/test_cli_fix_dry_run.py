"""The door of `quality check debt --fix --dry-run`: the frames it prints, the exit it ends in and the nothing it writes."""

import contextlib
import os
from pathlib import Path

import pytest

from quality.config import CONFIG_PATH
from quality.ratchet import BASELINE_PATH
from tests.support.clones import function as clone_function
from tests.support.door import DRY_RUN_WORDS, FIX_WORDS, door_in, fixed, restore_step, tree_of
from tests.support.generated_files import gate_declaring
from tests.support.git_repositories import git, head_commit, put, shallow_clone
from tests.support.source_code import branchy_function, long_file, long_function
from tests.support.terminal import Terminal, by_role, without_sgr


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


def held_above(build_repo) -> Path:
    return build_repo({"src/a.py": long_function(62), BASELINE_PATH: ceiling('[function_lines."src/a.py"]\nlong_one = 80\n')})


def moved_with_git(build_repo) -> Path:
    held = ceiling('[function_lines."src/old.py"]\nlong_one = 70\n')
    repo = build_repo({"src/old.py": long_function(70), BASELINE_PATH: held})
    git(repo, "mv", "src/old.py", "src/new.py")
    return repo


def a_fall_beside_a_move(build_repo) -> Path:
    held = ceiling('[function_lines."src/old.py"]\nlong_one = 70\n\n[file_lines]\n"src/big.py" = 850\n')
    repo = build_repo({"src/old.py": long_function(70), "src/big.py": long_file(850), BASELINE_PATH: held})
    git(repo, "mv", "src/old.py", "src/new.py")
    put(repo / "src/big.py", long_file(830))
    return repo


def a_stale_target(build_repo) -> Path:
    held = ceiling('[function_lines."src/old.py"]\nlong_one = 100\n\n[function_lines."src/new.py"]\nlong_one = 200\n')
    repo = build_repo({"src/old.py": long_function(100), BASELINE_PATH: held})
    git(repo, "mv", "src/old.py", "src/new.py")
    put(repo / "src/new.py", long_function(150))
    return repo


def a_rename_over_a_stale_target_that_is_generated(build_repo) -> Path:
    held = ceiling('[function_lines."src/old.py"]\nlong_one = 100\n\n[function_lines."src/new.py"]\nlong_one = 200\n')
    repo = build_repo({"src/old.py": long_function(100), CONFIG_PATH: gate_declaring("src/new.py"), BASELINE_PATH: held})
    git(repo, "mv", "src/old.py", "src/new.py")
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
            held_above,
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
            a_fall_beside_a_move,
            "  ~ debt          1 to lower     quality/baseline.toml\n"
            "    ~ src/new.py  move   function long_one 70 lines from src/old.py\n"
            "    ~ src/big.py  lower  file 850 → 830 lines\n"
            "~ dry run · 1 ceiling entry would fall · nothing written · <1s\n",
            id="a fall beside a move",
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
            a_rename_over_a_stale_target_that_is_generated,
            "  ~ debt          2 to lower     quality/baseline.toml\n"
            "    ~ src/new.py  move  function long_one 100 lines from src/old.py\n"
            "    ~ src/new.py  drop  function long_one 200 lines · left from before the move\n"
            "    ~ src/new.py  drop  function long_one is left out as generated · was 100 lines\n"
            "~ dry run · 2 ceiling entries would fall · nothing written · <1s\n",
            id="a rename over a stale target that is generated",
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
        pytest.param(
            holding({"src/a.py": long_function(62), "tests/test_a.py": clone_function("run", "a", "b"), "tests/test_b.py": clone_function("check", "c", "d")}),
            "  ~ debt          to record      1 entry, 1 clone pair · quality/baseline.toml would be new\n"
            "~ dry run · ceiling would be recorded · nothing written · <1s\n",
            id="a first recording with a clone pair",
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


def shallow_clone_holding(files: dict[str, str]):
    def arrange(build_repo, elsewhere) -> Path:
        origin = build_repo(files)
        put(origin / "second", "2\n")
        git(origin, "add", "second")
        git(origin, "commit", "-q", "-m", "second")
        return shallow_clone(origin, elsewhere)

    return arrange


def a_ceiling_git_holds(build_repo, elsewhere) -> Path:
    repo = held_above(build_repo)
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


def a_path_no_ceiling_can_hold(build_repo, elsewhere) -> Path:
    return build_repo({"src/a.py": "x = 1\n", os.fsdecode(b"src/\xff.py"): long_file(850)})


def a_path_no_ceiling_can_hold_beside_a_held_ceiling(build_repo, elsewhere) -> Path:
    return build_repo(
        {
            "src/a.py": "x = 1\n",
            "tests/test_a.py": clone_function("run", "a", "b"),
            os.fsdecode(b"tests/test_b\xff.py"): clone_function("check", "c", "d"),
            BASELINE_PATH: "[file_lines]\n",
        }
    )


def a_config_refusal(build_repo, elsewhere) -> Path:
    return build_repo({"src/a.py": long_function(62), CONFIG_PATH: "nope = 1\n", BASELINE_PATH: ceiling("[file_lines]\n")})


def a_committed_ceiling_that_does_not_parse_repaired_in_the_tree(build_repo, elsewhere) -> Path:
    repo = build_repo({"src/a.py": long_function(62), BASELINE_PATH: "[file_lines\n"})
    put(repo / BASELINE_PATH, ceiling("[file_lines]\n"))
    return repo


def refused_asking_to(step: str) -> str:
    return f"error: 1 of 1 area could not check\nnext: {step}\n"


def unshallow_error() -> str:
    return refused_asking_to("git fetch --unshallow")


def restore_error() -> str:
    return refused_asking_to(restore_step())


def repair_rows_error() -> str:
    return refused_asking_to("fix what the ✗ rows name, then run quality check --fix --dry-run again")


def shallow_new_row() -> str:
    return "  ✗ debt          not fixed      this clone is shallow, so git cannot show the ceiling is new\n"


@pytest.mark.parametrize(
    ("arrange", "out", "err"),
    [
        pytest.param(shallow_clone_holding({"src/a.py": long_function(62)}), shallow_new_row(), unshallow_error(), id="shallow"),
        pytest.param(
            a_ceiling_git_holds,
            "  ✗ debt          not fixed      quality/baseline.toml is gone from the tree but git holds it\n", restore_error(), id="the ceiling git holds",
        ),
        pytest.param(
            a_ceiling_that_is_no_toml,
            "  ✗ debt          not checked    quality/baseline.toml is not valid TOML (Expected ']' at the end of a table declaration (at line 1, column 12))\n",
            restore_error(),
            id="no TOML",
        ),
        pytest.param(
            a_clones_table_git_holds,
            "  ✗ debt          not fixed      quality/baseline.toml lost its [clones] table, which git holds\n", restore_error(), id="a clones table git holds",
        ),
        pytest.param(shallow_clone_holding({"src/a.py": "x = 1\n", BASELINE_PATH: "[file_lines]\n"}), shallow_new_row(), unshallow_error(), id="shallow, a ceiling without a table"),
        pytest.param(
            shallow_clone_holding({"src/a.py": "x = 1\n", BASELINE_PATH: ceiling("[file_lines]\n")}),
            "  ✗ debt          not fixed      this clone is shallow, so git cannot show where quality/baseline.toml last changed\n",
            unshallow_error(),
            id="shallow, the lineage",
        ),
        pytest.param(
            a_file_that_does_not_parse,
            "  ✗ debt          not checked    src/broken.py is not valid Python (invalid syntax (broken.py, line 1))\n",
            repair_rows_error(),
            id="a file that does not parse",
        ),
        pytest.param(
            a_path_no_ceiling_can_hold,
            "  ✗ debt          not fixed      src/\\udcff.py is not valid UTF-8, so quality/baseline.toml cannot hold it\n",
            repair_rows_error(),
            id="a path no ceiling can hold",
        ),
        pytest.param(
            a_path_no_ceiling_can_hold_beside_a_held_ceiling,
            "  ✗ debt          not fixed      tests/test_b\\udcff.py is not valid UTF-8, so quality/baseline.toml cannot hold it\n",
            repair_rows_error(),
            id="a path no ceiling can hold beside a held ceiling",
        ),
        pytest.param(
            a_config_refusal,
            "  ✗ debt          not checked    quality/config.toml has unknown key nope - only [gate] mandatory_jobs, [gate] legacy, [[gate.loosened]], "
            "[secrets] allow, [[advisories.allow]], [[root.allow]], [[layout.allow]], [[python.allow]], [[compose.allow]] and [[size.generated]] exist\n",
            repair_rows_error(),
            id="a config refusal",
        ),
        pytest.param(
            a_committed_ceiling_that_does_not_parse_repaired_in_the_tree,
            "  ✗ debt          not fixed      the ceiling at its last commit {commit}: quality/baseline.toml is not valid TOML "
            "(Expected ']' at the end of a table declaration (at line 1, column 12))\n"
            "    ! src/a.py:1  new debt  function long_one has 62 lines · debt from 60 lines\n"
            "      fix: split the function below 60 lines\n",
            "error: 1 of 1 area could not check · 1 finding, 1 of them in those areas\n"
            "next: commit the repaired quality/baseline.toml, then run quality check --fix --dry-run again\n",
            id="a committed ceiling that does not parse",
        ),
    ],
)
def test_a_dry_run_refuses_where_the_fix_refuses_before_writing(build_repo, elsewhere, capsys, arrange, out, err):
    repo = arrange(build_repo, elsewhere)
    before = untouched(repo)
    expected_out = out.replace("{commit}", head_commit(repo))
    assert (*fixed(capsys, repo, *DRY_RUN_WORDS), untouched(repo)) == (2, expected_out, err, before)
    assert fixed(capsys, repo, *FIX_WORDS)[:2] == (2, expected_out)


@pytest.mark.parametrize(
    ("arrange", "printed", "refused"),
    [
        pytest.param(
            held_above,
            "  ~ debt          1 to lower     quality/baseline.toml\n"
            "    ~ src/a.py:1  lower  function long_one 80 → 62 lines\n"
            "~ dry run · 1 ceiling entry would fall · nothing written · <1s\n",
            (
                (
                    "  ✗ debt          not fixed      cannot write quality/baseline.toml: [Errno 13] Permission denied\n"
                    "    ! src/a.py:1  can fall  function long_one has 62 lines · ceiling 80\n"
                    "      fix: quality check --fix\n"
                ),
                (
                    "error: 1 of 1 area could not check · 1 finding, 1 of them in those areas\n"
                    "next: fix what the ✗ rows name, then run quality check --fix again\n"
                ),
            ),
            marks=pytest.mark.skipif(os.geteuid() == 0, reason="root writes a mode-0444 file"),
            id="a read-only ceiling and folder",
        ),
        pytest.param(
            holding({"src/a.py": long_function(62), "quality": "not a folder\n"}),
            "  ~ debt          to record      1 entry, 0 clone pairs · quality/baseline.toml would be new\n"
            "~ dry run · ceiling would be recorded · nothing written · <1s\n",
            (
                "  ✗ debt          not fixed      cannot write quality/baseline.toml: [Errno 17] File exists\n",
                "error: 1 of 1 area could not check\nnext: fix what the ✗ rows name, then run quality check --fix again\n",
            ),
            id="quality is a file",
        ),
    ],
)
def test_a_dry_run_attempts_no_write(build_repo, capsys, request, arrange, printed, refused):
    repo = arrange(build_repo)
    if (repo / BASELINE_PATH).exists():
        (repo / BASELINE_PATH).chmod(0o444)
        (repo / BASELINE_PATH).parent.chmod(0o555)
        request.addfinalizer(lambda: (repo / BASELINE_PATH).parent.chmod(0o755))
    before = untouched(repo)
    assert (*fixed(capsys, repo, *DRY_RUN_WORDS), untouched(repo)) == (1, printed, "", before)
    assert (*fixed(capsys, repo, *FIX_WORDS), untouched(repo)) == (2, *refused, before)


def test_another_areas_refusal_leaves_the_dry_run_standing(build_repo, capsys):
    repo = held_above(build_repo)
    put(repo / "compose.yaml", "services: [\n")
    git(repo, "add", "compose.yaml")
    before = untouched(repo)
    printed = (
        "  ✗ files         not checked    cannot read compose.yaml: line 2, column 1: while parsing a flow node, expected the node content, but found '<stream end>'\n"
        "  ~ debt          1 to lower     quality/baseline.toml\n"
        "    ~ src/a.py:1  lower  function long_one 80 → 62 lines\n"
    )
    err = "error: 1 of 2 areas could not check\nnext: fix what the ✗ rows name, then run quality check --fix --dry-run again\n"
    words = ("check", "files", "debt", "--fix", "--dry-run")
    assert (*fixed(capsys, repo, *words), untouched(repo)) == (2, printed, err, before)


def painted(repo: Path) -> tuple[int, str]:
    stdout = Terminal()
    with contextlib.redirect_stdout(stdout):
        code = door_in(repo, *DRY_RUN_WORDS, clock=lambda: 0.0, environment={"TERM": "xterm"})
    return code, stdout.getvalue()


def test_at_a_terminal_a_dry_run_keeps_the_plain_words(build_repo, capsys):
    repo = held_above(build_repo)
    piped = fixed(capsys, repo, *DRY_RUN_WORDS)
    code, frame = painted(repo)
    painted_run = (code, without_sgr(frame), capsys.readouterr().err)
    assert painted_run == piped


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


def test_a_debt_row_that_would_change_is_not_counted_among_the_clean_areas(build_repo, capsys):
    repo = held_above(build_repo)
    put(repo / ".env", "SECRET=1\n")
    git(repo, "add", ".env")
    printed = (
        "  ! files         2 findings\n"
        "    ! .env  tracked   an env file carries configuration, often secrets\n"
        "      fix: git --literal-pathspecs rm --cached -- .env, then add .env to .gitignore\n"
        "    ! .env  unlisted  not on quality's base list\n"
        "      fix: move it to its owner's directory, or declare it in quality/config.toml with [[root.allow]], an entry and a reason\n"
        "  ~ debt          1 to lower     quality/baseline.toml\n"
        "    ~ src/a.py:1  lower  function long_one 80 → 62 lines\n"
        "! 2 findings in 1 area · 1 ceiling entry would fall · nothing written · <1s\n"
    )
    words = ("check", "files", "debt", "--fix", "--dry-run")
    assert fixed(capsys, repo, *words) == (1, printed, "")


def test_a_dry_run_that_would_change_the_file_exits_one_beside_an_area_that_only_measures(build_repo, capsys):
    repo = held_above(build_repo)
    printed = (
        "  ~ debt          1 to lower     quality/baseline.toml\n"
        "    ~ src/a.py:1  lower  function long_one 80 → 62 lines\n"
        "  · imports       not set up     no import-linter config\n"
        "~ dry run · 1 ceiling entry would fall · nothing written · imports not enforced · <1s\n"
    )
    words = ("check", "imports", "debt", "--fix", "--dry-run")
    assert fixed(capsys, repo, *words) == (1, printed, "")


def test_at_a_terminal_a_dry_run_paints_each_glyph_and_the_verdict_in_one_colour_and_the_rest_in_others(build_repo):
    code, frame = painted(held_above(build_repo))
    printed = (
        "  {glyph}~{reset} debt          1 to lower     {other}quality/baseline.toml{reset}\n"
        "    {glyph}~{reset} src/a.py:1  lower  {other}function long_one 80 → 62 lines{reset}\n"
        "{verdict}~ dry run{reset} · {other}1 ceiling entry would fall{reset} · {other}nothing written{reset} · {other}<1s{reset}\n"
    )
    roles = by_role(frame, "~")
    assert (code, roles) == (1, printed)
