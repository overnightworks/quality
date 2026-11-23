"""The weekly CI time of the fleet's repositories: each one's workflow runs of the last seven days read from GitHub,
grouped by head commit, and the commits' median, p90 and red shares, then the jobs of a sample of those runs."""

import json
from collections import defaultdict
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from email.message import Message
from pathlib import Path
from urllib.parse import quote

from quality import ci_time, fleet_job_time, output
from quality.report import CannotJudge

WINDOW_DAYS = 7
PAGE_SIZE = 100
CALLS_PER_REPORT = 600
RUNS_URL = "https://api.github.com/repos/{name}/actions/runs?created={created}&per_page={size}&page={page}"
PULL_REQUEST, PUSH = "pull_request", "push"
COUNTED_EVENTS = frozenset({PULL_REQUEST, PUSH})
RED_CONCLUSIONS = frozenset({"failure", "timed_out"})
CANCELLED, SUCCESS = "cancelled", "success"
UNREADABLE = "cannot read CI time"
MEDIAN, P90 = 50, 90
COLUMNS = ("Repo", "Commits", "Median", "p90", "Red", "Red on main", "Re-runs", "Green on re-run", "Jobs sampled", "Queue p90", "Over 12 min")
PARTLY_READ = "partly read"
DASH = "—"
LIMIT = timedelta(minutes=10)
DOCUMENT_SCHEMA = 1
DOCUMENT_PATH = Path("site") / "fleet-ci-time.json"

RunsFetch = Callable[[str, Mapping[str, str], Callable[[Exception], CannotJudge]], tuple[bytes, Message]]


@dataclass(frozen=True)
class RedShare:
    """The commits of one population that are red, of all those the population counted."""

    red: int
    of: int

    def cell(self) -> str:
        return f"{round(100 * self.red / self.of)} % ({self.red} of {self.of})" if self.of else DASH


@dataclass(frozen=True)
class WeeklyCiTime:
    """What a repository's counted commits come to; the walls are None when no commit was counted. `red` is over the
    commits with a pull_request run, `red_on_main` over those with a push run on the default branch. `reruns` counts the
    finished runs whose `run_attempt` is above 1, `green_on_rerun` those among them whose latest attempt succeeded."""

    commits: int
    median: timedelta | None
    p90: timedelta | None
    red: RedShare
    red_on_main: RedShare
    reruns: int
    green_on_rerun: int


@dataclass(frozen=True)
class PartlyRead:
    """A repository whose runs a cap or a failed read kept from a number."""

    reason: str


@dataclass(frozen=True)
class FleetCiTime:
    """One entry per repository, the calls the whole read made and the window start the runs query used."""

    window_start: datetime
    rows: dict[str, WeeklyCiTime | PartlyRead]
    jobs: dict[str, fleet_job_time.JobTime | fleet_job_time.JobsUnread | fleet_job_time.JobsCut]
    calls: int


@dataclass(frozen=True)
class _Run:
    run_id: int
    sha: str
    event: str
    branch: str
    created: datetime
    updated: datetime
    finished: bool
    conclusion: str | None
    attempt: int


def api_headers(token: str) -> dict[str, str]:
    """GitHub's API headers; the token goes to the API only, never to git, and a local run reads anonymously."""
    return {"Accept": "application/vnd.github+json", **({"Authorization": f"Bearer {token}"} if token else {})}


def read_fleet_ci_time(names: tuple[str, ...], today: date, token: str, fetch: RunsFetch, default_branch: str) -> FleetCiTime:
    """Each repository's weekly CI time, read page by page within the report's call cap, then the jobs of a sample of
    each one's runs within what the cap leaves."""
    window_start = datetime.combine(today - timedelta(days=WINDOW_DAYS), time.min, UTC)
    created = quote(f">={window_start.date()}", safe="")
    headers = api_headers(token)
    rows: dict[str, WeeklyCiTime | PartlyRead] = {}
    sampled: dict[str, list[fleet_job_time.RunToSample]] = {}
    calls = 0
    for name in names:
        rows[name], sampled[name], used = _read_repository(name, created, headers, fetch, default_branch, CALLS_PER_REPORT - calls)
        calls += used
    jobs = {}
    share = sum(bool(runs) for runs in sampled.values())
    for name, runs in sampled.items():
        size = fleet_job_time.sample_size(len(runs), CALLS_PER_REPORT - calls, share)
        if size:
            jobs[name], used = fleet_job_time.read_job_time(name, fleet_job_time.evenly(runs, size), headers, fetch)
            calls += used
        elif runs:
            jobs[name] = fleet_job_time.JobsCut()
        share -= bool(runs)
    return FleetCiTime(window_start, rows, jobs, calls)


def table_cells(name: str, fleet: FleetCiTime) -> tuple[str, ...]:
    """The cells of the repository's row under COLUMNS."""
    reading = fleet.rows[name]
    if isinstance(reading, PartlyRead):
        return (name, f"{PARTLY_READ}{output.SEPARATOR}{reading.reason}", *[DASH] * (len(COLUMNS) - 2))
    if reading.median is None or reading.p90 is None:
        return (name, "0", *[DASH] * (len(COLUMNS) - 2))
    walls = (str(reading.commits), _duration(reading.median), _duration(reading.p90), reading.red.cell(), reading.red_on_main.cell(), str(reading.reruns), str(reading.green_on_rerun))
    return (name, *walls, *fleet_job_time.table_cells(fleet.jobs.get(name)))


def document(fleet: FleetCiTime, generated_at: datetime) -> str:
    """The weekly numbers as the JSON document platform's alert reads: one flat row per repository in the report's order,
    only numbers and booleans beside the name. A repository is measured when it has a median over at least one counted
    commit, and breaches when that median is strictly above LIMIT; `complete` is false when any repository's runs were
    cut by the call cap or could not be read. The window is what the runs query reads: midnight UTC seven days before
    the report's day, up to the moment the document is made."""
    generated = generated_at.astimezone(UTC)
    return json.dumps(
        {
            "schema": DOCUMENT_SCHEMA,
            "generated_at": _instant(generated),
            "window_start": _instant(fleet.window_start),
            "window_end": _instant(generated),
            "limit_seconds": int(LIMIT.total_seconds()),
            "complete": not any(isinstance(reading, PartlyRead) for reading in fleet.rows.values()),
            "repositories": [_document_row(name, reading) for name, reading in fleet.rows.items()],
        },
        indent=2,
    ) + "\n"


def write_document(fleet: FleetCiTime, generated_at: datetime) -> None:
    """Writes the document to DOCUMENT_PATH, creating its folder when it is missing; raises OSError when it cannot."""
    DOCUMENT_PATH.parent.mkdir(exist_ok=True)
    DOCUMENT_PATH.write_text(document(fleet, generated_at), encoding="utf-8")


def _document_row(name: str, reading: WeeklyCiTime | PartlyRead) -> dict[str, str | int | bool]:
    if isinstance(reading, WeeklyCiTime) and reading.median is not None:
        return {"name": name, "median_seconds": int(reading.median.total_seconds()), "commits_sampled": reading.commits, "measured": True, "breach": reading.median > LIMIT}
    return {"name": name, "median_seconds": 0, "commits_sampled": 0, "measured": False, "breach": False}


def _instant(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _duration(wall: timedelta) -> str:
    return output.format_duration(wall.total_seconds())


def _read_repository(
    name: str, created: str, headers: Mapping[str, str], fetch: RunsFetch, default_branch: str, budget: int
) -> tuple[WeeklyCiTime | PartlyRead, list[fleet_job_time.RunToSample], int]:
    runs: list[_Run] = []
    pages = 0
    try:
        while pages < budget:
            pages += 1
            body, _ = fetch(RUNS_URL.format(name=name, created=created, size=PAGE_SIZE, page=pages), headers, _unreadable)
            answered, total = _parse_page(body)
            runs.extend(answered)
            if pages * PAGE_SIZE >= total:
                return _weekly(runs, default_branch), [fleet_job_time.RunToSample(run.run_id, run.created) for run in runs if run.finished], pages
    except CannotJudge:
        return PartlyRead(UNREADABLE), [], pages
    return PartlyRead(f"{output.counted(pages, 'page')} read"), [], pages


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
    run_id, sha, branch = entry["id"], entry["head_sha"], entry["head_branch"] or ""
    attempt = entry["run_attempt"]
    if created is None or updated is None or type(run_id) is not int or type(attempt) is not int or not isinstance(sha, str) or not isinstance(branch, str):
        raise _unreadable(ValueError(entry))
    conclusion = entry["conclusion"]
    return _Run(run_id, sha, entry["event"], branch, created, updated, entry["status"] == ci_time.COMPLETED, conclusion if isinstance(conclusion, str) else None, attempt)


def _weekly(runs: list[_Run], default_branch: str) -> WeeklyCiTime:
    by_commit: dict[str, list[_Run]] = defaultdict(list)
    for run in runs:
        by_commit[run.sha].append(run)
    counted = [commit for commit in by_commit.values() if all(run.finished for run in commit) and any(run.conclusion != CANCELLED for run in commit)]
    reruns = [run for run in runs if run.finished and run.attempt > 1]
    green_on_rerun = sum(run.conclusion == SUCCESS for run in reruns)
    none = RedShare(0, 0)
    if not counted:
        return WeeklyCiTime(0, None, None, none, none, len(reruns), green_on_rerun)
    walls = [max(run.updated for run in commit) - min(run.created for run in commit) for commit in counted]
    red = _red_share(counted, lambda run: run.event == PULL_REQUEST)
    red_on_main = _red_share(counted, lambda run: run.event == PUSH and run.branch == default_branch)
    return WeeklyCiTime(len(counted), ci_time.nearest_rank(walls, MEDIAN), ci_time.nearest_rank(walls, P90), red, red_on_main, len(reruns), green_on_rerun)


def _red_share(commits: list[list[_Run]], in_population: Callable[[_Run], bool]) -> RedShare:
    """The commits with a run of the population, and those among them with such a run concluded red."""
    members = [[run for run in commit if in_population(run)] for commit in commits]
    members = [runs for runs in members if runs]
    return RedShare(sum(any(run.conclusion in RED_CONCLUSIONS for run in runs) for runs in members), len(members))
