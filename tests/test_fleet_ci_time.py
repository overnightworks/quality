"""The weekly CI time of a repository: its runs read from GitHub, grouped by head commit and counted."""

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from email.message import Message

import pytest

from quality.fleet_ci_time import (
    CALLS_PER_REPORT,
    PAGES_PER_REPOSITORY,
    PartlyRead,
    WeeklyCiTime,
    read_fleet_ci_time,
)
from quality.report import CannotJudge

TODAY = date(2026, 11, 21)
START = datetime(2026, 11, 20, 10, tzinfo=UTC)


def run(sha, created=0, updated=60, event="pull_request", status="completed", conclusion="success"):
    """A workflow run of `sha` whose moments are seconds after START."""
    return {
        "head_sha": sha,
        "event": event,
        "status": status,
        "conclusion": conclusion,
        "created_at": (START + timedelta(seconds=created)).isoformat(),
        "updated_at": (START + timedelta(seconds=updated)).isoformat(),
    }


@dataclass
class Runs:
    """A fake runs listing: per repository the runs it serves 100 to a page, or a failure; every url it was asked."""

    served: dict[str, list[dict] | Exception]
    urls: list[str] = field(default_factory=list)

    def __call__(self, url: str, headers: Mapping[str, str], unreadable: Callable[[Exception], CannotJudge]) -> tuple[bytes, Message]:
        self.urls.append(url)
        name = url.split("/repos/")[1].split("/actions")[0]
        runs = self.served[name]
        if isinstance(runs, Exception):
            raise unreadable(runs)
        page = int(url.split("&page=")[1])
        body = {"total_count": len(runs), "workflow_runs": runs[(page - 1) * 100 : page * 100]}
        return json.dumps(body).encode(), Message()


def read(served, *names):
    fetch = Runs(served)
    return read_fleet_ci_time(names or tuple(served), TODAY, "", fetch), fetch


def weekly(served, name="o/a"):
    reading, _ = read(served)
    return reading.rows[name]


def test_the_runs_of_a_commit_count_from_the_earliest_creation_to_the_latest_update():
    counted = weekly({"o/a": [run("x", created=0, updated=100), run("x", created=30, updated=500, event="push")]})
    assert counted == WeeklyCiTime(commits=1, median=timedelta(seconds=500), p90=timedelta(seconds=500), red=0)


def test_median_and_p90_are_the_nearest_rank_over_the_commit_walls():
    runs = [run(f"c{wall}", updated=wall) for wall in range(10, 110, 10)]
    counted = weekly({"o/a": runs})
    assert (counted.commits, counted.median.total_seconds(), counted.p90.total_seconds()) == (10, 50, 90)


def test_a_commit_with_a_run_in_progress_or_only_cancelled_runs_is_left_out():
    runs = [
        run("busy", status="in_progress", conclusion=None),
        run("busy", updated=900),
        run("gone", conclusion="cancelled"),
        run("mixed", conclusion="cancelled"),
        run("mixed", updated=70),
        run("kept", updated=40),
    ]
    counted = weekly({"o/a": runs})
    assert (counted.commits, counted.median.total_seconds()) == (2, 40)


def test_only_pull_request_and_push_runs_count():
    counted = weekly({"o/a": [run("x", event="schedule", updated=9000), run("y", updated=20)]})
    assert (counted.commits, counted.median.total_seconds()) == (1, 20)


@pytest.mark.parametrize("conclusion", ["failure", "timed_out"])
def test_a_commit_with_a_failed_or_timed_out_run_is_red_and_a_skipped_one_is_not(conclusion):
    counted = weekly({"o/a": [run("x"), run("x", conclusion=conclusion), run("y", conclusion="skipped"), run("z")]})
    assert (counted.commits, counted.red) == (3, 1)


def test_a_repository_without_counted_commits_reads_zero_commits():
    assert weekly({"o/a": []}) == WeeklyCiTime(commits=0, median=None, p90=None, red=0)


def test_a_repository_over_its_page_cap_is_partly_read_with_the_pages_read():
    runs = [run(f"c{number}") for number in range(PAGES_PER_REPOSITORY * 100 + 1)]
    reading, fetch = read({"o/a": runs})
    assert (reading.rows["o/a"], reading.calls, len(fetch.urls)) == (PartlyRead(f"{PAGES_PER_REPOSITORY} pages read"), PAGES_PER_REPOSITORY, PAGES_PER_REPOSITORY)


def test_the_calls_of_the_whole_report_are_capped_and_the_repository_it_stops_is_partly_read():
    served = {f"o/r{number}": [run(f"c{number}-{page}") for page in range(PAGES_PER_REPOSITORY * 100)] for number in range(CALLS_PER_REPORT // PAGES_PER_REPOSITORY + 1)}
    served["o/r0"] = [run("only")]
    reading, fetch = read(served)
    assert (reading.calls, len(fetch.urls)) == (CALLS_PER_REPORT, CALLS_PER_REPORT)
    last = reading.rows[f"o/r{CALLS_PER_REPORT // PAGES_PER_REPOSITORY}"]
    assert last == PartlyRead(f"{PAGES_PER_REPOSITORY - 1} pages read")


@pytest.mark.parametrize("failure", [OSError("down"), ValueError("not json")], ids=["request", "answer"])
def test_a_repository_that_cannot_be_read_is_partly_read_and_the_others_stay(failure):
    served = {"o/a": failure, "o/b": [run("x")]}
    reading, _ = read(served)
    assert (reading.rows["o/a"], reading.rows["o/b"].commits) == (PartlyRead("cannot read CI time"), 1)


@pytest.mark.parametrize(
    "body",
    [
        {"total_count": "many", "workflow_runs": []},
        {"total_count": 1},
        {"total_count": 1, "workflow_runs": ["not an object"]},
        {"total_count": 1, "workflow_runs": [{"event": "push"}]},
    ],
    ids=["total-not-integer", "runs-missing", "run-not-object", "run-missing-fields"],
)
def test_an_answer_of_the_wrong_shape_is_partly_read_and_the_others_stay(body):
    served = {"o/b": [run("x")]}

    def fetch(url, headers, unreadable):
        if "/o/a/" in url:
            return json.dumps(body).encode(), Message()
        return Runs(served)(url, headers, unreadable)

    reading = read_fleet_ci_time(("o/a", "o/b"), TODAY, "", fetch)
    assert (reading.rows["o/a"], reading.rows["o/b"].commits) == (PartlyRead("cannot read CI time"), 1)


def test_a_run_without_its_moments_cannot_be_read():
    broken = {**run("x"), "created_at": None}
    assert weekly({"o/a": [broken]}) == PartlyRead("cannot read CI time")


def test_the_window_starts_seven_days_before_the_report_day():
    _, fetch = read({"o/a": []})
    assert "created=%3E%3D2026-11-14" in fetch.urls[0]


def test_a_request_that_failed_still_counts_as_a_call():
    reading, _ = read({"o/a": OSError("down")})
    assert reading.calls == 1
