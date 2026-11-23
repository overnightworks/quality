"""The weekly CI time of a repository: its runs read from GitHub, grouped by head commit and counted."""

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta, timezone
from email.message import Message
from itertools import count

import pytest

from quality.deadline import RunDeadline
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
from tests.support.clock import Clock

TODAY = date(2026, 11, 21)
START = datetime(2026, 11, 20, 10, tzinfo=UTC)
DEFAULT_BRANCH = "trunk"
WINDOW_START = datetime(2026, 11, 14, tzinfo=UTC)
WINDOW_START_SECONDS = int((WINDOW_START - START).total_seconds())
SEARCH_RESULT_LIMIT = 1000
PAGE_SIZE = 100
fresh_ids = count(1_000_000)


def run(sha, created=0, updated=60, event="pull_request", status="completed", conclusion="success", branch="feature", run_id=None, attempt=1):
    """A workflow run of `sha` on `branch` whose moments are seconds after START; without a `run_id` it gets its own."""
    return {
        "id": next(fresh_ids) if run_id is None else run_id,
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
    """A fake runs listing as GitHub documents it: per repository the runs newest first by creation and id, 100 to a page,
    or a failure; a url with a `created` filter returns at most 1,000 results and `total_count` 0 past them. Every url it
    was asked."""

    served: dict[str, list[dict] | Exception]
    jobs: dict[int, list[dict]] = field(default_factory=dict)
    urls: list[str] = field(default_factory=list)
    clock: Clock = field(default_factory=Clock)
    seconds_per_call: float = 0.0

    def __call__(self, url: str, headers: Mapping[str, str], unreadable: Callable[[Exception], CannotJudge]) -> tuple[bytes, Message]:
        self.urls.append(url)
        self.clock.now += self.seconds_per_call
        name = url.split("/repos/")[1].split("/actions")[0]
        runs = self.served[name]
        if isinstance(runs, Exception):
            raise unreadable(runs)
        if "/jobs?" in url:
            entries = self.jobs.get(int(url.split("/runs/")[1].split("/jobs")[0]), [])
            return json.dumps({"total_count": len(entries), "jobs": entries}).encode(), Message()
        page = int(url.split("&page=")[1])
        newest_first = sorted(runs, key=lambda entry: (entry["created_at"], entry["id"]), reverse=True)
        first = (page - 1) * PAGE_SIZE
        last = page * PAGE_SIZE
        if "created=" in url and first >= SEARCH_RESULT_LIMIT:
            body = {"total_count": 0, "workflow_runs": []}
        else:
            reachable = newest_first[:SEARCH_RESULT_LIMIT] if "created=" in url else newest_first
            body = {"total_count": len(newest_first), "workflow_runs": reachable[first:last]}
        return json.dumps(body).encode(), Message()


def read(served, *names, jobs=None, seconds_per_call=0.0, seconds_left=1e9):
    """The read under a deadline `seconds_left` after the start, every request costing `seconds_per_call` on the clock."""
    clock = Clock()
    fetch = Runs(served, jobs or {}, clock=clock, seconds_per_call=seconds_per_call)
    return read_fleet_ci_time(names or tuple(served), TODAY, "", fetch, DEFAULT_BRANCH, RunDeadline(seconds_left, clock)), fetch


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
    served = {f"o/r{number}": [run(f"c{number}-{page}") for page in range(30 * 100 - 1)] for number in range(CALLS_PER_REPORT // 30 + 1)}
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

    reading = read_fleet_ci_time(("o/a", "o/b"), TODAY, "", fetch, DEFAULT_BRANCH, RunDeadline(1e9, Clock()))
    assert (reading.rows["o/a"], reading.rows["o/b"].commits) == (PartlyRead("cannot read CI time"), 1)


def test_a_run_without_its_moments_cannot_be_read():
    broken = {**run("x"), "created_at": None}
    assert weekly({"o/a": [broken]}) == PartlyRead("cannot read CI time")


@pytest.mark.parametrize(("seconds_from_start", "commits"), [(0, 1), (-1, 0)], ids=["at the window start", "a second before"])
def test_a_run_counts_from_midnight_seven_days_before_the_report_day(seconds_from_start, commits):
    """FLEET-105, FLEET-128: a run created at the window's start counts, one created a second earlier does not."""
    assert weekly({"o/a": [run("x", created=WINDOW_START_SECONDS + seconds_from_start)]}).commits == commits


def runs_listing_calls(fetch):
    return [url for url in fetch.urls if "/jobs?" not in url]


def test_a_window_of_more_than_a_thousand_runs_is_counted_whole():
    """FLEET-128: a window of 1,235 runs is counted whole, past the 1,000 results a filtered listing returns."""
    runs = [run(f"c{number}", created=-number) for number in range(1235)]
    assert weekly({"o/a": runs}).commits == 1235


def test_a_run_that_a_new_run_pushes_onto_the_next_page_counts_once():
    """FLEET-129: a run listed on two pages counts once."""
    shifted = run("c99", created=-100, run_id=1, attempt=2)
    served = {"o/a": [run(f"c{number}", created=-number - 1) for number in range(99)] + [shifted] + [run(f"c{number}", created=-number - 1) for number in range(100, 150)]}
    listing = Runs(served)

    def fetch(url, headers, unreadable):
        answer = listing(url, headers, unreadable)
        if "&page=1" in url:
            served["o/a"].append(run("newest", created=1000))
            served["o/a"][served["o/a"].index(shifted)] = run("c99", created=-100, run_id=1, attempt=2, conclusion="failure")
        return answer

    reading = read_fleet_ci_time(("o/a",), TODAY, "", fetch, DEFAULT_BRANCH, RunDeadline(1e9, Clock()))
    counted = reading.rows["o/a"]
    assert (counted.commits, counted.reruns, counted.green_on_rerun, counted.red) == (150, 1, 0, RedShare(1, 150))


def test_the_read_stops_at_the_first_page_whose_oldest_run_predates_the_window():
    """FLEET-128: the runs before the window are not counted and no page past that one is read."""
    in_window = [run(f"in{number}", created=-number) for number in range(150)]
    older = [run(f"old{number}", created=WINDOW_START_SECONDS - 1 - number) for number in range(300)]
    reading, fetch = read({"o/a": in_window + older})
    assert (reading.rows["o/a"].commits, len(runs_listing_calls(fetch))) == (150, 2)


def test_a_page_of_runs_of_an_uncounted_event_older_than_the_window_stops_the_read():
    """FLEET-128: the oldest run of any event decides the stop."""
    in_window = [run(f"in{number}", created=-number) for number in range(30)]
    scheduled = [run(f"old{number}", created=WINDOW_START_SECONDS - 1 - number, event="schedule") for number in range(170)]
    reading, fetch = read({"o/a": in_window + scheduled})
    assert (reading.rows["o/a"].commits, len(runs_listing_calls(fetch))) == (30, 1)


def test_a_listing_that_ends_short_of_its_total_is_partly_read_and_the_report_is_not_complete():
    """FLEET-130: the pages read are named and `complete` is false."""
    page = {"total_count": 150, "workflow_runs": [run(f"c{number}", created=-number) for number in range(40)]}
    reading = read_fleet_ci_time(("o/a",), TODAY, "", lambda *_: (json.dumps(page).encode(), Message()), DEFAULT_BRANCH, RunDeadline(1e9, Clock()))
    assert reading.rows["o/a"] == PartlyRead("1 page read")
    assert json.loads(document(reading, datetime.combine(TODAY, time(1), UTC)))["complete"] is False


def test_an_uncounted_event_without_its_creation_moment_cannot_be_read():
    """FLEET-128: the stop reads every entry's `created_at`, whatever its event."""
    assert weekly({"o/a": [{**run("x", event="schedule"), "created_at": None}]}) == PartlyRead("cannot read CI time")


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
    names = [f"o/r{number}" for number in range(61)]
    served = {"o/empty": [], **{name: [run(f"{name}-{number}", created=number, run_id=number) for number in range(30)] for name in names}}
    _, fetch = read(served)
    job_calls = [sum(f"/repos/{name}/" in url and "/jobs?" in url for url in fetch.urls) for name in names]
    assert job_calls[:10] == [(CALLS_PER_REPORT - len(served)) // len(names)] * 10


def test_the_deadline_stops_a_runs_read_and_the_repository_is_partly_read_with_the_pages_it_read():
    served = {"o/a": [run(f"c{number}", run_id=number) for number in range(5 * 100)], "o/b": [run("late")]}
    reading, fetch = read(served, seconds_per_call=4, seconds_left=10)
    assert reading.rows == {"o/a": PartlyRead("3 pages read"), "o/b": PartlyRead("0 pages read")}
    assert (reading.calls, len(fetch.urls)) == (3, 3)
    assert not json.loads(document(reading, START))["complete"]


def test_a_jobs_sample_the_deadline_stops_before_its_first_call_is_not_sampled_for_the_time_limit():
    reading, fetch = read({"o/a": [run("x", run_id=1)]}, seconds_per_call=5, seconds_left=5)
    assert (table_cells("o/a", reading)[-3:], reading.calls, len(fetch.urls)) == (("not sampled · time limit",) * 3, 1, 1)


def test_a_jobs_sample_the_deadline_stops_midway_shows_the_runs_it_read():
    runs = [run(f"c{number}", created=number, run_id=number) for number in range(30)]
    jobs = {number: [job("tests")] for number in range(30)}
    reading, fetch = read({"o/a": runs}, jobs=jobs, seconds_per_call=3, seconds_left=8)
    assert (reading.jobs["o/a"].runs, reading.calls, len(fetch.urls)) == (2, 3, 3)


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
def test_the_document_window_starts_where_the_runs_count_starts_whatever_the_clock_zone(zone):
    """FLEET-106: `window_start` is the instant from which a run counts, taken from the report's day and not from the
    instant or the zone the document is made in."""
    reading, _ = read({"o/a": []})
    edge = datetime.fromisoformat(json.loads(document(reading, datetime.combine(TODAY, time(0, 30), zone)))["window_start"])
    seconds = int((edge - START).total_seconds())
    assert (weekly({"o/a": [run("at", created=seconds)]}).commits, weekly({"o/a": [run("before", created=seconds - 1)]}).commits) == (1, 0)
