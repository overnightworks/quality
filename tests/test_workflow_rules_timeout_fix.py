"""`workflow_rules.lower_job_entries`: the held `[job_minutes]` entries a fix lowers or drops, judged by the rule's own
reading of the jobs."""

import pytest

from quality import checkout
from quality.baseline_file import Baseline, JobKey
from quality.report import CannotJudge, NoteEvent
from quality.workflow_rules import lower_job_entries

LOWERED, DROPPED = NoteEvent.LOWERED, NoteEvent.DROPPED
WORKFLOW = ".github/workflows/w.yml"
CALLED = ".github/workflows/x.yml"
KEY = JobKey(WORKFLOW, "e2e")
STEPS = "    runs-on: ubuntu-latest\n    steps:\n      - run: 'true'\n"


def job(name: str, limit: str | None) -> str:
    return f"  {name}:\n" + ("" if limit is None else f"    timeout-minutes: {limit}\n") + STEPS


def workflow(*jobs: str, on: str = "pull_request") -> str:
    return f"name: w\non: {on}\npermissions: {{}}\njobs:\n{''.join(jobs)}"


def lowered_from(build_repo, files: dict[str, str], entries: dict[JobKey, int] | None, delete: tuple[str, ...] = ()):
    """The job table and the notes `lower_job_entries` makes over the repository of `files`, minus the `delete` files
    the tree lost while the index still lists them."""
    repo = build_repo(files)
    for name in delete:
        (repo / name).unlink()
    fix = lower_job_entries(repo, checkout.tracked_files(repo), Baseline({}, frozenset(), entries))
    return fix.ceiling.job_minutes, [(note.path, note.event, note.fact, note.line) for note in fix.changes]


def note(event: NoteEvent, fact: str, path: str = WORKFLOW) -> tuple[str, NoteEvent, str, None]:
    return (path, event, fact, None)


@pytest.mark.parametrize(
    ("limit", "expected_entry", "fact"),
    [
        ("30", 30, "job e2e 40 → 30 minutes"),
        ("17.5", 18, "job e2e 40 → 18 minutes"),
        ("15.5", 16, "job e2e 40 → 16 minutes"),
    ],
)
def test_an_entry_above_its_jobs_rounded_up_limit_falls_to_it(build_repo, limit, expected_entry, fact):
    """TIMEOUT-20."""
    table, notes = lowered_from(build_repo, {WORKFLOW: workflow(job("e2e", limit))}, {KEY: 40})
    assert (table, notes) == ({KEY: expected_entry}, [note(LOWERED, fact)])


@pytest.mark.parametrize("limit", ["40", "45"])
def test_an_entry_equal_to_its_job_or_below_it_stays_and_is_no_change(build_repo, limit):
    """TIMEOUT-20, TIMEOUT-22."""
    assert lowered_from(build_repo, {WORKFLOW: workflow(job("e2e", limit))}, {KEY: 40}) == ({KEY: 40}, [])


@pytest.mark.parametrize(
    ("limit", "fact"),
    [
        ("12", "job e2e 40 → 12 minutes · debt from 16 minutes"),
        ("15", "job e2e 40 → 15 minutes · debt from 16 minutes"),
        ("14.5", "job e2e 40 → 15 minutes · debt from 16 minutes"),
    ],
)
def test_an_entry_whose_jobs_rounded_up_limit_is_no_debt_is_dropped(build_repo, limit, fact):
    """TIMEOUT-21."""
    table, notes = lowered_from(build_repo, {WORKFLOW: workflow(job("e2e", limit))}, {KEY: 40})
    assert (table, notes) == ({}, [note(DROPPED, fact)])


@pytest.mark.parametrize(
    ("limit", "entry"),
    [("30", 10), ("15", 15), ("10", 12)],
    ids=["a job in debt", "equal", "a job below"],
)
def test_an_entry_of_fifteen_or_less_is_dropped_whatever_its_job_holds(build_repo, limit, entry):
    """TIMEOUT-21."""
    table, notes = lowered_from(build_repo, {WORKFLOW: workflow(job("e2e", limit))}, {KEY: entry})
    assert (table, notes) == ({}, [note(DROPPED, f"job e2e has no debt · was {entry} minutes")])


def a_job_removed() -> dict[str, str]:
    return {WORKFLOW: workflow(job("lint", "10"))}


def a_workflow_no_root_reaches() -> dict[str, str]:
    return {WORKFLOW: workflow(job("e2e", "40"), on="workflow_call")}


def a_job_turned_into_a_call() -> dict[str, str]:
    call = f"  e2e:\n    uses: ./{CALLED}\n"
    return {WORKFLOW: workflow(call), CALLED: workflow(job("build", "10"), on="workflow_call")}


@pytest.mark.parametrize(
    ("files", "delete"),
    [
        pytest.param(a_job_removed(), (), id="the job is removed from the file"),
        pytest.param(a_workflow_no_root_reaches(), (), id="no root reaches the workflow"),
        pytest.param(a_job_turned_into_a_call(), (), id="the job calls a workflow"),
        pytest.param(
            {WORKFLOW: workflow(job("e2e", "40")), CALLED: workflow(job("lint", "10"))},
            (WORKFLOW,),
            id="the file is deleted",
        ),
    ],
)
def test_an_entry_whose_job_is_gone_is_dropped(build_repo, files, delete):
    """TIMEOUT-21."""
    table, notes = lowered_from(build_repo, files, {KEY: 40}, delete)
    assert (table, notes) == ({}, [note(DROPPED, "job e2e is gone · was 40 minutes")])


def test_a_job_above_fifteen_without_an_entry_gets_none_and_the_held_entries_are_kept_as_they_were(build_repo):
    """TIMEOUT-22."""
    other = JobKey(WORKFLOW, "lint")
    files = {WORKFLOW: workflow(job("e2e", "20"), job("lint", "20"))}
    assert lowered_from(build_repo, files, {other: 20}) == ({other: 20}, [])


@pytest.mark.parametrize("limit", [None, "${{ vars.T }}"], ids=["missing", "invalid"])
@pytest.mark.parametrize("entry", [40, 10])
def test_an_entry_whose_jobs_limit_cannot_be_read_stands(build_repo, limit, entry):
    """TIMEOUT-22."""
    assert lowered_from(build_repo, {WORKFLOW: workflow(job("e2e", limit))}, {KEY: entry}) == ({KEY: entry}, [])


def test_entries_are_judged_in_the_order_of_their_files_and_jobs(build_repo):
    """TIMEOUT-20, TIMEOUT-21."""
    files = {WORKFLOW: workflow(job("a", "30"), job("b", "12"), job("c", "20"))}
    entries = {JobKey(WORKFLOW, "c"): 20, JobKey(WORKFLOW, "b"): 40, JobKey(WORKFLOW, "a"): 40}
    table, notes = lowered_from(build_repo, files, entries)
    assert (table, notes) == (
        {JobKey(WORKFLOW, "a"): 30, JobKey(WORKFLOW, "c"): 20},
        [note(LOWERED, "job a 40 → 30 minutes"), note(DROPPED, "job b 40 → 12 minutes · debt from 16 minutes")],
    )


UNREADABLE = [
    pytest.param({WORKFLOW: "on: [pull_request\njobs: {"}, "cannot read .github/workflows/w.yml", id="no YAML"),
    pytest.param(
        {WORKFLOW: "x: &j\n  e2e: 1\non: pull_request\njobs: *j\n"},
        "cannot read .github/workflows/w.yml",
        id="an alias hides the jobs",
    ),
    pytest.param(
        {WORKFLOW: workflow(f"  e2e:\n    uses: ./{CALLED}\n")},
        "cannot read .github/workflows/w.yml",
        id="a local call names an untracked workflow",
    ),
    pytest.param(
        {WORKFLOW: workflow(job("e2e", "40")), CALLED: "on: [pull_request\njobs: {"},
        "cannot read .github/workflows/x.yml",
        id="an unreadable workflow no root reaches",
    ),
]


@pytest.mark.parametrize(("files", "reason"), UNREADABLE)
def test_a_workflow_the_rule_cannot_read_refuses_while_an_entry_is_held(build_repo, files, reason):
    """TIMEOUT-23."""
    with pytest.raises(CannotJudge) as refusal:
        lowered_from(build_repo, files, {KEY: 40})
    assert refusal.value.reason.fact.startswith(reason)


@pytest.mark.parametrize("entries", [None, {}], ids=["no table", "an empty table"])
@pytest.mark.parametrize(("files", "reason"), UNREADABLE)
def test_a_workflow_the_rule_cannot_read_is_not_read_while_no_entry_is_held(build_repo, files, reason, entries):
    """TIMEOUT-23."""
    assert lowered_from(build_repo, files, entries) == (entries, [])
