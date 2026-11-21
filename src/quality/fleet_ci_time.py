"""The weekly CI time of the fleet's repositories: each one's workflow runs of the last seven days read from GitHub,
grouped by head commit, and the commits' median, p90 and red share."""

import json
from collections import defaultdict
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from email.message import Message
from urllib.parse import quote

from quality import ci_time, output
from quality.report import CannotJudge

WINDOW_DAYS = 7
PAGE_SIZE = 100
PAGES_PER_REPOSITORY = 10
CALLS_PER_REPORT = 600
RUNS_URL = "https://api.github.com/repos/{name}/actions/runs?created={created}&per_page={size}&page={page}"
COUNTED_EVENTS = frozenset({"pull_request", "push"})
RED_CONCLUSIONS = frozenset({"failure", "timed_out"})
CANCELLED = "cancelled"
UNREADABLE = "cannot read CI time"
MEDIAN, P90 = 50, 90
COLUMNS = ("Repo", "Commits", "Median", "p90", "Red (incl. declared-only)")
PARTLY_READ = "partly read"
DASH = "—"

RunsFetch = Callable[[str, Mapping[str, str], Callable[[Exception], CannotJudge]], tuple[bytes, Message]]


@dataclass(frozen=True)
class WeeklyCiTime:
    """What a repository's counted commits come to; the walls are None when no commit was counted."""

    commits: int
    median: timedelta | None
    p90: timedelta | None
    red: int


@dataclass(frozen=True)
class PartlyRead:
    """A repository whose runs a cap or a failed read kept from a number."""

    reason: str


@dataclass(frozen=True)
class FleetCiTime:
    """One entry per repository and the calls the whole read made."""

    rows: dict[str, WeeklyCiTime | PartlyRead]
    calls: int


@dataclass(frozen=True)
class _Run:
    sha: str
    created: datetime
    updated: datetime
    finished: bool
    conclusion: str | None


def api_headers(token: str) -> dict[str, str]:
    """GitHub's API headers; the token goes to the API only, never to git, and a local run reads anonymously."""
    return {"Accept": "application/vnd.github+json", **({"Authorization": f"Bearer {token}"} if token else {})}


def read_fleet_ci_time(names: tuple[str, ...], today: date, token: str, fetch: RunsFetch) -> FleetCiTime:
    """Each repository's weekly CI time, read page by page within the report's call cap."""
    created = quote(f">={today - timedelta(days=WINDOW_DAYS)}", safe="")
    rows: dict[str, WeeklyCiTime | PartlyRead] = {}
    calls = 0
    for name in names:
        reading, used = _read_repository(name, created, api_headers(token), fetch, CALLS_PER_REPORT - calls)
        rows[name] = reading
        calls += used
    return FleetCiTime(rows, calls)


def table_cells(name: str, reading: WeeklyCiTime | PartlyRead) -> tuple[str, ...]:
    """The cells of the repository's row under COLUMNS."""
    if isinstance(reading, PartlyRead):
        return (name, f"{PARTLY_READ}{output.SEPARATOR}{reading.reason}", DASH, DASH, DASH)
    if reading.median is None or reading.p90 is None:
        return (name, "0", DASH, DASH, DASH)
    red = f"{round(100 * reading.red / reading.commits)} % ({reading.red} of {reading.commits})"
    return (name, str(reading.commits), _duration(reading.median), _duration(reading.p90), red)


def _duration(wall: timedelta) -> str:
    return output.format_duration(wall.total_seconds())


def _read_repository(name: str, created: str, headers: Mapping[str, str], fetch: RunsFetch, budget: int) -> tuple[WeeklyCiTime | PartlyRead, int]:
    runs: list[_Run] = []
    pages = 0
    try:
        while pages < PAGES_PER_REPOSITORY and pages < budget:
            body, _ = fetch(RUNS_URL.format(name=name, created=created, size=PAGE_SIZE, page=pages + 1), headers, _unreadable)
            pages += 1
            answered, total = _parse_page(body)
            runs.extend(answered)
            if pages * PAGE_SIZE >= total:
                return _weekly(runs), pages
    except CannotJudge:
        return PartlyRead(UNREADABLE), pages
    return PartlyRead(f"{output.counted(pages, 'page')} read"), pages


def _unreadable(error: Exception) -> CannotJudge:
    return CannotJudge(UNREADABLE)


def _parse_page(body: bytes) -> tuple[list[_Run], int]:
    """The counted runs of one page and the total GitHub reports; anything else is unreadable."""
    try:
        page = json.loads(body)
        total = page["total_count"]
        if not isinstance(total, int):
            raise _unreadable(ValueError(total))
        return [run for entry in page["workflow_runs"] if (run := _run(entry))], total
    except (ValueError, KeyError, TypeError, RecursionError) as error:
        raise _unreadable(error) from error


def _run(entry: Mapping[str, object]) -> _Run | None:
    """None for an event no commit's CI time counts; a counted run missing its moments is unreadable."""
    if entry["event"] not in COUNTED_EVENTS:
        return None
    created, updated = ci_time.parsed_moment(entry["created_at"]), ci_time.parsed_moment(entry["updated_at"])
    if created is None or updated is None or not isinstance(entry["head_sha"], str):
        raise _unreadable(ValueError(entry))
    conclusion = entry["conclusion"]
    return _Run(entry["head_sha"], created, updated, entry["status"] == ci_time.COMPLETED, conclusion if isinstance(conclusion, str) else None)


def _weekly(runs: list[_Run]) -> WeeklyCiTime:
    by_commit: dict[str, list[_Run]] = defaultdict(list)
    for run in runs:
        by_commit[run.sha].append(run)
    counted = [commit for commit in by_commit.values() if all(run.finished for run in commit) and any(run.conclusion != CANCELLED for run in commit)]
    if not counted:
        return WeeklyCiTime(0, None, None, 0)
    walls = [max(run.updated for run in commit) - min(run.created for run in commit) for commit in counted]
    red = sum(any(run.conclusion in RED_CONCLUSIONS for run in commit) for commit in counted)
    return WeeklyCiTime(len(counted), ci_time.nearest_rank(walls, MEDIAN), ci_time.nearest_rank(walls, P90), red)
