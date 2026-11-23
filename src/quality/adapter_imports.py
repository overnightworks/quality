"""The adapter-import rule: a core unit test imports no adapter the repository's `core-imports-no-adapter` contract names.

The contract's `source_modules` are the core and its `forbidden_modules` the adapters. A test is a core test by the module
its name mirrors, never by what it imports; its own import statements and those of the support modules it imports are
judged against the one import graph the imports area builds."""

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import NamedTuple

import grimp

from quality import architecture, layout
from quality.config import LayoutAllowance
from quality.report import Finding, FindingEvent
from quality.rules import Rule

FIX = "name the test after an adapter or composition module, or import no adapter in it"


@dataclass(frozen=True)
class Split:
    """The core and the adapters of a repository, as modules of its contract: names, never wildcards."""

    core: tuple[str, ...]
    adapters: tuple[str, ...]
    as_packages: bool

    def inside(self, name: str, modules: Iterable[str]) -> str | None:
        """The module `name` is, or lies inside when the contract treats its modules as packages."""
        return next((module for module in modules if name == module or (self.as_packages and name.startswith(f"{module}."))), None)

    def is_core_test(self, subject: str | None) -> bool:
        """A test with no subject is a core test, so no declaration becomes a path allowlist."""
        return subject is None or (self.inside(subject, self.adapters) is None and self.inside(subject, self.core) is not None)


def split_of(read: architecture.ContractsRead | None) -> Split | None:
    """The split the config declares, None without a config or without a contract that carries the id."""
    if read is None:
        return None
    contracts = zip(read.contracts, read.config.options.contracts_options, strict=True)
    contract = architecture.one_adapter_carrier(read.config.name, [contract for contract, options in contracts if architecture.carries_adapter_id(options)])
    if contract is None:
        return None
    lists = (contract.source_modules, contract.forbidden_modules)
    if any(expression.has_wildcard_expression() for expressions in lists for expression in expressions):
        raise architecture.ContractError(
            f"contract {contract.name} in {read.config.name} has a wildcard in source_modules or forbidden_modules;"
            " the adapter-import rule judges plain module names only"
        )
    core, adapters = (tuple(sorted(expression.expression for expression in expressions)) for expressions in lists)
    return Split(core, adapters, bool(contract.as_packages))


class _Red(NamedTuple):
    """An imported name that is an adapter or reaches one, and the support module that carried the import."""

    name: str
    adapter: str
    inside: bool
    through: str | None

    def words(self) -> str:
        reading = f"{self.name} ({'is' if self.inside else 'reaches'} {self.adapter})"
        return reading if self.through is None else f"{reading} through {self.through}"


def _reach(split: Split, graph: grimp.ImportGraph) -> dict[str, str]:
    """Each module that imports an adapter the graph holds, directly or not, with the first adapter it reaches; an
    adapter nothing imports is no module of the graph and reaches nothing."""
    reach: dict[str, str] = {}
    for adapter in split.adapters:
        if adapter in graph.modules:
            for module in graph.find_downstream_modules(adapter, as_package=split.as_packages):
                reach.setdefault(module, adapter)
    return reach


@dataclass(frozen=True)
class _Judgement:
    split: Split
    reach: Mapping[str, str]

    def red(self, name: str, through: str | None) -> _Red | None:
        adapter = self.split.inside(name, self.split.adapters)
        if adapter is not None:
            return _Red(name, adapter, True, through)
        if any(module.startswith(f"{name}.") for module in self.split.adapters):
            return None  # an ancestor of an adapter is a namespace, and its own __init__ is not judged
        reached = self.reach.get(name)
        return None if reached is None else _Red(name, reached, False, through)


def check_adapter_imports(
    split: Split, graph: grimp.ImportGraph, modes: Mapping[str, str], contents: Mapping[str, bytes],
    allowances: Sequence[LayoutAllowance],
) -> list[Finding]:
    """One finding per import statement of a core test that is red, itself or through a support module it imports,
    sorted by path and line. `contents` holds the bytes of `layout.test_import_candidates`."""
    judgement = _Judgement(split, _reach(split, graph))
    support = layout.ImportTargets.support(modes)
    parsed: dict[str, list[layout.ModuleImport]] = {}

    def imports_of(path: str) -> list[layout.ModuleImport]:
        if path not in parsed:
            parsed[path] = layout.module_imports(path, contents[path], support) if path in contents else []
        return parsed[path]

    subjects = layout.test_subjects(modes, allowances)
    findings = []
    for path in sorted(path for path, subject in subjects.items() if split.is_core_test(subject) and path in contents):
        for statement in imports_of(path):
            reds = {red for name in statement.names if (red := judgement.red(name, None))}
            for module in _support_modules(statement.modules, imports_of):
                reds |= {red for inner in imports_of(module) for name in inner.names if (red := judgement.red(name, module))}
            if reds:
                words = ", ".join(red.words() for red in sorted(reds, key=lambda red: (red.name, red.through or "")))
                findings.append(Finding(Rule.ADAPTER_IMPORT, path, statement.line, f"imports {words}", FIX, event=FindingEvent.ADAPTER_IMPORT))
    return sorted(findings, key=lambda finding: (finding.path, finding.line or 0))


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
