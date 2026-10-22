import json
import re
import urllib.error
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from quality import engines, sonar
from quality.config import (
    CONFIG_PATH,
    ConfigError,
    Declaration,
    GateSettings,
    LegacyMode,
    load_gate_settings,
    read_gate_settings,
)
from quality.hygiene import CannotJudge, read_baseline
from quality.ratchet import (
    BASELINE_PATH,
    REWRITE_HINT,
    Baseline,
    SizeError,
    added_pairs,
    load_baseline,
    moved_forward,
    raised_entries,
)

JOBS_URL = "https://api.github.com/repos/{repo}/actions/runs/{run_id}/jobs?filter=all&per_page=100"
MAX_PAGES = 10
MAX_REREADS = 12
REREAD_INTERVAL_SECONDS = 10
NEXT_LINK = re.compile(r'<([^>]+)>;\s*rel="next"')
SUCCESS = "success"
COMPLETED = "completed"
PROGRESS_OF_STATUS = MappingProxyType({"in_progress": 1, COMPLETED: 2})
FIRST_SERVER_ERROR = 500
PERMISSION_REFUSALS = frozenset({403, 404})
PERMISSION_HINT = "(does the caller grant actions: read?)"
NEEDS_HINT = " (a mandatory job missing from the caller's needs: must join it)"

PageFetcher = Callable[[str], tuple[str, str | None]]


NO_BASE_SETTINGS = GateSettings((), LegacyMode.REPORT)
HEAD = "the head"
GREEN_LINE = "verdict: green"
RED_LINE = "verdict: red"
ONLY_DECLARED_LINE = (
    "verdict: red - only declared loosenings: only the operator's quality emergency-merge lands this pull request"
)


@dataclass(frozen=True)
class Side:
    """What the gate holds at one side of a pull request: its settings and its baseline, None without one."""

    settings: GateSettings
    baseline: Baseline | None


@dataclass(frozen=True)
class Job:
    """One entry of a run's jobs; `id` is GitHub's own, and the door's jobs from check runs keep the default."""

    name: str
    run_attempt: int
    status: str
    conclusion: str | None
    id: int = 0

    @property
    def state(self) -> str:
        return self.conclusion if self.status == COMPLETED and self.conclusion else self.status


@dataclass(frozen=True)
class Unsettled:
    """A judged mandatory job GitHub's answer still lists as running."""

    name: str
    attempt: int
    state: str

    def listed(self) -> str:
        return f'"{self.name}" (attempt {self.attempt}) as {self.state}'


@dataclass(frozen=True)
class Loosening:
    """One way the head loosens the gate against its base; `what` is the raw text, before any escaping."""

    what: str
    declaration: Declaration | None

    def line(self) -> str:
        if self.declaration is None:
            return f"loosened (undeclared): {self.what}"
        return f"loosened (declared, {self.declaration.ruling()}): {self.what}"


@dataclass(frozen=True)
class Verdict:
    """The one judgment of a run: its loosenings, the new declarations that bind none, and every other problem;
    `unsettled` holds the judged jobs GitHub's answer leaves open."""

    loosenings: tuple[Loosening, ...]
    stale: tuple[Declaration, ...]
    failures: tuple[str, ...]
    unsettled: tuple[Unsettled, ...]

    @property
    def declared(self) -> tuple[Declaration, ...]:
        return tuple(loosening.declaration for loosening in self.loosenings if loosening.declaration is not None)

    @property
    def blocking(self) -> tuple[str, ...]:
        """Every problem but a declared loosening: what no ruling lets the operator's door merge."""
        undeclared = (loosening.line() for loosening in self.loosenings if loosening.declaration is None)
        stale = (f"declared loosening matches nothing: {declaration.what}" for declaration in self.stale)
        return (*undeclared, *stale, *self.failures)

    @property
    def green(self) -> bool:
        return not (self.loosenings or self.blocking or self.unsettled)

    def lines(self) -> tuple[str, ...]:
        """The verdict as it prints, one problem per line and the result last."""
        if self.green:
            return (GREEN_LINE,)
        declared = tuple(loosening.line() for loosening in self.loosenings if loosening.declaration is not None)
        return (*declared, *self.blocking, RED_LINE if self.blocking else ONLY_DECLARED_LINE)


def judge(
    head: Side,
    base: Side | None,
    jobs: list[Job],
    gate_results: Mapping[str, str],
    current_attempt: int,
    renames: Mapping[str, str] = MappingProxyType({}),
) -> Verdict:
    """Without a base (a push, a merge group) no loosening and no declaration is judged; a declared removal of a
    mandatory job leaves that job unjudged. `renames` maps a path the head renamed to its new one."""
    failures = [f'gate job "{name}" result {result}' for name, result in gate_results.items() if result != SUCCESS]
    loosenings, stale = ((), ()) if base is None else _loosenings(head, base, renames)
    declared = {loosening.what for loosening in loosenings if loosening.declaration is not None}
    base_jobs = () if base is None else base.settings.mandatory_jobs
    unsettled: list[Unsettled] = []
    for name in dict.fromkeys((*base_jobs, *head.settings.mandatory_jobs)):
        if _removal(name) in declared:
            continue
        unsettled_job, problems = _judge_job(name, jobs, current_attempt)
        if unsettled_job:
            unsettled.append(unsettled_job)
        failures += problems
    return Verdict(loosenings, stale, tuple(failures), tuple(unsettled))


def _loosenings(head: Side, base: Side, renames: Mapping[str, str]) -> tuple[tuple[Loosening, ...], tuple[Declaration, ...]]:
    """Each loosening, bound to the head's new declaration of it; a declaration the base holds already binds nothing.
    An advisory allowance declares its own loosening, so no [[gate.loosened]] entry binds one."""
    whats = [_removal(name) for name in base.settings.mandatory_jobs if name not in head.settings.mandatory_jobs]
    if base.settings.legacy is LegacyMode.FAIL and head.settings.legacy is not LegacyMode.FAIL:
        whats.append(f"legacy {base.settings.legacy.value} → {head.settings.legacy.value}")
    whats += _baseline_loosenings(base.baseline, head.baseline, renames)
    new = {
        declaration.what: declaration
        for declaration in head.settings.loosened
        if declaration not in base.settings.loosened
    }
    loosened = set(whats)
    stale = tuple(declaration for what, declaration in new.items() if what not in loosened)
    own = (Loosening(what, new.get(what)) for what in whats)
    return (*own, *_allowance_loosenings(base.settings, head.settings)), stale


def _allowance_loosenings(base: GateSettings, head: GateSettings) -> list[Loosening]:
    """Each head allowance the base lacks, or whose `until` the head moves later, declared by its own item and date."""
    base_until = {allowance.key: allowance.until for allowance in base.advisory_allowances}
    loosenings = []
    for allowance in head.advisory_allowances:
        until = base_until.get(allowance.key)
        if until is None:
            what = f"advisory allowance adds {allowance.id} in {allowance.lock} until {allowance.until.isoformat()}"
        elif allowance.until > until:
            what = f"advisory allowance extends {allowance.id} in {allowance.lock} to {allowance.until.isoformat()}"
        else:
            continue
        loosenings.append(Loosening(what, Declaration(what, allowance.item, allowance.date, allowance.reason)))
    return loosenings


def _removal(name: str) -> str:
    return f'mandatory job "{name}" removed'


def _baseline_loosenings(base: Baseline | None, head: Baseline | None, renames: Mapping[str, str]) -> list[str]:
    """Without a base baseline any head one is adoption; with one, the head may only lower or drop entries and pairs.

    The base is read under the paths the head renamed its files to, so a moved file is the same entry and a raise in it
    names the head's path.

    A base `[clones]` table is held the same way: the head keeps it, and a base without one lets the head introduce it.
    """
    if base is None:
        return []
    if head is None:
        return [f"{BASELINE_PATH} removed"]
    base = moved_forward(base, renames)
    lines = [f"size baseline {entry.describe()}" for entry in raised_entries(base.size, head.size)]
    if base.clones is None:
        return lines
    if head.clones is None:
        return [*lines, "clone baseline removed"]
    return [*lines, *(f"clone baseline adds {pair.label}" for pair in added_pairs(base.clones, head.clones))]


def _judge_job(name: str, jobs: list[Job], current_attempt: int) -> tuple[Unsettled | None, list[str]]:
    """The job's unsettled entry, or its failure lines."""
    attempt, effective = _effective_entries(name, jobs, current_attempt)
    if not effective:
        return None, [f'mandatory job "{name}" is missing from the run']
    running = [job.status for job in effective if job.status != COMPLETED]
    if running:
        return Unsettled(name, attempt, running[0]), []
    return None, _problems_of_job(name, attempt, effective)


def _effective_entries(name: str, jobs: list[Job], current_attempt: int) -> tuple[int, list[Job]]:
    """The entries of `name` in its newest attempt up to the verdict's own, and that attempt (0 when it has none)."""
    candidates = [job for job in jobs if job.name == name and job.run_attempt <= current_attempt]
    attempt = max((job.run_attempt for job in candidates), default=0)
    return attempt, [job for job in candidates if job.run_attempt == attempt]


def _problems_of_job(name: str, attempt: int, effective: list[Job]) -> list[str]:
    failing = [job for job in effective if job.state != SUCCESS]
    if len(effective) > 1 and failing:
        return [f'mandatory job "{name}" (attempt {attempt}) has {len(effective)} entries, not all success']
    return [f'mandatory job "{name}" (attempt {attempt}) concluded {job.state}' for job in failing]


def read_jobs(repo: str, run_id: str, fetch: PageFetcher) -> list[Job]:
    url: str | None = JOBS_URL.format(repo=repo, run_id=run_id)
    jobs: list[Job] = []
    total = None
    for _ in range(MAX_PAGES):
        body, link = fetch(url)
        try:
            page = json.loads(body)
            total = page["total_count"]
            jobs += [_job_of(entry) for entry in page["jobs"]]
        except (ValueError, KeyError, TypeError) as error:
            raise CannotJudge(f"{url} did not return the expected jobs page: {error!r}") from error
        match = NEXT_LINK.search(link or "")
        if not match:
            if len(jobs) != total:
                raise CannotJudge(f"read {len(jobs)} jobs but the run has {total}")
            return jobs
        url = match.group(1)
    raise CannotJudge(f"the run has more than {MAX_PAGES} pages of jobs")


def _job_of(entry: dict) -> Job:
    job_id, name, run_attempt, status = entry["id"], entry["name"], entry["run_attempt"], entry["status"]
    numbers_typed = type(job_id) is int and type(run_attempt) is int
    if not (numbers_typed and isinstance(name, str) and isinstance(status, str)):
        raise TypeError(f"a job's id, name, run_attempt or status has the wrong type: {entry!r}")
    return Job(name, run_attempt, status, entry["conclusion"], job_id)


class GitHubServerError(CannotJudge):
    """GitHub answered 5xx: its fault, not the caller's, so the re-read tries again."""

    def __init__(self, status: int, reason: str, url: str) -> None:
        self.answered = f"answered HTTP {status} {reason}"
        super().__init__(f"GitHub {self.answered} for {url}")


def unreadable_jobs(url: str, error: Exception) -> CannotJudge:
    """What a failed jobs read says: only GitHub's 403 and 404 can mean the caller withholds `actions: read`."""
    if isinstance(error, urllib.error.HTTPError):
        if error.code >= FIRST_SERVER_ERROR:
            return GitHubServerError(error.code, error.reason, url)
        hint = f" {PERMISSION_HINT}" if error.code in PERMISSION_REFUSALS else ""
        return CannotJudge(f"cannot read {url}: {error}{hint}")
    if isinstance(error, OSError):
        if sonar.timed_out(error):
            seconds = sonar.REQUEST_TIMEOUT_SECONDS
            return CannotJudge(f"GitHub did not answer {url} within {seconds}s; re-run the verdict")
        return CannotJudge(f"could not reach GitHub at {url}: {error}; re-run the verdict")
    return CannotJudge(f"cannot read {url}: {error}")


def github_fetcher(token: str) -> PageFetcher:
    def fetch(url: str) -> tuple[str, str | None]:
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
        body, answer_headers = sonar.fetch(url, headers, lambda error: unreadable_jobs(url, error))
        try:
            text = body.decode("utf-8")
        except UnicodeDecodeError as error:
            raise unreadable_jobs(url, error) from error
        return text, answer_headers.get("Link")

    return fetch


Judgement = Callable[[list[Job]], Verdict]


@dataclass(frozen=True)
class Settled:
    """The verdict of the first read that settled every judged job; `note` says what the reads before it showed."""

    verdict: Verdict
    note: str | None

    def lines(self) -> tuple[str, ...]:
        return (*_noted(self.note), *self.verdict.lines())


class UnsettledRun(CannotJudge):
    """The re-read could not judge the run; it prints its note and the last judged read's blocking lines first."""

    def __init__(self, reason: str, note: str | None, blocking: tuple[str, ...]) -> None:
        super().__init__(reason)
        self.preceding_lines = (*_noted(note), *blocking)


def read_settled(read: Callable[[], list[Job]], judgement: Judgement, sleep: sonar.Sleep) -> Settled:
    """Judges the run's jobs until no judged job is unsettled, re-reading a stale answer or GitHub's server error.

    The gate starts after every mandatory job, so GitHub listing one as running is its lagging answer, not the run.
    Each read judges the merged view of every answer so far, so a lagging answer can neither drop nor undo an entry.
    """
    view: Mapping[int, Job] = {}
    last: Verdict | None = None
    first: str | None = None
    for reads in range(1, MAX_REREADS + 2):
        if reads > 1:
            sleep(REREAD_INTERVAL_SECONDS)
        try:
            view = merged_view(view, read())
            verdict = judgement(list(view.values()))
        except GitHubServerError as error:
            said, reason = error.answered, _server_error_line(error, reads)
        except CannotJudge as error:
            raise _unsettled_run(str(error), reads, first, last) from error
        else:
            if not verdict.unsettled:
                return Settled(verdict, _reread_note(reads, first))
            last = verdict
            said, reason = "listed " + _listed(verdict.unsettled), _stale_line(verdict.unsettled, reads)
        first = first or said
    raise _unsettled_run(reason, reads, first, last)


def merged_view(view: Mapping[int, Job], answer: Iterable[Job]) -> Mapping[int, Job]:
    """Every entry any answer listed, keyed by id, at the furthest state an answer reported: a waiting status, then
    `in_progress`, then `completed`. Within an attempt GitHub only moves an entry forward and never removes one, so an
    entry missing from a later answer stays, one listed late joins, and a completed entry never changes."""
    merged = dict(view)
    for job in answer:
        known = merged.get(job.id)
        if known is None or _progress(job) > _progress(known):
            merged[job.id] = job
    return MappingProxyType(merged)


def _progress(job: Job) -> int:
    return PROGRESS_OF_STATUS.get(job.status, 0)


def _listed(unsettled: Iterable[Unsettled]) -> str:
    return ", ".join(job.listed() for job in unsettled)


def _noted(note: str | None) -> tuple[str, ...]:
    return () if note is None else (note,)


def _reread_note(reads: int, first: str | None) -> str | None:
    return None if reads == 1 else f"verdict: read the run's jobs {reads} times - GitHub first {first}"


def _unsettled_run(reason: str, reads: int, first: str | None, last: Verdict | None) -> UnsettledRun:
    return UnsettledRun(reason, _reread_note(reads, first), () if last is None else last.blocking)


def _server_error_line(error: GitHubServerError, reads: int) -> str:
    return f"{error} after {reads} reads; this is GitHub's fault, not the caller's: re-run the verdict"


def _stale_line(unsettled: tuple[Unsettled, ...], reads: int) -> str:
    return (
        f"GitHub still lists {_listed(unsettled)} after {reads} reads; the gate starts after every mandatory job, so "
        f"this answer is stale: re-run the verdict{NEEDS_HINT}"
    )


FORBIDDEN_REF_CHARACTERS = re.compile(r"[\x00-\x20\x7f~^:?*\[\\]")


def _is_git_ref_name(ref: str) -> bool:
    """git-check-ref-format's rules, plus no leading '-' so a ref can never read as an option."""
    broken_name = (
        ref.startswith("-") or ref == "@" or ref.endswith(".")
        or ".." in ref or "@{" in ref or FORBIDDEN_REF_CHARACTERS.search(ref)
    )
    broken_component = any(
        part == "" or part.startswith(".") or part.endswith(".lock") for part in ref.split("/")
    )
    return not (broken_name or broken_component)


def _require_safe_ref(ref: str) -> None:
    if not _is_git_ref_name(ref):
        raise CannotJudge(f"the base {ref!r} is not a ref name or a SHA")


def read_head(repo: Path) -> Side:
    """The gate's settings and baseline in the checked-out tree; an unusable file names the head.

    Only the head's malformed baseline says how to rewrite it: the pull request cannot rewrite its base.
    """
    try:
        settings = read_gate_settings(repo)
    except ConfigError as error:
        raise _unusable(CONFIG_PATH, HEAD, error) from error
    try:
        return Side(settings, read_baseline(repo))
    except SizeError as error:
        raise _unusable(BASELINE_PATH, HEAD, f"{error}; {REWRITE_HINT}") from error
    except CannotJudge as error:
        raise _unusable(BASELINE_PATH, HEAD, error) from error


def read_base(repo: Path, base_sha: str) -> Side:
    """The gate's settings and baseline at the base commit; a file the base lacks reads as absent."""
    _require_safe_ref(base_sha)
    where = f"the base commit {base_sha}"
    try:
        config, baseline = (
            engines.read_blob(repo, base_sha, path, f"cannot read {where}", f"cannot read {path} at {where}")
            for path in (CONFIG_PATH, BASELINE_PATH)
        )
    except engines.EngineError as error:
        raise CannotJudge(str(error)) from error
    return side_of(config, baseline, where)


def read_renames(repo: Path, base_sha: str) -> dict[str, str]:
    """The files the checked-out commit renamed from the base commit, old path to new, as git detects them."""
    _require_safe_ref(base_sha)
    try:
        listed = engines.git(
            ["-C", str(repo.resolve()), "diff", "-M", "--name-status", "-z", "--end-of-options", base_sha, "HEAD", "--"],
            f"cannot list the files renamed since the base commit {base_sha}",
        )
    except engines.EngineError as error:
        raise CannotJudge(str(error)) from error
    return _renames_of(listed)


def _renames_of(name_status: str) -> dict[str, str]:
    """`git diff --name-status -z` records: a status, then one path, or two for a rename."""
    fields = iter(name_status.split("\0")[:-1])
    renames = {}
    for status in fields:
        if status.startswith("R"):
            old, new = next(fields), next(fields)
            renames[old] = new
        else:
            next(fields)
    return renames


def side_of(config: str | None, baseline: str | None, where: str) -> Side:
    """One side from its file texts, None where the side lacks the file; an unreadable file names `where`."""
    try:
        settings = NO_BASE_SETTINGS if config is None else load_gate_settings(config)
    except ConfigError as error:
        raise _unusable(CONFIG_PATH, where, error) from error
    try:
        return Side(settings, None if baseline is None else load_baseline(baseline))
    except SizeError as error:
        raise _unusable(BASELINE_PATH, where, error) from error


def _unusable(name: str, where: str, reason: object) -> CannotJudge:
    return CannotJudge(f"{name} at {where}: {reason}")
