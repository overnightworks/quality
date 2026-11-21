"""The jobs of a sample of a repository's runs: the 90th percentile of the time a job waited for a runner, counted apart
from its running time, and every job whose running time is above twelve minutes at the 90th percentile."""

import json
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from email.message import Message

from quality import ci_time, output
from quality.report import CannotJudge

JOBS_PER_REPOSITORY = 20
JOBS_URL = "https://api.github.com/repos/{name}/actions/runs/{run_id}/jobs?filter=latest&per_page=100"
JOB_LIMIT = timedelta(minutes=12)
NOT_RAN = frozenset({"skipped", "cancelled"})
FEWEST_SAMPLES_PER_JOB = 5
QUEUE, JOB = 90, 90
UNREADABLE = "cannot read jobs"
DASH = "—"

JobsFetch = Callable[[str, Mapping[str, str], Callable[[Exception], CannotJudge]], tuple[bytes, Message]]


@dataclass(frozen=True)
class RunToSample:
    """A finished run of a repository's last seven days: its id and the moment it was created."""

    run_id: int
    created: datetime


@dataclass(frozen=True)
class JobTime:
    """What the sampled runs' jobs come to: how many runs were sampled, the queue p90 (None without a job that ran) and
    the jobs above the limit at their p90, the slowest first."""

    runs: int
    queue_p90: timedelta | None
    over_limit: tuple[tuple[str, timedelta], ...]


@dataclass(frozen=True)
class JobsUnread:
    """A repository whose jobs a failed read kept from a number."""

    reason: str


def sample_size(runs_available: int, budget: int, repositories: int) -> int:
    """How many runs one of `repositories` reads: an even share of the `budget`, at most JOBS_PER_REPOSITORY."""
    return min(runs_available, JOBS_PER_REPOSITORY, budget // max(repositories, 1))


def evenly(runs: Sequence[RunToSample], size: int) -> list[RunToSample]:
    """`size` of the runs spread evenly over their creation order."""
    ordered = sorted(runs, key=lambda run: run.created)
    return [ordered[(2 * position + 1) * len(ordered) // (2 * size)] for position in range(size)]


def read_job_time(name: str, runs: Sequence[RunToSample], headers: Mapping[str, str], fetch: JobsFetch) -> tuple[JobTime | JobsUnread, int]:
    """The sample's job numbers and the calls it made; a failed read is JobsUnread."""
    queues: list[timedelta] = []
    durations: dict[str, list[timedelta]] = defaultdict(list)
    calls = 0
    try:
        for run in runs:
            calls += 1
            body, _ = fetch(JOBS_URL.format(name=name, run_id=run.run_id), headers, _unreadable)
            for job_name, queued, ran in _timed_jobs(body):
                queues.append(queued)
                durations[job_name].append(ran)
    except CannotJudge:
        return JobsUnread(UNREADABLE), calls
    return JobTime(len(runs), ci_time.nearest_rank(queues, QUEUE) if queues else None, _over_limit(durations)), calls


def table_cells(reading: JobTime | JobsUnread | None) -> tuple[str, str, str]:
    """The sample size, the queue p90 and the jobs above the limit under their columns; a repository without a
    sample shows dashes."""
    if isinstance(reading, JobsUnread):
        return (reading.reason, DASH, DASH)
    if reading is None or reading.queue_p90 is None:
        return (DASH, DASH, DASH)
    over = ", ".join(f"{name} {output.format_duration(wall.total_seconds())}" for name, wall in reading.over_limit)
    return (str(reading.runs), output.format_duration(reading.queue_p90.total_seconds()), over or "none")


def _unreadable(error: Exception) -> CannotJudge:
    return CannotJudge(UNREADABLE)


def _timed_jobs(body: bytes) -> list[tuple[str, timedelta, timedelta]]:
    """Each job that ran as its name, the time it waited and the time it ran; anything else is unreadable."""
    try:
        timed = []
        for entry in json.loads(body)["jobs"]:
            if entry["conclusion"] in NOT_RAN:
                continue
            created, started, completed = (ci_time.parsed_moment(entry[key]) for key in ("created_at", "started_at", "completed_at"))
            if created is None or started is None or completed is None or not isinstance(entry["name"], str):
                raise _unreadable(ValueError(entry))
            timed.append((entry["name"], started - created, completed - started))
        return timed
    except (ValueError, KeyError, TypeError, RecursionError) as error:
        raise _unreadable(error) from error


def _over_limit(durations: Mapping[str, list[timedelta]]) -> tuple[tuple[str, timedelta], ...]:
    at_p90 = {name: ci_time.nearest_rank(walls, JOB) for name, walls in durations.items() if len(walls) >= FEWEST_SAMPLES_PER_JOB}
    return tuple(sorted(((name, wall) for name, wall in at_p90.items() if wall > JOB_LIMIT), key=lambda pair: (-pair[1], pair[0])))
