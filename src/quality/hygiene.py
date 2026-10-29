from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from functools import partial
from pathlib import Path, PurePosixPath

from packaging.specifiers import Specifier, SpecifierSet
from packaging.version import Version

from quality import (
    advisories,
    architecture,
    checkout,
    clones,
    compose,
    engines,
    layout,
    process,
    python_lint,
    ratchet,
    repository,
)
from quality.config import (
    CONFIG_PATH,
    Allowance,
    ConfigError,
    LayoutRule,
    PythonAllowance,
    RootAllowance,
    read_advisory_allowances,
    read_compose_allowances,
    read_layout_allowances,
    read_python_allowances,
    read_root_allowances,
    read_secret_allowances,
)
from quality.report import CannotJudge, Finding, Unparsable, shell_word

ENV_FILE = "env-file"
CLAUDE_MD = "claude-md"
ROOT_LAYOUT = "root-layout"
FILE_SUFFIX = LayoutRule.FILE_SUFFIX.value
SCRIPT_NAME = LayoutRule.SCRIPT_NAME.value
SCRIPT_HEADER = LayoutRule.SCRIPT_HEADER.value
TEST_LAYOUT = LayoutRule.TEST_LAYOUT.value
TEST_IMPORT = LayoutRule.TEST_IMPORT.value
PYTHON_FLOOR = "python-floor"
PYTHON_SECURITY = python_lint.SECURITY_RULE
PYTHON_PRINT = python_lint.PRINT_RULE
PYTHON_TARGET = Version("3.14")
LOWER_BOUND_OPERATORS = frozenset({">=", ">", "~=", "==", "==="})
WORKFLOW_RULE_IDS = tuple(engines.WORKFLOW_ENGINES)
ENGINE_RULE_IDS = (engines.LEAK_RULE, *WORKFLOW_RULE_IDS, advisories.RULE)

CLAUDE_NESTED_NAME = ".claude/claude.md"
CLAUDE_FILE_NAMES = frozenset({"claude.md", CLAUDE_NESTED_NAME})
CLAUDE_IMPORTS = frozenset({b"@AGENTS.md", b"@AGENTS.md\n"})
AGENTS_FILE = "AGENTS.md"

# A directory is written with its trailing slash, as `[[root.allow]]` writes it.
ROOT_BASE_FILES = frozenset({
    "README.md", "AGENTS.md", "CLAUDE.md", "HEART.md", "LICENSE",
    ".gitignore", ".gitattributes", ".gitmodules",
    "pyproject.toml", "uv.lock", ".python-version",
    "package.json", "pnpm-lock.yaml", "package-lock.json",
    "Cargo.toml", "Cargo.lock", "rust-toolchain.toml",
    "Dockerfile", ".dockerignore", "compose.yaml", "compose.yml", "docker-compose.yaml", "docker-compose.yml",
    "sonar-project.properties", ".importlinter", ".pre-commit-config.yaml",
})
ROOT_BASE_DIRECTORIES = frozenset({".github/", ".aco/", "quality/", "src/", "tests/", "docs/", "scripts/"})
ROOT_BASE_ENTRIES = ROOT_BASE_FILES | ROOT_BASE_DIRECTORIES

ENV_TEMPLATE_SUFFIX = ".example"


def _untrack_fix(name: str) -> str:
    word = shell_word(name)
    if word is None:
        return "remove it from the index by its bytes (git ls-files -z shows them), then add it to .gitignore."
    return f"git --literal-pathspecs rm --cached -- {word} and add it to .gitignore."


def check_env_files(tracked: list[str]) -> list[Finding]:
    return [
        Finding(
            ENV_FILE, name, None,
            "a tracked env file publishes configuration and often secrets.",
            _untrack_fix(name),
        )
        for name in tracked
        if _is_env_file(PurePosixPath(name).name)
    ]


def _is_env_file(name: str) -> bool:
    if name.endswith(ENV_TEMPLATE_SUFFIX):
        return False
    return name == ".env" or name.startswith(".env.") or name.endswith(".env")


def check_claude_md(root: Path, tracked: list[str]) -> list[Finding]:
    """A tracked CLAUDE.md, in any case, is exactly `@AGENTS.md` beside a tracked AGENTS.md; `.claude/CLAUDE.md` is none.

    The findings carry fixed texts and the file's name, never its content.
    """
    findings = []
    for name in (name for name in tracked if name.casefold() in CLAUDE_FILE_NAMES):
        problem = _claude_md_problem(root, name, AGENTS_FILE in tracked)
        if problem is not None:
            findings.append(Finding(CLAUDE_MD, name, None, *problem))
    return findings


def _claude_md_problem(root: Path, name: str, agents_tracked: bool) -> tuple[str, str] | None:
    """The reason and the fix for a CLAUDE.md that is not clean; None when it is clean or gone from the tree."""
    is_link = checkout.judged(repository.is_symlink, root, name)
    if name.casefold() == CLAUDE_NESTED_NAME:
        if not (is_link or checkout.judged(repository.is_on_disk, root, name)):
            return None
        return (f"{name} gives Claude guidance Codex and Grok never read.",
                f"move its lines into {AGENTS_FILE} and delete {name}.")
    if is_link:
        return (f"{name} is a symlink, not a file holding @AGENTS.md.",
                f"replace the link with a file whose only line is @AGENTS.md; {AGENTS_FILE} stays as it is.")
    on_disk = checkout.judged(repository.is_on_disk, root, name)
    content = checkout.judged(repository.read_bytes, root, name) if on_disk else None
    if content is None:
        return None
    if content not in CLAUDE_IMPORTS:
        return (f"{name} is not exactly @AGENTS.md, so Claude reads other guidance than Codex and Grok.",
                f"move its own lines into {AGENTS_FILE}, then leave @AGENTS.md as its only line, or delete it.")
    if not (agents_tracked and checkout.judged(repository.is_on_disk, root, AGENTS_FILE)):
        return (f"{name} imports {AGENTS_FILE}, which the repository does not hold.",
                f"add {AGENTS_FILE} with the repository's guidance, or delete {name}.")
    return None


def check_root_layout(tracked: list[str], allowances: Sequence[RootAllowance]) -> list[Finding]:
    """Every tracked root entry, a directory with its trailing `/`, is on the base list or declared; a declaration that
    matches no tracked root entry is a finding too. Findings are sorted by entry."""
    entries = {name.partition("/")[0] + "/" if "/" in name else name for name in tracked}
    declared = {allowance.entry for allowance in allowances}
    base_spelling = {entry.casefold(): entry for entry in ROOT_BASE_ENTRIES}
    findings = [
        _unlisted_root_entry(entry, base_spelling.get(entry.casefold()))
        for entry in entries - ROOT_BASE_ENTRIES - declared
    ]
    findings += [_stale_root_declaration(entry, entries) for entry in declared - entries]
    return sorted(findings, key=lambda finding: finding.path)


def _unlisted_root_entry(entry: str, base_spelling: str | None) -> Finding:
    hint = f" (the base list knows {base_spelling})" if base_spelling else ""
    return Finding(
        ROOT_LAYOUT, entry, None,
        f"not on quality's base list{hint}.",
        f"move it to its owner's directory, or declare it in {CONFIG_PATH} with [[root.allow]], an entry and a reason.",
    )


@dataclass(frozen=True)
class FloorResult:
    """The python-floor findings and one report line per allowed file."""

    findings: list[Finding]
    reports: tuple[str, ...]


def _lower_bound(clause: Specifier) -> Version:
    """The version a clause sets as a lower bound; `==3.14.*` counts as 3.14, while `===` takes its version as written."""
    return Version(clause.version.removesuffix(".*") if clause.operator == "==" else clause.version)


def _floor(path: str, requires_python: str) -> Version | None:
    """The highest lower bound among the clauses, None when no clause sets one."""
    try:
        bounds = [_lower_bound(clause) for clause in SpecifierSet(requires_python) if clause.operator in LOWER_BOUND_OPERATORS]
    except ValueError as error:
        raise Unparsable(f'{path} has requires-python "{requires_python}", which is no PEP 440 version specifier ({error})') from error
    return max(bounds, default=None)


def _requires_python(path: str, content: bytes) -> tuple[bool, str]:
    """Whether the file holds a `[project]` table, and its `requires-python` ("" when it names none)."""
    try:
        document = repository.parse_toml(content.decode("utf-8"))
    except (UnicodeDecodeError, repository.TomlError) as error:
        raise Unparsable(f"{path} is not UTF-8 TOML that Python reads ({error})") from error
    project = document.get("project")
    if not isinstance(project, dict):
        return False, ""
    value = project.get("requires-python", "")
    if not isinstance(value, str):
        raise Unparsable(f"{path} has requires-python as {type(value).__name__}, not a string")
    return True, value


def _floor_finding(path: str, requires_python: str, floor: Version | None) -> Finding | None:
    if floor is not None and floor >= PYTHON_TARGET:
        return None
    reason = (
        f'requires-python "{requires_python}" names no floor, so it admits every Python, below {PYTHON_TARGET}.'
        if floor is None
        else f'requires-python "{requires_python}" admits Python {floor}, below {PYTHON_TARGET}.'
    )
    return Finding(
        PYTHON_FLOOR, path, None, reason,
        f"raise it to >={PYTHON_TARGET} and move CI, .python-version, Dockerfiles and ruff's target-version with it, "
        f"or declare it in {CONFIG_PATH} with [[python.allow]], its path and a reason.",
    )


def _stale_python_allowance(allowance: PythonAllowance, why: str) -> Finding:
    return Finding(
        PYTHON_FLOOR, allowance.path, None,
        f"declared in {CONFIG_PATH} with [[python.allow]], but {why}.",
        f"delete its [[python.allow]] entry from {CONFIG_PATH}.",
    )


def check_python_floor(pyprojects: dict[str, bytes], allowances: Sequence[PythonAllowance]) -> FloorResult:
    """Each judged `pyproject.toml` (one with a `[project]` table) must name a floor of the target version or higher, or be allowed."""
    declared = {allowance.path: allowance for allowance in allowances}
    findings: list[Finding] = []
    reports: list[str] = []
    judged: set[str] = set()
    for path, content in sorted(pyprojects.items()):
        has_project, requires_python = _requires_python(path, content)
        if not has_project:
            continue
        judged.add(path)
        finding = _floor_finding(path, requires_python, _floor(path, requires_python))
        if finding is None:
            continue
        if (allowance := declared.pop(path, None)) is None:
            findings.append(finding)
        else:
            reports.append(f'python-floor: allowed {path} (requires-python "{requires_python}") - {allowance.reason}')
    for allowance in declared.values():
        why = "that file is already at the floor" if allowance.path in judged else "no tracked pyproject.toml with a [project] table lies there"
        findings.append(_stale_python_allowance(allowance, why))
    return FloorResult(sorted(findings, key=lambda finding: finding.path), tuple(reports))


def _python_floor_result(root: Path) -> FloorResult:
    modes = checkout.index_modes(root)
    names = [str(PurePosixPath(project.directory) / layout.PROJECT_FILE) for project in layout.python_projects(modes)]
    present = checkout.on_disk(root, [name for name in names if modes[name] != layout.GITLINK_MODE])
    return check_python_floor(checkout.read_existing(repository.read_bytes, root, present), _declared(read_python_allowances, root))


def _stale_root_declaration(entry: str, tracked_entries: set[str]) -> Finding:
    hint = f" (the repository tracks {entry}/)" if f"{entry}/" in tracked_entries else ""
    return Finding(
        ROOT_LAYOUT, entry, None,
        f"declared in {CONFIG_PATH} with [[root.allow]], but the repository tracks no such root entry{hint}.",
        f"delete its [[root.allow]] entry from {CONFIG_PATH}.",
    )


def _declared[T](read: Callable[[Path], T], root: Path) -> T:
    """The repository's declarations; a config that cannot be read cannot be judged."""
    try:
        return read(root)
    except ConfigError as error:
        raise CannotJudge(str(error)) from error


@dataclass(frozen=True)
class RuleResult:
    """A baseline rule's findings, and its report line when it only counts."""

    findings: list[Finding]
    report: str | None


def judged_baseline(root: Path) -> ratchet.Baseline | None:
    """The baseline both baseline rules judge by, read once; a file that cannot be read cannot be judged."""
    try:
        return ratchet.read_baseline(root)
    except ratchet.SizeError as error:
        raise CannotJudge(f"{error}; {ratchet.head_advice(error)}") from error


def _complete(tree: ratchet.TreeMeasurement) -> ratchet.Measurements:
    """Holding a baseline needs every function measured, so an unparsable file cannot be judged."""
    try:
        return tree.complete()
    except ratchet.UnparsableSource as error:
        raise Unparsable(str(error)) from error


def check_size(tree: ratchet.TreeMeasurement, baseline: ratchet.Baseline | None) -> RuleResult:
    """Report-only without a baseline; with one, the tree on disk must hold it."""
    if baseline is None:
        return RuleResult([], ratchet.report_counts(tree))
    return RuleResult(ratchet.compare(_complete(tree), baseline.size), None)


def check_clones(tree: ratchet.TreeMeasurement, sources: dict[str, bytes], baseline: ratchet.Baseline | None) -> RuleResult:
    """Report-only without a `[clones]` table, even in a baseline of size tables; with one, every new pair and every
    held pair the tree lost is a finding. A pair can hide in a file the detector cannot read, so then it cannot judge."""
    pairs = clones.find_pairs(sources)
    if baseline is None or baseline.clones is None:
        return RuleResult([], clones.report_line(pairs.pairs))
    _complete(tree)
    return RuleResult(clones.compare_pairs(pairs, baseline.clones), None)


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


def lint_python(root: Path, locate_ruff: Callable[[], str]) -> python_lint.Lint:
    """ruff's one run over every tracked regular `.py` file, which python-security and python-print both judge."""
    try:
        return python_lint.lint(root, checkout.index_modes(root), PYTHON_TARGET, locate_ruff)
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


def check_secrets(root: Path, engine_dir: Path) -> engines.SecretScan:
    try:
        return engines.scan_secrets(root, engine_dir, engines.read_pins(), read_secret_allowances(root))
    except (process.ProcessError, ConfigError) as error:
        raise CannotJudge(str(error)) from error


def check_workflows(root: Path, engine_dir: Path, rule: str, tracked: list[str]) -> list[Finding]:
    workflows = checkout.on_disk(root, [name for name in tracked if checkout.is_workflow(PurePosixPath(name))])
    try:
        return engines.scan_workflows(root, engine_dir, engines.read_pins(), rule, workflows)
    except process.ProcessError as error:
        raise CannotJudge(str(error)) from error


def check_advisories(root: Path, engine_dir: Path, tracked: list[str]) -> advisories.AdvisoryScan:
    """The allowances' `until` is judged against today's date in UTC, read here, once."""
    try:
        allowances = read_advisory_allowances(root)
    except ConfigError as error:
        raise CannotJudge(str(error)) from error
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
) -> tuple[RuleResult | CannotJudge, RuleResult | CannotJudge]:
    """The size and clone verdicts, both from one measurement of the tree and one read of the baseline."""
    if isinstance(sources, CannotJudge):
        return sources, sources
    if isinstance(baseline, CannotJudge):
        return baseline, baseline
    tree = ratchet.measure(sources)
    return _attempt(lambda: check_size(tree, baseline)), _attempt(lambda: check_clones(tree, sources, baseline))


def _content_findings(
    root: Path, candidates: Callable[[dict[str, str]], list[str]], check: Callable[..., list[Finding]]
) -> list[Finding] | CannotJudge:
    """The findings of a rule over the index modes and the contents of the files `candidates` names."""

    def judge() -> list[Finding]:
        modes = checkout.index_modes(root)
        contents = checkout.read_existing(repository.read_bytes, root, checkout.on_disk(root, candidates(modes)))
        try:
            return check(modes, contents, _declared(read_layout_allowances, root))
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
    floor = _attempt(lambda: _python_floor_result(root))
    lint = _attempt(lambda: lint_python(root, locate_ruff))
    printing = _judge_lint(lint, python_lint.judge_print)
    verdicts = [
        _verdict(ENV_FILE, check_env_files(tracked)),
        _verdict(compose.COMPOSE_PORT, _attempt(lambda: compose.check_compose_ports(root, tracked, _declared(read_compose_allowances, root)))),
        _verdict(ratchet.SIZE, size if isinstance(size, CannotJudge) else size.findings),
        _verdict(clones.RULE, clone if isinstance(clone, CannotJudge) else clone.findings),
        _verdict(architecture.RULE, contracts if isinstance(contracts, CannotJudge) else contracts.findings),
        _verdict(CLAUDE_MD, _attempt(lambda: check_claude_md(root, tracked))),
        _verdict(ROOT_LAYOUT, _attempt(lambda: check_root_layout(tracked, _declared(read_root_allowances, root)))),
        _verdict(FILE_SUFFIX, _attempt(lambda: layout.check_file_suffix(checkout.index_modes(root), _declared(read_layout_allowances, root)))),
        _verdict(SCRIPT_NAME, _content_findings(root, layout.script_candidates, layout.check_script_name)),
        _verdict(SCRIPT_HEADER, _content_findings(root, layout.script_candidates, layout.check_script_header)),
        _verdict(TEST_LAYOUT, _attempt(lambda: layout.check_test_layout(checkout.index_modes(root), _declared(read_layout_allowances, root)))),
        _verdict(TEST_IMPORT, _content_findings(root, layout.test_import_candidates, layout.check_test_import)),
        _verdict(PYTHON_FLOOR, floor if isinstance(floor, CannotJudge) else floor.findings),
        _verdict(PYTHON_SECURITY, _judge_lint(lint, python_lint.judge_security)),
        _verdict(PYTHON_PRINT, printing if isinstance(printing, CannotJudge) else printing.findings),
    ]
    reports = _reports(size, clone, contracts, floor, printing)
    if engine_dir is None:
        return HygieneResult(tuple(verdicts), reports, [], {})
    secrets = _attempt(lambda: check_secrets(root, engine_dir))
    verdicts.append(_verdict(engines.LEAK_RULE, secrets if isinstance(secrets, CannotJudge) else secrets.findings))
    verdicts += [_verdict(rule, _attempt(partial(check_workflows, root, engine_dir, rule, tracked))) for rule in WORKFLOW_RULE_IDS]
    advisory = _attempt(partial(check_advisories, root, engine_dir, tracked))
    verdicts.append(_verdict(advisories.RULE, advisory if isinstance(advisory, CannotJudge) else advisory.findings))
    allowed_advisories = () if isinstance(advisory, CannotJudge) else advisory.report()
    allowed, unused = ([], {}) if isinstance(secrets, CannotJudge) else (secrets.allowed, secrets.unused_allowances)
    return HygieneResult(tuple(verdicts), (*reports, *allowed_advisories), allowed, unused)


def _reports(
    size: RuleResult | CannotJudge,
    clone: RuleResult | CannotJudge,
    contracts: architecture.ContractsVerdict | CannotJudge,
    floor: FloorResult | CannotJudge,
    printing: python_lint.PrintResult | CannotJudge,
) -> tuple[str, ...]:
    """The report lines of the rules that judged: the size counts without a baseline, the clone pairs without a `[clones]` table, the import-contract notes, each python-floor exception, each output owner."""
    size_reports = [] if isinstance(size, CannotJudge) or size.report is None else [size.report]
    clone_reports = [] if isinstance(clone, CannotJudge) or clone.report is None else [clone.report]
    contract_notes = [] if isinstance(contracts, CannotJudge) else contracts.notes
    floor_reports = () if isinstance(floor, CannotJudge) else floor.reports
    owner_reports = () if isinstance(printing, CannotJudge) else printing.reports
    return (*size_reports, *clone_reports, *contract_notes, *floor_reports, *owner_reports)
