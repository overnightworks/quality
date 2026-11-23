from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

COMPLETED = "completed"
SKIPPED = "skipped"
WAITING_TOLERANCE = timedelta(seconds=2)


class TimedJob(Protocol):
    """One entry of a run's jobs with its status and the three moments GitHub stamps on it; a moment GitHub did not give
    or gave unusably is None."""

    name: str
    run_attempt: int
    status: str
    conclusion: str | None
    created_at: datetime | None
    started_at: datetime | None
    completed_at: datetime | None


@dataclass(frozen=True)
class Chain:
    """Jobs waiting on each other: their count and the time from the first job's creation to the last job's completion."""

    jobs: int
    time: timedelta


@dataclass(frozen=True)
class CiTime:
    """What a run's CI time comes to: its wall clock from the first attempt's first job, its slowest job of the newest
    attempt, the longest a job waited between its creation and its start and the longest chain of jobs waiting on each
    other (a job created within two seconds of another's completion waits on it; a skipped job never ran and waits on
    none) in the newest attempt, ending at its last finished job. The chain is None where the timestamps allow several
    chains, since a job created when two others finished may have waited on either."""

    wall: timedelta
    slowest_job: str
    slowest: timedelta
    longest_wait: timedelta
    chain: Chain | None


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
    return CiTime(max(run.completed for run in finished) - min(created), slowest.job.name, slowest.duration, longest_wait, _chain(newest_runs))


@dataclass(frozen=True)
class _FinishedRun:
    job: TimedJob
    created: datetime
    started: datetime
    completed: datetime

    @property
    def duration(self) -> timedelta:
        return self.completed - self.started


def _chain(runs: list[_FinishedRun]) -> Chain | None:
    """The one chain the timestamps allow, None when no job ran or when jobs created as others finished lead to chains of
    different counts or starts."""
    ordered = sorted((run for run in runs if run.job.conclusion != SKIPPED), key=lambda run: run.completed)
    if not ordered:
        return None
    shapes: list[set[tuple[int, datetime]]] = []
    for position, run in enumerate(ordered):
        waited_on = [
            shape for other, shape in zip(ordered[:position], shapes, strict=True) if other.completed < run.completed and abs(run.created - other.completed) <= WAITING_TOLERANCE
        ]
        shapes.append({(count + 1, start) for shape in waited_on for count, start in shape} or {(1, run.created)})
    last = ordered[-1].completed
    ending = {shape for run, shapes_of_run in zip(ordered, shapes, strict=True) if run.completed == last for shape in shapes_of_run}
    if len(ending) != 1:
        return None
    (jobs, start), = ending
    return Chain(jobs, last - start)


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
