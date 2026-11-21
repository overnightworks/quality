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
    created = _present([job.created_at for job in every])
    finished = _finished_runs(every)
    if created is None or finished is None:
        return None
    newest = max((job.run_attempt for job in every), default=0)
    newest_runs = [run for run in finished if run.job.run_attempt == newest]
    if not newest_runs:
        return None
    slowest = max(newest_runs, key=lambda run: run.duration)
    longest_wait = max(run.started - run.created for run in finished)
    return CiTime(max(run.completed for run in finished) - min(created), slowest.job.name, slowest.duration, longest_wait)


@dataclass(frozen=True)
class _FinishedRun:
    job: TimedJob
    created: datetime
    started: datetime
    completed: datetime

    @property
    def duration(self) -> timedelta:
        return self.completed - self.started


def _present(moments: list[datetime | None]) -> list[datetime] | None:
    kept = [moment for moment in moments if moment is not None]
    return kept if len(kept) == len(moments) else None


def _finished_runs(jobs: list[TimedJob]) -> list[_FinishedRun] | None:
    runs = []
    for job in jobs:
        if job.status != COMPLETED:
            continue
        if job.created_at is None or job.started_at is None or job.completed_at is None:
            return None
        runs.append(_FinishedRun(job, job.created_at, job.started_at, job.completed_at))
    return runs
