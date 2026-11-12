"""`quality check debt --fix` records the `[job_minutes]` table where git never held one, and `--dry-run` plans the same
rows; a held table is never added to."""

from dataclasses import dataclass, field
from pathlib import Path

import pytest

from quality.baseline_file import BASELINE_PATH, JobKey, load_baseline
from tests.support.baselines import rendered
from tests.support.clones import function as clone_function
from tests.support.door import CHECK_WORD, DRY_RUN_WORDS, FIX_WORDS, fixed, restore_step, tree_of
from tests.support.git_repositories import git, put, shallow_clone
from tests.support.job_workflows import job, workflow
from tests.support.source_code import long_function
from tests.support.verdict import verdict_streams
from tests.support.verdict_renames import commit_all

WORKFLOW = ".github/workflows/e2e.yml"
OTHER = ".github/workflows/other.yml"
CALLED = ".github/workflows/called.yml"
BROKEN = ".github/workflows/broken.yml"
SIZE_FILE = "src/old.py"
RECORD_WORDS = "lower timeout-minutes to 15 or less, or run quality check --fix to record it"
HELD_WORDS = "lower timeout-minutes to 15 or less"


def tables(entries: dict[str, dict[str, int]]) -> str:
    return "".join(
        f'[job_minutes."{path}"]\n' + "".join(f'"{name}" = {minutes}\n' for name, minutes in jobs.items()) + "\n"
        for path, jobs in entries.items()
    )


def ceiling(entries: dict[str, dict[str, int]] | None = None, size: int | None = None, clones: bool = True) -> str:
    """A ceiling with a `long_one` entry of `size` lines under `SIZE_FILE` when given, the `[clones]` table when
    `clones`, and the job limits `entries` when given: a table without entries when they are empty."""
    size_table = "" if size is None else f'[function_lines."{SIZE_FILE}"]\nlong_one = {size}\n\n'
    job_tables = "" if entries is None else tables(entries) or "[job_minutes]\n"
    return rendered(f"{size_table}{'[clones]' if clones else ''}\n\n{job_tables}")


def squashed(text: str) -> str:
    return " ".join(text.split())


def limits(count: int) -> str:
    return "1 job limit" if count == 1 else f"{count} job limits"


def both(capsys, repo: Path) -> tuple[tuple[int, str], tuple[int, str]]:
    """The dry run, which leaves the tree and the index as they were, then the fix: the exit and the squashed output of
    each."""
    index, tree = git(repo, "ls-files", "--stage").stdout, tree_of(repo)
    planned_code, planned, _ = fixed(capsys, repo, *DRY_RUN_WORDS)
    assert (tree_of(repo), git(repo, "ls-files", "--stage").stdout) == (tree, index), planned
    code, done, _ = fixed(capsys, repo, *FIX_WORDS)
    return (planned_code, squashed(planned)), (code, squashed(done))


def jobs_of(*specs: tuple[str, str | None]) -> str:
    return "".join(job(name, limit) for name, limit in specs)


def the_called_workflow() -> dict[str, str]:
    call = workflow(jobs=f"  call:\n    uses: ./{CALLED}\n")
    return {WORKFLOW: call, CALLED: workflow("on: workflow_call\n", job("build", "20"))}


@dataclass(frozen=True)
class Recording:
    """The workflows of a repository whose ceiling holds `[clones]` and no job table, the job limits the fix writes
    (none: no table), and what it exits with and leaves as findings."""

    files: dict[str, str]
    written: dict[str, dict[str, int]]
    exit_code: int = 0
    findings: tuple[str, ...] = field(default=())

    @property
    def count(self) -> int:
        return sum(len(jobs) for jobs in self.written.values())


RECORDINGS = {
    "one job above 15": Recording({WORKFLOW: workflow(jobs=job("e2e", "30"))}, {WORKFLOW: {"e2e": 30}}),
    "jobs of two workflows": Recording(
        {WORKFLOW: workflow(jobs=jobs_of(("e2e", "30"), ("lint", "20"), ("fast", "10"))), OTHER: workflow(jobs=job("build", "45"))},
        {WORKFLOW: {"e2e": 30, "lint": 20}, OTHER: {"build": 45}},
    ),
    "a limit that rounds up": Recording(
        {WORKFLOW: workflow(jobs=jobs_of(("e2e", "17.5"), ("lint", "15.5")))}, {WORKFLOW: {"e2e": 18, "lint": 16}}
    ),
    "a job of a reusable workflow": Recording(the_called_workflow(), {CALLED: {"build": 20}}),
    "a job id that needs quotes": Recording({WORKFLOW: workflow(jobs=job("my job", "20"))}, {WORKFLOW: {"my job": 20}}),
    "a job with no limit beside one above 15": Recording(
        {WORKFLOW: workflow(jobs=jobs_of(("e2e", "30"), ("lint", None)))},
        {WORKFLOW: {"e2e": 30}},
        1,
        ("job lint has no timeout-minutes",),
    ),
    "an invalid limit beside one above 15": Recording(
        {WORKFLOW: workflow(jobs=jobs_of(("e2e", "30"), ("lint", "${{ x }}")))},
        {WORKFLOW: {"e2e": 30}},
        1,
        ("job lint has a timeout-minutes that is not a number above zero",),
    ),
    "a job of exactly 15 and of 14.5": Recording({WORKFLOW: workflow(jobs=jobs_of(("e2e", "15"), ("lint", "14.5")))}, {}),
    "a job above 15 no root reaches": Recording({WORKFLOW: workflow("on: workflow_call\n", job("e2e", "40"))}, {}),
}


@pytest.mark.parametrize("recording", RECORDINGS.values(), ids=RECORDINGS.keys())
def test_the_fix_records_exactly_the_judged_jobs_above_fifteen_at_their_rounded_up_limits(build_repo, capsys, recording):
    """TIMEOUT-27, TIMEOUT-31, TIMEOUT-30: a file with no table gets one holding exactly the jobs above 15, each at its
    limit rounded up under the file that defines it, none when no job is above 15, the rows name the count, the dry run
    plans the same rows and writes nothing, a job whose limit cannot be read keeps its finding, and the check then finds
    no new debt."""
    held = ceiling()
    repo = build_repo({**recording.files, BASELINE_PATH: held})
    planned, done = both(capsys, repo)
    recorded = bool(recording.written)
    assert (done[0], planned[0], (repo / BASELINE_PATH).read_text()) == (
        recording.exit_code,
        1 if recorded or recording.exit_code else 0,
        ceiling(recording.written) if recorded else held,
    )
    assert [finding in done[1] for finding in recording.findings] == [True] * len(recording.findings), done
    if recorded:
        noun = limits(recording.count)
        assert f"✓ quality/baseline.toml recorded {noun}" in done[1]
        assert f"~ quality/baseline.toml record {noun}" in planned[1]
    if recorded and not recording.exit_code:
        assert f"✓ debt recorded {noun} · quality/baseline.toml" in done[1]
        assert f"~ debt to record {noun} · quality/baseline.toml" in planned[1]
        assert ("ceiling recorded" in done[1], "ceiling would be recorded" in planned[1]) == (True, True)
    if not recorded:
        assert ("nothing to lower" in done[1], "nothing to lower" in planned[1]) == (True, True)
    code, out, _ = fixed(capsys, repo, CHECK_WORD, "debt")
    assert (code, "new debt" in out, "can fall" in out) == (recording.exit_code, False, False), out
    reread = load_baseline((repo / BASELINE_PATH).read_text()).job_minutes
    assert reread == ({JobKey(path, name): minutes for path, jobs in recording.written.items() for name, minutes in jobs.items()} if recorded else None)


def test_a_second_fix_changes_nothing_once_the_table_is_recorded(build_repo, capsys):
    """TIMEOUT-27: the recorded table is held, so a second fix leaves the file byte for byte and says nothing recorded."""
    repo = build_repo({WORKFLOW: workflow(jobs=job("e2e", "30")), BASELINE_PATH: ceiling()})
    assert fixed(capsys, repo, *FIX_WORDS)[0] == 0
    first = (repo / BASELINE_PATH).read_bytes()
    code, out, _ = fixed(capsys, repo, *FIX_WORDS)
    assert (code, (repo / BASELINE_PATH).read_bytes(), "recorded" in out, "nothing to lower" in out) == (0, first, False, True)


FIRST_FILES = {SIZE_FILE: long_function(70)}


@pytest.mark.parametrize(
    ("limit", "written", "recorded"),
    [
        pytest.param("30", ceiling({WORKFLOW: {"e2e": 30}}, size=70), "1 entry, 0 clone pairs, 1 job limit", id="a job above 15"),
        pytest.param("10", ceiling(size=70), "1 entry, 0 clone pairs", id="no job above 15"),
    ],
)
def test_a_first_fix_with_no_ceiling_file_records_the_job_table_beside_the_size_entries_and_clone_pairs(
    build_repo, capsys, limit, written, recorded
):
    """TIMEOUT-27, TIMEOUT-31: with no ceiling file the first recording names the job limits last and only when it
    records them, and writes the three tables, or two where no job is above 15; the dry run plans it."""
    repo = build_repo({**FIRST_FILES, WORKFLOW: workflow(jobs=job("e2e", limit))})
    planned, done = both(capsys, repo)
    assert (done[0], planned[0], (repo / BASELINE_PATH).read_text()) == (0, 1, written)
    assert f"✓ debt recorded {recorded} · quality/baseline.toml is new" in done[1]
    assert f"~ debt to record {recorded} · quality/baseline.toml would be new" in planned[1]
    assert ("ceiling recorded" in done[1], "ceiling would be recorded" in planned[1]) == (True, True)
    assert "✓ quality/baseline.toml recorded" not in done[1]


CLONE_FILES = {
    "src/a.py": "x = 1\n",
    "tests/test_a.py": clone_function("run", "a", "b"),
    "tests/test_b.py": clone_function("check", "c", "d"),
}


def test_a_recording_beside_clone_pairs_and_a_lowered_size_entry_names_each_and_counts_only_the_fall(build_repo, capsys):
    """TIMEOUT-31: the clone pairs and the job limits recorded in one fix are named in one debt row, each under its own
    note after the lowered entry, and the verdict counts the lowered entry alone; with nothing lowered it counts the
    recording."""
    files = {**CLONE_FILES, SIZE_FILE: long_function(62), WORKFLOW: workflow(jobs=job("e2e", "20"))}
    lowered = build_repo({**files, BASELINE_PATH: ceiling(size=80, clones=False)})
    planned, done = both(capsys, lowered)
    assert (done[0], planned[0]) == (0, 1)
    assert "ceiling lowered by 1 entry" in done[1]
    notes = [
        "✓ src/old.py:1 lowered function long_one 80 → 62 lines",
        "✓ quality/baseline.toml recorded 1 clone pair",
        "✓ quality/baseline.toml recorded 1 job limit",
    ]
    assert [done[1].index(note) for note in notes] == sorted(done[1].index(note) for note in notes)
    assert "1 ceiling entry would fall" in planned[1]


def test_a_recording_beside_clone_pairs_names_both_in_the_row(build_repo, capsys):
    """TIMEOUT-31."""
    files = {**CLONE_FILES, WORKFLOW: workflow(jobs=job("e2e", "20"))}
    repo = build_repo({**files, BASELINE_PATH: ceiling(clones=False)})
    planned, done = both(capsys, repo)
    assert "✓ debt recorded 1 clone pair, 1 job limit · quality/baseline.toml" in done[1]
    assert "~ debt to record 1 clone pair, 1 job limit · quality/baseline.toml" in planned[1]
    assert ("ceiling recorded" in done[1], "ceiling would be recorded" in planned[1]) == (True, True)


def commit_baseline(repo: Path, text: str) -> None:
    put(repo / BASELINE_PATH, text)
    git(repo, "add", "-f", BASELINE_PATH)
    git(repo, "commit", "-q", "-m", "baseline")


def held_then(build_repo, limit: str) -> Path:
    """A repository whose ceiling file, committed with a job table, is at `limit` for its one job."""
    files = {SIZE_FILE: long_function(70), WORKFLOW: workflow(jobs=job("e2e", limit))}
    return build_repo({**files, BASELINE_PATH: ceiling({WORKFLOW: {"e2e": 30}}, size=80)})


def lost_by_hand(build_repo, limit: str) -> Path:
    repo = held_then(build_repo, limit)
    put(repo / BASELINE_PATH, ceiling(size=80))
    return repo


def lost_in_a_commit(build_repo, limit: str) -> Path:
    repo = held_then(build_repo, limit)
    commit_baseline(repo, ceiling(size=80))
    return repo


def unreadable_in_the_past(build_repo, limit: str) -> Path:
    repo = build_repo({SIZE_FILE: long_function(70), WORKFLOW: workflow(jobs=job("e2e", limit)), BASELINE_PATH: "[job_minutes"})
    commit_baseline(repo, ceiling(size=80))
    return repo


REFUSALS = {
    "lost by hand": (lost_by_hand, "quality/baseline.toml lost its [job_minutes] table, which git holds"),
    "lost in a commit": (lost_in_a_commit, "quality/baseline.toml lost its [job_minutes] table, which git holds"),
    "unreadable in the past": (unreadable_in_the_past, "a version of quality/baseline.toml in git cannot be read"),
}


@pytest.mark.parametrize(("arrange", "fact"), REFUSALS.values(), ids=REFUSALS.keys())
def test_a_table_git_holds_is_not_recorded_again_where_a_job_above_fifteen_would_be(build_repo, capsys, arrange, fact):
    """TIMEOUT-28: a ceiling that lost its job table although a version of it in git held one, or a version no parser
    reads, refuses the whole fix with the restore step, in the dry run too, and nothing is written, the size entries
    included."""
    repo = arrange(build_repo, "30")
    index, tree = git(repo, "ls-files", "--stage").stdout, tree_of(repo)
    for words in (DRY_RUN_WORDS, FIX_WORDS):
        code, out, err = fixed(capsys, repo, *words)
        assert (code, f"✗ debt not fixed {fact}" in squashed(out)) == (2, True), out
        assert err.endswith(f"next: {restore_step()}\n"), err
        assert (tree_of(repo), git(repo, "ls-files", "--stage").stdout) == (tree, index)


@pytest.mark.parametrize("arrange", [lost_by_hand, lost_in_a_commit, unreadable_in_the_past], ids=REFUSALS.keys())
def test_a_table_less_file_with_no_job_above_fifteen_reads_no_history_for_the_job_table(build_repo, capsys, arrange):
    """TIMEOUT-28: where no job is above 15 nothing would be recorded, so the guard does not run: the size entry is
    lowered, no job table is written and nothing is refused."""
    repo = arrange(build_repo, "10")
    planned, done = both(capsys, repo)
    assert (done[0], planned[0], (repo / BASELINE_PATH).read_text()) == (0, 1, ceiling(size=70))


def a_shallow_clone(build_repo, elsewhere, limit: str) -> Path:
    """A clone of three commits out of four, the ceiling file in the oldest it holds that has a parent."""
    origin = build_repo({SIZE_FILE: long_function(70), WORKFLOW: workflow(jobs=job("e2e", limit))})
    put(origin / "second.txt", "2\n")
    git(origin, "add", "-f", "second.txt")
    git(origin, "commit", "-q", "-m", "second")
    commit_baseline(origin, ceiling(size=70))
    put(origin / "third.txt", "3\n")
    git(origin, "add", "-f", "third.txt")
    git(origin, "commit", "-q", "-m", "third")
    return shallow_clone(origin, elsewhere, depth=3)


def test_a_shallow_clone_cannot_prove_the_job_table_was_never_held_where_a_job_above_fifteen_would_be_recorded(
    build_repo, elsewhere, capsys
):
    """TIMEOUT-28: a shallow clone names `git fetch --unshallow` and writes nothing, in the dry run too."""
    clone = a_shallow_clone(build_repo, elsewhere, "30")
    tree = tree_of(clone)
    for words in (DRY_RUN_WORDS, FIX_WORDS):
        code, out, err = fixed(capsys, clone, *words)
        assert (code, "✗ debt not fixed this clone is shallow, so git cannot show the ceiling is new" in squashed(out)) == (2, True)
        assert (err.endswith("next: git fetch --unshallow\n"), tree_of(clone)) == (True, tree)
    git(clone, "fetch", "-q", "--unshallow")
    assert fixed(capsys, clone, *FIX_WORDS)[0] == 0
    assert load_baseline((clone / BASELINE_PATH).read_text()).job_minutes == {JobKey(WORKFLOW, "e2e"): 30}


def test_a_shallow_clone_with_no_job_above_fifteen_refuses_nothing_and_writes_no_table(build_repo, elsewhere, capsys):
    """TIMEOUT-28."""
    clone = a_shallow_clone(build_repo, elsewhere, "10")
    tree = tree_of(clone)
    planned, done = both(capsys, clone)
    assert (planned[0], done[0], tree_of(clone)) == (0, 0, tree)
    assert "not fixed" not in done[1]


HELD_TABLES = {
    "an empty table": ceiling({}, size=70),
    "entries": ceiling({WORKFLOW: {"lint": 20}}, size=70),
}


@pytest.mark.parametrize("held", HELD_TABLES.values(), ids=HELD_TABLES.keys())
def test_a_held_table_never_gains_an_entry_and_a_new_job_above_fifteen_stays_new_debt(build_repo, capsys, held):
    """TIMEOUT-29: the fix records and never raises, so a job above 15 with no entry in a held table, an empty one
    included, stays new debt with the words that only lower the limit, in the dry run too."""
    files = {SIZE_FILE: long_function(70), WORKFLOW: workflow(jobs=jobs_of(("e2e", "30"), ("lint", "20")))}
    repo = build_repo({**files, BASELINE_PATH: held})
    planned, done = both(capsys, repo)
    assert (done[0], planned[0], (repo / BASELINE_PATH).read_text()) == (1, 1, held)
    for out in (planned[1], done[1]):
        assert (HELD_WORDS in out, "to record it" in out, "e2e has timeout-minutes 30" in out) == (True, False, True), out


NEW_DEBT_STATES = {
    "no ceiling file": (None, RECORD_WORDS),
    "a file without the table": (ceiling(), RECORD_WORDS),
    "a table lost to git": ("lost", RECORD_WORDS),
    "an empty table": (ceiling({}), HELD_WORDS),
    "a table with entries": (ceiling({WORKFLOW: {"lint": 20}}), HELD_WORDS),
}


@pytest.mark.parametrize(("held", "words"), NEW_DEBT_STATES.values(), ids=NEW_DEBT_STATES.keys())
def test_check_words_a_new_debt_job_by_the_state_of_the_table_and_reads_no_history(build_repo, capsys, held, words):
    """TIMEOUT-29: where the file has no table the fix names the recording, where it holds one only the lowered limit;
    a table git held and the file lost is the first state."""
    files = {WORKFLOW: workflow(jobs=jobs_of(("e2e", "30"), ("lint", "10")))}
    if held == "lost":
        repo = lost_by_hand(build_repo, "30")
    else:
        repo = build_repo(files if held is None else {**files, BASELINE_PATH: held})
    code, out, _ = fixed(capsys, repo, CHECK_WORD, "debt")
    printed = squashed(out)
    assert (code, f"fix: {words}" in printed, (RECORD_WORDS in printed) == (words == RECORD_WORDS)) == (1, True, True), out


UNREADABLE_WORKFLOWS = {
    "an alias hides the jobs": {BROKEN: "x: &j\n  e2e: 1\non: pull_request\njobs: *j\n"},
    "a local call names an untracked workflow": {BROKEN: workflow(jobs=f"  e2e:\n    uses: ./{CALLED}\n")},
}
FIRST_OR_TABLE_LESS = {"no ceiling file": None, "a file without the table": ceiling(size=80)}


@pytest.mark.parametrize("held", FIRST_OR_TABLE_LESS.values(), ids=FIRST_OR_TABLE_LESS.keys())
@pytest.mark.parametrize("unreadable", UNREADABLE_WORKFLOWS.values(), ids=UNREADABLE_WORKFLOWS.keys())
def test_a_workflow_that_cannot_be_read_records_no_job_table_not_even_a_partial_one(build_repo, capsys, unreadable, held):
    """TIMEOUT-30: the job table is made only from a full reading, so a readable job above 15 in another workflow is
    not recorded either, the size entries are recorded or lowered, the rule reports the file with exit 2 and nothing is
    refused, in the dry run too."""
    files = {SIZE_FILE: long_function(70), WORKFLOW: workflow(jobs=job("e2e", "30")), **unreadable}
    repo = build_repo(files if held is None else {**files, BASELINE_PATH: held})
    planned, done = both(capsys, repo)
    assert (done[0], planned[0], (repo / BASELINE_PATH).read_text()) == (2, 2, ceiling(size=70))
    for out in (planned[1], done[1]):
        assert (f"cannot read {BROKEN}" in out, "not fixed" in out) == (True, False), out


def test_a_malformed_job_limit_names_the_workflow_path_as_a_quoted_key(build_repo, capsys):
    """TIMEOUT-33: the table, the quoted path and the job are one TOML dotted key, with exit 2."""
    repo = build_repo({"w.yml": workflow(), BASELINE_PATH: '[job_minutes."w.yml"]\ne2e = 0\n'})
    code, out, _ = fixed(capsys, repo, CHECK_WORD, "debt")
    assert (code, 'needs job_minutes."w.yml".e2e as a positive count, not 0' in squashed(out)) == (2, True), out


def test_the_recorded_file_committed_on_a_branch_whose_base_lacks_the_table_is_a_green_verdict_with_no_loosening(
    pull_request, capsys
):
    """TIMEOUT-18, TIMEOUT-31: a head whose ceiling gains `[job_minutes]` against a base without it is an adoption, so
    the recording leaves no loosening line."""
    repo, commit_base = pull_request
    put(repo / WORKFLOW, workflow(jobs=job("e2e", "30")))
    commit_base(ceiling())
    assert fixed(capsys, repo, *FIX_WORDS)[0] == 0
    commit_all(repo, "recorded")
    code, out, err = verdict_streams(repo, capsys)
    assert (code, "✓ green" in out, "job limit baseline" in out, err) == (0, True, False, ""), out
