"""`quality check debt --fix` lowers and drops the entries of a held `[job_minutes]` table."""

from pathlib import Path

import pytest

from quality.baseline_file import BASELINE_PATH
from tests.support.baselines import rendered
from tests.support.door import CHECK_WORD, DRY_RUN_WORDS, FIX_WORDS, digest, fixed
from tests.support.git_repositories import git, put
from tests.support.source_code import long_function
from tests.support.verdict import verdict_streams
from tests.support.verdict_renames import commit_all

WORKFLOW = ".github/workflows/e2e.yml"
BROKEN = ".github/workflows/broken.yml"
CALLED = ".github/workflows/called.yml"
RENAMED = ".github/workflows/renamed.yml"
SIZE_FILE = "src/old.py"
FIX_RUN = "fix: quality check --fix"
PLANNED_WORDS = {"lowered": "lower", "dropped": "drop"}


def workflow_of(on: str = "pull_request", **limits: str | None) -> str:
    """A workflow whose jobs (id to `timeout-minutes`, none when the value is None) run on `on`."""
    steps = "    runs-on: ubuntu-latest\n    steps:\n      - run: 'true'\n"
    jobs = "".join(
        f"  {name}:\n" + (f"    timeout-minutes: {limit}\n" if limit else "") + steps for name, limit in limits.items()
    )
    return f"name: w\non: {on}\npermissions: {{}}\njobs:\n{jobs}"


def ceiling(entries: dict[str, int] | None = None, size: int | None = None, path: str = WORKFLOW) -> str:
    """A ceiling holding the job entries under `path` (an empty table when `entries` is empty, no table when it is
    None) and a `long_one` entry of `size` lines under `SIZE_FILE` when given."""
    size_table = "" if size is None else f'[function_lines."{SIZE_FILE}"]\nlong_one = {size}\n\n'
    if entries is None:
        return rendered(f"{size_table}[clones]\n")
    if not entries:
        job_tables = "[job_minutes]\n"
    else:
        job_tables = f'[job_minutes."{path}"]\n' + "".join(f"{job} = {minutes}\n" for job, minutes in entries.items())
    return rendered(f"{size_table}[clones]\n{job_tables}")


def squashed(text: str) -> str:
    return " ".join(text.split())


def a_job_removed() -> dict[str, str]:
    return {WORKFLOW: workflow_of(lint="10")}


def a_workflow_no_root_reaches() -> dict[str, str]:
    return {WORKFLOW: workflow_of("workflow_call", e2e="40")}


def a_job_turned_into_a_call() -> dict[str, str]:
    call = f"name: w\non: pull_request\npermissions: {{}}\njobs:\n  e2e:\n    uses: ./{CALLED}\n"
    return {WORKFLOW: call, CALLED: workflow_of("workflow_call", build="10")}


GONE = "job e2e is gone · was 40 minutes"
NO_DEBT = "job e2e 40 → {} minutes · debt from 16 minutes"
# the last column is the exit: a job of 30 minutes whose entry of 10 is dropped is new debt
CASES = [
    pytest.param(
        {WORKFLOW: workflow_of(e2e="30")},
        (),
        {"e2e": 40},
        {"e2e": 30},
        [("lowered", "job e2e 40 → 30 minutes")],
        0,
        id="30",
    ),
    pytest.param(
        {WORKFLOW: workflow_of(e2e="17.5")},
        (),
        {"e2e": 40},
        {"e2e": 18},
        [("lowered", "job e2e 40 → 18 minutes")],
        0,
        id="17.5",
    ),
    pytest.param(
        {WORKFLOW: workflow_of(e2e="15.5")},
        (),
        {"e2e": 40},
        {"e2e": 16},
        [("lowered", "job e2e 40 → 16 minutes")],
        0,
        id="15.5",
    ),
    pytest.param({WORKFLOW: workflow_of(e2e="15")}, (), {"e2e": 40}, {}, [("dropped", NO_DEBT.format(15))], 0, id="15"),
    pytest.param(
        {WORKFLOW: workflow_of(e2e="14.5")}, (), {"e2e": 40}, {}, [("dropped", NO_DEBT.format(15))], 0, id="14.5"
    ),
    pytest.param(
        {WORKFLOW: workflow_of(e2e="30")},
        (),
        {"e2e": 10},
        {},
        [("dropped", "job e2e has no debt · was 10 minutes")],
        1,
        id="an entry of 10",
    ),
    pytest.param(
        {WORKFLOW: workflow_of(e2e="15")},
        (),
        {"e2e": 15},
        {},
        [("dropped", "job e2e has no debt · was 15 minutes")],
        0,
        id="an entry of 15",
    ),
    pytest.param(a_job_removed(), (), {"e2e": 40}, {}, [("dropped", GONE)], 0, id="the job is removed"),
    pytest.param(a_job_turned_into_a_call(), (), {"e2e": 40}, {}, [("dropped", GONE)], 0, id="the job is a call"),
    pytest.param(
        a_workflow_no_root_reaches(), (), {"e2e": 40}, {}, [("dropped", GONE)], 0, id="no root reaches the workflow"
    ),
    pytest.param(
        {WORKFLOW: workflow_of(e2e="40")},
        (WORKFLOW,),
        {"e2e": 40},
        {},
        [("dropped", GONE)],
        0,
        id="the file is deleted",
    ),
]


def held_repo(build_repo, files: dict[str, str], deleted: tuple[str, ...], entries: dict[str, int]) -> Path:
    repo = build_repo({**files, BASELINE_PATH: ceiling(entries)})
    for name in deleted:
        (repo / name).unlink()
    return repo


@pytest.mark.parametrize(("files", "deleted", "entries", "written", "rows", "exit_code"), CASES)
def test_the_fix_writes_the_lowered_table_names_each_change_and_leaves_a_ceiling_the_check_holds(
    build_repo, capsys, files, deleted, entries, written, rows, exit_code
):
    """TIMEOUT-20, TIMEOUT-21, TIMEOUT-25: the table is written lowered, each job change is a row naming the workflow
    and its facts, and the check then finds nothing to lower."""
    repo = held_repo(build_repo, files, deleted, entries)
    code, out, err = fixed(capsys, repo, *FIX_WORDS)
    printed = squashed(out)
    assert (code, err, (repo / BASELINE_PATH).read_text()) == (exit_code, "", ceiling(written))
    assert [f"{WORKFLOW} {event} {fact}" in printed for event, fact in rows] == [True] * len(rows), out
    checked_code, checked_out, _ = fixed(capsys, repo, CHECK_WORD, "debt")
    assert (checked_code, "can fall" in checked_out) == (exit_code, False)


@pytest.mark.parametrize(("files", "deleted", "entries", "written", "rows", "exit_code"), CASES)
def test_a_dry_run_words_each_change_as_what_the_fix_would_do_and_writes_nothing(
    build_repo, capsys, files, deleted, entries, written, rows, exit_code
):
    """TIMEOUT-26: the dry run plans the rows the fix then writes, with the same facts, and exits 1."""
    repo = held_repo(build_repo, files, deleted, entries)
    before = digest(repo)
    code, planned, _ = fixed(capsys, repo, *DRY_RUN_WORDS)
    assert (code, digest(repo)) == (1, before)
    printed = squashed(planned)
    assert [f"{WORKFLOW} {PLANNED_WORDS[event]} {fact}" in printed for event, fact in rows] == [True] * len(rows), (
        planned
    )
    assert fixed(capsys, repo, *FIX_WORDS)[0] == exit_code


def test_the_fixed_ceiling_is_committed_to_a_green_verdict_and_a_second_fix_changes_nothing(pull_request, capsys):
    """TIMEOUT-20, TIMEOUT-25: the count names one entry in the debt row and the verdict line; the verdict after the
    commit is green; a second fix writes the same bytes and says nothing to lower."""
    repo, commit_base = pull_request
    put(repo / WORKFLOW, workflow_of(e2e="30"))
    commit_base(ceiling({"e2e": 40}))
    code, out, _ = fixed(capsys, repo, *FIX_WORDS)
    printed = squashed(out)
    assert (code, "debt lowered 1 entry" in printed, "ceiling lowered by 1 entry" in printed) == (0, True, True), out
    commit_all(repo, "fixed")
    verdict_code, verdict_out, verdict_err = verdict_streams(repo, capsys)
    assert (verdict_code, "✓ green" in verdict_out, verdict_err) == (0, True, ""), verdict_out
    written = digest(repo)
    code, out, _ = fixed(capsys, repo, *FIX_WORDS)
    assert (code, digest(repo), "✓ clean · nothing to lower" in out) == (0, written, True)


def test_every_lowered_and_dropped_entry_counts_with_the_size_entries_in_the_ceiling_line(build_repo, capsys):
    """TIMEOUT-25: a lowered job, a dropped job and a lowered size entry are three entries, the job rows after the size
    row in the order of the file's entries."""
    files = {SIZE_FILE: long_function(70), WORKFLOW: workflow_of(a="30", b="12")}
    repo = build_repo({**files, BASELINE_PATH: ceiling({"a": 40, "b": 40}, size=80)})
    code, out, _ = fixed(capsys, repo, *FIX_WORDS)
    printed = squashed(out)
    assert (code, "debt lowered 3 entries" in printed, "ceiling lowered by 3 entries" in printed) == (0, True, True), (
        out
    )
    rows = [
        printed.index(row)
        for row in (f"{SIZE_FILE}:1 lowered", f"{WORKFLOW} lowered job a", f"{WORKFLOW} dropped job b")
    ]
    assert rows == sorted(rows)
    assert (repo / BASELINE_PATH).read_text() == ceiling({"a": 30}, size=70)


def test_a_dry_run_counts_every_job_entry_it_would_lower_or_drop_with_the_size_entries(build_repo, capsys):
    """TIMEOUT-25, TIMEOUT-26."""
    files = {SIZE_FILE: long_function(70), WORKFLOW: workflow_of(a="30", b="12")}
    repo = build_repo({**files, BASELINE_PATH: ceiling({"a": 40, "b": 40}, size=80)})
    code, out, _ = fixed(capsys, repo, *DRY_RUN_WORDS)
    printed = squashed(out)
    assert (code, "debt 3 to lower" in printed, "3 ceiling entries would fall" in printed) == (1, True, True), out


def test_dropping_the_last_entry_leaves_the_table_held_and_empty(build_repo, capsys):
    """TIMEOUT-21."""
    repo = held_repo(build_repo, {WORKFLOW: workflow_of(e2e="12")}, (), {"e2e": 40})
    assert fixed(capsys, repo, *FIX_WORDS)[0] == 0
    assert "[job_minutes]\n" in (repo / BASELINE_PATH).read_text()


def renamed_and_shrunk(pull_request) -> Path:
    repo, commit_base = pull_request
    put(repo / WORKFLOW, workflow_of(e2e="40", other="10"))
    commit_base(ceiling({"e2e": 40}))
    git(repo, "mv", WORKFLOW, RENAMED)
    put(repo / RENAMED, workflow_of(e2e="30", other="10"))
    return repo


def test_a_renamed_workflow_takes_its_entry_and_has_it_lowered_in_one_fix(pull_request, capsys):
    """TIMEOUT-20, TIMEOUT-21: the renames are followed before an entry is judged, so a renamed workflow's entry moves
    and falls to its job's limit instead of falling as gone."""
    repo = renamed_and_shrunk(pull_request)
    code, out, _ = fixed(capsys, repo, *FIX_WORDS)
    printed = squashed(out)
    assert (code, (repo / BASELINE_PATH).read_text()) == (0, ceiling({"e2e": 30}, path=RENAMED))
    assert (f"{RENAMED} lowered job e2e 40 → 30 minutes" in printed, "is gone" in printed) == (True, False), out


@pytest.mark.parametrize(
    ("files", "entries", "expected_rows"),
    [
        pytest.param(
            {WORKFLOW: workflow_of(e2e="45")}, {"e2e": 40}, ["bring it back to 40 or below"], id="a job above its entry"
        ),
        pytest.param(
            {WORKFLOW: workflow_of(e2e="20", lint="20")},
            {"lint": 20},
            ["lower timeout-minutes to 15 or less"],
            id="a job without an entry",
        ),
        pytest.param(
            {WORKFLOW: workflow_of(e2e=None)},
            {"e2e": 40},
            ["set timeout-minutes to a number of at most 15"],
            id="no limit",
        ),
        pytest.param(
            {WORKFLOW: workflow_of(e2e="${{ vars.T }}")},
            {"e2e": 10},
            ["set timeout-minutes to a number"],
            id="an invalid limit",
        ),
    ],
)
def test_the_fix_never_raises_adds_or_drops_what_it_cannot_measure(build_repo, capsys, files, entries, expected_rows):
    """TIMEOUT-22: the table stays as held and the finding stays with its own fix."""
    repo = build_repo({**files, BASELINE_PATH: ceiling(entries)})
    before = digest(repo)
    code, out, _ = fixed(capsys, repo, *FIX_WORDS)
    assert (code, digest(repo)) == (1, before)
    assert [row in out for row in expected_rows] == [True] * len(expected_rows), out


@pytest.mark.parametrize(
    ("files", "entries"),
    [
        pytest.param({WORKFLOW: workflow_of(e2e="30")}, {"e2e": 40}, id="measured"),
        pytest.param({WORKFLOW: workflow_of(lint="10")}, {"e2e": 40}, id="gone"),
        pytest.param({WORKFLOW: workflow_of(e2e="30")}, {"e2e": 10}, id="no debt"),
    ],
)
def test_a_can_fall_finding_names_the_fix_command_as_its_fix(build_repo, capsys, files, entries):
    """TIMEOUT-24."""
    repo = build_repo({**files, BASELINE_PATH: ceiling(entries)})
    code, out, _ = fixed(capsys, repo, CHECK_WORD, "debt")
    assert (code, "can fall" in out, FIX_RUN in out, "by hand" in out) == (1, True, True, False), out


UNREADABLE_WORKFLOW = {BROKEN: "on: [pull_request\njobs: {"}


@pytest.mark.parametrize("words", [FIX_WORDS, DRY_RUN_WORDS], ids=["fix", "dry run"])
def test_a_workflow_that_cannot_be_read_refuses_the_whole_fix_while_an_entry_is_held(build_repo, capsys, words):
    """TIMEOUT-23, TIMEOUT-26: nothing is written, the size entries and the job entries stand, and the row names the
    file."""
    files = {SIZE_FILE: long_function(70), WORKFLOW: workflow_of(e2e="30"), **UNREADABLE_WORKFLOW}
    repo = build_repo({**files, BASELINE_PATH: ceiling({"e2e": 40}, size=80)})
    before = digest(repo)
    code, out, _ = fixed(capsys, repo, *words)
    assert (code, digest(repo), f"✗ debt not checked cannot read {BROKEN}" in squashed(out)) == (2, before, True), out


@pytest.mark.parametrize("entries", [None, {}], ids=["no table", "an empty table"])
def test_a_workflow_that_cannot_be_read_does_not_block_the_size_fix_where_no_job_entry_is_held(
    build_repo, capsys, entries
):
    """TIMEOUT-23: the fix reads no workflow where the table is absent or empty, so the size entry falls and the rule
    alone reports the file."""
    files = {SIZE_FILE: long_function(70), **UNREADABLE_WORKFLOW}
    repo = build_repo({**files, BASELINE_PATH: ceiling(entries, size=80)})
    code, out, _ = fixed(capsys, repo, *FIX_WORDS)
    assert (code, (repo / BASELINE_PATH).read_text(), "not fixed" in out, f"cannot read {BROKEN}" in out) == (
        2,
        ceiling(entries, size=70),
        False,
        True,
    )
