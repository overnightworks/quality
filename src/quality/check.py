from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import Enum, auto
from functools import cached_property, partial
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
    AllowLists,
    ConfigError,
    read_allow_lists,
)
from quality.output import SEPARATOR
from quality.report import (
    CannotJudge,
    Extent,
    Finding,
    Instruction,
    Note,
    NoteEvent,
    Reason,
    RuleResult,
    ScannerRun,
    Unparsable,
    config_refusal,
)
from quality.rules import Area, Rule


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
        raise config_refusal(error) from error


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
    judged = workflows
    if rule is Rule.WORKFLOW_SECURITY:
        judged = [*workflows, *checkout.on_disk(root, [name for name in tracked if checkout.is_action(PurePosixPath(name))])]
    try:
        return RuleResult(scanners.scan_workflows(root, rule, judged, locate_zizmor, log), len(workflows), False)
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
    """A rule's or a dry run's verdict, mildest first: the worst verdict of a run decides its exit."""

    CLEAN = "clean"
    MEASURED_ONLY = "measured only"
    WOULD_CHANGE = "would change"
    FINDINGS = "findings"
    UNPARSABLE = Unparsable.label
    CANNOT_JUDGE = CannotJudge.label


class Fix(Enum):
    """What `--fix` does with the ceiling it makes: writes it, or under `--dry-run` only shows it."""

    WRITE = auto()
    DRY_RUN = auto()


DRY_RUN_WORD = "--dry-run"
DRY_RUN_COMMAND = f"{ratchet.FIX_COMMAND} {DRY_RUN_WORD}"
FIX_RUN_COMMAND = {Fix.WRITE: ratchet.FIX_COMMAND, Fix.DRY_RUN: DRY_RUN_COMMAND}


PLANNED_EVENT = {
    NoteEvent.LOWERED: NoteEvent.LOWER,
    NoteEvent.DROPPED: NoteEvent.DROP,
    NoteEvent.MOVED: NoteEvent.MOVE,
    NoteEvent.RECORDED: NoteEvent.RECORD,
}


@dataclass(frozen=True)
class DryRun:
    """What `--fix` would make of the ceiling, not written."""

    fix: ratchet.CeilingFix

    @property
    def would_change(self) -> bool:
        return self.fix.changed


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
    ceiling: ratchet.CeilingFix | DryRun | CannotJudge | None = None

    @property
    def outcome(self) -> Outcome:
        severity = list(Outcome)
        outcomes = [verdict.outcome for verdict in self.verdicts]
        if isinstance(self.ceiling, CannotJudge):
            outcomes.append(Outcome(self.ceiling.label))
        if isinstance(self.ceiling, DryRun) and self.ceiling.would_change:
            outcomes.append(Outcome.WOULD_CHANGE)
        return max(outcomes, key=severity.index, default=Outcome.CLEAN)

    @property
    def only_the_fix_refused(self) -> bool:
        return isinstance(self.ceiling, CannotJudge) and all(verdict.refusal is None for verdict in self.verdicts)

    @property
    def findings(self) -> tuple[Finding, ...]:
        return tuple(finding for verdict in self.verdicts for finding in verdict.findings)

    @property
    def notes(self) -> tuple[Note, ...]:
        return tuple(note for verdict in self.verdicts for note in verdict.notes)

    @property
    def reasons(self) -> tuple[Reason, ...]:
        """The distinct reasons its rules could not judge, equal reasons once, in run order."""
        reasons = [verdict.refusal.reason for verdict in self.verdicts if verdict.refusal is not None]
        if isinstance(self.ceiling, CannotJudge):
            reasons.append(self.ceiling.reason)
        return tuple(dict.fromkeys(reasons))

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
    def ceiling(self) -> ratchet.CeilingFix | DryRun | None:
        return next((area.ceiling for area in self.areas if isinstance(area.ceiling, ratchet.CeilingFix | DryRun)), None)

    @property
    def worst(self) -> Outcome:
        severity = list(Outcome)
        return max((area.outcome for area in self.areas), key=severity.index, default=Outcome.CLEAN)


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


def _area_verdict(
    area: Area, judged: Mapping[Rule, Judged], runs: Sequence[ScannerRun] = (), ceiling: ratchet.CeilingFix | DryRun | CannotJudge | None = None
) -> AreaVerdict:
    return AreaVerdict(area, tuple(_rule_verdict(rule, judged[rule]) for rule in Rule if rule.area is area), tuple(runs), ceiling)


def _run_log(verbose: bool) -> tuple[list[ScannerRun], scanners.ScannerLog]:
    """Where an area's scanner runs go: a list under `-v`; without it no run is read, so nothing is kept and no vet starts."""
    runs: list[ScannerRun] = []
    return runs, (partial(scanners.keep_run, runs) if verbose else scanners.no_log)


def _judge_baseline_rules(
    sources: dict[str, bytes] | CannotJudge,
    baseline: ratchet.Baseline | None | CannotJudge,
    allow_lists: AllowLists | CannotJudge,
    tracked: frozenset[str],
) -> tuple[RuleResult | CannotJudge, RuleResult | CannotJudge]:
    """The size and clone verdicts, both from one measurement of the tree and one read of the baseline."""
    if isinstance(sources, CannotJudge):
        return sources, sources
    if isinstance(baseline, CannotJudge):
        return baseline, baseline
    tree = ratchet.measure(sources)
    size = _attempt(lambda: ratchet.check_size(tree, baseline, _required(allow_lists).generated, tracked))
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


@dataclass(frozen=True)
class RunInputs:
    """What every area of one run reads: the repository and what the door injected. Each shared read happens once,
    when the first area that needs it asks, so a run of other areas never starts it."""

    root: Path
    tracked: list[str]
    locate_ruff: Callable[[], str]
    locate_zizmor: Callable[[], str]
    running: Running
    verbose: bool
    fix: Fix | None

    @cached_property
    def allow_lists(self) -> AllowLists | CannotJudge:
        return _attempt(lambda: _read_allow_lists(self.root))

    @cached_property
    def modes(self) -> dict[str, str] | CannotJudge:
        return _attempt(lambda: checkout.index_modes(self.root))


def _judge_files(inputs: RunInputs) -> AreaVerdict:
    root, tracked, allow_lists, modes = inputs.root, inputs.tracked, inputs.allow_lists, inputs.modes
    return _area_verdict(
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


def _require_whole_history(inputs: RunInputs) -> None:
    if checkout.is_shallow(inputs.root):
        raise CannotJudge("this clone is shallow, so git cannot show the ceiling is new", (checkout.UNSHALLOW_STEP,))


def _require_no_ceiling_in_git(inputs: RunInputs) -> None:
    if ratchet.BASELINE_PATH in inputs.tracked or checkout.blobs_of(inputs.root, ratchet.BASELINE_PATH):
        raise CannotJudge(f"{ratchet.BASELINE_PATH} is gone from the tree but git holds it", (ratchet.RESTORE_STEP,))
    _require_whole_history(inputs)


def _require_no_clones_table_in_git(inputs: RunInputs) -> None:
    name = ratchet.BASELINE_PATH
    revisions = [f":{name}"] if name in inputs.tracked else []
    revisions += checkout.blobs_of(inputs.root, name)
    try:
        held = any(ratchet.holds_clones_table(checkout.blob(inputs.root, revision)) for revision in revisions)
    except ratchet.SizeError as error:
        raise CannotJudge(str(error), (ratchet.RESTORE_STEP,)) from error
    if held:
        raise CannotJudge(f"{name} lost its [clones] table, which git holds", (ratchet.RESTORE_STEP,))
    _require_whole_history(inputs)


def _written(inputs: RunInputs, fix: ratchet.CeilingFix) -> ratchet.CeilingFix | DryRun:
    """A changed ceiling rendered, so one no file can hold refuses a dry run too; written only by the fix."""
    if fix.changed:
        try:
            text = ratchet.render_baseline(fix.ceiling)
            if inputs.fix is Fix.WRITE:
                repository.write_text(inputs.root, ratchet.BASELINE_PATH, text)
        except (repository.RepositoryFileError, ratchet.SizeError) as error:
            raise CannotJudge(str(error)) from error
    return DryRun(fix) if inputs.fix is Fix.DRY_RUN else fix


def _with_what_they_held(gone: dict[ratchet.Key, str], held: ratchet.SizeEntries) -> dict[ratchet.Key, str]:
    """Each gone entry's reason, then the value the ceiling held."""
    return {key: SEPARATOR.join((why, f"was {key.metric.describe(held[key])}")) for key, why in gone.items()}


def _commit_repair_step(fix: Fix | None) -> Instruction:
    return Instruction(f"commit the repaired {ratchet.BASELINE_PATH}, then run {FIX_RUN_COMMAND.get(fix, ratchet.FIX_COMMAND)} again")


def _ceiling_lineage(inputs: RunInputs) -> tuple[ratchet.Baseline, ratchet.Renames] | None:
    """The ceiling at its last commit and what git renamed from there to the index; None where no commit holds it."""
    change = checkout.last_change(inputs.root, ratchet.BASELINE_PATH)
    if change is None:
        return None
    if not change.has_parent and checkout.is_shallow(inputs.root):
        fact = f"this clone is shallow, so git cannot show where {ratchet.BASELINE_PATH} last changed"
        raise CannotJudge(fact, (checkout.UNSHALLOW_STEP,))
    where = f"{ratchet.BASELINE_PATH} at its last commit {change.commit}"
    try:
        text = process.read_blob(inputs.root, change.commit, ratchet.BASELINE_PATH, f"cannot read {where}", f"cannot read {where}")
    except process.ProcessError as error:
        raise CannotJudge(str(error)) from error
    if text is None:
        return None
    try:
        committed = ratchet.load_baseline(text)
    except ratchet.SizeError as error:
        raise CannotJudge(f"the ceiling at its last commit {change.commit}: {error}", (_commit_repair_step(inputs.fix),)) from error
    return committed, ratchet.renames_of(checkout.renamed_since(inputs.root, change.commit))


def _followed(inputs: RunInputs, held: ratchet.Baseline) -> ratchet.CeilingFix:
    lineage = _ceiling_lineage(inputs)
    return ratchet.CeilingFix(held, ()) if lineage is None else ratchet.follow_renames(held, *lineage, frozenset(inputs.tracked))


def _fix_ceiling(inputs: RunInputs, sources: dict[str, bytes], held: ratchet.Baseline | None) -> ratchet.CeilingFix:
    """The ceiling lowered, or recorded where git never held one; the caller writes it."""
    tree = ratchet.measure(sources)
    ratchet.complete(tree)
    generated = _required(inputs.allow_lists).generated
    measurements = ratchet.without_generated(tree, generated).measurements
    pairs = clones.find_pairs(sources).pairs
    if held is None:
        _require_no_ceiling_in_git(inputs)
        return ratchet.CeilingFix(ratchet.Baseline(ratchet.debt(measurements), pairs), (), first=True)
    if held.clones is None:
        _require_no_clones_table_in_git(inputs)
    followed = _followed(inputs, held)  # renames first, so a renamed file's entries move instead of falling as gone
    gone = ratchet.gone_entries(followed.ceiling.size, measurements, frozenset(inputs.tracked), generated)
    if inputs.fix is Fix.DRY_RUN:
        gone = _with_what_they_held(gone, followed.ceiling.size)
    lowered = ratchet.lower_ceiling(followed.ceiling, measurements, pairs, gone)
    return replace(lowered, changes=(*followed.changes, *lowered.changes))


def _reported(ceiling: ratchet.CeilingFix | DryRun) -> ratchet.CeilingFix:
    """The fix whose changes the debt rows list: done, or under a dry run planned."""
    if isinstance(ceiling, ratchet.CeilingFix):
        return ceiling
    planned = tuple(replace(change, event=PLANNED_EVENT[change.event]) for change in ceiling.fix.changes)
    return replace(ceiling.fix, changes=planned)


def _judge_debt(inputs: RunInputs) -> AreaVerdict:
    sources = _attempt(lambda: ratchet.read_sources(inputs.root, inputs.tracked))
    baseline = sources if isinstance(sources, CannotJudge) else _attempt(lambda: judged_baseline(inputs.root))
    ceiling = None
    if inputs.fix is not None and not isinstance(baseline, CannotJudge):
        ceiling = _attempt(lambda: _written(inputs, _fix_ceiling(inputs, sources, baseline)))
    made = ceiling.fix if isinstance(ceiling, DryRun) else ceiling
    fixed = isinstance(made, ratchet.CeilingFix)
    size, clone = _judge_baseline_rules(
        sources, made.ceiling if fixed else baseline, inputs.allow_lists, frozenset(inputs.tracked)
    )
    if fixed:
        size = ratchet.after_fix(_required(size), _reported(ceiling))  # the config was read and the tree is complete
    return _area_verdict(Area.DEBT, {Rule.SIZE: size, Rule.CLONE: clone}, ceiling=ceiling)


def _judge_imports(inputs: RunInputs) -> AreaVerdict:
    return _area_verdict(Area.IMPORTS, {Rule.IMPORT_CONTRACT: _attempt(lambda: check_import_contract(inputs.root, inputs.tracked))})


def _judge_secrets(inputs: RunInputs) -> AreaVerdict:
    runs, log = _run_log(inputs.verbose)
    leak = _scanned(inputs.running, Area.SECRETS, scanners.GITLEAKS, lambda: check_secrets(inputs.root, inputs.allow_lists, log))
    return _area_verdict(Area.SECRETS, {Rule.LEAK: leak}, runs)


def _judge_workflows(inputs: RunInputs) -> AreaVerdict:
    runs, log = _run_log(inputs.verbose)
    judged = {
        rule: _scanned(inputs.running, Area.WORKFLOWS, scanner.name, partial(check_workflows, inputs.root, rule, inputs.tracked, inputs.locate_zizmor, log))
        for rule, scanner in scanners.WORKFLOW_SCANNERS.items()
    }
    return _area_verdict(Area.WORKFLOWS, judged, runs)


def _judge_dependencies(inputs: RunInputs) -> AreaVerdict:
    runs, log = _run_log(inputs.verbose)
    advisory = _scanned(
        inputs.running, Area.DEPENDENCIES, scanners.OSV_SCANNER, partial(check_advisories, inputs.root, inputs.tracked, inputs.allow_lists, log)
    )
    return _area_verdict(Area.DEPENDENCIES, {Rule.ADVISORY: advisory}, runs)


def _judge_code(inputs: RunInputs) -> AreaVerdict:
    runs, log = _run_log(inputs.verbose)
    lint = _scanned(inputs.running, Area.CODE, scanners.RUFF, lambda: lint_python(inputs.root, inputs.modes, inputs.locate_ruff, log))
    judged = {Rule.PYTHON_SECURITY: _judge_lint(lint, python_lint.judge_security), Rule.PYTHON_PRINT: _judge_lint(lint, python_lint.judge_print)}
    return _area_verdict(Area.CODE, judged, runs)


AREA_JUDGES: Mapping[Area, Callable[[RunInputs], AreaVerdict]] = {
    Area.FILES: _judge_files,
    Area.DEBT: _judge_debt,
    Area.IMPORTS: _judge_imports,
    Area.SECRETS: _judge_secrets,
    Area.WORKFLOWS: _judge_workflows,
    Area.DEPENDENCIES: _judge_dependencies,
    Area.CODE: _judge_code,
}


def _judged_areas(inputs: RunInputs, selected: frozenset[Area]) -> Iterator[AreaVerdict]:
    """Each selected area as its last rule ends, in the order the rows stand."""
    return (AREA_JUDGES[area](inputs) for area in Area if area in selected)


def judge_repository(
    root: Path, locate_ruff: Callable[[], str], locate_zizmor: Callable[[], str], report_area: AreaReport, running: Running,
    verbose: bool = False,
    named: Iterable[Area] = (),
    fix: Fix | None = None,
) -> CheckResult:
    """Judges every active rule of the areas `named`, of all of them where none is, over the repository `root` lies
    in, area by area in row order, and hands each finished area and the count of tracked paths to `report_area`;
    `running` holds the live line up around each scanner, and `verbose` keeps what each scanner said. Only one git
    cannot list stops the run."""
    root = checkout.work_tree_top(root)
    tracked = checkout.tracked_files(root)
    tracked_paths = len(set(tracked))
    inputs = RunInputs(root, tracked, locate_ruff, locate_zizmor, running, verbose, fix)
    areas: list[AreaVerdict] = []
    for area in _judged_areas(inputs, frozenset(named) or frozenset(Area)):
        report_area(area, tracked_paths)
        areas.append(area)
    return CheckResult(tuple(areas), tracked_paths)


type StagedScan = Callable[[Path, scanners.ScannerLog], RuleResult]


def _check_staged_change(scan: StagedScan, root: Path, log: scanners.ScannerLog) -> RuleResult:
    try:
        return scan(root, log)
    except process.ProcessError as error:
        raise CannotJudge(str(error)) from error


def judge_staged_change(
    root: Path, scan: StagedScan, report_area: AreaReport, running: Running, verbose: bool = False
) -> CheckResult:
    """Judges the secrets of what the next commit records and nothing else: `scan` comes from the door, because `staged`
    lies beside this module. Its one area counts no tracked path, which only `files` prints."""
    checkout.work_tree_top(root)  # the start check alone: a relative GIT_DIR names itself from the folder the hook stands in, so `scan` gets that folder
    runs, log = _run_log(verbose)
    leak = _scanned(running, Area.SECRETS, scanners.GITLEAKS, lambda: _check_staged_change(scan, root, log))
    area = _area_verdict(Area.SECRETS, {Rule.LEAK: leak}, runs)
    report_area(area, 0)
    return CheckResult((area,), 0)
