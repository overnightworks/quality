from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum, auto
from functools import partial
from pathlib import Path, PurePosixPath
from typing import Protocol

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
    Allowance,
    AllowLists,
    ConfigError,
    read_allow_lists,
)
from quality.report import CannotJudge, Example, Finding, Note, Reason, Unparsable
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


def check_import_contract(root: Path, tracked: list[str]) -> architecture.ContractsVerdict:
    """Report-only without an import-linter config at the top; with one, its contracts must be kept.

    A config counts when its name exists, so a tracked one deleted from the tree is none and a link to nowhere cannot be read.
    """
    configs = checkout.read_existing(repository.read_text, root, [name for name in tracked if name in architecture.CONFIG_FILES])
    try:
        return architecture.check_contracts(root, tracked, configs)
    except architecture.UnparsableModule as error:
        raise Unparsable(str(error)) from error
    except architecture.ContractError as error:
        raise CannotJudge(str(error)) from error


def lint_python(root: Path, modes: dict[str, str] | CannotJudge, locate_ruff: Callable[[], str]) -> python_lint.Lint:
    """ruff's one run over every tracked regular `.py` file, which python-security and python-print both judge."""
    try:
        return python_lint.lint(root, _required(modes), python_floor.PYTHON_TARGET, locate_ruff)
    except python_lint.UnparsablePython as error:
        raise Unparsable(str(error)) from error
    except python_lint.LintError as error:
        raise CannotJudge(str(error)) from error


def _judge_lint[T](lint: python_lint.Lint | CannotJudge, judge: Callable[[python_lint.Lint], T]) -> T | CannotJudge:
    """One rule's verdict on the shared run; a run that cannot judge refuses both rules, a judge's own refusal only its rule."""
    if isinstance(lint, CannotJudge):
        return lint
    try:
        return judge(lint)
    except python_lint.LintError as error:
        return CannotJudge(str(error))


def check_secrets(root: Path, allow_lists: AllowLists | CannotJudge) -> scanners.SecretScan:
    try:
        return scanners.scan_secrets(root, _required(allow_lists).secrets)
    except process.ProcessError as error:
        raise CannotJudge(str(error)) from error


def check_workflows(root: Path, rule: Rule, tracked: list[str], locate_zizmor: Callable[[], str]) -> list[Finding]:
    workflows = checkout.on_disk(root, [name for name in tracked if checkout.is_workflow(PurePosixPath(name))])
    try:
        return scanners.scan_workflows(root, rule, workflows, locate_zizmor)
    except process.ProcessError as error:
        raise CannotJudge(str(error)) from error


def check_advisories(root: Path, tracked: list[str], allow_lists: AllowLists | CannotJudge) -> advisories.AdvisoryScan:
    """The allowances' `until` is judged against today's date in UTC, read here, once."""
    allowances = _required(allow_lists).advisories
    locks = {name: checkout.judged(repository.resolve_file, root, name) for name in checkout.on_disk(root, [n for n in tracked if advisories.is_lock(n)])}
    try:
        return advisories.scan(locks, root, allowances, datetime.now(UTC).date())
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
class HygieneResult:
    verdicts: tuple[RuleVerdict, ...]
    reports: tuple[str, ...]
    tracked_paths: int
    allowed_secrets: list[Finding]
    unused_allowances: dict[Allowance, str]

    @property
    def findings(self) -> list[Finding]:
        return [finding for verdict in self.verdicts for finding in verdict.findings]

    @property
    def areas(self) -> tuple[AreaVerdict, ...]:
        return tuple(AreaVerdict(area, tuple(verdict for verdict in self.verdicts if verdict.rule.area is area)) for area in Area)

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


class _Judged(Protocol):
    @property
    def findings(self) -> Sequence[Finding]: ...


def _findings(judged: _Judged | CannotJudge) -> Sequence[Finding] | CannotJudge:
    """What a rule's check found, or the reason it cannot judge."""
    return judged if isinstance(judged, CannotJudge) else judged.findings


def _verdict(rule: Rule, outcome: Sequence[Finding] | CannotJudge, measured_only: bool, notes: tuple[Note, ...]) -> RuleVerdict:
    """The verdict of the rule a check judges; a check that cannot judge leaves its rule unjudged."""
    if isinstance(outcome, CannotJudge):
        return RuleVerdict(rule, refusal=outcome)
    foreign = sorted({finding.rule for finding in outcome} - {rule})
    if foreign:
        raise ValueError(f"a check judging {rule} returned findings under {', '.join(foreign)}")
    return RuleVerdict(rule, tuple(outcome), measured_only=measured_only, notes=notes)


def _judge_baseline_rules(
    sources: dict[str, bytes] | CannotJudge,
    baseline: ratchet.Baseline | None | CannotJudge,
    allow_lists: AllowLists | CannotJudge,
) -> tuple[ratchet.RuleResult | CannotJudge, ratchet.RuleResult | CannotJudge]:
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


def run_hygiene(root: Path, locate_ruff: Callable[[], str], locate_zizmor: Callable[[], str]) -> HygieneResult:
    """Judges every active rule, in run order, over the repository `root` lies in; only one git cannot list stops the run."""
    root = checkout.work_tree_top(root)
    tracked = checkout.tracked_files(root)
    sources = _attempt(lambda: ratchet.read_sources(root, tracked))
    baseline = sources if isinstance(sources, CannotJudge) else _attempt(lambda: judged_baseline(root))
    allow_lists = _attempt(lambda: _read_allow_lists(root))
    size, clone = _judge_baseline_rules(sources, baseline, allow_lists)
    contracts = _attempt(lambda: check_import_contract(root, tracked))
    modes = _attempt(lambda: checkout.index_modes(root))
    floor = _attempt(lambda: _python_floor_result(root, modes, allow_lists))
    lint = _attempt(lambda: lint_python(root, modes, locate_ruff))
    printing = _judge_lint(lint, python_lint.judge_print)
    outcomes: dict[Rule, Sequence[Finding] | CannotJudge] = {
        Rule.ENV_FILE: env_file.check_env_files(tracked),
        Rule.COMPOSE_PORT: _attempt(lambda: compose.check_compose_ports(root, tracked, _required(allow_lists).compose)),
        Rule.SIZE: _findings(size),
        Rule.CLONE: _findings(clone),
        Rule.IMPORT_CONTRACT: _findings(contracts),
        Rule.CLAUDE_MD: _attempt(lambda: claude_md.check_claude_md(root, tracked)),
        Rule.ROOT_LAYOUT: _attempt(lambda: root_layout.check_root_layout(tracked, _required(allow_lists).root)),
        Rule.FILE_SUFFIX: _attempt(lambda: layout.check_file_suffix(_required(modes), _required(allow_lists).layout)),
        Rule.SCRIPT_NAME: _content_findings(root, modes, allow_lists, layout.script_candidates, layout.check_script_name),
        Rule.SCRIPT_HEADER: _content_findings(root, modes, allow_lists, layout.script_candidates, layout.check_script_header),
        Rule.TEST_LAYOUT: _attempt(lambda: layout.check_test_layout(_required(modes), _required(allow_lists).layout)),
        Rule.TEST_IMPORT: _content_findings(root, modes, allow_lists, layout.test_import_candidates, layout.check_test_import),
        Rule.PYTHON_FLOOR: _findings(floor),
        Rule.PYTHON_SECURITY: _judge_lint(lint, python_lint.judge_security),
        Rule.PYTHON_PRINT: _findings(printing),
    }
    secrets = _attempt(lambda: check_secrets(root, allow_lists))
    advisory = _attempt(partial(check_advisories, root, tracked, allow_lists))
    outcomes |= {
        Rule.LEAK: _findings(secrets),
        **{rule: _attempt(partial(check_workflows, root, rule, tracked, locate_zizmor)) for rule in scanners.WORKFLOW_SCANNERS},
        Rule.ADVISORY: _findings(advisory),
    }
    measuring = {rule for rule, result in ((Rule.SIZE, size), (Rule.CLONE, clone)) if _measures_only(result)}
    notes = {Rule.PYTHON_FLOOR: () if isinstance(floor, CannotJudge) else floor.notes}
    verdicts = [_verdict(rule, outcomes[rule], rule in measuring, notes.get(rule, ())) for rule in Rule]
    reports = _reports(size, clone, contracts, printing)
    allowed_advisories = () if isinstance(advisory, CannotJudge) else advisory.report()
    allowed, unused = ([], {}) if isinstance(secrets, CannotJudge) else (secrets.allowed, secrets.unused_allowances)
    return HygieneResult(tuple(verdicts), (*reports, *allowed_advisories), len(set(tracked)), allowed, unused)


def _measures_only(result: ratchet.RuleResult | CannotJudge) -> bool:
    return not isinstance(result, CannotJudge) and result.measured_only is not None


def _measured_notes(result: ratchet.RuleResult | CannotJudge) -> list[str]:
    return [result.measured_only.note] if not isinstance(result, CannotJudge) and result.measured_only else []


def _reports(
    size: ratchet.RuleResult | CannotJudge,
    clone: ratchet.RuleResult | CannotJudge,
    contracts: architecture.ContractsVerdict | CannotJudge,
    printing: python_lint.PrintResult | CannotJudge,
) -> tuple[str, ...]:
    """The report lines of the rules that judged: the size counts without a baseline, the clone pairs without a `[clones]` table, the import-contract notes, each output owner."""
    size_reports = _measured_notes(size)
    clone_reports = _measured_notes(clone)
    contract_notes = [] if isinstance(contracts, CannotJudge) else contracts.notes
    owner_reports = () if isinstance(printing, CannotJudge) else printing.reports
    return (*size_reports, *clone_reports, *contract_notes, *owner_reports)
