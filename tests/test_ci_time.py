from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from quality.ci_time import CiTime, measure, nearest_rank, parsed_moment
from quality.verdict import Job

START = datetime.fromisoformat("2026-11-21T10:00:00+00:00")


def timed(name, attempt=1, created=0, started=0, completed=None, conclusion="success"):
    """A job whose moments are seconds after START; `completed=None` is a job still running."""

    def moment(seconds):
        return None if seconds is None else START + timedelta(seconds=seconds)

    status = "in_progress" if completed is None else "completed"
    return Job(name, attempt, status, None if completed is None else conclusion, 1, moment(created), moment(started), moment(completed))


def seconds(time: CiTime) -> tuple[float, str, float, float]:
    return time.wall.total_seconds(), time.slowest_job, time.slowest.total_seconds(), time.longest_wait.total_seconds()


def test_the_wall_clock_runs_to_the_last_completed_job_and_leaves_the_running_one_out():
    jobs = [timed("a", created=0, started=2, completed=30), timed("b", created=5, started=5, completed=60), timed("verdict", created=60, started=61)]
    assert seconds(measure(jobs)) == (60, "b", 55, 2)


def test_a_re_run_counts_from_its_first_attempt_and_names_the_slowest_job_of_the_newest():
    jobs = [
        timed("a", attempt=1, created=0, started=1, completed=500),
        timed("a", attempt=2, created=600, started=601, completed=640),
        timed("b", attempt=2, created=600, started=602, completed=630),
    ]
    assert seconds(measure(jobs)) == (640, "a", 39, 2)


def chain(time: CiTime) -> tuple[int, float] | None:
    return None if time.chain is None else (time.chain.jobs, time.chain.time.total_seconds())


def test_the_chain_follows_jobs_created_when_another_completed_and_leaves_the_parallel_ones_out():
    jobs = [
        timed("tests", created=0, started=1, completed=100),
        timed("lint", created=0, started=1, completed=20),
        timed("sonar", created=101, started=102, completed=200),
        timed("verdict", created=200, started=201, completed=215),
    ]
    assert chain(measure(jobs)) == (3, 215)


def test_the_chain_of_jobs_that_wait_on_nothing_is_the_last_finished_job_alone():
    jobs = [timed("a", created=0, started=1, completed=50), timed("b", created=10, started=11, completed=90)]
    assert chain(measure(jobs)) == (1, 80)


def test_the_chain_of_jobs_finishing_in_the_same_second_with_the_same_ancestry_is_measured():
    jobs = [
        timed("one", created=0, started=1, completed=60),
        timed("two", created=0, started=1, completed=60),
        timed("verdict", created=60, started=61, completed=70),
    ]
    assert chain(measure(jobs)) == (2, 70)


def test_the_chain_is_not_measured_where_a_job_was_created_as_two_jobs_of_different_ancestry_finished():
    jobs = [
        timed("early", created=0, started=1, completed=60),
        timed("late", created=30, started=31, completed=60),
        timed("verdict", created=60, started=61, completed=70),
    ]
    assert chain(measure(jobs)) is None


def test_the_chain_is_not_measured_where_the_last_two_jobs_finish_together_after_different_ancestry():
    jobs = [timed("first", created=0, started=1, completed=40), timed("second", created=40, started=41, completed=90), timed("alone", created=10, started=11, completed=90)]
    assert chain(measure(jobs)) is None


def test_a_skipped_job_finishing_with_the_job_it_followed_does_not_shorten_the_chain():
    jobs = [
        timed("shard", created=0, started=2, completed=100),
        timed("coverage", created=101, started=100, completed=100, conclusion="skipped"),
        timed("check", created=100, started=101, completed=150),
        timed("verdict", created=150, started=151, completed=170),
    ]
    assert chain(measure(jobs)) == (3, 170)


def test_the_chain_is_not_measured_where_every_job_of_the_newest_attempt_was_skipped():
    assert chain(measure([timed("only", created=0, started=1, completed=2, conclusion="skipped")])) is None


def test_the_chain_of_a_re_run_counts_the_newest_attempt_only():
    jobs = [
        timed("a", attempt=1, created=0, started=1, completed=100),
        timed("b", attempt=1, created=100, started=101, completed=300),
        timed("a", attempt=2, created=600, started=601, completed=640),
        timed("b", attempt=2, created=640, started=641, completed=650),
    ]
    assert chain(measure(jobs)) == (2, 50)


@pytest.mark.parametrize(
    "jobs",
    [
        [],
        [timed("verdict", completed=None)],
        [timed("a", completed=5), timed("b", created=None, completed=5)],
        [timed("a", completed=5), timed("b", started=None, completed=5)],
        [timed("a", completed=5), replace(timed("b", completed=5), completed_at=None)],
    ],
    ids=["no jobs", "nothing completed", "a job without created_at", "a completed job without started_at", "a completed job without completed_at"],
)
def test_a_missing_timestamp_is_not_measured(jobs):
    assert measure(jobs) is None


@pytest.mark.parametrize(
    ("text", "measured"),
    [("2026-11-21T10:00:00Z", True), (None, False), (5, False), ("yesterday", False), ("2026-11-21T10:00:00", False)],
)
def test_only_a_stamp_with_a_zone_is_a_moment(text, measured):
    assert (parsed_moment(text) is not None) == measured


@pytest.mark.parametrize(
    ("walls", "percent", "expected"),
    [([5], 50, 5), ([1, 2, 3, 4], 50, 2), ([1, 2, 3], 50, 2), (list(range(1, 11)), 90, 9), (list(range(1, 12)), 90, 10), ([9, 1, 5], 90, 9)],
)
def test_a_percentile_is_the_nearest_rank_of_the_sorted_walls(walls, percent, expected):
    assert nearest_rank([timedelta(seconds=wall) for wall in walls], percent) == timedelta(seconds=expected)
