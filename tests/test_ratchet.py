import os
import re
import tomllib
from collections.abc import Callable
from pathlib import Path

import pytest

from quality import __version__, cli
from quality.ratchet import (
    BASELINE_PATH,
    Baseline,
    ClonePair,
    Key,
    Metric,
    Place,
    RaisedEntry,
    Renames,
    carried_base,
    load_baseline,
    raised_entries,
    render_baseline,
)
from tests.support.git_repositories import make_repo
from tests.support.hygiene import CLEAN_LINE
from tests.support.hygiene import hygiene_output as hygiene
from tests.support.source_code import (
    branchy_function,
    long_file,
    long_function,
    minified_bundle,
    typescript_file,
    typescript_function,
)

EMPTY_BASELINE = ""


def method(lines: int) -> str:
    return "class Holder:\n" + "".join(f"    {line}\n" for line in long_function(lines).splitlines())


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


def build_repo_with_source(build_repo, files: dict[str, str], name: str, source: bytes) -> Path:
    """A repository tracking `name` with exactly these bytes, which a text fixture cannot carry."""
    repo = build_repo({**files, name: ""})
    (repo / name).write_bytes(source)
    return repo


SOURCE_NAME = "src/source.py"


@pytest.mark.parametrize(
    ("source", "python_security_cannot_judge"),
    [
        (b"# -*- coding: latin-1 -*-\n# \xe9\n" + long_function(60).encode(), True),
        (b"\xef\xbb\xbf" + long_function(60).encode(), False),
        ("# \u00e9\n".encode() + long_function(60).encode(), False),
        (b"# header\r" + long_function(60).encode().replace(b"\n", b"\r"), False),
        ((b"# header\r" + long_function(60).encode() + b"    return b''.decode(encoding=enc)\n").replace(b"\n", b"\r"), False),
        (b"\r# -*- coding: latin-1 -*-\r# \xe9\r" + long_function(60).encode().replace(b"\n", b"\r"), True),
    ],
    ids=[
        "pep263-latin-1",
        "utf-8-bom",
        "plain-utf-8",
        "cr-only-newlines",
        "cr-only-cookie-lookalike-in-code",
        "cr-only-cookie-on-line-2",
    ],
)
def test_a_file_python_can_read_is_measured_in_its_declared_encoding(build_repo, capsys, source, python_security_cannot_judge):
    """#46 rule 1: the size rule measures every tracked Python file Python itself reads; ruff reads UTF-8 only (#219)."""
    code, output = hygiene(capsys, build_repo_with_source(build_repo, {}, SOURCE_NAME, source))
    assert ("hygiene: python-security cannot judge" in output) == python_security_cannot_judge, output
    assert code == (2 if python_security_cannot_judge else 0), output
    assert "production: 0 file(s) >= 800 lines, 1 function(s) >= 60 lines" in output
    assert "not valid Python" not in output


NOT_PYTHON = pytest.mark.parametrize(
    "source",
    [
        b"# \xe9\nx = 1\n",
        b"# -*- coding: utf-8 -*-\nx = 1\n# \xe9\n",
        b"# -*- coding: klingon -*-\nx = 1\n",
        b"x = 1\x00\n",
        b'print "py2"\n',
        b"# coding: rot13\nx = 1\n",
        b"# coding: base64_codec\nx = 1\n",
    ],
    ids=[
        "undecodable-utf-8",
        "invalid-for-its-cookie",
        "unknown-codec",
        "null-byte",
        "python-2",
        "non-text-codec",
        "registered-binary-codec",
    ],
)


@NOT_PYTHON
def test_without_a_baseline_the_size_rule_names_a_file_python_cannot_read_and_python_security_decides_the_exit(
    build_repo, capsys, source
):
    """#46 rule 3: the size rule reports the file without refusing; python-security (#219) alone turns the run to exit 2."""
    code, output = hygiene(capsys, build_repo_with_source(build_repo, {"src/ok.py": "x = 1\n"}, SOURCE_NAME, source))
    assert code == 2, output
    assert re.search(rf"hygiene: python-security (cannot judge - cannot read|unparsable - ruff cannot parse) {re.escape(SOURCE_NAME)}", output), output
    assert "size cannot judge" not in output
    assert "size unparsable" not in output
    assert f"1 file(s) not valid Python, functions unmeasured: {SOURCE_NAME}" in output


@NOT_PYTHON
def test_against_a_baseline_a_file_python_cannot_read_is_unparsable(build_repo, capsys, source):
    """#46 rule 4: a baseline is held against every function, so an unmeasurable file is unparsable, exit 2."""
    repo = build_repo_with_source(build_repo, {BASELINE_PATH: EMPTY_BASELINE}, SOURCE_NAME, source)
    code, output = hygiene(capsys, repo)
    assert code == 2
    assert f"hygiene: size unparsable - {SOURCE_NAME} is not valid Python" in output


@NOT_PYTHON
def test_baseline_write_refuses_a_file_python_cannot_read(build_repo, capsys, source):
    """#46 rule 5: a baseline holds every function, so one written with a file unmeasured would hide its debt."""
    repo = build_repo_with_source(build_repo, {}, SOURCE_NAME, source)
    assert cli.main(["baseline", "write", str(repo)]) == 2
    assert f"baseline: not written - {SOURCE_NAME} is not valid Python" in capsys.readouterr().err
    assert not (repo / BASELINE_PATH).exists()


@pytest.mark.parametrize(
    ("files", "baseline", "expected"),
    [
        (
            {"src/big.py": long_file(800)},
            EMPTY_BASELINE,
            "size src/big.py - file has 800 lines; debt starts at 800 lines (new debt).",
        ),
        (
            {"src/a.py": method(60)},
            EMPTY_BASELINE,
            "size src/a.py:2 - function Holder.long_one has 60 lines; debt starts at 60 lines (new debt).",
        ),
        (
            {"src/a.py": branchy_function(16)},
            EMPTY_BASELINE,
            "size src/a.py:1 - function branchy has CCN 16; debt starts at CCN 16 (new debt).",
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
    assert CLEAN_LINE in output


@pytest.fixture(params=["deleted", "dangling", "cell 18 dangling out of the repository"])
def remove(request, elsewhere) -> Callable[[Path], None]:
    """Takes a tracked file off the disk: deleted, or replaced by a link to nowhere inside or outside the repository."""
    link_target = {
        "deleted": None,
        "dangling": Path("missing.py"),
        "cell 18 dangling out of the repository": elsewhere / "missing.py",
    }[request.param]

    def take_off_the_disk(path: Path) -> None:
        path.unlink()
        if link_target is not None:
            path.symlink_to(link_target)

    return take_off_the_disk


@pytest.mark.parametrize(
    "baseline",
    [None, '[function_lines."src/b.py"]\nlong_one = 62\n'],
    ids=["no-baseline", "baseline-holds-the-gone-file"],
)
def test_a_tracked_file_missing_from_disk_is_gone_for_the_size_rule_and_only_python_security_refuses_it(
    build_repo, capsys, remove, baseline
):
    """#46 rule 3/4 and #52 cell 18: the size rule judges the tree on disk; a tracked file absent from it is gone,
    which is fine, wherever a dangling link points. python-security (#219) must read every tracked `.py` file, so it
    alone refuses the run."""
    files = {"src/a.py": "x = 1\n", "src/b.py": long_function(62)}
    repo = build_repo(files if baseline is None else {**files, BASELINE_PATH: baseline})
    remove(repo / "src/b.py")
    code, output = hygiene(capsys, repo)
    refusals = [line for line in output.splitlines() if " cannot judge - " in line or " unparsable - " in line]
    assert (code, "hygiene: 0 finding(s);" in output) == (2, True), output
    assert len(refusals) == 1, output
    assert refusals[0].startswith("hygiene: python-security cannot judge - "), output
    assert "src/b.py" in refusals[0], output


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads a mode-0 path, so no permission is refused")
@pytest.mark.parametrize(
    ("locked", "reason"),
    [
        ("locked", "cannot reach {file}: [Errno 13] Permission denied"),
        ("{file}", "cannot read {file}: [Errno 13] Permission denied"),
    ],
    ids=["directory", "cell 17 file"],
)
@pytest.mark.parametrize("file", ["locked/b.py", "locked/b.ts", "locked/b.svelte"], ids=["Python", "#119 cell 21 TypeScript", "#119 cell 21 Svelte"])
@pytest.mark.parametrize("command", ["hygiene", "baseline write"])
def test_a_tracked_file_the_filesystem_refuses_to_reach_cannot_be_judged(build_repo, capsys, command, file, locked, reason):
    """#46 rule 3/5 and #52 rule 5, cell 17: a tracked file the rule may not reach or read is exit 2, never gone,
    never a traceback, and its reason names no checkout path. #119 cell 21: a file of any table language likewise."""
    repo = build_repo({"src/a.py": "x = 1\n", file: long_function(62)})
    locked, reason = locked.format(file=file), reason.format(file=file)
    (repo / locked).chmod(0)
    try:
        code = cli.main([*command.split(), str(repo)])
    finally:
        (repo / locked).chmod(0o755)
    assert code == 2
    prefix = "hygiene: size cannot judge - " if command == "hygiene" else "baseline: not written - "
    err = capsys.readouterr().err
    assert (prefix + reason in err, str(repo) in err) == (True, False), err
    if command == "hygiene":
        assert "hygiene: clone cannot judge - " + reason in err, err


@pytest.mark.parametrize(
    "content",
    [f"[file_lines]\n\"src/a.py\" = {'9' * 5000}\n", f"a = {'[' * 5000}{']' * 5000}\n"],
    ids=["oversized integer", "nested too deep"],
)
def test_a_size_baseline_the_toml_reader_gives_up_on_cannot_be_judged(build_repo, capsys, content):
    """#218 sentence 1: an integer or a nesting tomllib refuses is exit 2 with the ruled message, not a traceback."""
    repo = build_repo({"src/a.py": "x = 1\n", BASELINE_PATH: content})
    assert cli.main(["hygiene", str(repo)]) == 2
    assert f"{BASELINE_PATH} is not valid TOML" in capsys.readouterr().err


def property_pair(lines: int) -> str:
    getter = long_function(lines).replace("long_one():", "long_one(self):")
    setter = long_function(lines).replace("long_one():", "long_one(self, value):")
    decorated = f"@property\n{getter}@long_one.setter\n{setter}"
    return "class Holder:\n" + "".join(f"    {line}\n" for line in decorated.splitlines())


@pytest.mark.parametrize(
    "source",
    [long_function(62) + long_function(70), property_pair(61)],
    ids=["redefined-function", "property-getter-and-setter"],
)
def test_two_functions_sharing_a_qualified_name_count_as_two(build_repo, capsys, source):
    """#46 rule 3: the counts per class count every function, also two that share one qualified symbol."""
    _, output = hygiene(capsys, build_repo({"src/a.py": source}))
    assert "production: 0 file(s) >= 800 lines, 2 function(s) >= 60 lines" in output


def test_the_shorter_of_two_same_named_functions_cannot_grow_under_the_baseline(build_repo, capsys):
    """#46 rule 4: each function keeps its own entry, so the shorter twin growing is red, not hidden by the longer."""
    repo = build_repo({"src/a.py": long_function(62) + long_function(70)})
    assert cli.main(["baseline", "write", str(repo)]) == 0
    (repo / "src/a.py").write_text(long_function(65) + long_function(70))
    code, output = hygiene(capsys, repo)
    assert code == 1, output
    assert "has 65 lines; the baseline holds 62 (it grew)." in output


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
        "clones": {"src/a.py": {"long_one": [["src/b.py", "Holder.long_one"]]}},
    }
    assert written.index("[ccn") < written.index("[file_lines]") < written.index('[function_lines."src/a.py"]')
    assert written.index('[function_lines."src/a.py"]') < written.index('[function_lines."src/b.py"]')
    capsys.readouterr()
    code, output = hygiene(capsys, repo)
    assert (code, CLEAN_LINE in output) == (0, True), output


def test_writing_the_baseline_twice_yields_the_same_file(build_repo):
    """#46 rule 5: the written baseline is the measurement, so writing it again changes nothing."""
    repo = build_repo({"src/a.py": long_function(62)})
    cli.main(["baseline", "write", str(repo)])
    first = (repo / BASELINE_PATH).read_text(encoding="utf-8")
    cli.main(["baseline", "write", str(repo)])
    assert (repo / BASELINE_PATH).read_text(encoding="utf-8") == first


def baseline_write(capsys: pytest.CaptureFixture[str], repo: Path) -> tuple[int, list[str]]:
    code = cli.main(["baseline", "write", str(repo)])
    captured = capsys.readouterr()
    return code, (captured.out + captured.err).splitlines()


def wrote_line(repo: Path, count: str) -> str:
    return f"baseline: wrote {count} and 0 clone pairs to {repo / BASELINE_PATH}"


@pytest.mark.parametrize(
    ("previous", "named"),
    [
        (
            '[function_lines."src/a.py"]\nlong_one = 62\n',
            ["baseline: raises function_lines src/a.py long_one 62 -> 70"],
        ),
        (EMPTY_BASELINE, ["baseline: adds function_lines src/a.py long_one 70"]),
        ('[function_lines."src/a.py"]\nlong_one = 82\n', []),
        ('[function_lines."src/a.py"]\nlong_one = 70\n[file_lines]\n"src/gone.py" = 900\n', []),
        (None, []),
    ],
    ids=["cell-1-raised", "cell-2-added", "cell-3-lowered", "cell-4-file-gone", "cell-5-adoption"],
)
def test_baseline_write_names_each_raised_or_added_entry_before_the_count(build_repo, capsys, previous, named):
    """#50 rules 1-3 and 5, cells 1-5: raises and adds are named in the verdict's wording; a fall, a gone entry
    and an adoption name nothing; the write still happens and exits 0."""
    files = {"src/a.py": long_function(70)}
    repo = build_repo(files if previous is None else {**files, BASELINE_PATH: previous})
    assert baseline_write(capsys, repo) == (0, [*named, wrote_line(repo, "1 entry")])
    assert load_baseline((repo / BASELINE_PATH).read_text(encoding="utf-8")) == Baseline(
        {Key(Metric.FUNCTION_LINES, "src/a.py", "long_one"): 70}, frozenset()
    )


@pytest.mark.parametrize(
    "previous",
    ["[file_lines\n", '[file_lines]\n"src/a.py" = "many"\n'],
    ids=["not-toml", "not-a-count"],
)
def test_baseline_write_overwrites_an_unreadable_previous_baseline_with_a_note(build_repo, capsys, previous):
    """#50 rules 4 and 5, cell 6: an unreadable previous baseline is overwritten, the write says raises cannot be
    named, and it exits 0."""
    repo = build_repo({"src/a.py": long_function(70), BASELINE_PATH: previous})
    code, (note, wrote) = baseline_write(capsys, repo)
    assert (code, wrote) == (0, wrote_line(repo, "1 entry"))
    assert note.startswith(f"baseline: the previous {BASELINE_PATH} could not be read (")
    assert note.endswith("), so raises cannot be named")
    assert "rewrite it" not in note, "#52 rule 7, cell 15: the write is the rewrite, so its note carries no hint"
    assert load_baseline((repo / BASELINE_PATH).read_text(encoding="utf-8")) == Baseline(
        {Key(Metric.FUNCTION_LINES, "src/a.py", "long_one"): 70}, frozenset()
    )


@pytest.mark.parametrize(
    "baseline",
    ["[file_lines\n", '[file_lines]\n"src/a.py" = "many"\n'],
    ids=["not-toml", "not-a-count"],
)
def test_an_unreadable_baseline_cannot_be_judged(build_repo, capsys, baseline):
    """#46 rule 4 and #52 rule 7, cell 14: a baseline the rule cannot read is exit 2, never clean, and names the
    rewrite once per rule that reads it (size and clone, #136)."""
    code, output = hygiene(capsys, build_repo({"src/a.py": "x = 1\n", BASELINE_PATH: baseline}))
    assert code == 2
    assert "cannot judge" in output
    assert output.count("rewrite it with `quality baseline write`") == 2, output


def test_an_unknown_baseline_table_states_the_fact_and_sends_the_head_to_its_pinned_release(build_repo, capsys):
    """#153 cell 14: the fact and the tool version, not the command that may have written the table."""
    code, output = hygiene(capsys, build_repo({"src/a.py": "x = 1\n", BASELINE_PATH: "[future]\n"}))
    assert code == 2
    assert f"{BASELINE_PATH} has an unknown table [future]; quality {__version__} knows ccn, file_lines, function_lines, clones" in output
    assert output.count("write the baseline with the quality release your gate pins") == 2, output
    assert "rewrite it" not in output


def link_the_baseline_elsewhere(repo: Path, elsewhere: Path) -> None:
    (repo / BASELINE_PATH).parent.mkdir(exist_ok=True)
    (repo / BASELINE_PATH).symlink_to(elsewhere / "victim.toml")


def link_the_quality_directory_elsewhere(repo: Path, elsewhere: Path) -> None:
    (repo / BASELINE_PATH).parent.symlink_to(elsewhere)


@pytest.mark.parametrize(
    "arrange",
    [link_the_baseline_elsewhere, link_the_quality_directory_elsewhere],
    ids=["baseline-symlink", "quality-directory-symlink"],
)
def test_baseline_write_to_a_destination_it_cannot_own_cannot_be_judged(build_repo, capsys, arrange):
    """#46 rule 5 and #50 rule 4, cell 7: the baseline is written to the repository's own quality/baseline.toml,
    never through a symlink, and a refused write names nothing but the refusal."""
    repo = build_repo({"src/a.py": long_function(62)})
    elsewhere = repo / "untracked-elsewhere"
    elsewhere.mkdir()
    (elsewhere / "victim.toml").write_text("kept\n")
    arrange(repo, elsewhere)
    code, (refusal,) = baseline_write(capsys, repo)
    assert (code, refusal.startswith("baseline: not written - ")) == (2, True), refusal
    assert str(repo) not in refusal, "#52 rule 5, cell 20: the refusal names no resolved path"
    assert sorted(path.name for path in elsewhere.iterdir()) == ["victim.toml"]
    assert (elsewhere / "victim.toml").read_text() == "kept\n"


@pytest.mark.parametrize(
    "blocker",
    [{"quality": "a file\n"}, {f"{BASELINE_PATH}/inside.txt": "a directory\n"}],
    ids=["quality-is-a-file", "baseline-is-a-directory"],
)
def test_baseline_write_that_the_filesystem_refuses_cannot_be_judged(build_repo, capsys, blocker):
    """#46 rule 5: exit 2, not a traceback: a baseline that cannot be written is never a clean result."""
    repo = build_repo({"src/a.py": long_function(62), **blocker})
    assert cli.main(["baseline", "write", str(repo)]) == 2
    assert "baseline: not written - cannot write quality/baseline.toml" in capsys.readouterr().err


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
        "clones": {},
    }
    capsys.readouterr()
    code, output = hygiene(capsys, repo)
    assert (code, CLEAN_LINE in output) == (0, True), output


def test_a_path_that_is_not_utf8_cannot_be_written_into_the_baseline(build_repo, capsys):
    """#46 rule 5: TOML holds only Unicode, so a non-UTF-8 file name is exit 2, never a baseline hygiene cannot read."""
    repo = build_repo({"src/latin\udce9.py": long_file(810)})
    assert cli.main(["baseline", "write", str(repo)]) == 2
    assert "baseline: not written - src/latin\\udce9.py is not valid UTF-8" in capsys.readouterr().err
    assert not (repo / BASELINE_PATH).exists()


QUOTED_PATH = 'src/straße "q".py'


@pytest.mark.parametrize(
    ("base", "head", "expected"),
    [
        (
            {Key(Metric.CCN, "a.py", "f"): 16, Key(Metric.FUNCTION_LINES, "a.py", "f"): 70},
            {Key(Metric.CCN, "a.py", "f"): 18, Key(Metric.FUNCTION_LINES, "a.py", "f"): 65},
            [RaisedEntry(Key(Metric.CCN, "a.py", "f"), 16, 18)],
        ),
        ({Key(Metric.FILE_LINES, "a.py"): 900}, {Key(Metric.FILE_LINES, "a.py"): 900}, []),
        ({Key(Metric.FILE_LINES, "a.py"): 900}, {}, []),
        ({}, {Key(Metric.FILE_LINES, "b.py"): 800}, [RaisedEntry(Key(Metric.FILE_LINES, "b.py"), None, 800)]),
        (
            {Key(Metric.CCN, "a.py", "f"): 16},
            {Key(Metric.CCN, "b.py", "f"): 16},
            [RaisedEntry(Key(Metric.CCN, "b.py", "f"), None, 16)],
        ),
    ],
    ids=["ccn raised while lines fell", "equal", "entry removed", "entry added", "entry moved"],
)
def test_only_a_raised_or_added_entry_loosens_the_baseline(base, head, expected):
    """#48 rule 2, cell 18: growth and a new (moved) key loosen; lowering, equality and a removed key do not."""
    assert raised_entries(base, head) == expected


def sized(path):
    return Key(Metric.CCN, path, "f")


def renamed(moves, unnamed_sources=()):
    return Renames(moves, frozenset(moves.values()) | frozenset(unnamed_sources))


def paired(first, second):
    return ClonePair.of(Place(first, "f"), Place(second, "g"))


@pytest.mark.parametrize(
    ("renames", "base", "head", "expected"),
    [
        ({"a.py": "b.py"}, {sized("a.py"): 16}, {sized("b.py"): 16}, {sized("b.py"): 16}),
        ({"a.py": "b.py"}, {sized("a.py"): 16, sized("b.py"): 99}, {sized("b.py"): 16}, {sized("b.py"): 16}),
        ({"a.py": "b.py"}, {sized("b.py"): 99}, {sized("b.py"): 16}, {}),
        ({"a.py": "b.py"}, {sized("a.py"): 16}, {sized("a.py"): 16, sized("b.py"): 16}, {sized("a.py"): 16}),
        ({"a.py": "b.py"}, {sized("a.py"): 16, sized("c.py"): 17}, {sized("b.py"): 16}, {sized("b.py"): 16, sized("c.py"): 17}),
        ({"a.py": "b.py", "b.py": "c.py"}, {sized("a.py"): 16, sized("b.py"): 17}, {sized("c.py"): 17}, {sized("b.py"): 16}),
    ],
    ids=["the allowance moves", "a leftover under the target is dropped", "a lone leftover is dropped",
         "nothing moves while the head holds the old path", "an unrelated path stays",
         "leftovers under every target are dropped before the sources move"],
)
def test_the_base_allowance_moves_to_a_rename_target_and_leftovers_there_do_not_count(renames, base, head, expected):
    """#149 R1, rules 1 and 2: the old entry leaves its path; an entry under the target never counts."""
    assert carried_base(Baseline(base, None), Baseline(head, None), renamed(renames)).size == expected


@pytest.mark.parametrize(
    ("renames", "base", "head", "expected"),
    [
        ({"a.py": "b.py"}, {paired("a.py", "z.py")}, {paired("b.py", "z.py")}, {paired("b.py", "z.py")}),
        ({"a.py": "z.py"}, {paired("a.py", "b.py")}, {paired("z.py", "b.py")}, {ClonePair.of(Place("z.py", "f"), Place("b.py", "g"))}),
        ({"a.py": "c.py", "b.py": "d.py"}, {paired("a.py", "b.py")}, {paired("c.py", "d.py")}, {paired("c.py", "d.py")}),
        ({"a.py": "b.py"}, {paired("a.py", "z.py"), paired("b.py", "y.py")}, {paired("b.py", "z.py")}, {paired("b.py", "z.py")}),
        ({"a.py": "b.py"}, {paired("a.py", "z.py")}, {paired("a.py", "z.py"), paired("b.py", "z.py")}, {paired("a.py", "z.py")}),
    ],
    ids=["a side moves", "a moved side changes the canonical order", "both sides move", "a leftover pair under the target is dropped",
         "nothing moves while the head holds the old path"],
)
def test_a_clone_pair_moves_with_its_files_by_the_same_rules(renames, base, head, expected):
    """#149 R3: a pair whose sides only moved is the same pair; the sides re-sort through ClonePair.of."""
    carried = carried_base(Baseline({}, frozenset(base)), Baseline({}, frozenset(head)), renamed(renames))
    assert carried.clones == frozenset(expected)


def test_a_target_whose_source_cannot_be_named_is_cleared_and_nothing_moves_to_it():
    """#149 rule 2: a rename from a path that is not UTF-8 still drops the leftovers under its target."""
    base = Baseline({sized("new.py"): 200}, frozenset({paired("new.py", "z.py")}))
    carried = carried_base(base, Baseline({sized("new.py"): 150}, frozenset()), renamed({}, unnamed_sources=["new.py"]))
    assert carried == Baseline({}, frozenset())


def test_a_head_pair_keeps_the_old_path_occupied_for_size_entries_too():
    """#149 rule 1: the head holds the old path through a size entry or a pair side, either blocks the move."""
    base = Baseline({sized("a.py"): 16}, frozenset({paired("a.py", "z.py")}))
    head = Baseline({sized("b.py"): 16}, frozenset({paired("a.py", "z.py"), paired("b.py", "z.py")}))
    assert carried_base(base, head, renamed({"a.py": "b.py"})) == Baseline({sized("a.py"): 16}, frozenset({paired("a.py", "z.py")}))


def test_a_base_without_a_clone_table_stays_without_one():
    assert carried_base(Baseline({sized("a.py"): 16}, None), Baseline({sized("b.py"): 16}, None), renamed({"a.py": "b.py"})).clones is None


def test_a_key_with_quotes_or_unicode_matches_itself_exactly():
    """#48 cell 19: the same quoted or unicode key on both sides is no raise, and a raise on it is found."""
    key = Key(Metric.CCN, QUOTED_PATH, 'Holder."méthode"')
    base = load_baseline(render_baseline(Baseline({key: 16}, None))).size
    assert raised_entries(base, load_baseline(render_baseline(Baseline({key: 16}, None))).size) == []
    assert raised_entries(base, load_baseline(render_baseline(Baseline({key: 17}, None))).size) == [RaisedEntry(key, 16, 17)]


@pytest.fixture
def repo_with_debt_below_src(build_repo, monkeypatch) -> Path:
    """A repository whose root baseline holds nothing, with a 900-line file in src, entered at src."""
    repo = build_repo({BASELINE_PATH: EMPTY_BASELINE, "src/big.py": long_file(900)})
    monkeypatch.chdir(repo / "src")
    return repo


@pytest.mark.parametrize("path_argument", [[], ["."]], ids=["default path", "explicit path"])
def test_hygiene_from_a_subfolder_judges_the_whole_repository_by_its_root_baseline(
    repo_with_debt_below_src, capsys, path_argument
):
    """#76 rule 1, cells 1-2, 20: the size debt is found from src, named from the top, and no line names the checkout."""
    code = cli.main(["hygiene", *path_argument])
    output = (captured := capsys.readouterr()).out + captured.err
    assert (code, "size src/big.py - file has 900 lines" in output, str(repo_with_debt_below_src) in output) == (1, True, False), output


def test_hygiene_given_a_subfolder_by_name_judges_the_repository_from_its_top(build_repo, monkeypatch, capsys):
    """#76 rule 1, cell 2 (E1): `quality hygiene src` from the top is the run from src."""
    repo = build_repo({BASELINE_PATH: EMPTY_BASELINE, "src/big.py": long_file(900)})
    monkeypatch.chdir(repo)
    code = cli.main(["hygiene", "src"])
    assert (code, "size src/big.py - file has 900 lines" in capsys.readouterr().out) == (1, True)


def test_baseline_write_from_a_subfolder_writes_the_root_baseline(repo_with_debt_below_src, capsys):
    """#76 rule 1, cell 3 (E2, E11): the root file is written and named from where the command ran; a stray
    src/quality/baseline.toml is neither read nor touched."""
    stray = repo_with_debt_below_src / "src/quality/baseline.toml"
    stray.parent.mkdir()
    stray.write_text("# stray\n")
    assert cli.main(["baseline", "write"]) == 0
    assert "baseline: wrote 1 entry and 0 clone pairs to ../quality/baseline.toml" in capsys.readouterr().out
    assert ("src/big.py" in (repo_with_debt_below_src / BASELINE_PATH).read_text(), stray.read_text()) == (True, "# stray\n")


NOT_UTF8 = b"\xff"


def not_utf8_baseline_repo(build_repo) -> Path:
    repo = build_repo({"src/a.py": "x = 1\n", BASELINE_PATH: ""})
    (repo / BASELINE_PATH).write_bytes(NOT_UTF8)
    return repo


def test_a_baseline_that_is_not_utf8_is_malformed_and_names_the_rewrite_once_per_rule(build_repo, capsys):
    """#76 rule 6, cell 18: hygiene's size and clone rules each name the invalid encoding and the rewrite once."""
    code, output = hygiene(capsys, not_utf8_baseline_repo(build_repo))
    assert code == 2
    assert f"{BASELINE_PATH} is not valid UTF-8" in output
    assert output.count("rewrite it with `quality baseline write`") == 2, output


def test_baseline_write_overwrites_a_baseline_that_is_not_utf8_without_a_rewrite_hint(build_repo, capsys):
    """#76 rule 6, cell 18: the write is the rewrite, so its note carries no hint."""
    code, lines = baseline_write(capsys, not_utf8_baseline_repo(build_repo))
    note = next(line for line in lines if "could not be read" in line)
    assert (code, "is not valid UTF-8" in note, "rewrite it" in note) == (0, True, False), lines


MIXED_TREE = {
    "src/app.py": long_file(100),
    "src/frontend/src/lib/api/types.ts": typescript_file(50),
    "src/frontend/src/Card.svelte": typescript_file(20),
    "src/frontend/src/legacy.js": typescript_file(5),
    "scripts/deploy_app.sh": "# Deploys the app.\n" + "echo hi\n" * 399,
    "src/frontend/src/app.css": "a { color: red; }\n" * 400,
}
NEW_DEBT = "size {path} - file has {lines} lines; debt starts at 800 lines (new debt)."


def size_report(output: str) -> str:
    (line,) = [line for line in output.splitlines() if "size: report only" in line]
    return line


@pytest.mark.parametrize(
    ("files", "languages"),
    [
        ({"src/a.py": long_file(3)}, "; languages: Python"),
        (MIXED_TREE, "; languages: Python, TypeScript, Svelte, JavaScript (functions in Python only)"),
        ({"src/a.svelte": long_file(5), "src/b.js": long_file(5), "src/c.vue": long_file(5)}, "; languages: JavaScript, Svelte, Vue (functions in Python only)"),
        ({"src/a.py": long_file(3), "src/dist/index.js": minified_bundle()}, "; languages: Python, JavaScript (functions in Python only)"),
    ],
    ids=[
        "#119 cell 10 a Python-only tree names Python without the functions note",
        "#119 cell 11 a mixed tree lists its languages largest first, shell and CSS absent",
        "#119 cell 11 equal lines are ordered by name",
        "#119 cell 12 a minified bundle's language is listed and nothing is said to be skipped",
    ],
)
def test_without_a_baseline_the_report_names_the_measured_languages(build_repo, capsys, files, languages):
    """#119 E5: the report line names each measured language, and says functions are Python's only when another is."""
    code, output = hygiene(capsys, build_repo(files))
    line = size_report(output)
    assert (code, languages in line, "skip" in line) == (0, True, False), output
    assert line.endswith(languages)


@pytest.mark.parametrize("suffix", ["ts", "tsx", "mts", "cts", "js", "jsx", "mjs", "cjs", "svelte", "vue"])
def test_a_file_of_every_table_language_is_new_debt_from_800_lines_against_a_baseline(build_repo, capsys, suffix):
    """#119 E6, cell 13: against a baseline, a file of any table language from 800 lines is new debt, 799 is not."""
    path = f"frontend/src/lib/api/types.{suffix}"
    repo = build_repo({"src/a.py": "x = 1\n", path: typescript_file(1210), "frontend/small." + suffix: typescript_file(799), BASELINE_PATH: EMPTY_BASELINE})
    code, output = hygiene(capsys, repo)
    assert (code, NEW_DEBT.format(path=path, lines=1210) in output, "small" in output) == (1, True, False), output


@pytest.mark.parametrize("name", ["tool.rs", "run.sh", "app.css", "index.html", "notes.md"])
def test_a_file_of_a_language_the_table_lacks_is_never_debt(build_repo, capsys, name):
    """#119 O3: Rust, shell, CSS and HTML stay unmeasured."""
    code, output = hygiene(capsys, build_repo({f"src/{name}": "line\n" * 2000, BASELINE_PATH: EMPTY_BASELINE}))
    assert (code, "size " in output) == (0, False), output


def test_a_function_of_120_lines_in_typescript_is_no_finding_and_the_file_is_not_unparsable(build_repo, capsys):
    """#119 E6, cells 14 and 15: lines only, so a held baseline over a .ts that is not Python stays clean."""
    repo = build_repo({"src/a.py": "x = 1\n", "src/api.ts": typescript_function(120), BASELINE_PATH: EMPTY_BASELINE})
    code, output = hygiene(capsys, repo)
    assert (code, "not valid Python" in output, "size " in output) == (0, False, False), output


@pytest.mark.parametrize(
    ("bundle", "code", "named"),
    [(minified_bundle(), 0, False), ("var a=1;\n" * 800, 1, True)],
    ids=["#119 cell 16 a one-line bundle is no finding", "#119 cell 16 an 800-line minified file is new debt"],
)
def test_a_minified_file_is_counted_by_its_lines_like_any_other(build_repo, capsys, bundle, code, named):
    """#119 O4: nothing is skipped; the one-line bundle has one line."""
    result, output = hygiene(capsys, build_repo({"src/dist/index.js": bundle, BASELINE_PATH: EMPTY_BASELINE}))
    assert (result, "size src/dist/index.js" in output) == (code, named), output


@pytest.mark.parametrize(
    ("path", "area"),
    [
        ("src/a.test.ts", "tests"),
        pytest.param("src/a.test-d.ts", "tests", id="H21 test-d"),
        pytest.param("src/a.spec-d.ts", "tests", id="H21 spec-d"),
        ("e2e/login.spec.ts", "tests"),
        ("src/decision.svelte.test.ts", "tests"),
        ("src/__tests__/x.ts", "tests"),
        ("tests/Harness.svelte", "tests"),
        ("frontend/e2e/helpers.ts", "production"),
        ("src/lib/test-utils/x.ts", "production"),
        ("src/test_x.ts", "production"),
        ("src/Card.test.svelte", "production"),
        ("src/__tests__/Card.svelte", "production"),
    ],
)
def test_the_name_rule_of_a_typescript_or_javascript_file_is_the_runners_defaults(build_repo, capsys, path, area):
    """#119 E3, cell 9: `*.test.*`, `*.spec.*` and `__tests__/` for TS and JS; Svelte and Vue by directory only."""
    code, output = hygiene(capsys, build_repo({f"src/{path}": typescript_file(800)}))
    other = "tests" if area == "production" else "production"
    assert (code, f"{area}: 1 file(s) >= 800" in output, f"{other}: 0 file(s) >= 800" in output) == (0, True, True), output


def test_baseline_write_records_a_file_of_another_language_and_names_it_as_added(build_repo, capsys):
    """#119 E6, cells 17 and 18: the entry is an ordinary [file_lines] one; against a baseline it is named as added."""
    path = "frontend/src/lib/api/types.ts"
    repo = build_repo({"src/a.py": long_function(70), path: typescript_file(1210)})
    assert baseline_write(capsys, repo) == (0, [wrote_line(repo, "2 entries")])
    assert tomllib.loads((repo / BASELINE_PATH).read_text(encoding="utf-8"))["file_lines"] == {path: 1210}
    (repo / BASELINE_PATH).write_text('[function_lines."src/a.py"]\nlong_one = 70\n', encoding="utf-8")
    assert baseline_write(capsys, repo) == (0, [f"baseline: adds file_lines {path} 1210", wrote_line(repo, "2 entries")])


@pytest.mark.integration
def test_a_tree_with_typescript_holds_the_same_python_baseline(tmp_path):
    """#119 E7, cells 19 and 20: a file of another language adds no Python entry and moves none."""
    python_only = {"src/b.py": long_file(810) + method(61), "src/a.py": branchy_function(17) + long_function(62)}
    mixed = {**python_only, "src/ui.ts": typescript_file(100), "src/Card.svelte": typescript_file(30)}
    written = []
    for name, files in (("python", python_only), ("mixed", mixed)):
        (tmp_path / name).mkdir()
        repo = make_repo(tmp_path / name, files)
        assert cli.main(["baseline", "write", str(repo)]) == 0
        written.append((repo / BASELINE_PATH).read_text(encoding="utf-8"))
    assert written[0] == written[1]
