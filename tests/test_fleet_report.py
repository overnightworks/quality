"""`quality report`'s streams: the fleet line, a row per repository and the closing line or the pair on stderr, the
tables alone on stdout and in the step summary."""

import json
import subprocess

import pytest

from quality import __version__, fleet, fleet_ci_time
from quality.fleet import ReleaseTagsError
from tests.support.clones import function
from tests.support.fleet import (
    NEWEST,
    RERUN_NEXT,
    Answer,
    Listing,
    Printed,
    listed,
    report_in,
    row,
    ticks,
    write_fleet,
    write_organizations,
)
from tests.support.git_repositories import head_commit

pytestmark = pytest.mark.integration












def runs_of(*runs: tuple[str, str, str, str], attempt: int = 1) -> Answer:
    """GitHub's answer for the runs listing; each run is `(head_sha, created_at, updated_at, conclusion)` of a pull request at `run_attempt`."""
    entries = [
        {"id": number, "head_branch": "feature", "head_sha": sha, "event": "pull_request", "status": "completed", "conclusion": conclusion, "run_attempt": attempt, "created_at": created, "updated_at": updated}
        for number, (sha, created, updated, conclusion) in enumerate(runs, 1)
    ]
    return Answer(json.dumps({"total_count": len(entries), "workflow_runs": entries}).encode())












def gate_caller(ref: str) -> dict[str, str]:
    return {".github/workflows/ci.yml": f"jobs:\n  gate:\n    uses: overnightworks/quality/.github/workflows/gate.yml@{ref}\n"}


def test_a_measured_fleet_prints_its_rows_on_stderr_and_only_the_tables_on_stdout(capsys, monkeypatch, tmp_path, public, remotes):
    """FLEET-1, FLEET-7, FLEET-33, FLEET-34, FLEET-35, FLEET-39, FLEET-40, FLEET-41, FLEET-42, FLEET-45: the rows stand
    on stderr, the tables alone on stdout, and the heading names the newest release by version."""
    alpha = public("alpha", {"a.py": "a = 1\n"})
    beta = public("beta-repository", {"b.py": "b = 1\n"})
    folder = write_fleet(tmp_path / "fleet", [beta, alpha])
    printed = report_in(folder, capsys, monkeypatch, ticks(5, 6, 9, 9, 14, 77))
    alpha_commit, beta_commit = head_commit(remotes / alpha)[:7], head_commit(remotes / beta)[:7]
    assert (printed.code, printed.err) == (
        0,
        ("  · fleet              2 found        quality/fleet.toml\n"
        f"  ✓ o/alpha            measured       {alpha_commit} · 3s\n"
        f"  ✓ o/beta-repository  measured       {beta_commit} · 5s\n"
        "✓ 2 of 2 repositories measured · 1m12s\n"),
    )
    lines = printed.out.splitlines()
    assert lines[:6] == [
        f"**Fleet report, 2026-11-06, quality {__version__}**",
        "",
        ("| Repo | Commit | Languages | Lines prod / test | test:prod | Files ≥ 800 prod / test | Functions ≥ 60 prod / test "
        "| CCN \\> 15 prod / test | Clone pairs prod / test | Note |"),
        "|---" * 10 + "|",
        f"| o/alpha | {alpha_commit} | Python 1 | 1 / 0 | 0.00 | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 |  |",
        f"| o/beta-repository | {beta_commit} | Python 1 | 1 / 0 | 0.00 | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 |  |",
    ]
    assert lines[6:9] == ["", "**Repo × rule, newest release v0.10.0**", ""]
    assert [cells[:3] for cells in (row(printed.out, alpha, 1), row(printed.out, beta, 1))] == [
        ["o/alpha", "no gate", "none"],
        ["o/beta-repository", "no gate", "none"],
    ]
    assert lines[9].startswith("Every gate release enforces ")
    assert lines[11].startswith("| Repo | Gate |")
    assert lines[16].startswith("**CI time, last 7 days")
    assert lines[23:27] == ["**Clone pairs between repositories · 0 pairs**", "", "| Repo | Function | Repo | Function | Kind |", "|---|---|---|---|---|"]


def test_the_tests_per_layer_table_stands_last_with_the_counts_of_each_cloned_tip(capsys, monkeypatch, tmp_path, public):
    """FLEET-119: the table stands after the pairs between repositories, one row per repository, counted in its clone."""
    alpha = public("alpha", {
        "tests/test_a.py": "import pytest\n\n\n@pytest.mark.skip\ndef test_a():\n    pass\n\n\ndef test_b():\n    pass\n",
        "tests/e2e/test_flow.py": "def test_flow():\n    pass\n",
        "tests/e2e/live/test_live.py": "def test_live():\n    pass\n",
        "web/package.json": "{}",
        "web/src/a.test.ts": "test.fixme('a', () => {});\n",
        "web/src/bad.test.ts": "/* never closed",
    })
    printed = report_in(write_fleet(tmp_path / "fleet", [alpha]), capsys, monkeypatch, ticks(0, 1, 2, 3))
    assert (printed.code, printed.out.splitlines()[-5:]) == (0, [
        "**Tests per layer**",
        "",
        "| Repo | Unit | Integration | Flow | Live | Skipped | Fixme | Note |",
        "|---|---|---|---|---|---|---|---|",
        "| o/alpha | 3 | 0 | 1 | 1 | 1 | 1 | 1 file not read |",
    ])


def test_a_repository_that_cannot_be_cloned_says_so_in_the_tests_per_layer_table_and_the_exit_is_unchanged(capsys, monkeypatch, tmp_path, public, missing):
    """FLEET-119, FLEET-124: an unmeasured repository reads cannot judge in the note, and the exit is the report's own."""
    alpha = public("alpha", {"a.py": "a = 1\n"})
    printed = report_in(write_fleet(tmp_path / "fleet", [alpha, missing]), capsys, monkeypatch, ticks(0, 1, 2, 3, 4))
    assert (printed.code, printed.out.splitlines()[-2:]) == (
        2,
        ["| o/alpha | 0 | 0 | 0 | 0 | 0 | 0 |  |", "| o/gone | — | — | — | — | — | — | cannot judge: cannot clone (git exited 128) |"],
    )


def test_a_repository_that_cannot_be_cloned_is_its_row_and_the_pair_after_the_tables(capsys, monkeypatch, tmp_path, public, missing, remotes):
    """FLEET-26, FLEET-36, FLEET-46: a repository that cannot be cloned is its row, and the closing pair counts it."""
    alpha = public("alpha", {"a.py": "a = 1\n"})
    printed = report_in(write_fleet(tmp_path / "fleet", [alpha, missing]), capsys, monkeypatch, ticks(0, 1, 2, 3, 4))
    assert (printed.code, printed.err) == (
        2,
        ("  · fleet         2 found        quality/fleet.toml\n"
        f"  ✓ o/alpha       measured       {head_commit(remotes / alpha)[:7]} · 1s\n"
        "  ✗ o/gone        not measured   cannot clone (git exited 128)\n"
        "error: 1 of 2 repositories not measured · the tables say so\n"
        "next: quality report\n"),
    )
    assert row(printed.out, missing)[2] == "cannot judge: cannot clone (git exited 128)"


def test_a_repository_that_holds_no_commit_is_a_row_that_says_so(capsys, monkeypatch, tmp_path, public, remotes):
    """FLEET-36, FLEET-103: a served repository without a commit is not measured, and its row says it holds none."""
    alpha = public("alpha", {"a.py": "a = 1\n"})
    empty = remotes / "o" / "empty"
    empty.mkdir(parents=True)
    subprocess.run(["git", "init", "--quiet", str(empty)], check=True)
    printed = report_in(write_fleet(tmp_path / "fleet", [alpha, "o/empty"]), capsys, monkeypatch, ticks(0, 1, 2, 3, 4))
    assert (printed.code, printed.err.splitlines()[2:4]) == (
        2,
        ["  ✗ o/empty       not measured   holds no commit", "error: 1 of 2 repositories not measured · the tables say so"],
    )
    assert row(printed.out, "o/empty")[2] == "cannot judge: holds no commit"


def test_unread_release_tags_stand_under_the_fleet_line_and_in_the_pair(capsys, monkeypatch, tmp_path, public, remotes):
    """FLEET-26, FLEET-38, FLEET-46: unread release tags stand under the fleet line and in the closing pair."""
    alpha = public("alpha", {"a.py": "a = 1\n"})

    def unreadable() -> dict[str, str]:
        raise ReleaseTagsError("git exited 128")

    printed = report_in(write_fleet(tmp_path / "fleet", [alpha]), capsys, monkeypatch, ticks(0, 1, 2), tags=unreadable)
    assert (printed.code, printed.err) == (
        2,
        ("  · fleet         1 found        quality/fleet.toml\n"
        "  ✗ release tags  not read       git exited 128\n"
        f"  ✓ o/alpha       measured       {head_commit(remotes / alpha)[:7]} · 1s\n"
        "error: the release tags not read · the tables say so\n"
        "next: quality report\n"),
    )


def test_a_cell_that_cannot_read_a_file_makes_the_row_partly_read(capsys, monkeypatch, tmp_path, public):
    """FLEET-26, FLEET-37, FLEET-46: a cell that cannot read a file makes its row partly read."""
    alpha = public("alpha", {**gate_caller(NEWEST), "quality/config.toml": "[gate\n"})
    printed = report_in(write_fleet(tmp_path / "fleet", [alpha]), capsys, monkeypatch, ticks(0, 1, 2))
    assert (printed.code, printed.err) == (
        2,
        ("  · fleet         1 found        quality/fleet.toml\n"
        "  ✗ o/alpha       partly read    cannot read quality/config.toml\n"
        "error: 1 partly read · the tables say so\n"
        "next: quality report\n"),
    )


def test_an_unwritable_step_summary_is_the_pair_after_the_tables(capsys, monkeypatch, tmp_path, public):
    """FLEET-47: an unwritable step summary is the closing pair, after the tables."""
    alpha = public("alpha", {"a.py": "a = 1\n"})
    summary = tmp_path / "a-directory"
    summary.mkdir()
    printed = report_in(
        write_fleet(tmp_path / "fleet", [alpha]), capsys, monkeypatch, ticks(0, 1, 2), environment={"GITHUB_STEP_SUMMARY": str(summary)}
    )
    assert (printed.code, printed.out.splitlines()[0].startswith("**Fleet report, "), "| o/alpha |" in printed.out) == (2, True, True)
    assert printed.err.endswith(
        f"error: GITHUB_STEP_SUMMARY is not writable · [Errno 21] Is a directory: '{summary}'\n"
        "next: fix what the error names, then run quality report again\n"
    )


@pytest.mark.parametrize(
    ("ref", "cell"),
    [
        ("<!--x", "\\<!--x (not a commit)"),
        ("[a](b)", "\\[a\\](b) (not a commit)"),
        ("a\\b", "a\\\\b (not a commit)"),
        ("a|b", "a\\|b (not a commit)"),
        ("*a*_b_", "\\*a\\*\\_b\\_ (not a commit)"),
    ],
    ids=["comment", "link", "backslash", "cell border", "emphasis"],
)
def test_markdown_in_repository_text_stays_text_in_the_tables(capsys, monkeypatch, tmp_path, public, ref, cell):
    """FLEET-43, FLEET-44: repository text stays text in the tables, in stdout and in the step summary."""
    hostile = public("hostile", gate_caller(ref))
    summary = tmp_path / "summary.md"
    printed = report_in(
        write_fleet(tmp_path / "fleet", [hostile]), capsys, monkeypatch, ticks(0, 1, 2, 3), environment={"GITHUB_STEP_SUMMARY": str(summary)}
    )
    assert (row(printed.out, hostile, 1)[1], row(summary.read_text(), hostile, 1)[1]) == (cell, cell)


def test_report_takes_v_and_prints_the_same(capsys, monkeypatch, tmp_path, public):
    """FLEET-48: -v prints what the run prints without it."""
    folder = write_fleet(tmp_path / "fleet", [public("alpha", {"a.py": "a = 1\n"})])
    plain = report_in(folder, capsys, monkeypatch, ticks(0, 1, 2, 3))
    verbose = report_in(folder, capsys, monkeypatch, ticks(0, 1, 2, 3), words=("-v",))
    assert verbose == plain


def test_an_organizations_public_repositories_are_measured_and_nothing_else(capsys, monkeypatch, tmp_path, public):
    """FLEET-32, FLEET-33, FLEET-34, FLEET-92: an organization's public, non-archived, non-fork repositories are
    measured in name order, named as GitHub names them, and nothing else."""
    public("alpha", {"a.py": "a = 1\n"})
    public("Beta", {"b.py": "b = 1\n"})
    listing = Listing({"o": listed(("o/Beta",), ("o/alpha",), ("o/old", "archived"), ("o/copy", "fork"))})
    printed = report_in(
        write_organizations(tmp_path / "fleet", ["o"]), capsys, monkeypatch, ticks(0, 1, 2, 3, 4, 5), fetch_listing=listing
    )
    assert printed.code == 0, printed.err
    assert printed.err.splitlines()[0].split() == ["·", "fleet", "2", "found", "o", "·", "quality/fleet.toml"]
    assert [line.split()[1] for line in printed.err.splitlines()[1:3]] == ["o/alpha", "o/Beta"]
    assert "o/old" not in printed.err + printed.out
    assert "o/copy" not in printed.err + printed.out


def test_a_private_entry_of_the_listing_is_in_no_line(capsys, monkeypatch, tmp_path, public):
    """FLEET-32, FLEET-72: a private entry of the listing is measured nowhere and named in no line, the step summary
    included."""
    public("alpha", {"a.py": "a = 1\n"})
    summary = tmp_path / "summary.md"
    listing = Listing({"o": listed(("o/alpha",), ("o/hidden-name", "private"))})
    printed = report_in(
        write_organizations(tmp_path / "fleet", ["o"]), capsys, monkeypatch, ticks(0, 1, 2, 3),
        environment={"GITHUB_STEP_SUMMARY": str(summary)}, fetch_listing=listing,
    )
    assert printed.code == 0, printed.err
    assert "hidden-name" not in printed.err + printed.out + summary.read_text()


def test_a_private_repository_named_in_the_file_never_reads_as_found(capsys, monkeypatch, tmp_path, public, missing):
    """FLEET-36, FLEET-67, FLEET-94: a private repository the listing names does not make a `repositories` entry
    already listed; the entry is measured and, as nobody serves it, is a row that cannot be cloned."""
    listing = Listing({"o": listed(("o/gone", "private"))})
    printed = report_in(
        write_organizations(tmp_path / "fleet", ["o"], [missing]), capsys, monkeypatch, ticks(0, 1, 2, 3), fetch_listing=listing
    )
    assert printed.code == 2
    assert [line.split() for line in printed.err.splitlines() if "o/gone" in line] == [
        ["✗", "o/gone", "not", "measured", "cannot", "clone", "(git", "exited", "128)"]
    ]


@pytest.mark.parametrize(
    ("environment", "authorization"),
    [({"GITHUB_TOKEN": "dummy-token"}, "Bearer dummy-token"), ({}, None), ({"GITHUB_TOKEN": ""}, None)],
    ids=["token", "no token", "empty token"],
)
def test_the_listing_carries_the_job_token_only_when_there_is_one(capsys, monkeypatch, tmp_path, public, environment, authorization):
    """FLEET-50, FLEET-60: the listing is asked once and carries the job token as a bearer header only when there is
    one, and no output holds it."""
    public("alpha", {"a.py": "a = 1\n"})
    listing = Listing({"o": listed(("o/alpha",))})
    printed = report_in(
        write_organizations(tmp_path / "fleet", ["o"]), capsys, monkeypatch, ticks(0, 1, 2, 3),
        environment=environment, fetch_listing=listing,
    )
    ((url, headers),) = listing.requests
    assert (url, headers.get("Authorization")) == ("https://api.github.com/orgs/o/repos?type=public&per_page=100", authorization)
    assert "dummy-token" not in printed.err + printed.out
    ((_, runs_headers),) = listing.runs_requests
    assert runs_headers.get("Authorization") == authorization










def test_a_fork_named_in_the_file_is_measured(capsys, monkeypatch, tmp_path, public):
    """FLEET-30: a fork named in the file is measured."""
    public("alpha", {"a.py": "a = 1\n"})
    public("copy", {"c.py": "c = 1\n"})
    listing = Listing({"o": listed(("o/alpha",), ("o/copy", "fork"))})
    printed = report_in(
        write_organizations(tmp_path / "fleet", ["o"], ["o/copy"]), capsys, monkeypatch, ticks(0, 1, 2, 3, 4, 5), fetch_listing=listing
    )
    assert printed.code == 0, printed.err
    assert [cells[0] for cells in (row(printed.out, "o/alpha", 1), row(printed.out, "o/copy", 1))] == ["o/alpha", "o/copy"]




def test_an_excluded_repository_is_left_out_and_the_fleet_line_counts_the_rest(capsys, monkeypatch, tmp_path, public):
    """FLEET-31, FLEET-33, FLEET-45: an excluded repository is left out, the fleet line counts the rest, and a fleet of
    one closes on `1 of 1 repository`."""
    public("alpha", {"a.py": "a = 1\n"})
    listing = Listing({"o": listed(("o/alpha",), ("o/Beta",))})
    printed = report_in(
        write_organizations(tmp_path / "fleet", ["o"], exclude=["o/beta"]), capsys, monkeypatch, ticks(0, 1, 2, 3), fetch_listing=listing
    )
    assert printed.code == 0, printed.err
    assert "  · fleet         1 found        o · quality/fleet.toml\n" in printed.err
    assert "Beta" not in printed.err + printed.out
    assert printed.err.endswith("✓ 1 of 1 repository measured · 3s\n")








def test_the_weekly_ci_time_is_a_third_table_and_leaves_the_exit_as_it_is(capsys, monkeypatch, tmp_path, public):
    """FLEET-40, FLEET-104: each repository's commits, median, p90 and red share of the last 7 days stand in a third table
    after the two others, in the step summary too, and a red week raises no finding."""
    public("alpha", {"a.py": "a = 1\n"})
    public("beta", {"b.py": "b = 1\n"})
    slow = [("x", "2026-11-05T10:00:00Z", "2026-11-05T10:03:00Z", "failure"), ("y", "2026-11-05T11:00:00Z", "2026-11-05T11:01:00Z", "success")]
    listing = Listing({"o": listed(("o/alpha",), ("o/beta",))}, runs={"o/alpha": runs_of(*slow)})
    summary = tmp_path / "summary.md"
    printed = report_in(
        write_organizations(tmp_path / "fleet", ["o"]), capsys, monkeypatch, ticks(*range(8)),
        environment={"GITHUB_STEP_SUMMARY": str(summary)}, fetch_listing=listing,
    )
    assert printed.code == 0, printed.err
    third = printed.out.split("**CI time, last 7 days")[1].splitlines()
    assert third[0] == " · 4 API calls**"
    assert third[2:6] == [
        "| Repo | Commits | Median | p90 | Red | Red on main | Re-runs | Green on re-run | Jobs sampled | Queue p90 | Over 12 min |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
        "| o/alpha | 2 | 1m00s | 3m00s | 50 % (1 of 2) | — | 0 | 0 | 2 | 7s | none |",
        "| o/beta | 0 | — | — | — | — | — | — | — | — | — |",
    ]
    assert "| o/alpha | 2 | 1m00s |" in summary.read_text()


def test_re_runs_and_those_green_on_re_run_print_in_the_ci_time_table_and_leave_the_exit_as_it_is(capsys, monkeypatch, tmp_path, public):
    """FLEET-112: a run re-run to a green latest attempt counts in both cells, and no finding or exit follows."""
    public("alpha", {"a.py": "a = 1\n"})
    listing = Listing({"o": listed(("o/alpha",))}, runs={"o/alpha": runs_of(("x", "2026-11-05T10:00:00Z", "2026-11-05T10:01:00Z", "success"), attempt=2)})
    printed = report_in(write_organizations(tmp_path / "fleet", ["o"]), capsys, monkeypatch, ticks(*range(8)), fetch_listing=listing)
    assert printed.code == 0, printed.err
    assert "| o/alpha | 1 | 1m00s | 1m00s | 0 % (0 of 1) | — | 1 | 1 | 1 | 7s | none |" in printed.out


def test_a_repository_whose_runs_cannot_be_read_is_a_partly_read_row_and_the_exit_stays(capsys, monkeypatch, tmp_path, public):
    """FLEET-37, FLEET-105: unreadable runs are a stderr row and a partly read table row, the other repository keeps its
    numbers, and the closing line and exit are those of a report without the table."""
    public("alpha", {"a.py": "a = 1\n"})
    public("beta", {"b.py": "b = 1\n"})
    listing = Listing(
        {"o": listed(("o/alpha",), ("o/beta",))},
        runs={"o/alpha": Answer(failure=OSError("down")), "o/beta": runs_of(("x", "2026-11-05T10:00:00Z", "2026-11-05T10:00:30Z", "success"))},
    )
    printed = report_in(write_organizations(tmp_path / "fleet", ["o"]), capsys, monkeypatch, ticks(*range(8)), fetch_listing=listing)
    assert printed.code == 0, printed.err
    assert "  ✗ o/alpha  " in printed.err
    assert "partly read    cannot read CI time" in printed.err
    assert printed.err.splitlines()[-1].startswith("✓ 2 of 2 repositories measured")
    assert "| o/alpha | partly read · cannot read CI time | — | — | — | — | — | — | — | — | — |" in printed.out
    assert "| o/beta | 1 | 30s | 30s | 0 % (0 of 1) | — | 0 | 0 | 1 | 7s | none |" in printed.out


def slow_week() -> Answer:
    return runs_of(("x", "2026-11-05T10:00:00Z", "2026-11-05T10:11:00Z", "success"))


def fleet_of_two(tmp_path, public):
    """A fleet of a repository with a week of 11-minute CI and one without runs, and the listing that serves it."""
    public("alpha", {"a.py": "a = 1\n"})
    public("beta", {"b.py": "b = 1\n"})
    listing = Listing({"o": listed(("o/alpha",), ("o/beta",))}, runs={"o/alpha": slow_week()})
    return write_organizations(tmp_path / "fleet", ["o"]), listing


def test_the_switch_writes_the_weekly_document_and_leaves_stdout_and_stderr_as_they_are(capsys, monkeypatch, tmp_path, public):
    """FLEET-106, FLEET-107, FLEET-40: `--ci-time-json` creates the missing folder, writes the document and prints nothing
    of its own; without it the same report writes no file."""
    folder, listing = fleet_of_two(tmp_path, public)
    without = report_in(folder, capsys, monkeypatch, ticks(*range(8)), fetch_listing=listing)
    assert sorted(path.name for path in folder.iterdir()) == ["quality"]
    with_switch = report_in(folder, capsys, monkeypatch, ticks(*range(8)), words=("--ci-time-json",), fetch_listing=listing)
    assert with_switch == without
    written = json.loads((folder / fleet_ci_time.DOCUMENT_PATH).read_text(encoding="utf-8"))
    assert (written["generated_at"], written["window_start"], written["complete"]) == ("2026-11-06T12:00:00Z", "2026-10-30T00:00:00Z", True)
    assert written["repositories"] == [
        {"name": "o/alpha", "median_seconds": 660, "commits_sampled": 1, "measured": True, "breach": True},
        {"name": "o/beta", "median_seconds": 0, "commits_sampled": 0, "measured": False, "breach": False},
    ]


@pytest.mark.parametrize("blocker", ["the folder is a file", "the file is a directory"])
def test_a_document_that_cannot_be_written_is_the_pair_after_the_tables(capsys, monkeypatch, tmp_path, public, blocker):
    """FLEET-109: a folder or a file in the way of the document is `error: <path> is not writable · <reason>` with the
    rerun step, exit 2, after the tables."""
    folder, listing = fleet_of_two(tmp_path, public)
    if blocker == "the folder is a file":
        (folder / fleet_ci_time.DOCUMENT_PATH.parent).write_text("")
    else:
        (folder / fleet_ci_time.DOCUMENT_PATH).mkdir(parents=True)
    printed = report_in(folder, capsys, monkeypatch, ticks(*range(8)), words=("--ci-time-json",), fetch_listing=listing)
    assert printed.code == 2
    assert "| o/alpha |" in printed.out
    assert printed.err.endswith(RERUN_NEXT)
    assert printed.err.splitlines()[-2].startswith(f"error: {fleet_ci_time.DOCUMENT_PATH} is not writable · [Errno ")


def test_an_unwritable_step_summary_leaves_no_document(capsys, monkeypatch, tmp_path, public):
    """FLEET-47, FLEET-110: an unwritable step summary ends the report before the document is written."""
    folder, listing = fleet_of_two(tmp_path, public)
    summary = tmp_path / "a-directory"
    summary.mkdir()
    printed = report_in(
        folder, capsys, monkeypatch, ticks(*range(8)), words=("--ci-time-json",),
        environment={"GITHUB_STEP_SUMMARY": str(summary)}, fetch_listing=listing,
    )
    assert printed.code == 2
    assert not (folder / fleet_ci_time.DOCUMENT_PATH).exists()


def test_a_red_report_still_writes_the_document_and_a_repository_not_measured_by_size_keeps_its_ci_time(capsys, monkeypatch, tmp_path, public, missing):
    """FLEET-110: a repository that cannot be cloned makes the report exit 2 yet the document is written, its row
    following its CI-time reading."""
    alpha = public("alpha", {"a.py": "a = 1\n"})
    listing = Listing({}, runs={missing: slow_week()})
    folder = write_fleet(tmp_path / "fleet", [alpha, missing])
    printed = report_in(folder, capsys, monkeypatch, ticks(*range(8)), words=("--ci-time-json",), fetch_listing=listing)
    assert printed.code == 2
    rows = json.loads((folder / fleet_ci_time.DOCUMENT_PATH).read_text(encoding="utf-8"))["repositories"]
    assert [(entry["name"], entry["measured"], entry["breach"]) for entry in rows] == [(alpha, False, False), (missing, True, True)]


def test_a_private_repository_is_in_no_row_of_the_document(capsys, monkeypatch, tmp_path, public):
    """FLEET-110: the public document names no private repository."""
    public("alpha", {"a.py": "a = 1\n"})
    listing = Listing({"o": listed(("o/alpha",), ("o/hidden-name", "private"))})
    folder = write_organizations(tmp_path / "fleet", ["o"])
    printed = report_in(folder, capsys, monkeypatch, ticks(*range(8)), words=("--ci-time-json",), fetch_listing=listing)
    assert printed.code == 0, printed.err
    assert [entry["name"] for entry in json.loads((folder / fleet_ci_time.DOCUMENT_PATH).read_text(encoding="utf-8"))["repositories"]] == ["o/alpha"]


def clone_pair_table(printed: Printed) -> list[str]:
    """The lines of the table under `Clone pairs between repositories`, from the rest of its heading, up to the next table."""
    return printed.out.split("**Clone pairs between repositories")[1].split("**Tests per layer**")[0].rstrip().splitlines()


LOOPING = (
    "def moved(items):\n    for item in items:\n        if item:\n            items.append(item)\n        else:\n"
    "            items.remove(item)\n    while items:\n        items.pop()\n        items.sort()\n"
    "    return [entry for entry in items if entry]\n"
)
COPIED = {
    "same path": ("src/shared.py", "src/shared.py", "prod"),
    "tests": ("tests/test_a.py", "tests/test_b.py", "test"),
    "test copying production": ("src/shared.py", "tests/test_b.py", "prod"),
}


@pytest.mark.parametrize(("first", "second", "kind"), COPIED.values(), ids=COPIED.keys())
def test_a_function_copied_between_two_repositories_is_one_row_naming_both_with_its_kind(
    capsys, monkeypatch, tmp_path, public, first, second, kind
):
    """FLEET-113, FLEET-115, FLEET-117: one row names both repositories and paths, sides sorted; `Kind` is test only when both sides are tests."""
    alpha = public("alpha", {first: function("copied", "value", "local")})
    beta = public("beta", {second: function("copied", "other", "kept")})
    printed = report_in(write_fleet(tmp_path / "fleet", [beta, alpha]), capsys, monkeypatch, ticks(*range(8)))
    assert printed.code == 0, printed.err
    assert clone_pair_table(printed) == [
        " · 1 pair**",
        "",
        "| Repo | Function | Repo | Function | Kind |",
        "|---|---|---|---|---|",
        f"| {alpha} | {first.replace('_', chr(92) + '_')} copied | {beta} | {second.replace('_', chr(92) + '_')} copied | {kind} |",
    ]


def test_production_pairs_stand_before_test_pairs(capsys, monkeypatch, tmp_path, public):
    """FLEET-115: `prod` rows first, then by sides."""
    files = {"tests/test_a.py": function("copied", "value", "local"), "src/b.py": LOOPING}
    alpha, beta = public("alpha", files), public("beta", files)
    printed = report_in(write_fleet(tmp_path / "fleet", [alpha, beta]), capsys, monkeypatch, ticks(*range(8)))
    assert [line.rsplit(" | ", 1)[1] for line in clone_pair_table(printed)[4:]] == ["prod |", "test |"]


def test_a_copy_inside_one_repository_stays_out_of_the_table_and_in_its_own_cell(capsys, monkeypatch, tmp_path, public):
    """FLEET-114: the pair of one repository counts in its `Clone pairs prod / test` cell only."""
    alpha = public("alpha", {"a.py": function("copied", "value", "local"), "b.py": function("copied", "value", "local")})
    printed = report_in(write_fleet(tmp_path / "fleet", [alpha]), capsys, monkeypatch, ticks(*range(8)))
    assert row(printed.out, alpha)[-2] == "1 / 0"
    assert clone_pair_table(printed)[0] == " · 0 pairs**"
    assert len(clone_pair_table(printed)) == 4


def test_a_repository_without_the_copy_or_not_measured_leaves_no_row(capsys, monkeypatch, tmp_path, public, missing):
    """FLEET-116: a drifted copy and a repository that cannot be cloned add no pair; the heading reads `0 pairs`."""
    alpha = public("alpha", {"a.py": function("copied", "value", "local")})
    beta = public("beta", {"a.py": function("copied", "value", "local", assignments=1, passes=20)})
    printed = report_in(write_fleet(tmp_path / "fleet", [alpha, beta, missing]), capsys, monkeypatch, ticks(*range(10)))
    assert printed.code == 2
    assert clone_pair_table(printed) == [" · 0 pairs**", "", "| Repo | Function | Repo | Function | Kind |", "|---|---|---|---|---|"]


def test_markdown_in_a_path_of_a_pair_stays_text(capsys, monkeypatch, tmp_path, public):
    """FLEET-44: a path with markdown syntax is escaped in the pair table."""
    alpha = public("alpha", {"<!--x.py": function("copied", "value", "local")})
    beta = public("beta", {"<!--x.py": function("copied", "value", "local")})
    printed = report_in(write_fleet(tmp_path / "fleet", [alpha, beta]), capsys, monkeypatch, ticks(*range(8)))
    assert clone_pair_table(printed)[4].startswith(f"| {alpha} | \\<!--x.py copied | {beta} |")


@pytest.mark.parametrize(
    ("beta_reached_at", "clone_bounds", "exit_code", "not_measured"),
    [(721, [300], 2, True), (720, [300], 2, True), (719, [300, 1], 0, False)],
    ids=["overdrawn", "none left", "one second left"],
)
def test_a_clone_starts_only_within_the_reports_deadline_and_ends_at_it(
    capsys, monkeypatch, tmp_path, public, beta_reached_at, clone_bounds, exit_code, not_measured
):
    """FLEET-126: no clone starts once the 720 seconds are spent, the row says why and the exit is 2; a clone in
    flight is bound by the seconds left when it is smaller than the clone's own bound."""
    alpha, beta = public("alpha", {"a.py": "a = 1\n"}), public("beta", {"b.py": "b = 1\n"})
    bounds, clone = [], fleet.git

    def recording(arguments, scratch, environment, timeout):
        bounds.append(timeout)
        return clone(arguments, scratch, environment, fleet.CLONE_TIMEOUT_SECONDS)

    monkeypatch.setattr(fleet, "git", recording)
    ticks_read = ticks(0, 1, 2, beta_reached_at, beta_reached_at + 1, beta_reached_at + 2)
    printed = report_in(write_fleet(tmp_path / "fleet", [alpha, beta]), capsys, monkeypatch, ticks_read)
    assert bounds == clone_bounds
    beta_row = next(line for line in printed.err.splitlines() if beta in line)
    assert printed.code == exit_code
    assert ("not measured" in beta_row) == not_measured
    assert beta_row.endswith("the report's 720 seconds are spent") == not_measured


def test_an_exception_inside_the_ci_time_read_reaches_the_report(capsys, monkeypatch, tmp_path, public):
    """FLEET-125: the worker thread's failure is the report's failure, not a silent missing table."""
    alpha = public("alpha", {"a.py": "a = 1\n"})

    def broken(url, headers, unreadable):
        raise RuntimeError("the read broke")

    fleet_file, ticks_read = write_fleet(tmp_path / "fleet", [alpha]), ticks(*range(8))

    with pytest.raises(RuntimeError, match="the read broke"):
        report_in(fleet_file, capsys, monkeypatch, ticks_read, fetch_listing=broken)
