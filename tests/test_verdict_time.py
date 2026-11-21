"""The verdict's time row: the run's CI time, or `not measured` where a stamp is missing, between the jobs and the
loosenings."""

import json

import pytest

from quality import cli


def timed_entry(job_id, name, attempt, created, started, completed):
    """A finished job of the run's listing whose moments are seconds after 10:00:00 UTC; None leaves the stamp out."""

    def stamp(seconds):
        return None if seconds is None else f"2026-11-21T10:{seconds // 60:02d}:{seconds % 60:02d}Z"

    entry = {"id": job_id, "name": name, "run_attempt": attempt, "status": "completed", "conclusion": "success"}
    stamps = {"created_at": created, "started_at": started, "completed_at": completed}
    return entry | {key: stamp(seconds) for key, seconds in stamps.items() if seconds is not None}


@pytest.mark.parametrize(
    ("entries", "row"),
    [
        pytest.param(
            [timed_entry(1, "checks", 1, 0, 2, 30), timed_entry(2, "quality / sonar", 1, 5, 7, 62)],
            "· time measured 1m02s wall · slowest quality / sonar 55s · longest wait 2s",
            id="one attempt",
        ),
        pytest.param(
            [timed_entry(1, "checks", 1, 0, 1, 400), timed_entry(2, "checks", 2, 500, 503, 530)],
            "· time measured 8m50s wall · slowest checks 27s · longest wait 3s",
            id="a re-run counts from its first attempt",
        ),
        pytest.param([timed_entry(1, "checks", 1, 0, None, 30)], "· time not measured GitHub gave no timestamps", id="a missing timestamp"),
        pytest.param(
            [timed_entry(1, "checks", 1, 0, 2, 30), timed_entry(2, "quality / sonar", 1, 5, 7, 62) | {"completed_at": "garbage"}],
            "· time not measured GitHub gave no timestamps",
            id="an unparseable completed_at",
        ),
    ],
)
def test_the_verdict_prints_its_time_row_before_the_loosenings_and_the_exit_stays(capsys, run_env, entries, row):
    """VERDICT-74, VERDICT-75: the time row of the run's CI, or `not measured` where a stamp is missing; the exit is the jobs'."""
    page = json.dumps({"total_count": len(entries), "jobs": entries})
    assert cli.main(["verdict"], fetcher_for=lambda token: lambda url: (page, None)) == 0
    rows = [" ".join(line.split()) for line in capsys.readouterr().out.splitlines()]
    assert rows[rows.index(row) + 1].startswith("✓ green")


@pytest.mark.parametrize(("conclusion", "exit_code"), [("success", 0), ("failure", 1)])
def test_the_time_row_stands_between_the_jobs_and_the_loosenings_in_print_and_summary_and_leaves_the_exit(
    capsys, run_env, pull_request, conclusion, exit_code
):
    """VERDICT-74, VERDICT-75: after the jobs, before the loosenings, in the step summary too; the exit stays the jobs'."""
    _, commit_base = pull_request
    commit_base(None)
    entry = timed_entry(1, "checks", 1, 0, 2, 30) | {"conclusion": conclusion}
    page = json.dumps({"total_count": 1, "jobs": [entry]})
    assert cli.main(["verdict"], fetcher_for=lambda token: lambda url: (page, None)) == exit_code
    printed = capsys.readouterr().out
    _, summary = run_env
    for text in (printed, summary.read_text()):
        words = [line.split()[1] for line in text.splitlines() if line.startswith("  ")]
        at = words.index("time")
        assert words[at - 1] not in {"release", "base"} and words[at + 1] == "loosenings"
