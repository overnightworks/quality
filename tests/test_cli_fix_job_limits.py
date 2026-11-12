"""`quality check debt --fix` lowers and drops the entries of a held `[job_minutes]` table, and `--dry-run` plans the same
rows."""

import re
from dataclasses import dataclass
from pathlib import Path

import pytest

from quality.baseline_file import BASELINE_PATH
from tests.support.baselines import rendered
from tests.support.door import CHECK_WORD, DRY_RUN_WORDS, FIX_WORDS, fixed, tree_of
from tests.support.git_repositories import git, put
from tests.support.job_workflows import job, workflow
from tests.support.source_code import long_function
from tests.support.verdict import verdict_streams
from tests.support.verdict_renames import commit_all

WORKFLOW = ".github/workflows/e2e.yml"
BROKEN = ".github/workflows/broken.yml"
CALLED = ".github/workflows/called.yml"
RENAMED = ".github/workflows/renamed.yml"
SIZE_FILE = "src/old.py"
FIX_RUN = "fix: quality check --fix"
PLANNED_EVENTS = {"move": "moved", "lower": "lowered", "drop": "dropped"}
CHANGE_ROW = re.compile(r"^(?P<glyph>[~✓]) (?!debt )(?P<path>\S+) (?P<event>move|lower|drop|moved|lowered|dropped) (?P<fact>.+)$")
FINDING_ROW = re.compile(r"^(?:! \.github/\S+ .+|fix: .+)$")


def ceiling(entries: dict[str, int] | None = None, size: int | None = None, path: str = WORKFLOW) -> str:
    """A ceiling holding the job entries under `path` (an empty table when `entries` is empty, no table when it is
    None) and a `long_one` entry of `size` lines under `SIZE_FILE` when given."""
    size_table = "" if size is None else f'[function_lines."{SIZE_FILE}"]\nlong_one = {size}\n\n'
    if entries is None:
        return rendered(f"{size_table}[clones]\n")
    if not entries:
        job_tables = "[job_minutes]\n"
    else:
        job_tables = f'[job_minutes."{path}"]\n' + "".join(f"{name} = {minutes}\n" for name, minutes in entries.items())
    return rendered(f"{size_table}[clones]\n{job_tables}")


def squashed(text: str) -> str:
    return " ".join(text.split())


def change_rows(out: str) -> list[tuple[str, str, str]]:
    """The path, event and fact of each moved, lowered or dropped row, a planned row's event worded as the fix's."""
    rows = []
    for match in filter(None, map(CHANGE_ROW.match, map(squashed, out.splitlines()))):
        planned = match["glyph"] == "~"
        rows.append((match["path"], PLANNED_EVENTS[match["event"]] if planned else match["event"], match["fact"]))
    return rows


def finding_rows(out: str) -> list[str]:
    """The workflow findings and their fixes, which the fix leaves as it found them."""
    return [row for row in map(squashed, out.splitlines()) if FINDING_ROW.match(row)]


@dataclass(frozen=True)
class Run:
    planned_code: int
    planned: str
    code: int
    done: str


def dry_run_then_fix(capsys, repo: Path) -> Run:
    """The dry run, which writes nothing to the tree or the index and plans the change rows and findings the fix then
    prints, and the fix."""
    index = git(repo, "ls-files", "--stage").stdout
    tree = tree_of(repo)
    planned_code, planned, _ = fixed(capsys, repo, *DRY_RUN_WORDS)
    assert (tree_of(repo), git(repo, "ls-files", "--stage").stdout) == (tree, index), planned
    code, done, _ = fixed(capsys, repo, *FIX_WORDS)
    assert (change_rows(planned), finding_rows(planned)) == (change_rows(done), finding_rows(done)), (planned, done)
    return Run(planned_code, planned, code, done)


def a_job_removed() -> dict[str, str]:
    return {WORKFLOW: workflow(jobs=job("lint", "10"))}


def a_workflow_no_root_reaches() -> dict[str, str]:
    return {WORKFLOW: workflow("on: workflow_call\n", job("e2e", "40"))}


def a_job_turned_into_a_call() -> dict[str, str]:
    call = workflow(jobs=f"  e2e:\n    uses: ./{CALLED}\n")
    return {WORKFLOW: call, CALLED: workflow("on: workflow_call\n", job("build", "10"))}


@dataclass(frozen=True)
class Scenario:
    """A workflow tree and the table it is held against, with what the fix writes, prints and exits."""

    files: dict[str, str]
    entries: dict[str, int]
    written: dict[str, int]
    rows: tuple[tuple[str, str], ...] = ()
    exit_code: int = 0
    deleted: tuple[str, ...] = ()
    kept_findings: tuple[str, ...] = ()


GONE = "job e2e is gone · was 40 minutes"
NO_DEBT = "job e2e 40 → {} minutes · debt from 16 minutes"
LOWER_TO_15 = "lower timeout-minutes to 15 or less"
SET_A_LIMIT = "set timeout-minutes to a number"


def lowers(raw: str, to: int) -> Scenario:
    return Scenario({WORKFLOW: workflow(jobs=job("e2e", raw))}, {"e2e": 40}, {"e2e": to}, (("lowered", f"job e2e 40 → {to} minutes"),))


def drops(raw: str) -> Scenario:
    return Scenario({WORKFLOW: workflow(jobs=job("e2e", raw))}, {"e2e": 40}, {}, (("dropped", NO_DEBT.format(15)),))


def stands(raw: str | None, entry: int, kept_finding: str) -> Scenario:
    return Scenario({WORKFLOW: workflow(jobs=job("e2e", raw))}, {"e2e": entry}, {"e2e": entry}, (), 1, (), (kept_finding,))


def gone(files: dict[str, str], deleted: tuple[str, ...] = ()) -> Scenario:
    return Scenario(files, {"e2e": 40}, {}, (("dropped", GONE),), 0, deleted)


SCENARIOS = {
    "30": lowers("30", 30),
    "17.5": lowers("17.5", 18),
    "15.5": lowers("15.5", 16),
    "equal": Scenario({WORKFLOW: workflow(jobs=job("e2e", "40"))}, {"e2e": 40}, {"e2e": 40}),
    "above": stands("45", 40, "bring it back to 40 or below"),
    "15": drops("15"),
    "14.5": drops("14.5"),
    "an entry of 10": Scenario(
        {WORKFLOW: workflow(jobs=job("e2e", "30"))},
        {"e2e": 10},
        {},
        (("dropped", "job e2e has no debt · was 10 minutes"),),
        1,
        (),
        (LOWER_TO_15,),
    ),
    "an entry of 15": Scenario(
        {WORKFLOW: workflow(jobs=job("e2e", "15"))},
        {"e2e": 15},
        {},
        (("dropped", "job e2e has no debt · was 15 minutes"),),
    ),
    "the job is removed": gone(a_job_removed()),
    "the job is a call": gone(a_job_turned_into_a_call()),
    "no root reaches the workflow": gone(a_workflow_no_root_reaches()),
    "the file is deleted": gone({WORKFLOW: workflow(jobs=job("e2e", "40"))}, (WORKFLOW,)),
    "no limit beside an entry of 40": stands(None, 40, "set timeout-minutes to a number of at most 15"),
    "no limit beside an entry of 10": stands(None, 10, "set timeout-minutes to a number of at most 15"),
    "an invalid limit beside an entry of 40": stands("${{ vars.T }}", 40, SET_A_LIMIT),
    "an invalid limit beside an entry of 10": stands("${{ vars.T }}", 10, SET_A_LIMIT),
    "a job without an entry": Scenario(
        {WORKFLOW: workflow(jobs=job("e2e", "20") + job("lint", "20"))},
        {"lint": 20},
        {"lint": 20},
        (),
        1,
        (),
        (LOWER_TO_15,),
    ),
}


def held_repo(build_repo, scenario: Scenario) -> Path:
    repo = build_repo({**scenario.files, BASELINE_PATH: ceiling(scenario.entries)})
    for name in scenario.deleted:
        (repo / name).unlink()
    return repo


@pytest.mark.parametrize("scenario", SCENARIOS.values(), ids=SCENARIOS.keys())
def test_the_fix_writes_the_lowered_table_names_each_change_and_the_dry_run_plans_the_same_rows(
    build_repo, capsys, scenario
):
    """TIMEOUT-20, TIMEOUT-21, TIMEOUT-22, TIMEOUT-25, TIMEOUT-26: the table is written lowered, never raised or filled,
    each job change is a row naming the workflow and its facts, the findings keep their fixes, the dry run plans the
    same rows without writing and exits 1 where the fix changes the file, and the check then finds nothing to lower."""
    repo = held_repo(build_repo, scenario)
    run = dry_run_then_fix(capsys, repo)
    changes = [(WORKFLOW, event, fact) for event, fact in scenario.rows]
    changed = scenario.written != scenario.entries
    assert (run.code, run.planned_code, (repo / BASELINE_PATH).read_text()) == (
        scenario.exit_code,
        1 if changed or scenario.exit_code else 0,
        ceiling(scenario.written),
    )
    assert change_rows(run.done) == changes, run.done
    assert [fix in run.done for fix in scenario.kept_findings] == [True] * len(scenario.kept_findings), run.done
    checked_code, checked_out, _ = fixed(capsys, repo, CHECK_WORD, "debt")
    assert (checked_code, "can fall" in checked_out) == (scenario.exit_code, False)


def test_the_fixed_ceiling_is_committed_to_a_green_verdict_and_a_second_fix_changes_nothing(pull_request, capsys):
    """TIMEOUT-20, TIMEOUT-25, TIMEOUT-26: the count names one entry in the debt row and the verdict line; the verdict
    after the commit is green; a second fix and dry run find nothing to lower."""
    repo, commit_base = pull_request
    put(repo / WORKFLOW, workflow(jobs=job("e2e", "30")))
    commit_base(ceiling({"e2e": 40}))
    run = dry_run_then_fix(capsys, repo)
    printed = squashed(run.done)
    assert (run.code, "debt lowered 1 entry" in printed, "ceiling lowered by 1 entry" in printed) == (0, True, True)
    assert (run.planned_code, "debt 1 to lower" in squashed(run.planned)) == (1, True)
    commit_all(repo, "fixed")
    verdict_code, verdict_out, verdict_err = verdict_streams(repo, capsys)
    assert (verdict_code, "✓ green" in verdict_out, verdict_err) == (0, True, ""), verdict_out
    before_again = tree_of(repo)
    again = dry_run_then_fix(capsys, repo)
    assert tree_of(repo) == before_again
    assert (again.planned_code, again.code, "✓ clean · nothing to lower" in again.done) == (0, 0, True)


def test_every_lowered_and_dropped_entry_counts_with_the_size_entries_in_the_ceiling_line(build_repo, capsys):
    """TIMEOUT-25, TIMEOUT-26: a lowered job, a dropped job and a lowered size entry are three entries, the job rows
    after the size row in the order of the file's entries, in the fix and in the dry run."""
    files = {SIZE_FILE: long_function(70), WORKFLOW: workflow(jobs=job("a", "30") + job("b", "12"))}
    repo = build_repo({**files, BASELINE_PATH: ceiling({"a": 40, "b": 40}, size=80)})
    run = dry_run_then_fix(capsys, repo)
    done, planned = squashed(run.done), squashed(run.planned)
    assert (run.code, "debt lowered 3 entries" in done, "ceiling lowered by 3 entries" in done) == (0, True, True), run.done
    assert (run.planned_code, "debt 3 to lower" in planned, "3 ceiling entries would fall" in planned) == (1, True, True)
    assert [row[:2] for row in change_rows(run.done)] == [(f"{SIZE_FILE}:1", "lowered"), (WORKFLOW, "lowered"), (WORKFLOW, "dropped")]
    assert [row[2] for row in change_rows(run.done)[1:]] == ["job a 40 → 30 minutes", "job b 40 → 12 minutes · debt from 16 minutes"]
    assert (repo / BASELINE_PATH).read_text() == ceiling({"a": 30}, size=70)


def renamed_and_shrunk(pull_request) -> Path:
    repo, commit_base = pull_request
    put(repo / WORKFLOW, workflow(jobs=job("e2e", "40") + job("other", "10")))
    commit_base(ceiling({"e2e": 40}))
    git(repo, "mv", WORKFLOW, RENAMED)
    put(repo / RENAMED, workflow(jobs=job("e2e", "30") + job("other", "10")))
    return repo


def test_a_renamed_workflow_takes_its_entry_and_has_it_lowered_in_one_fix(pull_request, capsys):
    """TIMEOUT-20, TIMEOUT-21, TIMEOUT-26: the renames are followed before an entry is judged, so a renamed workflow's
    entry moves and falls to its job's limit instead of falling as gone, in the fix and in the dry run."""
    repo = renamed_and_shrunk(pull_request)
    run = dry_run_then_fix(capsys, repo)
    assert (run.code, run.planned_code, (repo / BASELINE_PATH).read_text()) == (0, 1, ceiling({"e2e": 30}, path=RENAMED))
    assert change_rows(run.done) == [
        (RENAMED, "moved", f"job e2e 40 minutes from {WORKFLOW}"),
        (RENAMED, "lowered", "job e2e 40 → 30 minutes"),
    ], run.done
    assert "is gone" not in run.done


@pytest.mark.parametrize(
    ("files", "entries"),
    [
        pytest.param({WORKFLOW: workflow(jobs=job("e2e", "30"))}, {"e2e": 40}, id="measured"),
        pytest.param({WORKFLOW: workflow(jobs=job("lint", "10"))}, {"e2e": 40}, id="gone"),
        pytest.param({WORKFLOW: workflow(jobs=job("e2e", "30"))}, {"e2e": 10}, id="no debt"),
    ],
)
def test_a_can_fall_finding_names_the_fix_command_as_its_fix(build_repo, capsys, files, entries):
    """TIMEOUT-24."""
    repo = build_repo({**files, BASELINE_PATH: ceiling(entries)})
    code, out, _ = fixed(capsys, repo, CHECK_WORD, "debt")
    assert (code, "can fall" in out, FIX_RUN in out, "by hand" in out) == (1, True, True, False), out




UNREADABLE_WORKFLOWS = {
    "no YAML": {BROKEN: "on: [pull_request\njobs: {"},
    "an alias hides the jobs": {BROKEN: "x: &j\n  e2e: 1\non: pull_request\njobs: *j\n"},
    "a local call names an untracked workflow": {BROKEN: workflow(jobs=f"  e2e:\n    uses: ./{CALLED}\n")},
}


@pytest.mark.parametrize("unreadable", UNREADABLE_WORKFLOWS.values(), ids=UNREADABLE_WORKFLOWS.keys())
def test_a_workflow_that_cannot_be_read_refuses_the_whole_fix_while_an_entry_is_held(build_repo, capsys, unreadable):
    """TIMEOUT-23, TIMEOUT-26: the dry run and the fix refuse alike with the file named, and nothing is written, the
    size entries and the job entries included."""
    files = {SIZE_FILE: long_function(70), WORKFLOW: workflow(jobs=job("e2e", "30")), **unreadable}
    repo = build_repo({**files, BASELINE_PATH: ceiling({"e2e": 40}, size=80)})
    before = tree_of(repo)
    run = dry_run_then_fix(capsys, repo)
    assert (run.planned_code, run.code, tree_of(repo)) == (2, 2, before)
    for printed in (run.planned, run.done):
        assert f"✗ debt not fixed cannot read {BROKEN}" in squashed(printed), printed


NO_CEILING_FILE = None
READ_NOWHERE = {
    "no ceiling file": (NO_CEILING_FILE, ceiling(None, size=70)),
    "no table": (ceiling(None, size=80), ceiling(None, size=70)),
    "an empty table": (ceiling({}, size=80), ceiling({}, size=70)),
}


@pytest.mark.parametrize(("held", "written"), READ_NOWHERE.values(), ids=READ_NOWHERE.keys())
def test_a_workflow_that_cannot_be_read_does_not_block_the_size_fix_where_no_job_entry_is_held(
    build_repo, capsys, held, written
):
    """TIMEOUT-23, TIMEOUT-26, TIMEOUT-30: a first recording, an absent table and an empty table read no workflow for the
    fix, so the size file is recorded or lowered, in the dry run too, and the rule alone reports the file; a recording
    of the job table is skipped there, so no job table is written."""
    files = {SIZE_FILE: long_function(70), WORKFLOW: workflow(jobs=job("e2e", "20")), **UNREADABLE_WORKFLOWS["no YAML"]}
    repo = build_repo(files if held is NO_CEILING_FILE else {**files, BASELINE_PATH: held})
    run = dry_run_then_fix(capsys, repo)
    assert (run.code, (repo / BASELINE_PATH).read_text(), "not fixed" in run.done, f"cannot read {BROKEN}" in run.done) == (
        2,
        written,
        False,
        True,
    )
    assert (run.planned_code, "not fixed" in run.planned, f"cannot read {BROKEN}" in run.planned) == (2, False, True)
    assert "e2e" not in (repo / BASELINE_PATH).read_text()


def test_a_first_recording_records_a_job_above_fifteen_beside_the_size_entries(build_repo, capsys):
    """TIMEOUT-22, TIMEOUT-27: with no ceiling file the fix records the size entries and the job above 15 at its limit,
    so the job is no new debt, in the dry run too."""
    repo = build_repo({SIZE_FILE: long_function(70), WORKFLOW: workflow(jobs=job("e2e", "20"))})
    run = dry_run_then_fix(capsys, repo)
    assert (run.code, run.planned_code, (repo / BASELINE_PATH).read_text()) == (0, 1, ceiling({"e2e": 20}, size=70))
    assert ("ceiling recorded" in run.done, "ceiling would be recorded" in run.planned) == (True, True)
    assert [("new debt" in out) for out in (run.done, run.planned)] == [False, False]
