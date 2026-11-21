from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

COMPLETED = "completed"


class TimedJob(Protocol):
    """One entry of a run's jobs with its status and the three moments GitHub stamps on it; a moment GitHub did not give
    or gave unusably is None."""

    name: str
    run_attempt: int
    status: str
    created_at: datetime | None
    started_at: datetime | None
    completed_at: datetime | None


@dataclass(frozen=True)
class CiTime:
    """What a run's CI time comes to: its wall clock from the first attempt's first job, its slowest job of the newest
    attempt and the longest a job waited between its creation and its start."""

    wall: timedelta
    slowest_job: str
    slowest: timedelta
    longest_wait: timedelta


def parsed_moment(text: object) -> datetime | None:
    """GitHub's ISO 8601 stamp, None when it is missing, no string or no moment."""
    if not isinstance(text, str):
        return None
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    return moment if moment.tzinfo else None


def measure(jobs: Iterable[TimedJob]) -> CiTime | None:
    """None when any stamp the numbers need is missing or unusable, a completed job's included. A job still running (the
    verdict's own) counts only for the moment it was created, so a re-run counts from its first attempt."""
    every = list(jobs)
    finished = [job for job in every if job.status == COMPLETED]
    newest = max((job.run_attempt for job in every), default=0)
    finished_newest = [job for job in finished if job.run_attempt == newest]
    created = [job.created_at for job in every]
    started = [job.started_at for job in finished]
    completed = [job.completed_at for job in finished]
    if not (finished_newest and None not in created and None not in started and None not in completed):
        return None
    first = min(job.created_at for job in every if job.created_at)
    last = max(job.completed_at for job in finished if job.completed_at)
    slowest = max(finished_newest, key=_job_time)
    longest_wait = max(job.started_at - job.created_at for job in finished if job.started_at and job.created_at)
    return CiTime(last - first, slowest.name, _job_time(slowest), longest_wait)


def _job_time(job: TimedJob) -> timedelta:
    assert job.completed_at is not None and job.started_at is not None
    return job.completed_at - job.started_at
