"""The adapter-import and integration-marker rules over the repository's `core-imports-no-adapter` contract.

The contract's `source_modules` are the core and its `forbidden_modules` the adapters. A test is a core test by the module
its name mirrors, never by what it imports; its own import statements and those of the support modules it imports are
judged against the one import graph the imports area builds. A core test imports no adapter, and a module marked
`imports_adapter` is the one exception, which the baseline's `[adapter_imports]` table holds per test directory. Any other
test module that reaches an adapter carries the registered `integration` mark in its module `pytestmark`. One pass reads both."""

import ast
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import NamedTuple

import grimp

from quality import architecture, layout, pytest_marks
from quality.baseline_file import BASELINE_PATH, FIX_COMMAND, adapter_directory
from quality.config_allowances import LayoutAllowance
from quality.output import SEPARATOR, counted
from quality.ratchet import Renames, python_text
from quality.report import Finding, FindingEvent, RuleResult, Unparsable
from quality.rules import Rule

FIX = "name the test after an adapter or composition module, or import no adapter in it"
REGISTRATION_FIX = f"register {pytest_marks.IMPORTS_ADAPTER} in [tool.pytest.ini_options] markers"
INTEGRATION_REGISTRATION_FIX = f"register {pytest_marks.INTEGRATION} in [tool.pytest.ini_options] markers"
INTEGRATION_FIX = f"put pytest.mark.{pytest_marks.INTEGRATION} in the module's one direct pytestmark binding"
UNMARKED_FACT = f"module not marked {pytest_marks.INTEGRATION}"
UNREGISTERED_FACT = f"{pytest_marks.INTEGRATION} is not registered"
UNUSED_FACT = f"the {pytest_marks.IMPORTS_ADAPTER} mark exempts no adapter import"
UNUSED_FIX = f"remove the {pytest_marks.IMPORTS_ADAPTER} mark"
COUNT_FIX = "import no adapter in one of them, or move it into the integration suite"


@dataclass(frozen=True)
class Split:
    """The core and the adapters of a repository, as modules of its contract: names, never wildcards."""

    core: tuple[str, ...]
    adapters: tuple[str, ...]
    as_packages: bool

    def adapter_of(self, name: str) -> str | None:
        """The adapter `name` is or lies inside."""
        return next((adapter for adapter in self.adapters if name == adapter or name.startswith(f"{adapter}.")), None)

    def in_core(self, subject: str) -> bool:
        """Whether `subject` is a source module, or lies inside one when the contract treats its modules as packages."""
        return any(subject == module or (self.as_packages and subject.startswith(f"{module}.")) for module in self.core)

    def is_core_test(self, subject: str | None) -> bool:
        """A test with no subject is a core test, so no declaration becomes a path allowlist."""
        return subject is None or (self.adapter_of(subject) is None and self.in_core(subject))


def split_of(read: architecture.ContractsRead | None) -> Split | None:
    """The split the config declares, None without a config or without a contract that carries the id."""
    if read is None:
        return None
    carrier = architecture.adapter_carrier_index(read.config)
    if carrier is None:
        return None
    contract = read.contracts[carrier]
    lists = (contract.source_modules, contract.forbidden_modules)
    if any(expression.has_wildcard_expression() for expressions in lists for expression in expressions):
        raise architecture.ContractError(
            f"contract {contract.name} in {read.config.name} has a wildcard in source_modules or forbidden_modules;"
            " the adapter-import rule judges plain module names only"
        )
    core, adapters = (tuple(sorted(expression.expression for expression in expressions)) for expressions in lists)
    return Split(core, adapters, bool(contract.as_packages))


class _Red(NamedTuple):
    """The forbidden module an imported name is or reaches, and the support module that carried the import."""

    adapter: str
    through: str | None


def _reach(split: Split, graph: grimp.ImportGraph) -> dict[str, str]:
    """Each module that imports an adapter the graph holds, directly or not, with the first adapter it reaches; an
    adapter nothing imports is no module of the graph and reaches nothing."""
    reach: dict[str, str] = {}
    for adapter in split.adapters:
        if adapter in graph.modules:
            for module in graph.find_downstream_modules(adapter, as_package=True):
                reach.setdefault(module, adapter)
    return reach


@dataclass(frozen=True)
class _Judgement:
    split: Split
    reach: Mapping[str, str]

    def red(self, name: str, through: str | None) -> _Red | None:
        adapter = self.split.adapter_of(name)
        if adapter is not None:
            return _Red(adapter, through)
        if any(module.startswith(f"{name}.") for module in self.split.adapters):
            return None  # an ancestor of an adapter is a namespace, and its own __init__ is not judged
        reached = self.reach.get(name)
        return None if reached is None else _Red(reached, through)

    def reds(self, statement: layout.ModuleImport, imports_of: Callable[[str], list[layout.ModuleImport]]) -> set[_Red]:
        """What a statement imports that is red, and what the support modules it imports do."""
        reds = {red for name in statement.names if (red := self.red(name, None))}
        for module in _support_modules(statement.modules, imports_of):
            reds |= {red for inner in imports_of(module) for name in inner.names if (red := self.red(name, module))}
        return reds


@dataclass(frozen=True)
class Measure:
    """What the rules read in the tree: for `adapter-import` the red imports and the unused marks as findings, before
    the held counts judge them, the marked modules that import an adapter per test directory and the core tests judged;
    for `integration-marker` its verdict, whose extent is the non-core test modules judged; and each rule's refusal, a
    module that does not parse refusing the rules that judge it or the support module it is carried by."""

    findings: list[Finding]
    counts: Mapping[str, int]
    extent: int
    integration: RuleResult
    refusals: Mapping[Rule, Unparsable]

    def of(self, rule: Rule) -> Measure | Unparsable:
        """What stands in the place of this measure for `rule`: its refusal, else the measure."""
        return self.refusals.get(rule, self)


def candidates(modes: Mapping[str, str]) -> list[str]:
    """The files the rule reads: the imports of each test and the `pyproject.toml` of each project that holds tests."""
    tests = layout.test_import_candidates(modes)
    projects = layout.test_project_files(modes)
    return [*tests, *sorted({project for test in tests if (project := projects[test]) in modes})]


def with_marks(renames: Renames, read: Callable[[str], str | None], projects: Mapping[str, str]) -> Renames:
    """The renames with `marked`: the targets that moved to another directory and carry the registered mark. `read` gives
    the text of a path at the side the marks are read from, None where it holds nothing; `projects` is the head's
    `layout.test_project_files`."""
    marked = set()
    registered: dict[str, bool] = {}
    for old, new in renames.moves.items():
        project = projects.get(new)
        if project is None or adapter_directory(old) == adapter_directory(new):
            continue
        if project not in registered:
            text = read(project)
            registered[project] = text is not None and pytest_marks.registers(text, pytest_marks.IMPORTS_ADAPTER)
        if registered[project] and (module := read(new)) is not None and pytest_marks.imports_adapter_line(module) is not None:
            marked.add(new)
    return Renames(renames.moves, renames.targets, frozenset(marked))


def _module_findings(path: str, mark_line: int | None, reds: list[tuple[layout.ModuleImport, set[_Red]]]) -> list[Finding]:
    """The findings of one module that no registered mark exempts: each red statement, or the unused mark where none is red."""
    if not reds:
        unused = Finding(Rule.ADAPTER_IMPORT, path, mark_line, UNUSED_FACT, UNUSED_FIX, event=FindingEvent.UNUSED)
        return [] if mark_line is None else [unused]
    fix = FIX if mark_line is None else REGISTRATION_FIX
    return [
        Finding(Rule.ADAPTER_IMPORT, path, statement.line, f"imports {_words(found)}", fix, event=FindingEvent.ADAPTER_IMPORT)
        for statement, found in reds
    ]


def _words(reds: set[_Red]) -> str:
    """Each forbidden module once; with support carriers it also says `directly` where the statement is the module."""
    carriers: dict[str, set[str | None]] = {}
    for red in reds:
        carriers.setdefault(red.adapter, set()).add(red.through)
    return ", ".join(_carried(adapter, through) for adapter, through in sorted(carriers.items()))


def _carried(adapter: str, through: set[str | None]) -> str:
    support = sorted(path for path in through if path is not None)
    if not support:
        return adapter
    ways = (["directly"] if None in through else []) + [f"through {', '.join(support)}"]
    return f"{adapter} ({', '.join(ways)})"


@dataclass
class _Imports:
    """The import statements of the test and support modules, each parsed once."""

    support: layout.ImportTargets
    contents: Mapping[str, bytes]
    parsed: dict[str, list[layout.ModuleImport]] = field(default_factory=dict)

    def of(self, path: str) -> list[layout.ModuleImport]:
        if path not in self.parsed:
            self.parsed[path] = layout.module_imports(path, self.contents[path], self.support) if path in self.contents else []
        return self.parsed[path]


def _registers(projects: Mapping[str, str], contents: Mapping[str, bytes], path: str, mark: str) -> bool:
    project = projects.get(path)
    return project in contents and pytest_marks.registers(contents[project].decode("utf-8", "replace"), mark)


def _unmarked_findings(path: str, content: bytes, reds: list[tuple[layout.ModuleImport, set[_Red]]], registered: bool) -> list[Finding]:
    """The finding of an adapter-bound test module whose one direct module-level `pytestmark` binding does not carry the
    registered `integration` mark, at its first red statement. A module with no red statement is not adapter-bound."""
    if not reds or (registered and pytest_marks.INTEGRATION in pytest_marks.module_marks(ast.parse(python_text(content)))):
        return []
    tail, fix = (UNMARKED_FACT, INTEGRATION_FIX) if registered else (UNREGISTERED_FACT, INTEGRATION_REGISTRATION_FIX)
    fact = SEPARATOR.join((f"imports {_words(set().union(*(found for _, found in reds)))}", tail))
    line = min(statement.line for statement, _ in reds)
    return [Finding(Rule.INTEGRATION_MARKER, path, line, fact, fix, event=FindingEvent.UNMARKED)]


def measure_adapter_imports(
    split: Split, graph: grimp.ImportGraph, modes: Mapping[str, str], contents: Mapping[str, bytes],
    allowances: Sequence[LayoutAllowance],
) -> Measure:
    """One finding per import statement of a core test that is red, itself or through a support module it imports, unless
    the module's `pytestmark` holds the `imports_adapter` mark its project registers; that exempts the module, which
    counts toward its directory. A mark on a module the rule would not report is an `unused` finding at its `pytestmark`
    line. One finding per other test module that reaches an adapter without the registered `integration` mark in its
    `pytestmark`. Findings are sorted by path and line, the extents are the core tests and the other test modules judged.
    `contents` holds the bytes of `candidates`."""
    judgement = _Judgement(split, _reach(split, graph))
    imports = _Imports(layout.ImportTargets.support(modes), contents)
    projects = layout.test_project_files(modes)
    subjects = layout.test_subjects(modes, allowances)
    present = {path: subject for path, subject in subjects.items() if path in contents}
    core = {path for path, subject in present.items() if split.is_core_test(subject)}
    findings: list[Finding] = []
    unmarked: list[Finding] = []
    counts: Counter[str] = Counter()
    refusals: dict[Rule, Unparsable] = {}
    for path in sorted(present):
        rule = Rule.ADAPTER_IMPORT if path in core else Rule.INTEGRATION_MARKER
        line = pytest_marks.imports_adapter_line(contents[path])
        if path not in core:
            findings.extend(_module_findings(path, line, []))
        try:
            reds = [(statement, found) for statement in imports.of(path) if (found := judgement.reds(statement, imports.of))]
        except layout.UnparsableTestModule as error:
            refusals.setdefault(rule, Unparsable(str(error)))
            continue
        if path not in core:
            unmarked.extend(_unmarked_findings(path, contents[path], reds, _registers(projects, contents, path, pytest_marks.INTEGRATION)))
        elif reds and line is not None and _registers(projects, contents, path, pytest_marks.IMPORTS_ADAPTER):
            counts[adapter_directory(path)] += 1
        else:
            findings.extend(_module_findings(path, line, reds))
    integration = RuleResult(_sorted(unmarked), len(present) - len(core), False)
    return Measure(_sorted(findings), counts, len(core), integration, refusals)


def _sorted(findings: list[Finding]) -> list[Finding]:
    return sorted(findings, key=lambda finding: (finding.path, finding.line or 0))


def _count_fact(count: int) -> str:
    return f"{counted(count, 'marked module')} {'imports' if count == 1 else 'import'} an adapter"


def judge_counts(measure: Measure, held: Mapping[str, int] | None) -> RuleResult:
    """The measure against the held counts, None where the baseline holds no table, which holds 0 for every directory: a
    directory above its count is `grew`, one with no entry `new debt`, and an entry above its count `can fall`."""
    findings = list(measure.findings)
    entries = held or {}
    for directory in sorted(measure.counts):
        count, ceiling = measure.counts[directory], entries.get(directory, 0)
        if count > ceiling:
            event = FindingEvent.GREW if directory in entries else FindingEvent.NEW_DEBT
            fix = FIX_COMMAND if held is None else COUNT_FIX
            findings.append(Finding(Rule.ADAPTER_IMPORT, directory, None, SEPARATOR.join((_count_fact(count), f"ceiling {ceiling}")), fix, event=event))
    for directory in sorted(entries):
        count = measure.counts.get(directory, 0)
        if count < entries[directory]:
            fact = SEPARATOR.join((directory, _count_fact(count), f"ceiling {entries[directory]}"))
            findings.append(Finding(Rule.ADAPTER_IMPORT, BASELINE_PATH, None, fact, FIX_COMMAND, event=FindingEvent.CAN_FALL))
    return RuleResult(findings, measure.extent, False)


def _support_modules(start: Iterable[str], imports_of: Callable[[str], list[layout.ModuleImport]]) -> list[str]:
    """The support modules reached from `start` through further support modules, each once."""
    reached: dict[str, None] = {}
    pending = sorted(start)
    while pending:
        module = pending.pop()
        if module not in reached:
            reached[module] = None
            pending += sorted(target for statement in imports_of(module) for target in statement.modules)
    return list(reached)
