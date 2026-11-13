import os
import re
import shlex
import tomllib
from collections.abc import Callable
from pathlib import Path

import pytest

from quality import __version__, cli
from quality.baseline_file import (
    BASELINE_PATH,
    ClonePair,
    JobKey,
    Key,
    Metric,
    PageKey,
    Place,
    load_baseline,
    render_baseline,
)
from quality.ratchet import (
    RaisedEntry,
    Renames,
    carried_base,
    debt_from,
    follow_renames,
    named_paths,
    raised_entries,
    raised_literal_texts,
)
from quality.report import FindingEvent, NoteEvent
from quality.rules import Rule
from tests.support.baselines import ceiling
from tests.support.check import (
    area_fact,
    assert_finding_vocabulary,
    assert_row_prints_finding,
    check_result,
    finding_rows,
    is_clean,
    leading_count,
    note_rows,
    recorded_entries,
    refusal_row,
    refusals,
    refusals_of,
    rows_of,
    rule_findings,
    rule_notes,
)
from tests.support.check import check_output as run
from tests.support.door import CHECK_WORD, FIX_WORDS, check_door, door_in
from tests.support.git_repositories import make_repo
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


def test_without_a_baseline_the_debt_row_counts_the_entries_a_first_recording_writes(build_repo, capsys):
    """O21, SIZE-3, SIZE-6, SIZE-7: with no baseline the debt row counts the entries over the thresholds, a first
    recording writes as many, and the exit is unchanged."""
    repo = build_repo({"src/big.py": long_file(800) + long_function(60) + branchy_function(16), "tests/test_big.py": long_file(900)})
    code, output = run(capsys, repo)
    assert code == 0, output
    assert leading_count(area_fact(output, "debt")) == 4
    assert recorded_entries(repo) == 4


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
    """SIZE-8, SECURITY-28, SECURITY-29: the size rule measures a Python file as Python reads it; where ruff, which
    reads UTF-8 only, cannot read it, python-security and python-print both cannot judge."""
    repo = build_repo_with_source(build_repo, {}, SOURCE_NAME, source)
    code, output = run(capsys, repo)
    assert (Rule.PYTHON_SECURITY in refusals(repo)) == python_security_cannot_judge, output
    assert (Rule.PYTHON_PRINT in refusals(repo)) == python_security_cannot_judge, output
    assert code == (2 if python_security_cannot_judge else 0), output
    assert leading_count(area_fact(output, "debt")) == 1, output
    assert recorded_entries(repo) == 1, output
    assert note_rows(output, SOURCE_NAME) == [], output


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
def test_without_a_baseline_the_size_rule_notes_a_file_python_cannot_read_and_python_security_decides_the_exit(
    build_repo, capsys, source
):
    """SIZE-9, SECURITY-20, SECURITY-28: with no baseline the size rule notes a file Python cannot read and refuses
    nothing; python-security and python-print alone turn the run to exit 2."""
    repo = build_repo_with_source(build_repo, {"src/ok.py": "x = 1\n"}, SOURCE_NAME, source)
    code, output = run(capsys, repo)
    assert code == 2, output
    assert list(refusals(repo)) == [Rule.PYTHON_SECURITY, Rule.PYTHON_PRINT]
    assert all(re.search(rf"(cannot read|ruff cannot parse) {re.escape(SOURCE_NAME)}", reason) for reason in refusals(repo).values())
    (note,) = rule_notes(repo, Rule.SIZE)
    assert (note.path, note.event) == (SOURCE_NAME, NoteEvent.NOT_MEASURED)
    assert [(row.event, row.fact) for row in note_rows(output, SOURCE_NAME)] == [(note.event, note.fact)], output


@NOT_PYTHON
def test_against_a_baseline_a_file_python_cannot_read_is_unparsable(build_repo, capsys, source):
    """SIZE-10: with a baseline a file Python cannot read makes the debt row `✗ not checked`, naming it as not valid
    Python, with no note, exit 2."""
    repo = build_repo_with_source(build_repo, {BASELINE_PATH: EMPTY_BASELINE}, SOURCE_NAME, source)
    code, output = run(capsys, repo)
    assert code == 2
    assert rule_notes(repo, Rule.SIZE) == []
    assert f"{refusal_row('debt')}{SOURCE_NAME} is not valid Python" in output


@NOT_PYTHON
def test_a_fix_refuses_a_file_python_cannot_read(build_repo, capsys, source):
    """SIZE-11: a first recording refuses a file Python cannot read with `✗ not fixed`, exit 2, writing no baseline,
    since a baseline holds every function and one recorded with a file unmeasured would hide its debt."""
    repo = build_repo_with_source(build_repo, {}, SOURCE_NAME, source)
    assert door_in(repo, *FIX_WORDS) == 2
    assert f"{refusal_row('debt', 'not fixed')}{SOURCE_NAME} is not valid Python" in capsys.readouterr().out
    assert not (repo / BASELINE_PATH).exists()


HELD_70 = '[function_lines."src/a.py"]\nlong_one = 70\n'


@pytest.mark.parametrize(
    ("files", "baseline", "path", "line", "event", "values"),
    [
        ({"src/big.py": long_file(800)}, EMPTY_BASELINE, "src/big.py", None, FindingEvent.NEW_DEBT, (str(debt_from(Metric.FILE_LINES)),)),
        ({"src/a.py": method(60)}, EMPTY_BASELINE, "src/a.py", 2, FindingEvent.NEW_DEBT, ("Holder.long_one", str(debt_from(Metric.FUNCTION_LINES)))),
        ({"src/a.py": branchy_function(16)}, EMPTY_BASELINE, "src/a.py", 1, FindingEvent.NEW_DEBT, ("branchy", str(debt_from(Metric.CCN)))),
        ({"src/a.py": long_function(71)}, HELD_70, "src/a.py", 1, FindingEvent.GREW, ("long_one", "71", "70")),
        ({"src/a.py": long_function(65)}, HELD_70, "src/a.py", 1, FindingEvent.CAN_FALL, ("long_one", "65", "70")),
    ],
    ids=["new-file-from-800-lines", "new-function-from-60-lines", "new-ccn-above-15", "grown-entry", "stale-entry"],
)
def test_against_a_baseline_new_grown_and_stale_entries_are_red(build_repo, capsys, files, baseline, path, line, event, values):
    """CEILING-4, CEILING-5, CEILING-6, SIZE-3, SIZE-5, ROW-7, ROW-14: against a baseline a new violation, a grown entry
    and an entry above its measured value are one finding each, exit 1, with the symbol and the values."""
    repo = build_repo({**files, BASELINE_PATH: baseline})
    code, output = run(capsys, repo)
    (finding,) = rule_findings(repo, Rule.SIZE)
    assert code == 1
    assert finding.path == path
    assert finding.line == line
    assert finding.event is event
    assert all(value in finding.fact for value in values), finding.fact
    assert_finding_vocabulary(finding)
    (row,) = rows_of(output, finding)
    assert_row_prints_finding(row, finding)


def test_the_fix_of_a_stale_entry_run_in_the_repository_leaves_a_clean_run(build_repo, capsys):
    """CEILING-6, FIX-3: the fix a `can fall` row prints is a command of this door, and running it lowers the entry."""
    repo = build_repo({"src/a.py": long_function(65), BASELINE_PATH: HELD_70})
    run(capsys, repo)
    (finding,) = rule_findings(repo, Rule.SIZE)
    command = shlex.split(finding.fix)
    assert command[0] == cli.build_parser().prog
    assert door_in(repo, *command[1:]) == 0
    code, output = run(capsys, repo)
    assert (code, is_clean(output)) == (0, True), output


def test_bringing_a_grown_function_back_to_its_held_value_clears_the_finding(build_repo, capsys):
    """CEILING-5: a grown function written back to its held value leaves a clean run."""
    repo = build_repo({"src/a.py": long_function(71), BASELINE_PATH: HELD_70})
    run(capsys, repo)
    assert [finding.event for finding in rule_findings(repo, Rule.SIZE)] == [FindingEvent.GREW]
    (repo / "src/a.py").write_text(long_function(70))
    code, output = run(capsys, repo)
    assert (code, is_clean(output)) == (0, True), output


@pytest.mark.parametrize(
    "files",
    [
        {"src/a.py": long_file(799)},
        {"src/a.py": long_function(59) + branchy_function(15)},
    ],
    ids=["file-just-below-800-lines", "functions-just-below-60-lines-and-ccn-15"],
)
def test_below_the_thresholds_is_clean(build_repo, capsys, files):
    """SIZE-3: a file of 799 lines, a function of 59 lines and a CCN of 15 are no debt."""
    code, output = run(capsys, build_repo({**files, BASELINE_PATH: ""}))
    assert code == 0, output
    assert is_clean(output)


@pytest.fixture(params=["deleted", "dangling", "dangling out of the repository"])
def remove(request, elsewhere) -> Callable[[Path], None]:
    """Takes a tracked file off the disk: deleted, or replaced by a link to nowhere inside or outside the repository."""
    link_target = {
        "deleted": None,
        "dangling": Path("missing.py"),
        "dangling out of the repository": elsewhere / "missing.py",
    }[request.param]

    def take_off_the_disk(path: Path) -> None:
        path.unlink()
        if link_target is not None:
            path.symlink_to(link_target)

    return take_off_the_disk


@pytest.mark.parametrize(
    "baseline",
    [None, '[function_lines."src/b.py"]\nlong_one = 62\n'],
    ids=["no-baseline", "baseline-holds-the-unmeasured-file"],
)
def test_a_tracked_file_missing_from_disk_is_unmeasured_for_the_size_rule_and_only_the_python_rules_refuse_it(
    build_repo, capsys, remove, baseline
):
    """CEILING-1, SECURITY-19, SECURITY-28: the size rule judges the tree on disk; a tracked file absent from it is
    unmeasured and its entry stands, wherever a dangling link points. python-security and python-print must read every
    tracked `.py` file, so they alone refuse the run."""
    files = {"src/a.py": "x = 1\n", "src/b.py": long_function(62)}
    repo = build_repo(files if baseline is None else {**files, BASELINE_PATH: baseline})
    remove(repo / "src/b.py")
    code, output = run(capsys, repo)
    result = check_result(repo)
    refusing = refusals_of(result)
    assert code == 2, output
    assert result.findings == [], output
    assert list(refusing) == [Rule.PYTHON_SECURITY, Rule.PYTHON_PRINT], output
    assert all("src/b.py" in reason for reason in refusing.values()), output


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads a mode-0 path, so no permission is refused")
@pytest.mark.parametrize(
    ("locked", "reason"),
    [
        ("locked", "cannot reach {file}: [Errno 13] Permission denied"),
        ("{file}", "cannot read {file}: [Errno 13] Permission denied"),
    ],
    ids=["directory", "file"],
)
@pytest.mark.parametrize(
    "file", ["locked/b.py", "locked/b.ts", "locked/b.svelte"], ids=["Python", "TypeScript", "Svelte"]
)
@pytest.mark.parametrize("command", [CHECK_WORD, "check debt --fix"])
def test_a_tracked_file_the_filesystem_refuses_to_reach_cannot_be_judged(build_repo, capsys, command, file, locked, reason):
    """SIZE-1, SIZE-12, SIZE-13: a tracked file of any measured language that the filesystem refuses to reach or read
    is exit 2 with its reason, never gone, never a traceback, and the reason names no checkout path."""
    repo = build_repo({"src/a.py": "x = 1\n", file: long_function(62)})
    locked, reason = locked.format(file=file), reason.format(file=file)
    (repo / locked).chmod(0)
    try:
        code = door_in(repo, *command.split())
    finally:
        (repo / locked).chmod(0o755)
    assert code == 2
    prefix = refusal_row("debt")
    captured = capsys.readouterr()
    output = captured.out + captured.err
    assert (prefix + reason in output, str(repo) in output) == (True, False), output


@pytest.mark.parametrize(
    "content",
    [f"[file_lines]\n\"src/a.py\" = {'9' * 5000}\n", f"a = {'[' * 5000}{']' * 5000}\n"],
    ids=["oversized integer", "nested too deep"],
)
def test_a_size_baseline_the_toml_reader_gives_up_on_cannot_be_judged(build_repo, capsys, content):
    """CEILING-9: an integer or a nesting the TOML reader refuses is exit 2 naming the baseline as not valid TOML, not a
    traceback."""
    repo = build_repo({"src/a.py": "x = 1\n", BASELINE_PATH: content})
    assert check_door(repo) == 2
    assert f"{BASELINE_PATH} is not valid TOML" in capsys.readouterr().out


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
    """SIZE-5, SIZE-6: two functions of one qualified symbol are two entries, in the debt row and in a first
    recording."""
    repo = build_repo({"src/a.py": source})
    _, output = run(capsys, repo)
    assert leading_count(area_fact(output, "debt")) == 2
    assert recorded_entries(repo) == 2


def test_the_shorter_of_two_same_named_functions_cannot_grow_under_the_baseline(build_repo, capsys):
    """CEILING-5, SIZE-5: each of two functions of one name keeps its own entry, so the shorter growing is a finding,
    not hidden by the longer."""
    repo = build_repo({"src/a.py": long_function(62) + long_function(70)})
    assert door_in(repo, *FIX_WORDS) == 0
    (repo / "src/a.py").write_text(long_function(65) + long_function(70))
    code, output = run(capsys, repo)
    assert code == 1, output
    (finding,) = rule_findings(repo, Rule.SIZE)
    assert (finding.event, "65" in finding.fact, "62" in finding.fact) == (FindingEvent.GREW, True, True)


def test_a_first_recording_writes_the_measured_debt_sorted_and_check_then_holds_it(build_repo, capsys):
    """FIX-11: the first `quality check debt --fix` writes sorted TOML keyed by path and qualified symbol."""
    repo = build_repo(
        {
            "src/b.py": long_file(810) + method(61),
            "src/a.py": branchy_function(17) + long_function(62),
            "src/small.py": "x = 1\n",
        }
    )
    assert door_in(repo, *FIX_WORDS) == 0
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
    code, output = run(capsys, repo)
    assert (code, is_clean(output)) == (0, True), output


def test_fixing_twice_yields_the_same_file(build_repo):
    """The recorded baseline is the measurement, so fixing again changes nothing."""
    repo = build_repo({"src/a.py": long_function(62)})
    door_in(repo, *FIX_WORDS)
    first = (repo / BASELINE_PATH).read_text(encoding="utf-8")
    door_in(repo, *FIX_WORDS)
    assert (repo / BASELINE_PATH).read_text(encoding="utf-8") == first


@pytest.mark.parametrize(
    "baseline",
    ["[file_lines\n", '[file_lines]\n"src/a.py" = "many"\n'],
    ids=["not-toml", "not-a-count"],
)
def test_an_unreadable_baseline_cannot_be_judged(build_repo, capsys, baseline):
    """CEILING-9, TIMEOUT-15: a baseline the rule cannot read is exit 2 for the size, clone and job-timeout rules, never
    clean, and the run names the restore step once."""
    repo = build_repo({"src/a.py": "x = 1\n", BASELINE_PATH: baseline})
    code, output = run(capsys, repo)
    assert code == 2
    assert list(refusals(repo)) == [Rule.SIZE, Rule.CLONE, Rule.JOB_TIMEOUT]
    assert (output.count("restore quality/baseline.toml from git, or repair it by hand"), "rewrite it with" in output) == (1, False), output  # both rules, one reason: the run names it once, on next:
    assert "next: restore quality/baseline.toml from git, or repair it by hand" in output.splitlines()


def test_an_unknown_baseline_table_states_the_fact_and_sends_the_head_to_its_pinned_release(build_repo, capsys):
    """CEILING-3, TIMEOUT-15: an unknown table is refused with the fact and the tables this quality knows, and the head
    is sent to the release its gate pins."""
    repo = build_repo({"src/a.py": "x = 1\n", BASELINE_PATH: "[future]\n"})
    code, output = run(capsys, repo)
    assert (code, list(refusals(repo))) == (2, [Rule.SIZE, Rule.CLONE, Rule.JOB_TIMEOUT])
    assert f"{BASELINE_PATH} has an unknown table [future]; quality {__version__} knows ccn, file_lines, function_lines, clones" in output
    assert output.count("run the quality release your gate pins") == 1, output
    assert "rewrite it" not in output


def link_the_baseline_elsewhere(repo: Path, elsewhere: Path) -> None:
    (repo / BASELINE_PATH).parent.mkdir(exist_ok=True)
    (repo / BASELINE_PATH).symlink_to(elsewhere / "victim.toml")


def link_the_quality_directory_elsewhere(repo: Path, elsewhere: Path) -> None:
    (repo / BASELINE_PATH).parent.symlink_to(elsewhere)


@pytest.mark.parametrize(
    ("arrange", "row"),
    [(link_the_baseline_elsewhere, "  ✗ debt          not checked    "), (link_the_quality_directory_elsewhere, "  ✗ debt          not fixed      ")],
    ids=["baseline-symlink", "quality-directory-symlink"],
)
def test_a_fix_to_a_destination_it_cannot_own_cannot_be_judged(build_repo, capsys, arrange, row):
    """FIX-30: the baseline is written to the repository's own quality/baseline.toml, never through a symlink."""
    repo = build_repo({"src/a.py": long_function(62)})
    elsewhere = repo / "untracked-elsewhere"
    elsewhere.mkdir()
    (elsewhere / "victim.toml").write_text("kept\n")
    arrange(repo, elsewhere)
    code = door_in(repo, *FIX_WORDS)
    refusal = capsys.readouterr().out
    assert (code, row in refusal) == (2, True), refusal
    assert str(repo) not in refusal, "the refusal names no resolved path"
    assert sorted(path.name for path in elsewhere.iterdir()) == ["victim.toml"]
    assert (elsewhere / "victim.toml").read_text() == "kept\n"


@pytest.mark.parametrize(
    ("blocker", "fact"),
    [({"quality": "a file\n"}, "cannot write quality/baseline.toml"), ({f"{BASELINE_PATH}/inside.txt": "a directory\n"}, "quality/baseline.toml is not a file")],
    ids=["quality-is-a-file", "baseline-is-a-directory"],
)
def test_a_fix_the_filesystem_refuses_cannot_be_judged(build_repo, capsys, blocker, fact):
    """CEILING-9: exit 2, not a traceback: a baseline that cannot be written is never a clean result."""
    repo = build_repo({"src/a.py": long_function(62), **blocker})
    assert door_in(repo, *FIX_WORDS) == 2
    assert fact in capsys.readouterr().out


@pytest.mark.parametrize(
    "path",
    ["src/\U0001f600.py", "src/café.py", "src/del\x7f.py", 'src/quote"back\\slash.py'],
    ids=["outside-the-bmp", "accented", "del-character", "quote-and-backslash"],
)
def test_a_written_baseline_holds_any_utf8_path(build_repo, capsys, path):
    """Whatever the path, the written baseline is TOML that the check then holds."""
    repo = build_repo({path: long_file(810) + long_function(62)})
    assert door_in(repo, *FIX_WORDS) == 0
    assert tomllib.loads((repo / BASELINE_PATH).read_text(encoding="utf-8")) == {
        "file_lines": {path: 810 + 62},
        "function_lines": {path: {"long_one": 62}},
        "clones": {},
    }
    capsys.readouterr()
    code, output = run(capsys, repo)
    assert (code, is_clean(output)) == (0, True), output


def test_a_path_that_is_not_utf8_cannot_be_written_into_the_baseline(build_repo, capsys):
    """TOML holds only Unicode, so a non-UTF-8 file name is exit 2, never a baseline the check cannot read."""
    repo = build_repo({"src/latin\udce9.py": long_file(810)})
    assert door_in(repo, *FIX_WORDS) == 2
    assert "src/latin\\udce9.py is not valid UTF-8" in capsys.readouterr().out
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
    """BASELINE-2, BASELINE-3, BASELINE-7: a raised entry and an entry the base lacks (moved to another path included)
    loosen; a lowered, equal or removed entry does not."""
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
    """BASELINE-12, BASELINE-13, BASELINE-16: the old entry leaves its path unless the head still names it; an entry
    under the target never counts."""
    assert carried_base(ceiling(base, None), renamed(renames), named_paths(ceiling(head, None))).size == expected


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
    """BASELINE-12, BASELINE-13, BASELINE-16, BASELINE-17: a pair whose sides only moved is the same pair, the sides
    re-sort through ClonePair.of, and a side the head still names does not move."""
    carried = carried_base(ceiling({}, frozenset(base)), renamed(renames), named_paths(ceiling({}, frozenset(head))))
    assert carried.clones == frozenset(expected)


def test_a_target_whose_source_cannot_be_named_is_cleared_and_nothing_moves_to_it():
    """BASELINE-14: a rename from a path that is not UTF-8 still drops the leftovers under its target."""
    base = ceiling({sized("new.py"): 200}, frozenset({paired("new.py", "z.py")}))
    carried = carried_base(base, renamed({}, unnamed_sources=["new.py"]), named_paths(ceiling({sized("new.py"): 150}, frozenset())))
    assert carried == ceiling({}, frozenset())


def test_a_head_pair_keeps_the_old_path_occupied_for_size_entries_too():
    """BASELINE-16: the head holds the old path through a size entry or a pair side, either blocks the move."""
    base = ceiling({sized("a.py"): 16}, frozenset({paired("a.py", "z.py")}))
    head = ceiling({sized("b.py"): 16}, frozenset({paired("a.py", "z.py"), paired("b.py", "z.py")}))
    assert carried_base(base, renamed({"a.py": "b.py"}), named_paths(head)) == ceiling({sized("a.py"): 16}, frozenset({paired("a.py", "z.py")}))


def test_a_base_without_a_clone_table_stays_without_one():
    """BASELINE-9: a base without a clone table is still without one after its renames are carried."""
    assert carried_base(ceiling({sized("a.py"): 16}, None), renamed({"a.py": "b.py"}), named_paths(ceiling({sized("b.py"): 16}, None))).clones is None


def page_counts(counts):
    return {PageKey(path): count for path, count in counts.items()}


def test_a_base_with_a_page_table_and_no_clone_table_keeps_its_table_through_the_rename_carry():
    """LITERAL-5: the carry rebuilds nothing it does not touch, so a page table survives a base without `[clones]`, and
    its entry follows the rename."""
    base = ceiling({}, None, None, {PageKey("a.svelte"): 3, PageKey("z.svelte"): 1})
    carried = carried_base(base, renamed({"a.svelte": "b.svelte"}), named_paths(ceiling({}, None, None, {PageKey("b.svelte"): 3})))
    assert (carried.clones, carried.literal_texts) == (None, {PageKey("b.svelte"): 3, PageKey("z.svelte"): 1})


def test_a_page_entry_is_a_path_the_baseline_names():
    """LITERAL-13: a rename of a page is followed because the baseline names its path."""
    assert named_paths(ceiling({}, None, None, {PageKey("a.svelte"): 3})) == {"a.svelte"}


@pytest.mark.parametrize(
    ("base", "head", "expected"),
    [
        ({"a.svelte": 3}, {"a.svelte": 3}, []),
        ({"a.svelte": 3}, {"a.svelte": 2}, []),
        ({"a.svelte": 3}, {"a.svelte": 5}, [RaisedEntry(PageKey("a.svelte"), 3, 5)]),
        ({"a.svelte": 3}, {"a.svelte": 3, "b.svelte": 1}, [RaisedEntry(PageKey("b.svelte"), None, 1)]),
    ],
    ids=["equal", "lowered", "raised", "added"],
)
def test_a_page_count_may_fall_but_never_rise_or_appear(base, head, expected):
    """LITERAL-8: the raised entries of one page table against another."""
    assert raised_literal_texts(page_counts(base), page_counts(head)) == expected


def booked(path, job="e2e"):
    return JobKey(path, job)


def job_baseline(entries, size=None):
    return ceiling(size or {}, None, entries)


@pytest.mark.parametrize(
    ("renames", "base", "head", "expected"),
    [
        ({"a.yml": "b.yml"}, {booked("a.yml"): 40}, {booked("b.yml"): 40}, {booked("b.yml"): 40}),
        ({"a.yml": "b.yml"}, {booked("a.yml"): 40, booked("b.yml"): 99}, {booked("b.yml"): 40}, {booked("b.yml"): 40}),
        ({"a.yml": "b.yml"}, {booked("b.yml"): 99}, {booked("b.yml"): 40}, {}),
        ({"a.yml": "b.yml"}, {booked("a.yml"): 40}, {booked("a.yml"): 40, booked("b.yml"): 40}, {booked("a.yml"): 40}),
        ({"a.yml": "b.yml"}, {booked("a.yml"): 40, booked("c.yml"): 20}, {booked("b.yml"): 40}, {booked("b.yml"): 40, booked("c.yml"): 20}),
    ],
    ids=["the allowance moves", "a leftover under the target is dropped", "a lone leftover is dropped",
         "nothing moves while the head holds the old path", "an unrelated path stays"],
)
def test_the_base_job_limits_move_to_a_rename_target_by_the_same_rules_as_size_entries(renames, base, head, expected):
    """TIMEOUT-17, BASELINE-12, BASELINE-13, BASELINE-16."""
    carried = carried_base(job_baseline(base), renamed(renames), named_paths(job_baseline(head)))
    assert carried.job_minutes == expected


def test_a_base_without_a_job_table_stays_without_one_and_a_held_empty_one_stays_held():
    """TIMEOUT-17, BASELINE-9."""
    rename = renamed({"a.yml": "b.yml"})
    absent, empty = (carried_base(job_baseline(entries), rename, frozenset()) for entries in (None, {}))
    assert (absent.job_minutes, empty.job_minutes) == (None, {})


def test_a_path_only_a_job_limit_names_is_a_path_the_baseline_names():
    """TIMEOUT-17, BASELINE-16: a head that names the old path only by a job entry keeps the move from happening."""
    assert "a.yml" in named_paths(job_baseline({booked("a.yml"): 40}))


@pytest.mark.parametrize("pairs", [None, frozenset()], ids=["no clone table", "an empty clone table"])
def test_a_carried_base_keeps_the_job_table_beside_the_size_entries_and_pairs(pairs):
    """TIMEOUT-17: the size entries move with a rename and the job table is handed on, whatever the clone table is."""
    base = ceiling({sized("a.py"): 16}, pairs, {booked("ci.yml"): 40})
    carried = carried_base(base, renamed({"a.py": "b.py"}), frozenset())
    assert (carried.size, carried.job_minutes) == ({sized("b.py"): 16}, {booked("ci.yml"): 40})


@pytest.mark.parametrize("pairs", [None, frozenset({paired("a.py", "z.py")})], ids=["no clone table", "a clone table"])
@pytest.mark.parametrize("jobs", [None, {}, {booked("ci.yml"): 40}], ids=["no job table", "an empty job table", "an entry"])
def test_following_renames_hands_a_held_job_table_on_unchanged(pairs, jobs):
    """TIMEOUT-17, FIX-31: the fix that moves size entries rewrites the job table byte for byte and never drops it."""
    committed = ceiling({sized("a.py"): 16}, pairs, jobs)
    held = ceiling({sized("b.py"): 16}, pairs and frozenset({paired("b.py", "z.py")}), jobs)
    fix = follow_renames(held, committed, renamed({"a.py": "b.py"}), frozenset({"b.py"}))
    assert fix.ceiling.job_minutes == jobs


def test_a_key_with_quotes_or_unicode_matches_itself_exactly():
    """BASELINE-2, BASELINE-7: a key with quotes or unicode is the same entry on both sides, so equal is no raise and a
    raise on it is found."""
    key = Key(Metric.CCN, QUOTED_PATH, 'Holder."méthode"')
    base = load_baseline(render_baseline(ceiling({key: 16}, None))).size
    assert raised_entries(base, load_baseline(render_baseline(ceiling({key: 16}, None))).size) == []
    assert raised_entries(base, load_baseline(render_baseline(ceiling({key: 17}, None))).size) == [RaisedEntry(key, 16, 17)]


@pytest.fixture
def repo_with_debt_below_src(build_repo) -> Path:
    """A repository whose root baseline holds nothing, with a 900-line file in src."""
    return build_repo({BASELINE_PATH: EMPTY_BASELINE, "src/big.py": long_file(900)})


def test_check_from_a_subfolder_judges_the_whole_repository_by_its_root_baseline(repo_with_debt_below_src, capsys):
    """CHECK-1: a run from a subfolder finds the size debt by the root baseline, names the file from the top, and no
    line names the checkout."""
    code = door_in(repo_with_debt_below_src / "src", CHECK_WORD)
    output = (captured := capsys.readouterr()).out + captured.err
    assert (code, [row.event for row in finding_rows(output, "src/big.py")], str(repo_with_debt_below_src) in output) == (1, [FindingEvent.NEW_DEBT], False), output


def test_a_fix_from_a_subfolder_lowers_the_root_baseline(build_repo, capsys):
    """FIX-1: the fix started in a subfolder lowers the root baseline; a stray src/quality/baseline.toml is neither
    read nor touched."""
    repo = build_repo({BASELINE_PATH: '[file_lines]\n"src/big.py" = 950\n\n[clones]\n', "src/big.py": long_file(900)})
    stray = repo / "src/quality/baseline.toml"
    stray.parent.mkdir()
    stray.write_text("# stray\n")
    assert door_in(repo / "src", *FIX_WORDS) == 0
    assert (load_baseline((repo / BASELINE_PATH).read_text()).size, stray.read_text()) == ({Key(Metric.FILE_LINES, "src/big.py"): 900}, "# stray\n")


NOT_UTF8 = b"\xff"


def not_utf8_baseline_repo(build_repo) -> Path:
    repo = build_repo({"src/a.py": "x = 1\n", BASELINE_PATH: ""})
    (repo / BASELINE_PATH).write_bytes(NOT_UTF8)
    return repo


def test_a_baseline_that_is_not_utf8_is_malformed_and_names_the_restore_step_once(build_repo, capsys):
    """CEILING-2, TIMEOUT-15: a baseline that is not UTF-8 is exit 2 for the size, clone and job-timeout rules, naming the
    invalid encoding, and the run names the restore step once."""
    repo = not_utf8_baseline_repo(build_repo)
    code, output = run(capsys, repo)
    assert (code, list(refusals(repo))) == (2, [Rule.SIZE, Rule.CLONE, Rule.JOB_TIMEOUT])
    assert f"{BASELINE_PATH} is not valid UTF-8" in output
    assert (output.count("restore quality/baseline.toml from git, or repair it by hand"), "rewrite it with" in output) == (1, False), output


@pytest.mark.parametrize("suffix", ["ts", "tsx", "mts", "cts", "js", "jsx", "mjs", "cjs", "svelte", "vue"])
def test_a_file_of_every_table_language_is_new_debt_from_800_lines_against_a_baseline(build_repo, capsys, suffix):
    """SIZE-1, SIZE-3: against a baseline a file of any measured language is new debt at 1210 lines, not at 799."""
    path = f"frontend/src/lib/api/types.{suffix}"
    repo = build_repo({"src/a.py": "x = 1\n", path: typescript_file(1210), "frontend/small." + suffix: typescript_file(799), BASELINE_PATH: EMPTY_BASELINE})
    code, output = run(capsys, repo)
    (finding,) = rule_findings(repo, Rule.SIZE)
    assert (code, finding.path, finding.event, "1210" in finding.fact) == (1, path, FindingEvent.NEW_DEBT, True), output
    assert len(rows_of(output, finding)) == 1


@pytest.mark.parametrize("name", ["tool.rs", "run.sh", "app.css", "index.html", "notes.md"])
def test_a_file_of_a_language_the_table_lacks_is_never_debt(build_repo, capsys, name):
    """SIZE-2: a file of no measured language is never debt, however long."""
    repo = build_repo({f"src/{name}": "line\n" * 2000, BASELINE_PATH: EMPTY_BASELINE})
    code, output = run(capsys, repo)
    assert (code, rule_findings(repo, Rule.SIZE)) == (0, []), output


def test_a_function_of_120_lines_in_typescript_is_no_finding_and_the_file_is_not_unparsable(build_repo, capsys):
    """SIZE-4: a 120-line TypeScript function is no finding and its file is not unparsable under a held baseline."""
    repo = build_repo({"src/a.py": "x = 1\n", "src/api.ts": typescript_function(120), BASELINE_PATH: EMPTY_BASELINE})
    code, output = run(capsys, repo)
    assert (code, rule_notes(repo, Rule.SIZE), rule_findings(repo, Rule.SIZE)) == (0, [], []), output


@pytest.mark.parametrize(
    ("bundle", "expected_code", "expected_named"),
    [(minified_bundle(), 0, 0), ("var a=1;\n" * 800, 1, 1)],
    ids=["a one-line bundle is no finding", "an 800-line minified file is new debt"],
)
def test_a_minified_file_is_counted_by_its_lines_like_any_other(build_repo, capsys, bundle, expected_code, expected_named):
    """SIZE-1, SIZE-3: a minified file is counted by its lines like any other: a one-line bundle has one line, an
    800-line one is new debt."""
    repo = build_repo({"src/dist/index.js": bundle, BASELINE_PATH: EMPTY_BASELINE})
    result, output = run(capsys, repo)
    assert (result, [finding.path for finding in rule_findings(repo, Rule.SIZE)]) == (expected_code, ["src/dist/index.js"] * expected_named), output


def test_a_first_recording_holds_a_file_of_another_language_as_an_ordinary_entry(build_repo):
    """SIZE-1: a first recording holds a TypeScript file from 800 lines as an ordinary `[file_lines]` entry."""
    path = "frontend/src/lib/api/types.ts"
    repo = build_repo({"src/a.py": long_function(70), path: typescript_file(1210)})
    assert door_in(repo, *FIX_WORDS) == 0
    assert tomllib.loads((repo / BASELINE_PATH).read_text(encoding="utf-8"))["file_lines"] == {path: 1210}


@pytest.mark.integration
def test_a_tree_with_typescript_holds_the_same_python_baseline(tmp_path):
    """SIZE-4: a file of another language adds no Python entry and moves none."""
    python_only = {"src/b.py": long_file(810) + method(61), "src/a.py": branchy_function(17) + long_function(62)}
    mixed = {**python_only, "src/ui.ts": typescript_file(100), "src/Card.svelte": typescript_file(30)}
    written = []
    for name, files in (("python", python_only), ("mixed", mixed)):
        (tmp_path / name).mkdir()
        repo = make_repo(tmp_path / name, files)
        assert door_in(repo, *FIX_WORDS) == 0
        written.append((repo / BASELINE_PATH).read_text(encoding="utf-8"))
    assert written[0] == written[1]
