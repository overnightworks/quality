from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum, auto
from functools import partial
from pathlib import Path, PurePosixPath

from quality import (
    advisories,
    architecture,
    checkout,
    claude_md,
    clones,
    compose,
    env_file,
    layout,
    process,
    python_floor,
    python_lint,
    ratchet,
    repository,
    root_layout,
    scanners,
)
from quality.config import (
    CONFIG_PATH,
    AllowLists,
    ConfigError,
    read_allow_lists,
)
from quality.report import CannotJudge, Example, Extent, Finding, Note, Reason, RuleResult, ScannerRun, Unparsable
from quality.rules import Area, Rule

CONFIG_EXAMPLE_LABEL = f"write it in {CONFIG_PATH} like this"


def _python_floor_result(root: Path, modes: dict[str, str] | CannotJudge, allow_lists: AllowLists | CannotJudge) -> python_floor.FloorResult:
    modes = _required(modes)
    names = [str(PurePosixPath(project.directory) / layout.PROJECT_FILE) for project in layout.python_projects(modes)]
    present = checkout.on_disk(root, [name for name in names if modes[name] != layout.GITLINK_MODE])
    return python_floor.check_python_floor(checkout.read_existing(repository.read_bytes, root, present), _required(allow_lists).python)


def _read_allow_lists(root: Path) -> AllowLists:
    """The repository's declarations; a config that cannot be read cannot be judged."""
    try:
        return read_allow_lists(root)
    except ConfigError as error:
        steps = () if error.example is None else (Example(CONFIG_EXAMPLE_LABEL, error.example),)
        raise CannotJudge(error.fact, steps) from error


def _required[T](read: T | CannotJudge) -> T:
    """An input read once for the run; one that could not be read refuses each rule that needs it, and no other."""
    if isinstance(read, CannotJudge):
        raise read
    return read


def judged_baseline(root: Path) -> ratchet.Baseline | None:
    """The baseline both baseline rules judge by, read once; a file that cannot be read cannot be judged."""
    try:
        return ratchet.read_baseline(root)
    except ratchet.SizeError as error:
        raise CannotJudge(str(error), (ratchet.head_step(error),)) from error


def check_import_contract(root: Path, tracked: list[str]) -> RuleResult:
    """Measured only without an import-linter config at the top; with one, its contracts must be kept.

    A config counts when its name exists, so a tracked one deleted from the tree is none and a link to nowhere cannot be read.
    """
    configs = checkout.read_existing(repository.read_text, root, [name for name in tracked if name in architecture.CONFIG_FILES])
    try:
        return architecture.check_contracts(root, tracked, configs)
    except architecture.UnparsableModule as error:
        raise Unparsable(str(error)) from error
    except architecture.ContractError as error:
        raise CannotJudge(str(error)) from error


def lint_python(
    root: Path, modes: dict[str, str] | CannotJudge, locate_ruff: Callable[[], str], log: scanners.ScannerLog
) -> python_lint.Lint:
    """ruff's one run over every tracked regular `.py` file, which python-security and python-print both judge."""
    try:
        return python_lint.lint(root, _required(modes), python_floor.PYTHON_TARGET, locate_ruff, log)
    except python_lint.UnparsablePython as error:
        raise Unparsable(str(error)) from error
    except python_lint.LintError as error:
        raise CannotJudge(str(error)) from error


def _judge_lint(lint: python_lint.Lint | CannotJudge, judge: Callable[[python_lint.Lint], RuleResult]) -> RuleResult | CannotJudge:
    """One rule's verdict on the shared run; a run that cannot judge refuses both rules, a judge's own refusal only its rule."""
    if isinstance(lint, CannotJudge):
        return lint
    try:
        return judge(lint)
    except python_lint.LintError as error:
        return CannotJudge(str(error))


def check_secrets(root: Path, allow_lists: AllowLists | CannotJudge, log: scanners.ScannerLog) -> RuleResult:
    try:
        return scanners.scan_secrets(root, _required(allow_lists).secrets, log)
    except process.ProcessError as error:
        raise CannotJudge(str(error)) from error


def check_workflows(
    root: Path, rule: Rule, tracked: list[str], locate_zizmor: Callable[[], str], log: scanners.ScannerLog
) -> RuleResult:
    workflows = checkout.on_disk(root, [name for name in tracked if checkout.is_workflow(PurePosixPath(name))])
    try:
        return RuleResult(scanners.scan_workflows(root, rule, workflows, locate_zizmor, log), len(workflows), False)
    except process.ProcessError as error:
        raise CannotJudge(str(error)) from error


def check_advisories(root: Path, tracked: list[str], allow_lists: AllowLists | CannotJudge, log: scanners.ScannerLog) -> RuleResult:
    """The allowances' `until` is judged against today's date in UTC, read here, once."""
    allowances = _required(allow_lists).advisories
    locks = {name: checkout.judged(repository.resolve_file, root, name) for name in checkout.on_disk(root, [n for n in tracked if advisories.is_lock(n)])}
    try:
        return advisories.scan(locks, root, allowances, datetime.now(UTC).date(), log=log)
    except process.ProcessError as error:
        raise CannotJudge(str(error)) from error


class Outcome(Enum):
    """A rule's verdict, mildest first: the worst verdict of a run decides its exit."""

    CLEAN = "clean"
    MEASURED_ONLY = "measured only"
    FINDINGS = "findings"
    UNPARSABLE = Unparsable.label
    CANNOT_JUDGE = CannotJudge.label


@dataclass(frozen=True)
class RuleVerdict:
    rule: Rule
    findings: tuple[Finding, ...] = ()
    refusal: CannotJudge | None = None
    measured_only: bool = False
    notes: tuple[Note, ...] = ()
    extent: Extent | None = None

    @property
    def outcome(self) -> Outcome:
        if self.refusal is not None:
            return Outcome(self.refusal.label)
        if self.findings:
            return Outcome.FINDINGS
        return Outcome.MEASURED_ONLY if self.measured_only else Outcome.CLEAN


class Measurement(Enum):
    """How much of an area's judging was only measured: none of it, some of its rules, or all of the rules that judged."""

    ENFORCED = auto()
    PARTLY_MEASURED_ONLY = auto()
    MEASURED_ONLY = auto()


@dataclass(frozen=True)
class AreaVerdict:
    """What the rules of one area said, in run order."""

    area: Area
    verdicts: tuple[RuleVerdict, ...]
    scanner_runs: tuple[ScannerRun, ...] = ()

    @property
    def outcome(self) -> Outcome:
        severity = list(Outcome)
        return max((verdict.outcome for verdict in self.verdicts), key=severity.index, default=Outcome.CLEAN)

    @property
    def findings(self) -> tuple[Finding, ...]:
        return tuple(finding for verdict in self.verdicts for finding in verdict.findings)

    @property
    def notes(self) -> tuple[Note, ...]:
        return tuple(note for verdict in self.verdicts for note in verdict.notes)

    @property
    def reasons(self) -> tuple[Reason, ...]:
        """The distinct reasons its rules could not judge, equal reasons once, in run order."""
        return tuple(dict.fromkeys(verdict.refusal.reason for verdict in self.verdicts if verdict.refusal is not None))

    @property
    def measurement(self) -> Measurement:
        judging = [verdict.measured_only for verdict in self.verdicts if verdict.refusal is None]
        if judging and all(judging):
            return Measurement.MEASURED_ONLY
        return Measurement.PARTLY_MEASURED_ONLY if any(judging) else Measurement.ENFORCED


@dataclass(frozen=True)
class CheckResult:
    areas: tuple[AreaVerdict, ...]
    tracked_paths: int

    @property
    def verdicts(self) -> tuple[RuleVerdict, ...]:
        return tuple(verdict for area in self.areas for verdict in area.verdicts)

    @property
    def findings(self) -> list[Finding]:
        return [finding for verdict in self.verdicts for finding in verdict.findings]

    @property
    def worst(self) -> Outcome:
        severity = list(Outcome)
        return max((verdict.outcome for verdict in self.verdicts), key=severity.index, default=Outcome.CLEAN)


def _attempt[T](check: Callable[[], T]) -> T | CannotJudge:
    """The check's result, or the reason it cannot judge, so one rule's refusal never stops the others."""
    try:
        return check()
    except CannotJudge as refusal:
        return refusal


type Running = Callable[[Area, str], AbstractContextManager[None]]


def _scanned[T](running: Running, area: Area, scanner: str, check: Callable[[], T]) -> T | CannotJudge:
    """The check's `_attempt` under the live line of `scanner`, which is down again before the area's row can print."""
    with running(area, scanner):
        return _attempt(check)


type Judged = RuleResult | python_floor.FloorResult | list[Finding] | CannotJudge
type AreaReport = Callable[[AreaVerdict, int], None]


def _rule_verdict(rule: Rule, judged: Judged) -> RuleVerdict:
    """The verdict of the rule a check judges; a check that cannot judge leaves its rule unjudged."""
    if isinstance(judged, CannotJudge):
        return RuleVerdict(rule, refusal=judged)
    findings = judged if isinstance(judged, list) else judged.findings
    foreign = sorted({finding.rule for finding in findings} - {rule})
    if foreign:
        raise ValueError(f"a check judging {rule} returned findings under {', '.join(foreign)}")
    if isinstance(judged, RuleResult):
        return RuleVerdict(rule, tuple(findings), measured_only=judged.measured_only, notes=judged.notes, extent=judged.extent)
    return RuleVerdict(rule, tuple(findings), notes=() if isinstance(judged, list) else judged.notes)


def _area_verdict(area: Area, judged: Mapping[Rule, Judged], runs: Sequence[ScannerRun] = ()) -> AreaVerdict:
    return AreaVerdict(area, tuple(_rule_verdict(rule, judged[rule]) for rule in Rule if rule.area is area), tuple(runs))


def _run_log(verbose: bool) -> tuple[list[ScannerRun], scanners.ScannerLog]:
    """Where an area's scanner runs go: a list under `-v`; without it no run is read, so nothing is kept and no vet starts."""
    runs: list[ScannerRun] = []
    return runs, (partial(scanners.keep_run, runs) if verbose else scanners.no_log)


def _judge_baseline_rules(
    sources: dict[str, bytes] | CannotJudge,
    baseline: ratchet.Baseline | None | CannotJudge,
    allow_lists: AllowLists | CannotJudge,
) -> tuple[RuleResult | CannotJudge, RuleResult | CannotJudge]:
    """The size and clone verdicts, both from one measurement of the tree and one read of the baseline."""
    if isinstance(sources, CannotJudge):
        return sources, sources
    if isinstance(baseline, CannotJudge):
        return baseline, baseline
    tree = ratchet.measure(sources)
    size = _attempt(lambda: ratchet.check_size(tree, baseline, _required(allow_lists).generated))
    return size, _attempt(lambda: clones.check_clones(tree, sources, baseline))


def _content_findings(
    root: Path,
    modes: dict[str, str] | CannotJudge,
    allow_lists: AllowLists | CannotJudge,
    candidates: Callable[[dict[str, str]], list[str]],
    check: Callable[..., list[Finding]],
) -> list[Finding] | CannotJudge:
    """The findings of a rule over the index modes and the contents of the files `candidates` names."""

    def judge() -> list[Finding]:
        indexed = _required(modes)
        contents = checkout.read_existing(repository.read_bytes, root, checkout.on_disk(root, candidates(indexed)))
        try:
            return check(indexed, contents, _required(allow_lists).layout)
        except layout.UnparsableTestModule as error:
            raise Unparsable(str(error)) from error

    return _attempt(judge)


def _judged_areas(
    root: Path, tracked: list[str], locate_ruff: Callable[[], str], locate_zizmor: Callable[[], str], running: Running, verbose: bool
) -> Iterator[AreaVerdict]:
    """Each area as its last rule ends, in the order the rows stand."""
    allow_lists = _attempt(lambda: _read_allow_lists(root))
    modes = _attempt(lambda: checkout.index_modes(root))
    yield _area_verdict(
        Area.FILES,
        {
            Rule.ENV_FILE: env_file.check_env_files(tracked),
            Rule.COMPOSE_PORT: _attempt(lambda: compose.check_compose_ports(root, tracked, _required(allow_lists).compose)),
            Rule.CLAUDE_MD: _attempt(lambda: claude_md.check_claude_md(root, tracked)),
            Rule.ROOT_LAYOUT: _attempt(lambda: root_layout.check_root_layout(tracked, _required(allow_lists).root)),
            Rule.FILE_SUFFIX: _attempt(lambda: layout.check_file_suffix(_required(modes), _required(allow_lists).layout)),
            Rule.SCRIPT_NAME: _content_findings(root, modes, allow_lists, layout.script_candidates, layout.check_script_name),
            Rule.SCRIPT_HEADER: _content_findings(root, modes, allow_lists, layout.script_candidates, layout.check_script_header),
            Rule.TEST_LAYOUT: _attempt(lambda: layout.check_test_layout(_required(modes), _required(allow_lists).layout)),
            Rule.TEST_IMPORT: _content_findings(root, modes, allow_lists, layout.test_import_candidates, layout.check_test_import),
            Rule.PYTHON_FLOOR: _attempt(lambda: _python_floor_result(root, modes, allow_lists)),
        },
    )
    sources = _attempt(lambda: ratchet.read_sources(root, tracked))
    baseline = sources if isinstance(sources, CannotJudge) else _attempt(lambda: judged_baseline(root))
    size, clone = _judge_baseline_rules(sources, baseline, allow_lists)
    yield _area_verdict(Area.DEBT, {Rule.SIZE: size, Rule.CLONE: clone})
    yield _area_verdict(Area.IMPORTS, {Rule.IMPORT_CONTRACT: _attempt(lambda: check_import_contract(root, tracked))})
    secret_runs, secret_log = _run_log(verbose)
    leak = _scanned(running, Area.SECRETS, scanners.GITLEAKS, lambda: check_secrets(root, allow_lists, secret_log))
    yield _area_verdict(Area.SECRETS, {Rule.LEAK: leak}, secret_runs)
    workflow_runs, workflow_log = _run_log(verbose)
    yield _area_verdict(
        Area.WORKFLOWS,
        {
            rule: _scanned(running, Area.WORKFLOWS, scanner.name, partial(check_workflows, root, rule, tracked, locate_zizmor, workflow_log))
            for rule, scanner in scanners.WORKFLOW_SCANNERS.items()
        },
        workflow_runs,
    )
    advisory_runs, advisory_log = _run_log(verbose)
    advisory = _scanned(running, Area.DEPENDENCIES, scanners.OSV_SCANNER, partial(check_advisories, root, tracked, allow_lists, advisory_log))
    yield _area_verdict(Area.DEPENDENCIES, {Rule.ADVISORY: advisory}, advisory_runs)
    code_runs, code_log = _run_log(verbose)
    lint = _scanned(running, Area.CODE, scanners.RUFF, lambda: lint_python(root, modes, locate_ruff, code_log))
    yield _area_verdict(
        Area.CODE,
        {Rule.PYTHON_SECURITY: _judge_lint(lint, python_lint.judge_security), Rule.PYTHON_PRINT: _judge_lint(lint, python_lint.judge_print)},
        code_runs,
    )


def judge_repository(
    root: Path, locate_ruff: Callable[[], str], locate_zizmor: Callable[[], str], report_area: AreaReport, running: Running,
    verbose: bool = False,
) -> CheckResult:
    """Judges every active rule over the repository `root` lies in, area by area in row order, and hands each finished
    area and the count of tracked paths to `report_area`; `running` holds the live line up around each scanner, and `verbose` keeps
    what each scanner said. Only
    one git cannot list stops the run."""
    root = checkout.work_tree_top(root)
    tracked = checkout.tracked_files(root)
    tracked_paths = len(set(tracked))
    areas: list[AreaVerdict] = []
    for area in _judged_areas(root, tracked, locate_ruff, locate_zizmor, running, verbose):
        report_area(area, tracked_paths)
        areas.append(area)
    return CheckResult(tuple(areas), tracked_paths)
