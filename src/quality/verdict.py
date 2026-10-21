import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

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
from quality.ratchet import BASELINE_PATH, REWRITE_HINT, Baseline, SizeError, load_baseline, raised_entries

JOBS_URL = "https://api.github.com/repos/{repo}/actions/runs/{run_id}/jobs?filter=all&per_page=100"
MAX_PAGES = 10
NEXT_LINK = re.compile(r'<([^>]+)>;\s*rel="next"')
SUCCESS = "success"

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
    """What the gate holds at one side of a pull request: its settings and its size baseline, None without one."""

    settings: GateSettings
    baseline: Baseline | None


@dataclass(frozen=True)
class Job:
    name: str
    run_attempt: int
    status: str
    conclusion: str | None

    @property
    def state(self) -> str:
        return self.conclusion if self.status == "completed" and self.conclusion else self.status


@dataclass(frozen=True)
class Loosening:
    """One way the head loosens the gate against its base; `what` is the raw text, before any escaping."""

    what: str
    declaration: Declaration | None

    def line(self) -> str:
        if self.declaration is None:
            return f"loosened (undeclared): {self.what}"
        return f"loosened (declared, {self.declaration.item} of {self.declaration.date.isoformat()}): {self.what}"


@dataclass(frozen=True)
class Verdict:
    """The one judgment of a run: its loosenings, the new declarations that bind none, and every other problem."""

    loosenings: tuple[Loosening, ...]
    stale: tuple[Declaration, ...]
    failures: tuple[str, ...]

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
        return not (self.loosenings or self.blocking)

    def lines(self) -> tuple[str, ...]:
        """The verdict as it prints, one problem per line and the result last."""
        if self.green:
            return (GREEN_LINE,)
        declared = tuple(loosening.line() for loosening in self.loosenings if loosening.declaration is not None)
        return (*declared, *self.blocking, RED_LINE if self.blocking else ONLY_DECLARED_LINE)


def judge(
    head: Side, base: Side | None, jobs: list[Job], gate_results: Mapping[str, str], current_attempt: int
) -> Verdict:
    """Without a base (a push, a merge group) no loosening and no declaration is judged; a declared removal of a
    mandatory job leaves that job unjudged."""
    failures = [f'gate job "{name}" result {result}' for name, result in gate_results.items() if result != SUCCESS]
    loosenings, stale = ((), ()) if base is None else _loosenings(head, base)
    declared = {loosening.what for loosening in loosenings if loosening.declaration is not None}
    base_jobs = () if base is None else base.settings.mandatory_jobs
    for name in dict.fromkeys((*base_jobs, *head.settings.mandatory_jobs)):
        if _removal(name) not in declared:
            failures += _problems_of_job(name, jobs, current_attempt)
    return Verdict(loosenings, stale, tuple(failures))


def _loosenings(head: Side, base: Side) -> tuple[tuple[Loosening, ...], tuple[Declaration, ...]]:
    """Each loosening, bound to the head's new declaration of it; a declaration the base holds already binds nothing."""
    whats = [_removal(name) for name in base.settings.mandatory_jobs if name not in head.settings.mandatory_jobs]
    if base.settings.legacy is LegacyMode.FAIL and head.settings.legacy is not LegacyMode.FAIL:
        whats.append(f"legacy {base.settings.legacy.value} → {head.settings.legacy.value}")
    whats += _baseline_loosenings(base.baseline, head.baseline)
    new = {
        declaration.what: declaration
        for declaration in head.settings.loosened
        if declaration not in base.settings.loosened
    }
    loosened = set(whats)
    stale = tuple(declaration for what, declaration in new.items() if what not in loosened)
    return tuple(Loosening(what, new.get(what)) for what in whats), stale


def _removal(name: str) -> str:
    return f'mandatory job "{name}" removed'


def _baseline_loosenings(base: Baseline | None, head: Baseline | None) -> list[str]:
    """Without a base baseline any head one is adoption; with one, the head may only lower or drop entries."""
    if base is None:
        return []
    if head is None:
        return [f"{BASELINE_PATH} removed"]
    return [f"size baseline {entry.describe()}" for entry in raised_entries(base, head)]


def _problems_of_job(name: str, jobs: list[Job], current_attempt: int) -> list[str]:
    candidates = [job for job in jobs if job.name == name and job.run_attempt <= current_attempt]
    if not candidates:
        return [f'mandatory job "{name}" is missing from the run']
    attempt = max(job.run_attempt for job in candidates)
    effective = [job for job in candidates if job.run_attempt == attempt]
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
    name, run_attempt, status = entry["name"], entry["run_attempt"], entry["status"]
    if not (isinstance(name, str) and type(run_attempt) is int and isinstance(status, str)):
        raise TypeError(f"a job's name, run_attempt or status has the wrong type: {entry!r}")
    return Job(name, run_attempt, status, entry["conclusion"])


def github_fetcher(token: str) -> PageFetcher:
    def fetch(url: str) -> tuple[str, str | None]:
        def unreadable(error: Exception) -> CannotJudge:
            return CannotJudge(f"cannot read {url}: {error} (does the caller grant actions: read?)")

        headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
        body, answer_headers = sonar.fetch(url, headers, unreadable)
        return body.decode("utf-8"), answer_headers.get("Link")

    return fetch


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
    """The gate's settings and size baseline in the checked-out tree; an unusable file names the head.

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
    """The gate's settings and size baseline at the base commit; a file the base lacks reads as absent."""
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
