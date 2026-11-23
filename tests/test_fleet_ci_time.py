"""The weekly CI time of a repository: its runs read from GitHub, grouped by head commit and counted."""

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta, timezone
from email.message import Message
from urllib.parse import unquote

import pytest

from quality.fleet_ci_time import (
    CALLS_PER_REPORT,
    FleetCiTime,
    PartlyRead,
    RedShare,
    WeeklyCiTime,
    document,
    read_fleet_ci_time,
    table_cells,
)
from quality.report import CannotJudge

TODAY = date(2026, 11, 21)
START = datetime(2026, 11, 20, 10, tzinfo=UTC)
DEFAULT_BRANCH = "trunk"


def run(sha, created=0, updated=60, event="pull_request", status="completed", conclusion="success", branch="feature", run_id=1, attempt=1):
    """A workflow run of `sha` on `branch` whose moments are seconds after START."""
    return {
        "id": run_id,
        "head_branch": branch,
        "head_sha": sha,
        "event": event,
        "status": status,
        "conclusion": conclusion,
        "run_attempt": attempt,
        "created_at": (START + timedelta(seconds=created)).isoformat(),
        "updated_at": (START + timedelta(seconds=updated)).isoformat(),
    }


@dataclass
class Runs:
    """A fake runs listing: per repository the runs it serves 100 to a page, or a failure; every url it was asked."""

    served: dict[str, list[dict] | Exception]
    jobs: dict[int, list[dict]] = field(default_factory=dict)
    urls: list[str] = field(default_factory=list)

    def __call__(self, url: str, headers: Mapping[str, str], unreadable: Callable[[Exception], CannotJudge]) -> tuple[bytes, Message]:
        self.urls.append(url)
        name = url.split("/repos/")[1].split("/actions")[0]
        runs = self.served[name]
        if isinstance(runs, Exception):
            raise unreadable(runs)
        if "/jobs?" in url:
            entries = self.jobs.get(int(url.split("/runs/")[1].split("/jobs")[0]), [])
            return json.dumps({"total_count": len(entries), "jobs": entries}).encode(), Message()
        page = int(url.split("&page=")[1])
        body = {"total_count": len(runs), "workflow_runs": runs[(page - 1) * 100 : page * 100]}
        return json.dumps(body).encode(), Message()


def read(served, *names, jobs=None):
    fetch = Runs(served, jobs or {})
    return read_fleet_ci_time(names or tuple(served), TODAY, "", fetch, DEFAULT_BRANCH), fetch


def weekly(served, name="o/a"):
    reading, _ = read(served)
    return reading.rows[name]


def test_the_runs_of_a_commit_count_from_the_earliest_creation_to_the_latest_update():
    counted = weekly({"o/a": [run("x", created=0, updated=100), run("x", created=30, updated=500, event="push")]})
    assert counted == WeeklyCiTime(commits=1, median=timedelta(seconds=500), p90=timedelta(seconds=500), red=RedShare(0, 1), red_on_main=RedShare(0, 0), reruns=0, green_on_rerun=0)


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
    assert (counted.commits, counted.red) == (3, RedShare(1, 3))


@pytest.mark.parametrize(
    ("runs", "reruns", "green"),
    [
        pytest.param([run("x")], 0, 0, id="a first attempt is no re-run"),
        pytest.param([run("x", attempt=2, conclusion="success")], 1, 1, id="a re-run that ends green"),
        pytest.param([run("x", attempt=3, conclusion="failure")], 1, 0, id="a re-run that ends red"),
        pytest.param([run("x", attempt=2, status="in_progress", conclusion=None)], 0, 0, id="a re-run still in progress is not counted"),
        pytest.param([run("x", attempt=2, event="schedule")], 0, 0, id="an event no commit counts is not counted"),
    ],
)
def test_re_runs_and_those_green_on_the_latest_attempt_count_over_the_finished_runs(runs, reruns, green):
    counted = weekly({"o/a": runs})
    assert (counted.reruns, counted.green_on_rerun) == (reruns, green)


@pytest.mark.parametrize("attempt", ["2", None, True, 1.5])
def test_a_run_attempt_that_is_not_an_integer_cannot_be_read(attempt):
    assert weekly({"o/a": [run("x", attempt=attempt)]}) == PartlyRead("cannot read CI time")


def test_a_repository_without_counted_commits_reads_zero_commits():
    assert weekly({"o/a": []}) == WeeklyCiTime(commits=0, median=None, p90=None, red=RedShare(0, 0), red_on_main=RedShare(0, 0), reruns=0, green_on_rerun=0)


def test_a_repository_with_many_pages_is_read_whole_within_the_call_cap():
    runs = [run(f"c{number}") for number in range(25 * 100 + 1)]
    reading, fetch = read({"o/a": runs})
    pages = [url for url in fetch.urls if "/jobs?" not in url]
    assert (reading.rows["o/a"].commits, len(pages)) == (2501, 26)


def test_the_calls_of_the_whole_report_are_capped_and_the_repository_it_stops_is_partly_read():
    served = {f"o/r{number}": [run(f"c{number}-{page}") for page in range(30 * 100)] for number in range(CALLS_PER_REPORT // 30 + 1)}
    served["o/r0"] = [run("only")]
    reading, fetch = read(served)
    assert (reading.calls, len(fetch.urls)) == (CALLS_PER_REPORT, CALLS_PER_REPORT)
    assert reading.rows[f"o/r{CALLS_PER_REPORT // 30}"] == PartlyRead(f"{29} pages read")
    assert table_cells("o/r0", reading)[-3:] == ("not sampled · call cap",) * 3


def test_a_run_without_a_head_branch_counts_in_neither_red_cell_of_the_default_branch_and_does_not_hide_the_repository():
    counted = weekly({"o/a": [run("x", event="push", branch=None, conclusion="failure"), run("y", event="push", branch=DEFAULT_BRANCH)]})
    assert (counted.commits, counted.red_on_main) == (2, RedShare(0, 1))


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

    reading = read_fleet_ci_time(("o/a", "o/b"), TODAY, "", fetch, DEFAULT_BRANCH)
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


def test_the_red_share_splits_into_pull_request_commits_and_push_commits_on_main_each_over_its_own_population():
    runs = [
        run("pr-red", conclusion="failure"),
        run("pr-green"),
        run("main-red", event="push", branch=DEFAULT_BRANCH, conclusion="failure"),
        run("main-green", event="push", branch=DEFAULT_BRANCH),
        run("main-green-too", event="push", branch=DEFAULT_BRANCH),
        run("side-red", event="push", branch="feature", conclusion="failure"),
    ]
    counted = weekly({"o/a": runs})
    assert (counted.commits, counted.red, counted.red_on_main) == (6, RedShare(1, 2), RedShare(1, 3))


def test_a_pull_request_run_red_on_a_commit_whose_push_on_main_is_green_is_red_in_the_pull_request_share_only():
    counted = weekly({"o/a": [run("x", conclusion="failure"), run("x", event="push", branch=DEFAULT_BRANCH)]})
    assert (counted.red, counted.red_on_main) == (RedShare(1, 1), RedShare(0, 1))


def job(name, queued=0, ran=60, conclusion="success"):
    return {"name": name, "status": "completed", "conclusion": conclusion, **moments(queued, ran)}


def moments(queued, ran):
    stamp = lambda seconds: (START + timedelta(seconds=seconds)).isoformat()
    return {"created_at": stamp(0), "started_at": stamp(queued), "completed_at": stamp(queued + ran)}


def test_the_jobs_of_a_sample_of_runs_spread_over_the_window_give_the_queue_p90_and_the_jobs_above_twelve_minutes():
    runs = [run(f"c{number}", created=number * 86400 // 10, run_id=number) for number in range(10)]
    jobs = {number: [job("tests", queued=number, ran=800), job("lint", queued=0, ran=30)] for number in range(10)}
    reading, fetch = read({"o/a": runs}, jobs=jobs)
    sampled = reading.jobs["o/a"]
    assert (sampled.runs, sampled.queue_p90.total_seconds(), [(name, wall.total_seconds()) for name, wall in sampled.over_limit]) == (10, 7, [("tests", 800)])
    assert [url.split("/runs/")[1].split("/jobs")[0] for url in fetch.urls if "/jobs?" in url] == [str(number) for number in range(10)]


def test_a_repository_without_runs_takes_no_share_of_the_calls_left_for_jobs():
    names = [f"o/r{number}" for number in range(31)]
    served = {"o/empty": [], **{name: [run(f"{name}-{number}", created=number, run_id=number) for number in range(30)] for name in names}}
    _, fetch = read(served)
    job_calls = [sum(f"/repos/{name}/" in url and "/jobs?" in url for url in fetch.urls) for name in names]
    assert job_calls[:20] == [(CALLS_PER_REPORT - len(served)) // len(names)] * 20


def counted_week(seconds: int, commits: int = 12) -> WeeklyCiTime:
    wall = timedelta(seconds=seconds)
    return WeeklyCiTime(commits, wall, wall, RedShare(0, 0), RedShare(0, 0), 0, 0)


def quiet_week() -> WeeklyCiTime:
    return WeeklyCiTime(0, None, None, RedShare(0, 0), RedShare(0, 0), 0, 0)


def written_text(rows: dict[str, WeeklyCiTime | PartlyRead]) -> str:
    return document(FleetCiTime(datetime(2026, 11, 14, tzinfo=UTC), rows, {}, 0), datetime(2026, 11, 21, 5, 17, 42, tzinfo=UTC))


def written(rows: dict[str, WeeklyCiTime | PartlyRead]) -> dict:
    return json.loads(written_text(rows))


@pytest.mark.parametrize(
    ("reading", "expected"),
    [
        (counted_week(660), {"median_seconds": 660, "commits_sampled": 12, "measured": True, "breach": True}),
        (counted_week(600), {"median_seconds": 600, "commits_sampled": 12, "measured": True, "breach": False}),
        (counted_week(599), {"median_seconds": 599, "commits_sampled": 12, "measured": True, "breach": False}),
        (counted_week(601, commits=1), {"median_seconds": 601, "commits_sampled": 1, "measured": True, "breach": True}),
        (PartlyRead("29 pages read"), {"median_seconds": 0, "commits_sampled": 0, "measured": False, "breach": False}),
        (PartlyRead("cannot read CI time"), {"median_seconds": 0, "commits_sampled": 0, "measured": False, "breach": False}),
        (quiet_week(), {"median_seconds": 0, "commits_sampled": 0, "measured": False, "breach": False}),
    ],
    ids=["above the limit", "exactly the limit", "below the limit", "one counted commit", "cut by the cap", "unreadable", "no counted commit"],
)
def test_a_repository_is_a_row_of_numbers_and_breaches_only_above_ten_minutes(reading, expected):
    """FLEET-107: a measured repository carries its median and commits and breaches when the median is strictly above
    600 seconds; one the cap or a failed read left unmeasured, or with no counted commit, is 0, 0, false, false."""
    assert written({"o/a": reading})["repositories"] == [{"name": "o/a", **expected}]


def test_the_document_holds_the_agreed_keys_in_order_and_flat_values_only():
    """FLEET-106: schema 1, the window as RFC 3339 UTC instants ending at the generation moment, the limit, the
    completeness and one row per repository in the order of the report; nothing nested, no reason text."""
    rows = {"o/a": counted_week(660), "o/b": PartlyRead("cannot read CI time"), "o/c": counted_week(30)}
    text = written_text(rows)
    parsed = json.loads(text)
    assert list(parsed) == ["schema", "generated_at", "window_start", "window_end", "limit_seconds", "complete", "repositories"]
    assert (parsed["schema"], parsed["limit_seconds"]) == (1, 600)
    assert (parsed["generated_at"], parsed["window_end"], parsed["window_start"]) == ("2026-11-21T05:17:42Z", "2026-11-21T05:17:42Z", "2026-11-14T00:00:00Z")
    assert [list(entry) for entry in parsed["repositories"]] == [["name", "median_seconds", "commits_sampled", "measured", "breach"]] * 3
    assert [entry["name"] for entry in parsed["repositories"]] == ["o/a", "o/b", "o/c"]
    assert all(isinstance(value, str | int | bool) for entry in parsed["repositories"] for value in entry.values())
    assert text.endswith("}\n")
    assert "cannot read" not in text


@pytest.mark.parametrize(
    ("rows", "complete"),
    [
        ({"o/a": counted_week(30), "o/b": quiet_week()}, True),
        ({"o/a": counted_week(30), "o/b": PartlyRead("29 pages read")}, False),
        ({"o/a": counted_week(30), "o/b": PartlyRead("cannot read CI time")}, False),
    ],
    ids=["a quiet repository", "cut by the cap", "unreadable"],
)
def test_the_document_is_complete_unless_a_repository_is_partly_read(rows, complete):
    """FLEET-108: `complete` is false when any repository's CI time was cut by the cap or could not be read; zero counted
    commits leave it true."""
    assert written(rows)["complete"] is complete


@pytest.mark.parametrize("zone", [UTC, timezone(timedelta(hours=-10)), timezone(timedelta(hours=13))], ids=["UTC", "ten hours behind", "thirteen hours ahead"])
def test_the_document_window_starts_where_the_runs_query_starts_whatever_the_clock_zone(zone):
    """FLEET-106: `window_start` is the midnight UTC the runs query's `created>=` bound names, taken from the report's
    day and not from the instant or the zone the document is made in."""
    reading, fetch = read({"o/a": [run("c1")]})
    text = document(reading, datetime.combine(TODAY, time(0, 30), zone))
    bound = unquote(fetch.urls[0].split("created=")[1].split("&")[0]).removeprefix(">=")
    assert json.loads(text)["window_start"] == f"{bound}T00:00:00Z"
