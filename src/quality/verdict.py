import json
import re
import urllib.error
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from types import MappingProxyType

from quality import architecture, checkout, output, process, repository, web
from quality.config import (
    CONFIG_PATH,
    REPOSITORY_NAME,
    ConfigError,
    Declaration,
    GateSettings,
    LegacyMode,
    load_gate_settings,
    read_gate_settings,
)
from quality.fleet import COMMIT_SHA
from quality.ratchet import (
    BASELINE_PATH,
    NO_RENAMES,
    Baseline,
    Renames,
    SizeError,
    added_pairs,
    carried_base,
    head_step,
    load_baseline,
    named_paths,
    raised_entries,
    read_baseline,
    renames_of,
)
from quality.report import CannotJudge, Instruction, Reason, Step, config_refusal

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
FIX_THEN_RERUN = Instruction("fix what the error names, then re-run this run's failed jobs")
PUSH_THE_FIX = Instruction("push the fix; the gate judges the new commit")
NEEDS_STEP = Instruction("a mandatory job missing from the caller job's needs: add it there")
PERMISSION_STEP = Instruction("grant actions: read in the caller job's permissions")
SET_BY_THE_GATE = Instruction("quality verdict runs as gate.yml's verdict step, which sets it")
WHOLE_NUMBER = re.compile(r"[1-9][0-9]*")
EXIT_GREEN, EXIT_RED, EXIT_CANNOT_JUDGE = 0, 1, 2
CREDENTIAL_HELPER = "!gh auth git-credential"
FETCH_TIMEOUT_SECONDS = 120

PageFetcher = Callable[[str], tuple[str, str | None]]


NO_BASE_SETTINGS = GateSettings((), LegacyMode.REPORT, sonar=False)
HEAD = "the head"
IMPORT_CONFIG = "import-linter config"
GREEN_LINE = "verdict: green"
RED_LINE = "verdict: red"
ONLY_DECLARED_LINE = (
    "verdict: red - only declared loosenings: only the operator's quality merge lands this pull request"
)


@dataclass(frozen=True)
class Side:
    """What the gate holds at one side of a pull request: its settings, its baseline (None without one) and its import contracts."""

    settings: GateSettings
    baseline: Baseline | None
    contracts: architecture.SideContracts = field(default_factory=architecture.SideContracts)


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
    renames: Renames,
) -> Verdict:
    """Without a base (a push, a merge group) no loosening and no declaration is judged; a declared removal of a
    mandatory job leaves that job unjudged. `renames` are the files git renamed from the base to the head."""
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


def _loosenings(head: Side, base: Side, renames: Renames) -> tuple[tuple[Loosening, ...], tuple[Declaration, ...]]:
    """Each loosening, bound to the head's new declaration of it; a declaration the base holds already binds nothing.
    An advisory allowance and a generated file declare their own loosening, so no [[gate.loosened]] entry binds one."""
    whats = [_removal(name) for name in base.settings.mandatory_jobs if name not in head.settings.mandatory_jobs]
    if base.settings.legacy is LegacyMode.FAIL and head.settings.legacy is not LegacyMode.FAIL:
        whats.append(f"legacy {base.settings.legacy.value} → {head.settings.legacy.value}")
    if base.settings.sonar and not head.settings.sonar:
        whats.append("sonar true → false")
    whats += _baseline_loosenings(base.baseline, head.baseline, renames)
    whats += architecture.contract_loosenings(base.contracts, head.contracts)
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
    """The verdict of the first read that settled every judged job; `note` says what the reads before it showed."""

    verdict: Verdict
    note: str | None

    def lines(self) -> tuple[str, ...]:
        return (*_noted(self.note), *self.verdict.lines())


class UnsettledRun(CannotJudge):
    """The re-read could not judge the run; it prints its note and the last judged read's blocking lines first."""

    def __init__(self, reason: Reason, note: str | None, blocking: tuple[str, ...]) -> None:
        super().__init__(reason.fact, reason.next_steps)
        self.preceding_lines = (*_noted(note), *blocking)


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
            raise _unsettled_run(error.reason, reads, first, last) from error
        else:
            if not verdict.unsettled:
                return Settled(verdict, _reread_note(reads, first))
            last = verdict
            said, reason = "listed " + _listed(verdict.unsettled), _stale(verdict.unsettled, reads)
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


def _unsettled_run(reason: Reason, reads: int, first: str | None, last: Verdict | None) -> UnsettledRun:
    return UnsettledRun(reason, _reread_note(reads, first), () if last is None else last.blocking)


def _server_error(error: GitHubServerError, reads: int) -> Reason:
    return Reason(f"{error.reason.fact} after {reads} reads{output.SEPARATOR}GitHub's fault, not the caller's", (RERUN,))


def _stale(unsettled: tuple[Unsettled, ...], reads: int) -> Reason:
    stale = "the gate starts after every mandatory job, so this answer is stale"
    return Reason(f"GitHub still lists {_listed(unsettled)} after {reads} reads{output.SEPARATOR}{stale}", (RERUN, NEEDS_STEP))


@dataclass(frozen=True)
class Base:
    """The pull request's base: the branch it merges into and the commit the gate compares against."""

    ref: str
    sha: str


@dataclass(frozen=True)
class GateRun:
    """What gate.yml's verdict step hands the verdict: the run to read and the base to compare, None outside a pull
    request."""

    repository: str
    run_id: str
    attempt: int
    token: str
    gate_results: Mapping[str, str]
    base: Base | None


def read_gate_run(environment: Mapping[str, str]) -> GateRun:
    """The step's variables, each in its accepted form; a missing or malformed one is the refusal that names it, never
    its value."""
    repository_name = _variable(environment, "GITHUB_REPOSITORY", REPOSITORY_NAME.fullmatch, "an owner/name")
    run_id = _variable(environment, "GITHUB_RUN_ID", WHOLE_NUMBER.fullmatch, "a run id")
    attempt = int(_variable(environment, "GITHUB_RUN_ATTEMPT", WHOLE_NUMBER.fullmatch, "a run attempt"))
    token = _variable(environment, "GITHUB_TOKEN")
    gate_results = _gate_results(_variable(environment, "GATE_NEEDS"))
    base = None
    if environment.get("GATE_BASE_SHA"):
        sha = _variable(environment, "GATE_BASE_SHA", COMMIT_SHA.fullmatch, "a commit sha")
        base = Base(_variable(environment, "GITHUB_BASE_REF", _is_git_ref_name, "a branch name"), sha)
    return GateRun(repository_name, run_id, attempt, token, gate_results, base)


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
    if run.base is not None:
        ref = run.base.ref
        fix = Instruction(f"fix it on {ref} first, then update the pull request's branch with {ref}")
        base = _side(partial(read_base, Path("."), run.base.sha), fix)
    return base, _side(partial(read_head, Path(".")), PUSH_THE_FIX)


def _side(read: Callable[[], Side], fix: Step) -> Side:
    try:
        return read()
    except UnusableConfig as unusable:
        raise config_refusal(unusable.error, fix) from unusable


def run_verdict(fetcher_for: Callable[[str], PageFetcher], sleep: web.Sleep, channels: output.Channels, environment: Mapping[str, str]) -> int:
    """Judges the run gate.yml's verdict step names, in the checkout it runs in: the verdict's lines on stdout, a
    refusal as the pair on stderr after the lines it judged, and the same lines in the step summary as a code block."""
    try:
        run = read_gate_run(environment)
        base, head = read_sides(run)
        renames = read_renames(Path("."), run.base.sha, "HEAD") if compares_baselines(base, head) else NO_RENAMES
        fetch = fetcher_for(run.token)
        settled = read_settled(
            lambda: read_jobs(run.repository, run.run_id, fetch),
            lambda jobs: judge(head, base, jobs, run.gate_results, run.attempt, renames),
            sleep,
        )
    except CannotJudge as refusal:
        judged = refusal.preceding_lines if isinstance(refusal, UnsettledRun) else ()
        show = partial(_shown, judged, refusal.reason.fact, refusal.reason.next_steps or (FIX_THEN_RERUN,))
        code = EXIT_CANNOT_JUDGE
    else:
        show = partial(_shown, settled.lines(), None, ())
        code = EXIT_GREEN if settled.verdict.green else EXIT_RED
    show(channels)
    try:
        output.append_step_summary(output.code_block(output.rendered(show)), environment)
    except OSError as error:
        unwritable = f"{output.STEP_SUMMARY_VARIABLE} is not writable{output.SEPARATOR}{error}"
        output.write_pair(unwritable, (FIX_THEN_RERUN,), channels.err)
        return EXIT_CANNOT_JUDGE
    return code


def _shown(lines: tuple[str, ...], error: str | None, next_steps: tuple[Step, ...], channels: output.Channels) -> None:
    output.write_lines(lines, channels.out)
    if error is not None:
        output.write_pair(error, next_steps, channels.err)


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


def _require_safe_ref(ref: str, role: str) -> None:
    if not _is_git_ref_name(ref):
        raise CannotJudge(f"the {role} {ref!r} is not a ref name or a SHA")


class UnusableConfig(CannotJudge):
    """A side's config that cannot be read: `where` names the side and `error` keeps the config's fact and example
    apart; the fact is the line the verdict prints for it."""

    def __init__(self, where: str, error: ConfigError) -> None:
        super().__init__(f"{CONFIG_PATH} at {where}: {error}")
        self.where, self.error = where, error


def read_head(repo: Path) -> Side:
    """The gate's settings and baseline in the checked-out tree; an unusable file names the head.

    Only the head's malformed baseline says how to restore it: the pull request cannot rewrite its base.
    """
    try:
        settings = read_gate_settings(repo)
    except ConfigError as error:
        raise UnusableConfig(HEAD, error) from error
    try:
        baseline = read_baseline(repo)
    except SizeError as error:
        raise _unusable(BASELINE_PATH, HEAD, error, (head_step(error),)) from error
    except CannotJudge as error:
        raise _unusable(BASELINE_PATH, HEAD, error.reason.fact, error.reason.next_steps) from error
    try:
        import_configs = {name: repository.read_text(repo, name) for name in architecture.CONFIG_FILES}
    except repository.RepositoryFileError as error:
        raise _unusable(IMPORT_CONFIG, HEAD, error) from error
    return Side(settings, baseline, _contracts_at(import_configs, HEAD))


def read_base(repo: Path, base_sha: str) -> Side:
    """The gate's settings, baseline and import-linter configs at the base commit; a file the base lacks reads as absent."""
    _require_safe_ref(base_sha, "base")
    where = f"the base commit {base_sha}"
    try:
        texts = {
            path: process.read_blob(repo, base_sha, path, f"cannot read {where}", f"cannot read {path} at {where}")
            for path in (CONFIG_PATH, BASELINE_PATH, *architecture.CONFIG_FILES)
        }
    except process.ProcessError as error:
        raise CannotJudge(str(error)) from error
    return side_of(texts[CONFIG_PATH], texts[BASELINE_PATH], where, {name: texts[name] for name in architecture.CONFIG_FILES})


def compares_baselines(base: Side | None, head: Side) -> bool:
    """Whether the renames can change the judgement: only a comparison of two baselines reads them. Any other pair is
    judged without them, so a pull request that renames nothing the baselines name is never held up by the diff."""
    return base is not None and base.baseline is not None and head.baseline is not None


def read_renames(
    repo: Path, base: str, head: str, env: dict[str, str] | None = None, bound: float | None = None
) -> Renames:
    """The files git renamed between two commits; the verdict and the door read the same pair.

    A `bound` in seconds runs git in its own process group, for the door inside its scratch directory."""
    _require_safe_ref(base, "base")
    _require_safe_ref(head, "head")
    try:
        listed = process.git(
            ["-C", str(repo.resolve()), "diff", *checkout.RENAME_LISTING, "--end-of-options", base, head, "--"],
            f"cannot list the files renamed between {base} and {head}",
            env,
            bound,
            encoding=None,
        )
    except process.ProcessError as error:
        raise CannotJudge(str(error)) from error
    return renames_of(listed)


def gh_configuration_directory(environment: Mapping[str, str]) -> str:
    """Where gh looks for its login: `GH_CONFIG_DIR`, else `$XDG_CONFIG_HOME/gh`, else `~/.config/gh`."""
    if environment.get("GH_CONFIG_DIR"):
        return environment["GH_CONFIG_DIR"]
    if environment.get("XDG_CONFIG_HOME"):
        return str(Path(environment["XDG_CONFIG_HOME"]) / "gh")
    return str(Path(environment.get("HOME", "~")).expanduser() / ".config" / "gh")


def fetch_environment(environment: Mapping[str, str], empty_home: Path) -> dict[str, str]:
    """The environment of git around the fetch: no inherited git routing or configuration, an empty home so that
    `.netrc` and every other file git reads there is absent, and no way to prompt for a password. gh is the only source
    of credentials, and it keeps its login by being told where the real configuration directory is."""
    isolated = process.attribute_free_git_environment(empty_home)
    kept = {name: value for name, value in isolated.items() if name != "SSH_ASKPASS"}
    return kept | {"GIT_TERMINAL_PROMPT": "0", "GH_CONFIG_DIR": gh_configuration_directory(environment)}


def read_renames_of_pull(remote: str, base_sha: str, merge_sha: str, environment: Mapping[str, str]) -> Renames:
    """The renames the verdict reads at the checkout, from the pull request's two commits fetched into a bare repository
    that is removed afterwards. Credentials stay in gh's credential protocol; git's own words never reach the operator."""
    with process.scratch_directory() as scratch:
        isolated = fetch_environment(environment, scratch / "home")
        repository = scratch / "repository"
        commands = (
            ["git", "init", "--bare", "--quiet", str(repository)],
            [
                "git", "-c", "credential.helper=", "-c", f"credential.helper={CREDENTIAL_HELPER}", "-c", "core.askPass=",
                "-C", str(repository), "fetch", "--quiet", "--depth=1", "--no-tags", "--", remote, base_sha, merge_sha,
            ],
        )
        for command in commands:
            try:
                done = process.run_bounded_in_own_group(command, FETCH_TIMEOUT_SECONDS, isolated)
            except process.ProcessError as error:
                raise CannotJudge(f"cannot fetch the pull request's commits to compare renamed files ({error})") from None
            if done.returncode != 0:
                raise CannotJudge(
                    f"cannot fetch the pull request's commits to compare renamed files (git exited {done.returncode})"
                )
        try:
            return read_renames(repository, base_sha, merge_sha, isolated, bound=FETCH_TIMEOUT_SECONDS)
        except CannotJudge:
            raise CannotJudge("cannot list the files git renamed between the pull request's commits") from None


def side_of(config: str | None, baseline: str | None, where: str, import_configs: Mapping[str, str | None] = MappingProxyType({})) -> Side:
    """One side from its file texts, None where the side lacks the file; an unreadable file names `where`.

    `import_configs` holds the text of each import-linter config file the side may have."""
    try:
        settings = NO_BASE_SETTINGS if config is None else load_gate_settings(config)
    except ConfigError as error:
        raise UnusableConfig(where, error) from error
    try:
        loaded = None if baseline is None else load_baseline(baseline)
    except SizeError as error:
        raise _unusable(BASELINE_PATH, where, error) from error
    return Side(settings, loaded, _contracts_at(import_configs, where))


def _contracts_at(texts: Mapping[str, str | None], where: str) -> architecture.SideContracts:
    try:
        return architecture.contracts_of({name: text for name, text in texts.items() if text is not None})
    except architecture.ContractError as error:
        raise _unusable(IMPORT_CONFIG, where, error) from error


def _unusable(name: str, where: str, reason: object, next_steps: tuple[Step, ...] = ()) -> CannotJudge:
    return CannotJudge(f"{name} at {where}: {reason}", next_steps)
