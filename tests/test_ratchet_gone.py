"""A held ceiling entry the tree no longer measures: which can fall and why, and what `--fix` does with each."""

import datetime
from pathlib import Path

import pytest

from quality.config import CONFIG_PATH, GeneratedFile
from quality.ratchet import BASELINE_PATH, Key, Measured, Metric, gone_entries, load_baseline
from tests.support.door import CHECK_WORD, FIX_WORDS, digest, fixed
from tests.support.generated_files import MANDATORY_JOB, generated_config
from tests.support.git_repositories import git, put
from tests.support.source_code import branchy_function, long_file, long_function
from tests.support.verdict import ONLY_DECLARED, gate_config, verdict_streams
from tests.support.verdict_renames import commit_all


def entry(metric: Metric, path: str, symbol: str = "") -> Key:
    return Key(metric, path, symbol)


def declared(*paths: str) -> list[GeneratedFile]:
    return [GeneratedFile(path, MANDATORY_JOB, "#1", datetime.date(2026, 1, 1), "written by a generator") for path in paths]


def gate_declaring(path: str) -> str:
    return f'[gate]\nmandatory_jobs = ["{MANDATORY_JOB}"]\n' + generated_config(path=path)


def ceiling(table: str) -> str:
    return table + "\n[clones]\n"


def checked(capsys, repo: Path) -> tuple[int, str, str]:
    return fixed(capsys, repo, CHECK_WORD, "debt")


@pytest.mark.parametrize(
    ("held", "measured", "tracked", "generated", "expected"),
    [
        pytest.param(entry(Metric.FILE_LINES, "src/legacy.py"), [], {"src/a.py"}, [], "file is gone", id="a file the index no longer tracks"),
        pytest.param(entry(Metric.FUNCTION_LINES, "src/legacy.py", "long_one"), [], {"src/a.py"}, [], "function long_one is gone", id="a function of that file"),
        pytest.param(
            entry(Metric.FUNCTION_LINES, "src/a.py", "long_one"),
            [entry(Metric.FILE_LINES, "src/a.py")],
            {"src/a.py"},
            [],
            "function long_one is gone",
            id="a function its Python file lost",
        ),
        pytest.param(
            entry(Metric.CCN, "src/a.py", "branchy"),
            [entry(Metric.FILE_LINES, "src/a.py")],
            {"src/a.py"},
            [],
            "function branchy is gone",
            id="a CCN entry its Python file lost",
        ),
        pytest.param(
            entry(Metric.FILE_LINES, "gen/api.py"),
            [entry(Metric.FILE_LINES, "src/a.py")],
            {"src/a.py", "gen/api.py"},
            declared("gen/api.py"),
            "file is left out as generated",
            id="a declared generated file",
        ),
        pytest.param(
            entry(Metric.FUNCTION_LINES, "gen/api.py", "long_one"),
            [],
            {"gen/api.py"},
            declared("gen/api.py"),
            "function long_one is left out as generated",
            id="a function of a declared generated file",
        ),
        pytest.param(entry(Metric.FILE_LINES, "gen/old.py"), [], {"src/a.py"}, declared("gen/old.py"), "file is gone", id="declared and untracked is gone"),
        pytest.param(entry(Metric.FILE_LINES, "src/a.py"), [], {"src/a.py"}, [], None, id="tracked and off the disk stands"),
        pytest.param(
            entry(Metric.FILE_LINES, "src/a.py"), [], {"src/a.py"}, declared("src/a.py"), "file is left out as generated", id="tracked, off the disk and declared is left out"
        ),
        pytest.param(
            entry(Metric.FUNCTION_LINES, "src/a.ts", "big"),
            [entry(Metric.FILE_LINES, "src/a.ts")],
            {"src/a.ts"},
            [],
            None,
            id="a function entry under a TypeScript file stands",
        ),
        pytest.param(entry(Metric.FILE_LINES, "docs.md"), [], {"docs.md"}, [], None, id="an undeclared file in no measured language stands"),
        pytest.param(
            entry(Metric.FILE_LINES, "docs.md"), [], {"docs.md"}, declared("docs.md"), "file is left out as generated", id="a declared file in no measured language is left out"
        ),
        pytest.param(entry(Metric.FILE_LINES, "src/a.py"), [entry(Metric.FILE_LINES, "src/a.py")], {"src/a.py"}, [], None, id="a measured entry is not gone"),
    ],
)
def test_a_held_entry_without_a_measurement_is_gone_left_out_or_stands_by_the_index_and_the_declarations(held, measured, tracked, generated, expected):
    measurements = {key: Measured(90, 1) for key in measured}
    gone = gone_entries({held: 900}, measurements, frozenset(tracked), generated)
    assert gone == ({} if expected is None else {held: expected})


@pytest.mark.parametrize(
    ("files", "held", "checking", "fixing"),
    [
        pytest.param(
            {"src/a.py": "x = 1\n"},
            '[file_lines]\n"src/legacy.py" = 802\n',
            "  ! debt          1 finding\n"
            "    ! src/legacy.py  can fall  file is gone · ceiling 802 lines\n"
            "      fix: quality check --fix\n"
            "! 1 finding in 1 area · <1s\n",
            "  ✓ debt          lowered        1 entry · quality/baseline.toml\n"
            "    ✓ src/legacy.py  dropped  file is gone\n"
            "✓ clean · ceiling lowered by 1 entry · 1 area · <1s\n",
            id="file",
        ),
        pytest.param(
            {"src/a.py": "x = 1\n"},
            '[function_lines."src/a.py"]\nlong_one = 70\n',
            "  ! debt          1 finding\n"
            "    ! src/a.py  can fall  function long_one is gone · ceiling 70 lines\n"
            "      fix: quality check --fix\n"
            "! 1 finding in 1 area · <1s\n",
            "  ✓ debt          lowered        1 entry · quality/baseline.toml\n"
            "    ✓ src/a.py  dropped  function long_one is gone\n"
            "✓ clean · ceiling lowered by 1 entry · 1 area · <1s\n",
            id="function",
        ),
        pytest.param(
            {"src/a.py": "x = 1\n"},
            '[ccn."src/a.py"]\nbranchy = 19\n',
            "  ! debt          1 finding\n"
            "    ! src/a.py  can fall  function branchy is gone · ceiling CCN 19\n"
            "      fix: quality check --fix\n"
            "! 1 finding in 1 area · <1s\n",
            "  ✓ debt          lowered        1 entry · quality/baseline.toml\n"
            "    ✓ src/a.py  dropped  function branchy is gone\n"
            "✓ clean · ceiling lowered by 1 entry · 1 area · <1s\n",
            id="ccn",
        ),
        pytest.param(
            {"gen/types.ts": "export {};\n" * 900, CONFIG_PATH: gate_declaring("gen/types.ts")},
            '[file_lines]\n"gen/types.ts" = 850\n',
            "  ! debt          1 finding\n"
            "    ! gen/types.ts  can fall  file is left out as generated · ceiling 850 lines\n"
            "      fix: quality check --fix\n"
            "! 1 finding in 1 area · <1s\n",
            "  ✓ debt          lowered        1 entry · quality/baseline.toml\n"
            "    ✓ gen/types.ts  dropped  file is left out as generated\n"
            "✓ clean · ceiling lowered by 1 entry · 1 area · <1s\n",
            id="generated file",
        ),
        pytest.param(
            {"gen/api.py": long_function(70), CONFIG_PATH: gate_declaring("gen/api.py")},
            '[function_lines."gen/api.py"]\nlong_one = 70\n',
            "  ! debt          1 finding\n"
            "    ! gen/api.py  can fall  function long_one is left out as generated · ceiling 70 lines\n"
            "      fix: quality check --fix\n"
            "! 1 finding in 1 area · <1s\n",
            "  ✓ debt          lowered        1 entry · quality/baseline.toml\n"
            "    ✓ gen/api.py  dropped  function long_one is left out as generated\n"
            "✓ clean · ceiling lowered by 1 entry · 1 area · <1s\n",
            id="generated function",
        ),
    ],
)
def test_a_gone_entry_can_fall_and_the_fix_drops_it(build_repo, capsys, files, held, checking, fixing):
    repo = build_repo({**files, BASELINE_PATH: ceiling(held)})
    assert checked(capsys, repo) == (1, checking, "")
    assert fixed(capsys, repo, *FIX_WORDS) == (0, fixing, "")
    assert (repo / BASELINE_PATH).read_text() == "# Measured by `quality check --fix`. Entries only ever go down.\n\n[clones]\n"
    assert checked(capsys, repo) == (0, "  ✓ debt          clean          ceiling held · 0 entries, 0 clone pairs\n✓ clean · 1 area · <1s\n", "")


def test_the_pictures_gone_rows(build_repo, capsys):
    held = '[ccn."src/cart.py"]\nbranchy = 19\n\n[file_lines]\n"src/orders.py" = 826\n"src/legacy.py" = 802\n'
    repo = build_repo({"src/cart.py": branchy_function(17), "src/orders.py": long_file(812), BASELINE_PATH: ceiling(held)})
    checking = (
        "  ! debt          3 findings\n"
        "    ! src/cart.py:1  can fall  function branchy has CCN 17 · ceiling 19\n"
        "    ! src/orders.py  can fall  file has 812 lines · ceiling 826\n"
        "    ! src/legacy.py  can fall  file is gone · ceiling 802 lines\n"
        "      fix: quality check --fix\n"
        "! 3 findings in 1 area · <1s\n"
    )
    fixing = (
        "  ✓ debt          lowered        3 entries · quality/baseline.toml\n"
        "    ✓ src/cart.py:1  lowered  function branchy CCN 19 → 17\n"
        "    ✓ src/orders.py  lowered  file 826 → 812 lines\n"
        "    ✓ src/legacy.py  dropped  file is gone\n"
        "✓ clean · ceiling lowered by 3 entries · 1 area · <1s\n"
    )
    assert checked(capsys, repo) == (1, checking, "")
    assert fixed(capsys, repo, *FIX_WORDS) == (0, fixing, "")


def moved_with_git(pull_request) -> Path:
    repo, commit_base = pull_request
    put(repo / "src/old.py", long_function(70))
    commit_base(ceiling('[function_lines."src/old.py"]\nlong_one = 70\n'))
    return repo


def test_a_staged_move_is_gone_beside_new_debt(pull_request, capsys):
    repo = moved_with_git(pull_request)
    git(repo, "mv", "src/old.py", "src/new.py")
    checking = (
        "  ! debt          2 findings\n"
        "    ! src/new.py:1  new debt  function long_one has 70 lines · debt from 60 lines\n"
        "      fix: split the function below 60 lines\n"
        "    ! src/old.py    can fall  function long_one is gone · ceiling 70 lines\n"
        "      fix: quality check --fix\n"
        "! 2 findings in 1 area · <1s\n"
    )
    assert checked(capsys, repo) == (1, checking, "")
    code, out, _ = fixed(capsys, repo, *FIX_WORDS)
    assert (code, "moved  function long_one 70 lines from src/old.py" in out, "dropped" in out) == (0, True, False)
    assert checked(capsys, repo)[0] == 0


def test_an_untracked_move_destination_keeps_the_entry(pull_request, capsys):
    repo = moved_with_git(pull_request)
    (repo / "src/old.py").rename(repo / "src/new.py")
    before = digest(repo)
    assert checked(capsys, repo)[0] == 0
    code, out, _ = fixed(capsys, repo, *FIX_WORDS)
    assert (code, "dropped" in out, digest(repo)) == (0, False, before)
    git(repo, "add", "src/new.py")
    code, out, _ = checked(capsys, repo)
    assert (code, "new debt" in out, "can fall" in out) == (1, True, False)
    git(repo, "add", "-A")
    code, out, _ = fixed(capsys, repo, *FIX_WORDS)
    assert (code, "moved" in out, "dropped" in out) == (0, True, False)


def test_a_staged_deletion_with_an_untracked_destination_drops_the_entry_until_the_file_is_tracked(pull_request, capsys):
    repo = moved_with_git(pull_request)
    (repo / "src/old.py").rename(repo / "src/new.py")
    git(repo, "rm", "-q", "--cached", "src/old.py")
    assert "function long_one is gone" in checked(capsys, repo)[1]
    code, out, _ = fixed(capsys, repo, *FIX_WORDS)
    assert (code, "dropped  function long_one is gone" in out, "src/old.py" in (repo / BASELINE_PATH).read_text()) == (0, True, False)
    git(repo, "add", "src/new.py")
    code, out, _ = checked(capsys, repo)
    assert (code, "new debt" in out) == (1, True)
    git(repo, "checkout", "HEAD", "--", BASELINE_PATH)
    code, out, _ = fixed(capsys, repo, *FIX_WORDS)
    assert (code, "moved" in out, "dropped" in out) == (0, True, False)


def test_a_sparse_checkout_keeps_the_entries_it_does_not_hold(build_repo, capsys):
    held = ceiling('[function_lines."src/out/b.py"]\nlong_one = 70\n')
    repo = build_repo({"src/in/a.py": "x = 1\n", "src/out/b.py": long_function(70), BASELINE_PATH: held})
    git(repo, "sparse-checkout", "set", "src/in", "quality")
    before = digest(repo)
    assert (repo / "src/out/b.py").exists() is False
    assert checked(capsys, repo)[0] == 0
    code, out, _ = fixed(capsys, repo, *FIX_WORDS)
    assert (code, "dropped" in out, digest(repo)) == (0, False, before)


def test_a_deletion_on_disk_only_stands_until_it_is_staged(build_repo, capsys):
    repo = build_repo({"src/a.py": long_function(70), BASELINE_PATH: ceiling('[function_lines."src/a.py"]\nlong_one = 70\n')})
    (repo / "src/a.py").unlink()
    before = digest(repo)
    assert checked(capsys, repo)[0] == 0
    code, out, _ = fixed(capsys, repo, *FIX_WORDS)
    assert (code, "dropped" in out, digest(repo)) == (0, False, before)
    git(repo, "add", "-A")
    assert "function long_one is gone" in checked(capsys, repo)[1]
    assert fixed(capsys, repo, *FIX_WORDS)[0] == 0
    assert load_baseline((repo / BASELINE_PATH).read_text()).size == {}


def test_an_unparsable_file_refuses_before_anything_is_gone(build_repo, capsys):
    held = ceiling('[file_lines]\n"src/legacy.py" = 802\n')
    repo = build_repo({"src/bad.py": "def broken(:\n", BASELINE_PATH: held})
    before = digest(repo)
    for words in ((CHECK_WORD, "debt"), FIX_WORDS):
        code, out, _ = fixed(capsys, repo, *words)
        assert (code, "not valid Python" in out, "is gone" in out, digest(repo)) == (2, True, False, before)


def test_a_dropped_generated_entry_beside_its_new_declaration_prints_only_the_declaration(pull_request, capsys):
    repo, commit_base = pull_request
    put(repo / "gen/api.py", long_function(70))
    commit_base(ceiling('[function_lines."gen/api.py"]\nlong_one = 70\n'))
    put(repo / CONFIG_PATH, gate_config(MANDATORY_JOB) + generated_config(path="gen/api.py"))
    assert fixed(capsys, repo, *FIX_WORDS)[0] == 0
    assert "gen/api.py" not in (repo / BASELINE_PATH).read_text()
    commit_all(repo, "declare and drop")
    code, out, err = verdict_streams(repo, capsys)
    lines = out.splitlines()
    assert (code, len(lines), lines[-1], err) == (1, 2, ONLY_DECLARED, "")
    assert lines[0].endswith("size leaves out generated file gen/api.py")
