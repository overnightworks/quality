import json
import re
import urllib.error
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from functools import partial
from pathlib import Path
from types import MappingProxyType

from quality import __version__, architecture, output, pin, web
from quality.architecture import PullTree
from quality.checkout import COMMIT_SHA
from quality.config import (
    REPOSITORY_NAME,
    Declaration,
    GateSettings,
    LegacyMode,
)
from quality.output import SEPARATOR, Glyph, Row, SubLine, counted, format_duration
from quality.ratchet import (
    BASELINE_PATH,
    Baseline,
    Renames,
    added_pairs,
    carried_base,
    named_paths,
    raised_entries,
)
from quality.report import FIX_THEN_RERUN, PUSH_THE_FIX, CannotJudge, Command, Instruction, Reason, Step, config_refusal
from quality.sides import (
    Side,
    UnusableConfig,
    is_git_ref_name,
    pull_files,
    read_base,
    read_head,
    read_pull_tree,
    read_renames,
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
RERUN = Instruction("re-run this run's failed jobs")
NEEDS_STEP = Instruction("a mandatory job missing from the caller job's needs: add it there")
PERMISSION_STEP = Instruction("grant actions: read in the caller job's permissions")
SET_BY_THE_GATE = Instruction("quality verdict runs as gate.yml's verdict step, which sets it")
WHOLE_NUMBER = re.compile(r"[1-9]\d{0,17}", re.ASCII)
EXIT_GREEN, EXIT_RED, EXIT_CANNOT_JUDGE = 0, 1, 2

PageFetcher = Callable[[str], tuple[str, str | None]]
MERGE_COMMAND = "quality merge"
# The caller contract's job id (gate.yml's first lines): GitHub names each gate job's check `quality / <job>`.
CALLER_JOB = "quality"


def merge_command(repository: str, pull_request: int) -> str:
    """The operator's door for one pull request, as a person types it."""
    return f"{MERGE_COMMAND} {repository}#{pull_request}"


class JobEvent(StrEnum):
    """How a judged job ended: GitHub's documented conclusions, then quality's own words."""

    SUCCESS = "success"
    FAILURE = "failure"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"
    TIMED_OUT = "timed out"
    NEUTRAL = "neutral"
    ACTION_REQUIRED = "action required"
    CONCLUDED = "concluded"
    MISSING = "missing"
    SEVERAL_ENTRIES = "several entries"
    RUNNING = "running"


CONCLUDED_AS = MappingProxyType({
    "success": JobEvent.SUCCESS,
    "failure": JobEvent.FAILURE,
    "cancelled": JobEvent.CANCELLED,
    "skipped": JobEvent.SKIPPED,
    "timed_out": JobEvent.TIMED_OUT,
    "neutral": JobEvent.NEUTRAL,
    "action_required": JobEvent.ACTION_REQUIRED,
})


class JobKind(StrEnum):
    MANDATORY = "mandatory"
    GATE = "gate"


class VerdictEvent(StrEnum):
    COMPARED = "compared"
    NONE = "none"
    READ = "read"
    UNDECLARED = "undeclared"
    DECLARED = "declared"
    UNMATCHED = "unmatched"


@dataclass(frozen=True)
class JobOutcome:
    """One judged job: a mandatory job of the run or one of gate.yml's own, how it ended and what more there is to say."""

    name: str
    kind: JobKind
    event: JobEvent
    fact: str = ""

    @property
    def green(self) -> bool:
        return self.event is JobEvent.SUCCESS

    @property
    def subject(self) -> str:
        """A gate job stands as the check name GitHub shows, so it never reads like a mandatory job of the same name."""
        return self.name if self.kind is JobKind.MANDATORY else f"{CALLER_JOB} / {self.name}"


def _concluded(name: str, kind: JobKind, word: str, fact: str = "") -> JobOutcome:
    """A conclusion GitHub adds later is red, its word the fact; a success says nothing more."""
    event = CONCLUDED_AS.get(word, JobEvent.CONCLUDED)
    return JobOutcome(name, kind, event, {JobEvent.SUCCESS: "", JobEvent.CONCLUDED: word}.get(event, fact))


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


@dataclass(frozen=True)
class Verdict:
    """The one judgment of a run: its loosenings, the new declarations that bind none, and each judged job in
    judgement order; `unsettled` holds the judged jobs GitHub's answer leaves open."""

    loosenings: tuple[Loosening, ...]
    stale: tuple[Declaration, ...]
    jobs: tuple[JobOutcome, ...]
    unsettled: tuple[Unsettled, ...]

    @property
    def declared(self) -> tuple[Declaration, ...]:
        return tuple(loosening.declaration for loosening in self.loosenings if loosening.declaration is not None)

    @property
    def undeclared(self) -> tuple[str, ...]:
        return tuple(loosening.what for loosening in self.loosenings if loosening.declaration is None)

    @property
    def failed(self) -> tuple[JobOutcome, ...]:
        """The settled jobs that did not succeed; a running job is unsettled, not failed."""
        return tuple(job for job in self.jobs if not job.green and job.event is not JobEvent.RUNNING)

    @property
    def blocked(self) -> bool:
        """Anything but a declared loosening: what no ruling lets the operator's door merge."""
        return bool(self.undeclared or self.stale or self.failed)

    @property
    def green(self) -> bool:
        return not (self.loosenings or self.blocked or self.unsettled)


def declaration_line(declaration: Declaration) -> SubLine:
    """A declaration as the verdict and the door show it: its `what`, its ruling, and its reason below."""
    return SubLine(Glyph.NEEDS_YOU, f"{declaration.what}{SEPARATOR}{declaration.ruling()}", whole=True, below=(f"reason: {declaration.reason}",))


def loosening_lines(verdict: Verdict) -> tuple[SubLine, ...]:
    """Each loosening in judgement order as its `what`; a declared one with its ruling and reason."""
    return tuple(_undeclared_line(each.what) if each.declaration is None else declaration_line(each.declaration) for each in verdict.loosenings)


def blocking_lines(verdict: Verdict) -> tuple[SubLine, ...]:
    """What blocks the door, in the verdict's words: each undeclared loosening, unmatched declaration and failed job."""
    failed = (_undeclared_line(SEPARATOR.join(filter(None, (f"{job.subject} {job.event}", job.fact)))) for job in verdict.failed)
    return (*map(_undeclared_line, verdict.undeclared), *map(declaration_line, verdict.stale), *failed)


def _undeclared_line(text: str) -> SubLine:
    return SubLine(Glyph.NEEDS_YOU, text, whole=True)


def judge(
    head: Side,
    base: Side | None,
    jobs: list[Job],
    gate_results: Mapping[str, str],
    current_attempt: int,
    renames: Renames,
    *,
    pull_tree: PullTree | None = None,
) -> Verdict:
    """Without a base (a push, a merge group) no loosening and no declaration is judged; a declared removal of a
    mandatory job leaves that job unjudged. `renames` are the files git renamed from the base to the head; a
    `pull_tree` brings its own and replaces them."""
    loosenings, stale = ((), ()) if base is None else _loosenings(head, base, renames, pull_tree)
    declared = {loosening.what for loosening in loosenings if loosening.declaration is not None}
    base_jobs = () if base is None else base.settings.mandatory_jobs
    unsettled: list[Unsettled] = []
    outcomes: list[JobOutcome] = []
    for name in dict.fromkeys((*base_jobs, *head.settings.mandatory_jobs)):
        if _removal(name) in declared:
            continue
        outcome, unsettled_job = _judge_job(name, jobs, current_attempt)
        if unsettled_job:
            unsettled.append(unsettled_job)
        outcomes.append(outcome)
    outcomes += (_concluded(name, JobKind.GATE, result) for name, result in gate_results.items())
    return Verdict(loosenings, stale, tuple(outcomes), tuple(unsettled))


def _loosenings(
    head: Side, base: Side, renames: Renames, pull_tree: PullTree | None
) -> tuple[tuple[Loosening, ...], tuple[Declaration, ...]]:
    """Each loosening, bound to the head's new declaration of it; a declaration the base holds already binds nothing.
    An advisory allowance and a generated file declare their own loosening, so no [[gate.loosened]] entry binds one."""
    whats = [_removal(name) for name in base.settings.mandatory_jobs if name not in head.settings.mandatory_jobs]
    if base.settings.legacy is LegacyMode.FAIL and head.settings.legacy is not LegacyMode.FAIL:
        whats.append(f"legacy {base.settings.legacy.value} → {head.settings.legacy.value}")
    if base.settings.sonar and not head.settings.sonar:
        whats.append("sonar true → false")
    whats += _baseline_loosenings(base.baseline, head.baseline, renames if pull_tree is None else pull_tree.renames)
    whats += architecture.contract_loosenings(base.contracts, head.contracts, pull_tree)
    new = {
        declaration.what: declaration
        for declaration in head.settings.loosened
        if declaration not in base.settings.loosened
    }
    loosened = set(whats)
    stale = tuple(declaration for what, declaration in new.items() if what not in loosened)
    own = (Loosening(what, new.get(what)) for what in whats)
    return (*own, *_allowance_loosenings(base.settings, head.settings), *_generated_loosenings(base.settings, head.settings)), stale


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


def _generated_loosenings(base: GateSettings, head: GateSettings) -> list[Loosening]:
    """Each head path the base does not declare generated, declared by its own item and date."""
    inherited = {file.path for file in base.generated_files}
    loosenings = []
    for file in head.generated_files:
        if file.path not in inherited:
            what = f"size leaves out generated file {file.path}"
            loosenings.append(Loosening(what, Declaration(what, file.item, file.date, file.reason)))
    return loosenings


def _removal(name: str) -> str:
    return f'mandatory job "{name}" removed'


def _baseline_loosenings(base: Baseline | None, head: Baseline | None, renames: Renames) -> list[str]:
    """Without a base baseline any head one is adoption; with one, the head may only lower or drop entries and pairs.

    A head entry in a file git renamed is held against the base entry under the old path; it is still named, and
    declared, under its head path.

    A base `[clones]` table is held the same way: the head keeps it, and a base without one lets the head introduce it.
    """
    if base is None:
        return []
    if head is None:
        return [f"{BASELINE_PATH} removed"]
    base = carried_base(base, renames, named_paths(head))
    lines = [f"size baseline {entry.describe()}" for entry in raised_entries(base.size, head.size)]
    if base.clones is None:
        return lines
    if head.clones is None:
        return [*lines, "clone baseline removed"]
    return [*lines, *(f"clone baseline adds {pair.label}" for pair in added_pairs(base.clones, head.clones))]


def _judge_job(name: str, jobs: list[Job], current_attempt: int) -> tuple[JobOutcome, Unsettled | None]:
    """The job's outcome and, while GitHub lists it as running, its unsettled entry."""
    attempt, effective = _effective_entries(name, jobs, current_attempt)
    if not effective:
        return JobOutcome(name, JobKind.MANDATORY, JobEvent.MISSING, "not in the run"), None
    running = [job.status for job in effective if job.status != COMPLETED]
    if running:
        fact = f"attempt {attempt}{SEPARATOR}{running[0]}"
        return JobOutcome(name, JobKind.MANDATORY, JobEvent.RUNNING, fact), Unsettled(name, attempt, running[0])
    failing = [job for job in effective if job.state != SUCCESS]
    if len(effective) > 1 and failing:
        fact = f"{len(effective)} entries in attempt {attempt}, not all success"
        return JobOutcome(name, JobKind.MANDATORY, JobEvent.SEVERAL_ENTRIES, fact), None
    return _concluded(name, JobKind.MANDATORY, failing[0].state if failing else SUCCESS, f"attempt {attempt}"), None


def _effective_entries(name: str, jobs: list[Job], current_attempt: int) -> tuple[int, list[Job]]:
    """The entries of `name` in its newest attempt up to the verdict's own, and that attempt (0 when it has none)."""
    candidates = [job for job in jobs if job.name == name and job.run_attempt <= current_attempt]
    attempt = max((job.run_attempt for job in candidates), default=0)
    return attempt, [job for job in candidates if job.run_attempt == attempt]


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
        steps = (PERMISSION_STEP, RERUN) if error.code in PERMISSION_REFUSALS else ()
        return CannotJudge(f"cannot read {url}: {error}", steps)
    if isinstance(error, OSError):
        if web.timed_out(error):
            seconds = web.REQUEST_TIMEOUT_SECONDS
            return CannotJudge(f"GitHub did not answer {url} within {seconds}s", (RERUN,))
        return CannotJudge(f"could not reach GitHub at {url}: {error}", (RERUN,))
    return CannotJudge(f"cannot read {url}: {error}")


def github_fetcher(token: str) -> PageFetcher:
    def fetch(url: str) -> tuple[str, str | None]:
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
        body, answer_headers = web.fetch(url, headers, lambda error: unreadable_jobs(url, error))
        try:
            text = body.decode("utf-8")
        except UnicodeDecodeError as error:
            raise unreadable_jobs(url, error) from error
        return text, answer_headers.get("Link")

    return fetch


Judgement = Callable[[list[Job]], Verdict]


@dataclass(frozen=True)
class Settled:
    """The verdict of the first read that settled every judged job, after `reads` reads; `first` is what GitHub said
    first while it was not settled."""

    verdict: Verdict
    reads: int
    first: str | None


class UnsettledRun(CannotJudge):
    """The re-read could not judge the run; its rows show the reads and the last judged one (None before any)."""

    def __init__(self, reason: Reason, reads: int, first: str | None, last: Verdict | None) -> None:
        super().__init__(reason.fact, reason.next_steps)
        self.reads, self.first, self.last = reads, first, last


def read_settled(read: Callable[[], list[Job]], judgement: Judgement, sleep: web.Sleep) -> Settled:
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
            said, reason = error.answered, _server_error(error, reads)
        except CannotJudge as error:
            raise UnsettledRun(error.reason, reads, first, last) from error
        else:
            if not verdict.unsettled:
                return Settled(verdict, reads, first)
            last = verdict
            said, reason = "listed " + _listed(verdict.unsettled), _stale(verdict.unsettled, reads)
        first = first or said
    raise UnsettledRun(reason, reads, first, last)


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


def _server_error(error: GitHubServerError, reads: int) -> Reason:
    return Reason(f"{error.reason.fact} after {reads} reads{output.SEPARATOR}GitHub's fault, not the caller's", (RERUN,))


def _stale(unsettled: tuple[Unsettled, ...], reads: int) -> Reason:
    stale = "the gate starts after every mandatory job, so this answer is stale"
    return Reason(f"GitHub still lists {_listed(unsettled)} after {reads} reads{output.SEPARATOR}{stale}", (RERUN, NEEDS_STEP))


@dataclass(frozen=True)
class PullRequest:
    """The pull request a gate run judges: its number, the branch it merges into, the commit the gate compares
    against and the pull request's head."""

    number: int
    base_ref: str
    base_sha: str
    head_sha: str


@dataclass(frozen=True)
class GateRun:
    """What gate.yml's verdict step hands the verdict: the run to read and the pull request, None outside one."""

    repository: str
    run_id: str
    attempt: int
    token: str
    gate_results: Mapping[str, str]
    pull_request: PullRequest | None


def read_gate_run(environment: Mapping[str, str]) -> GateRun:
    """The step's variables, each in its accepted form; a missing or malformed one is the refusal that names it, never
    its value."""
    repository_name = _variable(environment, "GITHUB_REPOSITORY", REPOSITORY_NAME.fullmatch, "an owner/name")
    run_id = _variable(environment, "GITHUB_RUN_ID", WHOLE_NUMBER.fullmatch, "a run id")
    attempt = int(_variable(environment, "GITHUB_RUN_ATTEMPT", WHOLE_NUMBER.fullmatch, "a run attempt"))
    token = _variable(environment, "GITHUB_TOKEN")
    gate_results = _gate_results(_variable(environment, "GATE_NEEDS"))
    pull_request = None
    if environment.get("GATE_BASE_SHA"):
        sha = _variable(environment, "GATE_BASE_SHA", COMMIT_SHA.fullmatch, "a commit sha")
        ref = _variable(environment, "GITHUB_BASE_REF", is_git_ref_name, "a branch name")
        number = int(_variable(environment, "GATE_PULL_REQUEST", WHOLE_NUMBER.fullmatch, "a pull request number"))
        head_sha = _variable(environment, "GATE_HEAD_SHA", COMMIT_SHA.fullmatch, "a commit sha")
        pull_request = PullRequest(number, ref, sha, head_sha)
    return GateRun(repository_name, run_id, attempt, token, gate_results, pull_request)


def _variable(environment: Mapping[str, str], name: str, accepted: Callable[[str], object] = bool, form: str = "") -> str:
    value = environment.get(name, "")
    if not value:
        raise CannotJudge(f"{name} is not set", (SET_BY_THE_GATE,))
    if not accepted(value):
        raise CannotJudge(f"{name} is not {form}", (SET_BY_THE_GATE,))
    return value


def _gate_results(needs_text: str) -> Mapping[str, str]:
    """Each need's result; anything but an object of objects with a string `result` is no needs of the verdict job."""
    try:
        needs = json.loads(needs_text)
    except (ValueError, RecursionError):
        needs = None
    if not isinstance(needs, dict) or not all(isinstance(need, dict) and isinstance(need.get("result"), str) for need in needs.values()):
        raise CannotJudge("GATE_NEEDS is not the needs of gate.yml's verdict job", (SET_BY_THE_GATE,))
    return MappingProxyType({name: need["result"] for name, need in needs.items()})


def read_sides(run: GateRun) -> tuple[Side | None, Side]:
    """The base, read first so that a refusal the pull request inherits is named at the base, then the head; a config
    either cannot read is its fact and example with that side's step."""
    base = None
    if run.pull_request is not None:
        ref = run.pull_request.base_ref
        fix = Instruction(f"fix it on {ref} first, then update the pull request's branch with {ref}")
        base = _side(partial(read_base, Path("."), run.pull_request.base_sha), fix)
    return base, _side(partial(read_head, Path(".")), PUSH_THE_FIX)


def _side(read: Callable[[], Side], fix: Step) -> Side:
    try:
        return read()
    except UnusableConfig as unusable:
        raise config_refusal(unusable.error, fix) from unusable


def run_verdict(
    fetcher_for: Callable[[str], PageFetcher], sleep: web.Sleep, clock: Callable[[], float], channels: output.Channels, environment: Mapping[str, str],
) -> int:
    """Judges the run gate.yml's verdict step names, in the checkout it runs in: the verdict's rows and closing line on
    stdout and `next:` on stderr, a refusal as the pair on stderr after the rows it judged, and the same lines in the
    step summary as a code block."""
    started = clock()
    release = pin.gate_run_release(__version__).row()
    try:
        run = read_gate_run(environment)
        base, head = read_sides(run)
        pull_tree, renames = pull_files(
            base, head,
            lambda: read_pull_tree(Path("."), run.pull_request.base_sha, "HEAD"),
            lambda: read_renames(Path("."), run.pull_request.base_sha, "HEAD"),
        )
        fetch = fetcher_for(run.token)
        settled = read_settled(
            lambda: read_jobs(run.repository, run.run_id, fetch),
            lambda jobs: judge(head, base, jobs, run.gate_results, run.attempt, renames, pull_tree=pull_tree),
            sleep,
        )
    except UnsettledRun as unsettled:
        show = partial(_refused, (release, *verdict_rows(unsettled.last, run, unsettled.reads, unsettled.first)), unsettled.reason)
        code = EXIT_CANNOT_JUDGE
    except CannotJudge as refusal:
        show = partial(_refused, (release,), refusal.reason)
        code = EXIT_CANNOT_JUDGE
    else:
        rows = (release, *verdict_rows(settled.verdict, run, settled.reads, settled.first))
        show = partial(_shown, rows, *closing(settled.verdict, run, clock() - started))
        code = EXIT_GREEN if settled.verdict.green else EXIT_RED
    show(channels)
    try:
        output.append_step_summary(output.code_block(output.rendered(show)), environment)
    except OSError as error:
        unwritable = f"{output.STEP_SUMMARY_VARIABLE} is not writable{output.SEPARATOR}{error}"
        output.write_pair(unwritable, (FIX_THEN_RERUN,), channels.err)
        return EXIT_CANNOT_JUDGE
    return code


def verdict_rows(verdict: Verdict | None, run: GateRun, reads: int, first: str | None) -> tuple[Row, ...]:
    """The re-read after several reads; then, once a read is judged, the base, the jobs (one summing row when all are
    green), with a base the loosenings, and the new declarations that match nothing."""
    rows = [Row(Glyph.NOTE, "run", VerdictEvent.READ, f"GitHub first {first}", count=reads)] if reads > 1 else []
    if verdict is None:
        return tuple(rows)
    pull = run.pull_request
    if pull is None:
        rows.append(Row(Glyph.NOTE, "base", VerdictEvent.NONE, "a push or merge group: no loosening judged"))
    else:
        compared = f"{pull.base_ref} {pull.base_sha[:7]}{SEPARATOR}head {pull.head_sha[:7]}"
        rows.append(Row(Glyph.NOTE, "base", VerdictEvent.COMPARED, compared))
    if all(job.green for job in verdict.jobs):
        mandatory = _mandatory(verdict.jobs)
        counts = f"{counted(mandatory, 'mandatory job')}{SEPARATOR}{counted(len(verdict.jobs) - mandatory, 'gate job')}"
        rows.append(Row(Glyph.CLEAN, "jobs", JobEvent.SUCCESS, counts))
    else:
        rows += (Row(Glyph.CLEAN if job.green else Glyph.NEEDS_YOU, job.subject, job.event, job.fact) for job in verdict.jobs)
    if pull is not None:
        rows.append(_loosenings_row(verdict))
    if verdict.stale:
        unmatched = tuple(map(declaration_line, verdict.stale))
        rows.append(Row(Glyph.NEEDS_YOU, "declarations", VerdictEvent.UNMATCHED, count=len(unmatched), sub_lines=unmatched, counts_plural=False))
    return tuple(rows)


def _loosenings_row(verdict: Verdict) -> Row:
    if not verdict.loosenings:
        return Row(Glyph.CLEAN, "loosenings", VerdictEvent.NONE)
    undeclared, declared, lines = len(verdict.undeclared), len(verdict.declared), loosening_lines(verdict)
    if undeclared:
        fact = f"{declared} declared" if declared else ""
        return Row(Glyph.NEEDS_YOU, "loosenings", VerdictEvent.UNDECLARED, fact, undeclared, lines, counts_plural=False)
    return Row(Glyph.NEEDS_YOU, "loosenings", VerdictEvent.DECLARED, count=declared, sub_lines=lines, counts_plural=False)


def _mandatory(jobs: Iterable[JobOutcome]) -> int:
    return sum(job.kind is JobKind.MANDATORY for job in jobs)


def closing(verdict: Verdict, run: GateRun, seconds: float) -> tuple[Glyph, tuple[str, ...], tuple[Step, ...]]:
    """The closing line's glyph and parts, its duration last, and the one command an only-declared red names."""
    duration = format_duration(seconds)
    mandatory = _mandatory(verdict.jobs)
    if verdict.green:
        return Glyph.CLEAN, ("green", counted(mandatory, "mandatory job"), duration), ()
    if not verdict.blocked and run.pull_request is not None:
        door = Command(merge_command(run.repository, run.pull_request.number))
        return Glyph.NEEDS_YOU, ("red", "only declared loosenings", "only the operator merges it", duration), (door,)
    failed_mandatory = _mandatory(verdict.failed)
    failed_gate = len(verdict.failed) - failed_mandatory
    parts = (
        failed_mandatory and f"{failed_mandatory} of {counted(mandatory, 'mandatory job')} failed",
        failed_gate and f"{counted(failed_gate, 'gate job')} failed",
        verdict.undeclared and counted(len(verdict.undeclared), "undeclared loosening"),
        verdict.stale and f"{counted(len(verdict.stale), 'declaration')} unmatched",
        verdict.declared and counted(len(verdict.declared), "declared loosening"),
    )
    return Glyph.NEEDS_YOU, ("red", *(part for part in parts if part), duration), ()


def _shown(rows: tuple[Row, ...], glyph: Glyph, parts: tuple[str, ...], next_steps: tuple[Step, ...], channels: output.Channels) -> None:
    output.write_rows(rows, channels.out, output.subject_width(rows))
    output.write_closing_line(glyph, parts, channels.out)
    if next_steps:
        output.write_next(next_steps, channels.err)


def _refused(rows: tuple[Row, ...], reason: Reason, channels: output.Channels) -> None:
    output.write_rows(rows, channels.out, output.subject_width(rows))
    output.write_pair(reason.fact, reason.next_steps or (FIX_THEN_RERUN,), channels.err)
