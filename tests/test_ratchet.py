import tomllib
from pathlib import Path

import pytest

from quality import cli
from quality.ratchet import BASELINE_PATH

EMPTY_BASELINE = ""


def long_file(lines: int) -> str:
    return "x = 1\n" * lines


def long_function(lines: int) -> str:
    return "def long_one():\n" + "    x = 1\n" * (lines - 1)


def branchy_function(ccn: int) -> str:
    branches = "".join(f"    if x == {value}:\n        return {value}\n" for value in range(ccn - 1))
    return f"def branchy(x):\n{branches}    return -1\n"


def method(lines: int) -> str:
    return "class Holder:\n" + "".join(f"    {line}\n" for line in long_function(lines).splitlines())


def hygiene(capsys: pytest.CaptureFixture[str], path: Path) -> tuple[int, str]:
    code = cli.main(["hygiene", str(path)])
    captured = capsys.readouterr()
    return code, captured.out + captured.err


def test_without_a_baseline_the_rule_only_counts_production_and_tests(build_repo, capsys):
    """#46 rule 3: no baseline, counts per class for production and tests, exit unchanged."""
    repo = build_repo(
        {
            "src/big.py": long_file(800) + long_function(60) + branchy_function(16),
            "tests/test_big.py": long_file(900),
        }
    )
    code, output = hygiene(capsys, repo)
    assert code == 0, output
    assert (
        "size: report only (no quality/baseline.toml) - production: 1 file(s) >= 800 lines, "
        "1 function(s) >= 60 lines, 1 function(s) CCN > 15; tests: 1 file(s) >= 800 lines, "
        "0 function(s) >= 60 lines, 0 function(s) CCN > 15"
    ) in output


@pytest.mark.parametrize(
    ("files", "baseline", "expected"),
    [
        (
            {"src/big.py": long_file(800)},
            EMPTY_BASELINE,
            "size src/big.py - file has 800 lines; the threshold is 800 (new debt).",
        ),
        (
            {"src/a.py": method(60)},
            EMPTY_BASELINE,
            "size src/a.py:2 - function Holder.long_one has 60 lines; the threshold is 60 (new debt).",
        ),
        (
            {"src/a.py": branchy_function(16)},
            EMPTY_BASELINE,
            "size src/a.py:1 - function branchy has CCN 16; the threshold is above 15 (new debt).",
        ),
        (
            {"src/a.py": long_function(71)},
            '[function_lines."src/a.py"]\nlong_one = 70\n',
            "size src/a.py:1 - function long_one has 71 lines; the baseline holds 70 (it grew).",
        ),
        (
            {"src/a.py": long_function(65)},
            '[function_lines."src/a.py"]\nlong_one = 70\n',
            "size src/a.py:1 - function long_one has 65 lines; the baseline holds 70 (stale).",
        ),
    ],
    ids=["new-file-from-800-lines", "new-function-from-60-lines", "new-ccn-above-15", "grown-entry", "stale-entry"],
)
def test_against_a_baseline_new_grown_and_stale_entries_are_red(build_repo, capsys, files, baseline, expected):
    """#46 rule 4: a new violation, a grown entry and a stale entry each name path, symbol, value and fix."""
    code, output = hygiene(capsys, build_repo({**files, BASELINE_PATH: baseline}))
    assert code == 1
    assert expected in output
    assert "\n  Fix: " in output


def test_a_stale_entry_tells_how_to_lower_it(build_repo, capsys):
    """#46 rule 4: a stale entry says "lower it with `quality baseline write`"."""
    repo = build_repo({"src/a.py": long_function(65), BASELINE_PATH: '[function_lines."src/a.py"]\nlong_one = 70\n'})
    _, output = hygiene(capsys, repo)
    assert "lower it with `quality baseline write`" in output


@pytest.mark.parametrize(
    "files",
    [
        {"src/a.py": long_file(799)},
        {"src/a.py": long_function(59) + branchy_function(15)},
        {"src/other.py": "x = 1\n"},
    ],
    ids=["file-just-below-800-lines", "functions-just-below-60-lines-and-ccn-15", "baselined-file-is-gone"],
)
def test_below_the_thresholds_or_with_a_fixed_entry_gone_is_clean(build_repo, capsys, files):
    """#46 rule 2/4: values below the thresholds are no debt, and a fixed entry that is gone is fine."""
    baseline = '[file_lines]\n"src/gone.py" = 900\n'
    code, output = hygiene(capsys, build_repo({**files, BASELINE_PATH: baseline}))
    assert code == 0, output
    assert "hygiene: clean (3 rules)" in output


def test_baseline_write_records_the_measured_debt_sorted_and_hygiene_then_holds_it(build_repo, capsys):
    """#46 rule 5: `quality baseline write` writes sorted TOML keyed by path and qualified symbol."""
    repo = build_repo(
        {
            "src/b.py": long_file(810) + method(61),
            "src/a.py": branchy_function(17) + long_function(62),
            "src/small.py": "x = 1\n",
        }
    )
    assert cli.main(["baseline", "write", str(repo)]) == 0
    written = (repo / BASELINE_PATH).read_text(encoding="utf-8")
    assert tomllib.loads(written) == {
        "ccn": {"src/a.py": {"branchy": 17}},
        "file_lines": {"src/b.py": 810 + 61 + 1},
        "function_lines": {"src/a.py": {"long_one": 62}, "src/b.py": {"Holder.long_one": 61}},
    }
    assert written.index("[ccn") < written.index("[file_lines]") < written.index('[function_lines."src/a.py"]')
    assert written.index('[function_lines."src/a.py"]') < written.index('[function_lines."src/b.py"]')
    capsys.readouterr()
    code, output = hygiene(capsys, repo)
    assert (code, "hygiene: clean (3 rules)" in output) == (0, True), output


def test_writing_the_baseline_twice_yields_the_same_file(build_repo):
    """#46 rule 5: the written baseline is the measurement, so writing it again changes nothing."""
    repo = build_repo({"src/a.py": long_function(62)})
    cli.main(["baseline", "write", str(repo)])
    first = (repo / BASELINE_PATH).read_text(encoding="utf-8")
    cli.main(["baseline", "write", str(repo)])
    assert (repo / BASELINE_PATH).read_text(encoding="utf-8") == first


@pytest.mark.parametrize(
    "baseline",
    ["[file_lines\n", '[unknown]\n"src/a.py" = 1\n', '[file_lines]\n"src/a.py" = "many"\n'],
    ids=["not-toml", "unknown-metric", "not-a-count"],
)
def test_an_unreadable_baseline_cannot_be_judged(build_repo, capsys, baseline):
    """#46 rule 4: a baseline the rule cannot read is exit 2, never clean."""
    code, output = hygiene(capsys, build_repo({"src/a.py": "x = 1\n", BASELINE_PATH: baseline}))
    assert code == 2
    assert "cannot judge" in output


def link_the_baseline_outside(repo: Path, outside: Path) -> None:
    (repo / BASELINE_PATH).parent.mkdir(exist_ok=True)
    (repo / BASELINE_PATH).symlink_to(outside / "victim.toml")


def link_the_quality_directory_outside(repo: Path, outside: Path) -> None:
    (repo / BASELINE_PATH).parent.symlink_to(outside)


@pytest.mark.parametrize(
    "arrange",
    [link_the_baseline_outside, link_the_quality_directory_outside],
    ids=["baseline-symlink-outside", "quality-directory-symlink-outside"],
)
def test_baseline_write_to_a_destination_it_cannot_own_cannot_be_judged(build_repo, tmp_path_factory, capsys, arrange):
    """#46 rule 5: the baseline is written to the repository's own quality/baseline.toml, never through a symlink."""
    outside = tmp_path_factory.mktemp("outside")
    (outside / "victim.toml").write_text("kept\n")
    repo = build_repo({"src/a.py": long_function(62)})
    arrange(repo, outside)
    assert cli.main(["baseline", "write", str(repo)]) == 2
    assert "cannot judge" in capsys.readouterr().err
    assert sorted(path.name for path in outside.iterdir()) == ["victim.toml"]
    assert (outside / "victim.toml").read_text() == "kept\n"


@pytest.mark.parametrize(
    "blocker",
    [{"quality": "a file\n"}, {f"{BASELINE_PATH}/inside.txt": "a directory\n"}],
    ids=["quality-is-a-file", "baseline-is-a-directory"],
)
def test_baseline_write_that_the_filesystem_refuses_cannot_be_judged(build_repo, capsys, blocker):
    """#46 rule 5: exit 2, not a traceback: a baseline that cannot be written is never a clean result."""
    repo = build_repo({"src/a.py": long_function(62), **blocker})
    assert cli.main(["baseline", "write", str(repo)]) == 2
    assert "baseline: cannot judge - cannot write quality/baseline.toml" in capsys.readouterr().err


@pytest.mark.parametrize(
    "path",
    ["src/\U0001f600.py", "src/café.py", "src/del\x7f.py", 'src/quote"back\\slash.py'],
    ids=["outside-the-bmp", "accented", "del-character", "quote-and-backslash"],
)
def test_a_written_baseline_holds_any_utf8_path(build_repo, capsys, path):
    """#46 rule 5: whatever the path, the written baseline is TOML that hygiene then holds."""
    repo = build_repo({path: long_file(810) + long_function(62)})
    assert cli.main(["baseline", "write", str(repo)]) == 0
    assert tomllib.loads((repo / BASELINE_PATH).read_text(encoding="utf-8")) == {
        "file_lines": {path: 810 + 62},
        "function_lines": {path: {"long_one": 62}},
    }
    capsys.readouterr()
    code, output = hygiene(capsys, repo)
    assert (code, "hygiene: clean (3 rules)" in output) == (0, True), output


def test_a_path_that_is_not_utf8_cannot_be_written_into_the_baseline(build_repo, capsys):
    """#46 rule 5: TOML holds only Unicode, so a non-UTF-8 file name is exit 2, never a baseline hygiene cannot read."""
    repo = build_repo({"src/latin\udce9.py": long_file(810)})
    assert cli.main(["baseline", "write", str(repo)]) == 2
    assert "baseline: cannot judge - src/latin\\udce9.py is not valid UTF-8" in capsys.readouterr().err
    assert not (repo / BASELINE_PATH).exists()
