"""The jobs of a sample of runs: the even sample, the queue p90 and the jobs above twelve minutes at their p90."""

import json
from datetime import UTC, datetime, timedelta
from email.message import Message

import pytest

from quality.fleet_job_time import (
    JOBS_PER_REPOSITORY,
    JobsUnread,
    JobTime,
    RunToSample,
    evenly,
    read_job_time,
    sample_size,
    table_cells,
)

START = datetime(2026, 11, 20, 10, tzinfo=UTC)


def job(name="tests", queued=0, ran=60, conclusion="success"):
    stamp = lambda seconds: (START + timedelta(seconds=seconds)).isoformat()
    return {"name": name, "conclusion": conclusion, "created_at": stamp(0), "started_at": stamp(queued), "completed_at": stamp(queued + ran)}


def read(*pages):
    served = iter(pages)

    def fetch(url, headers, unreadable):
        page = next(served)
        if isinstance(page, Exception):
            raise unreadable(page)
        return (page if isinstance(page, bytes) else json.dumps({"jobs": page}).encode()), Message()

    runs = [RunToSample(number, START) for number, _ in enumerate(pages)]
    return read_job_time("o/a", runs, {}, fetch)


def test_the_sample_spreads_over_the_runs_by_their_creation():
    runs = [RunToSample(number, START + timedelta(hours=number)) for number in reversed(range(40))]
    assert [run.run_id for run in evenly(runs, 4)] == [5, 15, 25, 35]


@pytest.mark.parametrize(
    ("available", "budget", "repositories", "size"),
    [(100, 600, 3, JOBS_PER_REPOSITORY), (4, 600, 3, 4), (100, 50, 5, 10), (100, 0, 2, 0), (100, 30, 0, JOBS_PER_REPOSITORY)],
    ids=["capped", "fewer runs", "budget share", "no budget", "no repository"],
)
def test_a_repository_reads_the_fewest_of_its_runs_the_cap_and_its_share_of_the_budget(available, budget, repositories, size):
    assert sample_size(available, budget, repositories) == size


def test_queue_time_is_the_wait_before_the_start_counted_apart_from_the_running_time_and_skipped_jobs_are_left_out():
    reading, calls = read([job(queued=2, ran=900), job("lint", queued=30), job("later", queued=500, conclusion="skipped"), job("gone", conclusion="cancelled")])
    assert (calls, reading.runs, reading.queue_p90, reading.over_limit) == (1, 1, timedelta(seconds=30), ())


def test_a_job_is_above_the_limit_at_its_p90_only_from_five_samples_and_the_slowest_comes_first():
    pages = [[job("tests", ran=800), job("e2e", ran=900), job("rare", ran=5000)] if number else [job("tests", ran=800), job("e2e", ran=900)] for number in range(5)]
    reading, _ = read(*pages)
    assert reading.over_limit == (("e2e", timedelta(seconds=900)), ("tests", timedelta(seconds=800)))


@pytest.mark.parametrize("page", [OSError("down"), b"not json", json.dumps({"jobs": [{"name": "x"}]}).encode(), json.dumps({"jobs": [{**job(), "started_at": None}]}).encode()])
def test_a_jobs_read_that_fails_leaves_the_repository_without_a_sample(page):
    reading, calls = read([job()], page, [job()])
    assert (reading, calls) == (JobsUnread("cannot read jobs"), 2)


@pytest.mark.parametrize(
    ("reading", "cells"),
    [
        (None, ("—", "—", "—")),
        (JobTime(3, None, ()), ("—", "—", "—")),
        (JobsUnread("cannot read jobs"), ("cannot read jobs", "—", "—")),
        (JobTime(5, timedelta(seconds=75), ()), ("5", "1m15s", "none")),
        (JobTime(5, timedelta(seconds=7), (("e2e", timedelta(seconds=900)), ("tests", timedelta(seconds=800)))), ("5", "7s", "e2e 15m00s, tests 13m20s")),
    ],
)
def test_the_cells_are_the_sample_size_the_queue_p90_and_the_jobs_above_the_limit(reading, cells):
    assert table_cells(reading) == cells

