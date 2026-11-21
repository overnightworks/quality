from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

COMPLETED = "completed"
WAITING_TOLERANCE = timedelta(seconds=2)


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
    attempt, the longest a job waited between its creation and its start and the longest chain of jobs waiting on each
    other (a job created within two seconds of another's completion waits on it) in the newest attempt, ending at its
    last finished job: its job count and its time from the first job's creation to the last job's completion."""

    wall: timedelta
    slowest_job: str
    slowest: timedelta
    longest_wait: timedelta
    chain_jobs: int
    chain: timedelta


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
    chain = _chain(newest_runs)
    return CiTime(
        max(run.completed for run in finished) - min(created), slowest.job.name, slowest.duration, longest_wait, len(chain), chain[-1].completed - chain[0].created
    )


@dataclass(frozen=True)
class _FinishedRun:
    job: TimedJob
    created: datetime
    started: datetime
    completed: datetime

    @property
    def duration(self) -> timedelta:
        return self.completed - self.started


def _chain(runs: list[_FinishedRun]) -> list[_FinishedRun]:
    """The runs from the first job to the last finished one; each is followed back to the job it waited on, the later
    finished (the later listed on a tie) when it waited on several."""
    chain = [_latest(runs)]
    while waited_on := [run for run in runs if run.completed < chain[0].completed and abs(chain[0].created - run.completed) <= WAITING_TOLERANCE]:
        chain.insert(0, _latest(waited_on))
    return chain


def _latest(runs: list[_FinishedRun]) -> _FinishedRun:
    return max(reversed(runs), key=lambda run: run.completed)


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


def nearest_rank(walls: Sequence[timedelta], percent: int) -> timedelta:
    """The percentile of `walls` by nearest rank: the value at rank ceil(percent/100 x count) of the sorted walls."""
    ordered = sorted(walls)
    return ordered[max(-(-percent * len(ordered) // 100), 1) - 1]
