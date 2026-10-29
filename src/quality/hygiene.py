from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from functools import partial
from pathlib import Path, PurePosixPath

from quality import (
    advisories,
    architecture,
    checkout,
    claude_md,
    clones,
    compose,
    engines,
    env_file,
    layout,
    process,
    python_floor,
    python_lint,
    ratchet,
    repository,
    root_layout,
)
from quality.config import (
    Allowance,
    AllowLists,
    ConfigError,
    LayoutRule,
    read_allow_lists,
)
from quality.report import CannotJudge, Finding, Unparsable

WORKFLOW_RULE_IDS = tuple(engines.WORKFLOW_ENGINES)
ENGINE_RULE_IDS = (engines.LEAK_RULE, *WORKFLOW_RULE_IDS, advisories.RULE)


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
        raise CannotJudge(str(error)) from error


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
        raise CannotJudge(f"{error}; {ratchet.head_advice(error)}") from error


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


def check_secrets(root: Path, engine_dir: Path, allow_lists: AllowLists | CannotJudge) -> engines.SecretScan:
    try:
        return engines.scan_secrets(root, engine_dir, engines.read_pins(), _required(allow_lists).secrets)
    except process.ProcessError as error:
        raise CannotJudge(str(error)) from error


def check_workflows(root: Path, engine_dir: Path, rule: str, tracked: list[str]) -> list[Finding]:
    workflows = checkout.on_disk(root, [name for name in tracked if checkout.is_workflow(PurePosixPath(name))])
    try:
        return engines.scan_workflows(root, engine_dir, engines.read_pins(), rule, workflows)
    except process.ProcessError as error:
        raise CannotJudge(str(error)) from error


def check_advisories(root: Path, engine_dir: Path, tracked: list[str], allow_lists: AllowLists | CannotJudge) -> advisories.AdvisoryScan:
    """The allowances' `until` is judged against today's date in UTC, read here, once."""
    allowances = _required(allow_lists).advisories
    locks = {name: checkout.judged(repository.resolve_file, root, name) for name in checkout.on_disk(root, [n for n in tracked if advisories.is_lock(n)])}
    try:
        return advisories.scan(engine_dir, engines.read_pins(), locks, root, allowances, datetime.now(UTC).date())
    except process.ProcessError as error:
        raise CannotJudge(str(error)) from error


def skipped_rules(engine_dir: Path | None) -> tuple[str, ...]:
    return ENGINE_RULE_IDS if engine_dir is None else ()


class Outcome(Enum):
    """A rule's verdict, mildest first: the worst verdict of a run decides its exit."""

    CLEAN = "clean"
    FINDINGS = "findings"
    UNPARSABLE = Unparsable.label
    CANNOT_JUDGE = CannotJudge.label


@dataclass(frozen=True)
class RuleVerdict:
    rule: str
    findings: tuple[Finding, ...] = ()
    refusal: CannotJudge | None = None

    @property
    def outcome(self) -> Outcome:
        if self.refusal is not None:
            return Outcome(self.refusal.label)
        return Outcome.FINDINGS if self.findings else Outcome.CLEAN


@dataclass(frozen=True)
class HygieneResult:
    verdicts: tuple[RuleVerdict, ...]
    reports: tuple[str, ...]
    allowed_secrets: list[Finding]
    unused_allowances: dict[Allowance, str]

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


def _verdict(rule: str, outcome: Sequence[Finding] | CannotJudge) -> RuleVerdict:
    """The verdict of the rule a check judges; a check that cannot judge leaves its rule unjudged."""
    if isinstance(outcome, CannotJudge):
        return RuleVerdict(rule, refusal=outcome)
    foreign = sorted({finding.rule for finding in outcome} - {rule})
    if foreign:
        raise ValueError(f"a check judging {rule} returned findings under {', '.join(foreign)}")
    return RuleVerdict(rule, tuple(outcome))


def _judge_baseline_rules(
    sources: dict[str, bytes] | CannotJudge, baseline: ratchet.Baseline | None | CannotJudge
) -> tuple[ratchet.RuleResult | CannotJudge, ratchet.RuleResult | CannotJudge]:
    """The size and clone verdicts, both from one measurement of the tree and one read of the baseline."""
    if isinstance(sources, CannotJudge):
        return sources, sources
    if isinstance(baseline, CannotJudge):
        return baseline, baseline
    tree = ratchet.measure(sources)
    return _attempt(lambda: ratchet.check_size(tree, baseline)), _attempt(lambda: clones.check_clones(tree, sources, baseline))


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


def run_hygiene(root: Path, engine_dir: Path | None, locate_ruff: Callable[[], str]) -> HygieneResult:
    """Judges every active rule, in run order, over the repository `root` lies in; only one git cannot list stops the run."""
    root = checkout.work_tree_top(root)
    tracked = checkout.tracked_files(root)
    sources = _attempt(lambda: ratchet.read_sources(root, tracked))
    baseline = sources if isinstance(sources, CannotJudge) else _attempt(lambda: judged_baseline(root))
    size, clone = _judge_baseline_rules(sources, baseline)
    contracts = _attempt(lambda: check_import_contract(root, tracked))
    modes = _attempt(lambda: checkout.index_modes(root))
    allow_lists = _attempt(lambda: _read_allow_lists(root))
    floor = _attempt(lambda: _python_floor_result(root, modes, allow_lists))
    lint = _attempt(lambda: lint_python(root, modes, locate_ruff))
    printing = _judge_lint(lint, python_lint.judge_print)
    verdicts = [
        _verdict(env_file.ENV_FILE, env_file.check_env_files(tracked)),
        _verdict(compose.COMPOSE_PORT, _attempt(lambda: compose.check_compose_ports(root, tracked, _required(allow_lists).compose))),
        _verdict(ratchet.SIZE, size if isinstance(size, CannotJudge) else size.findings),
        _verdict(clones.RULE, clone if isinstance(clone, CannotJudge) else clone.findings),
        _verdict(architecture.RULE, contracts if isinstance(contracts, CannotJudge) else contracts.findings),
        _verdict(claude_md.CLAUDE_MD, _attempt(lambda: claude_md.check_claude_md(root, tracked))),
        _verdict(root_layout.ROOT_LAYOUT, _attempt(lambda: root_layout.check_root_layout(tracked, _required(allow_lists).root))),
        _verdict(LayoutRule.FILE_SUFFIX.value, _attempt(lambda: layout.check_file_suffix(_required(modes), _required(allow_lists).layout))),
        _verdict(LayoutRule.SCRIPT_NAME.value, _content_findings(root, modes, allow_lists, layout.script_candidates, layout.check_script_name)),
        _verdict(LayoutRule.SCRIPT_HEADER.value, _content_findings(root, modes, allow_lists, layout.script_candidates, layout.check_script_header)),
        _verdict(LayoutRule.TEST_LAYOUT.value, _attempt(lambda: layout.check_test_layout(_required(modes), _required(allow_lists).layout))),
        _verdict(LayoutRule.TEST_IMPORT.value, _content_findings(root, modes, allow_lists, layout.test_import_candidates, layout.check_test_import)),
        _verdict(python_floor.PYTHON_FLOOR, floor if isinstance(floor, CannotJudge) else floor.findings),
        _verdict(python_lint.SECURITY_RULE, _judge_lint(lint, python_lint.judge_security)),
        _verdict(python_lint.PRINT_RULE, printing if isinstance(printing, CannotJudge) else printing.findings),
    ]
    reports = _reports(size, clone, contracts, floor, printing)
    if engine_dir is None:
        return HygieneResult(tuple(verdicts), reports, [], {})
    secrets = _attempt(lambda: check_secrets(root, engine_dir, allow_lists))
    verdicts.append(_verdict(engines.LEAK_RULE, secrets if isinstance(secrets, CannotJudge) else secrets.findings))
    verdicts += [_verdict(rule, _attempt(partial(check_workflows, root, engine_dir, rule, tracked))) for rule in WORKFLOW_RULE_IDS]
    advisory = _attempt(partial(check_advisories, root, engine_dir, tracked, allow_lists))
    verdicts.append(_verdict(advisories.RULE, advisory if isinstance(advisory, CannotJudge) else advisory.findings))
    allowed_advisories = () if isinstance(advisory, CannotJudge) else advisory.report()
    allowed, unused = ([], {}) if isinstance(secrets, CannotJudge) else (secrets.allowed, secrets.unused_allowances)
    return HygieneResult(tuple(verdicts), (*reports, *allowed_advisories), allowed, unused)


def _reports(
    size: ratchet.RuleResult | CannotJudge,
    clone: ratchet.RuleResult | CannotJudge,
    contracts: architecture.ContractsVerdict | CannotJudge,
    floor: python_floor.FloorResult | CannotJudge,
    printing: python_lint.PrintResult | CannotJudge,
) -> tuple[str, ...]:
    """The report lines of the rules that judged: the size counts without a baseline, the clone pairs without a `[clones]` table, the import-contract notes, each python-floor exception, each output owner."""
    size_reports = [] if isinstance(size, CannotJudge) or size.report is None else [size.report]
    clone_reports = [] if isinstance(clone, CannotJudge) or clone.report is None else [clone.report]
    contract_notes = [] if isinstance(contracts, CannotJudge) else contracts.notes
    floor_reports = () if isinstance(floor, CannotJudge) else floor.reports
    owner_reports = () if isinstance(printing, CannotJudge) else printing.reports
    return (*size_reports, *clone_reports, *contract_notes, *floor_reports, *owner_reports)
