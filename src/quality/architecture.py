"""Judges a repository's import-linter contracts, with import-linter as a library and never its CLI.

import-linter's own reader parses the config from a private temporary copy, and grimp builds the graph without a
cache, so nothing is written to the checkout. grimp reads each root package from its tracked directory, handed in
through a package finder of quality's own, so neither sys.path nor sys.modules is consulted and no caller code runs:
a package quality has itself imported is judged from the repository like any other. A plugin contract type, a dotted
root package, a symlink out of the repository (also through a link inside it) and a symlink loop are refused before
grimp reads anything. Every message names paths repository-relative. The private import-linter helpers and grimp's
package-finder port used here are pinned by the exact versions in pyproject.toml.
"""

import codecs
import copy
import errno
import io
import logging
import os
import re
import stat
import tempfile
import tokenize
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from enum import Enum, auto
from pathlib import Path, PurePosixPath
from types import MappingProxyType

import grimp
from grimp.application.config import settings as grimp_settings
from grimp.application.ports.filesystem import AbstractFileSystem
from grimp.application.ports.packagefinder import AbstractPackageFinder
from grimp.exceptions import SourceSyntaxError
from importlinter import configuration
from importlinter.application import use_cases
from importlinter.application.user_options import UserOptions
from importlinter.contracts.acyclic_siblings import AcyclicSiblingsContract
from importlinter.contracts.forbidden import ForbiddenContract
from importlinter.contracts.independence import IndependenceContract
from importlinter.contracts.layers import Layer, LayersContract
from importlinter.contracts.protected import ProtectedContract
from importlinter.domain.contract import Contract, InvalidContractOptions
from importlinter.domain.helpers import MissingImport
from importlinter.domain.imports import ImportExpression, ModuleExpression

from quality import contract_results, external_imports, python_imports, ratchet
from quality import repository as checkout_files
from quality.report import Note, NoteEvent, RuleResult

CONFIG_FILES = (".importlinter", "setup.cfg", "pyproject.toml")
ADAPTER_CONTRACT_ID = "core-imports-no-adapter"
BUILT_IN_CONTRACT_TYPES: Mapping[str, type[Contract]] = {
    "forbidden": ForbiddenContract,
    "layers": LayersContract,
    "independence": IndependenceContract,
    "protected": ProtectedContract,
    "acyclic_siblings": AcyclicSiblingsContract,
}


class Tightening(Enum):
    """A change of one contract field that can only forbid more."""

    EXPRESSIONS_ADDED = auto()
    INDIRECT_IMPORTS_FORBIDDEN = auto()
    MODULES_PLACED_IN_LAYERS = auto()


TIGHTENINGS: Mapping[tuple[str, str], Tightening] = MappingProxyType({
    (ForbiddenContract.type_name, "source_modules"): Tightening.EXPRESSIONS_ADDED,
    (ForbiddenContract.type_name, "forbidden_modules"): Tightening.EXPRESSIONS_ADDED,
    (ForbiddenContract.type_name, "allow_indirect_imports"): Tightening.INDIRECT_IMPORTS_FORBIDDEN,
    (IndependenceContract.type_name, "modules"): Tightening.EXPRESSIONS_ADDED,
    (LayersContract.type_name, "layers"): Tightening.MODULES_PLACED_IN_LAYERS,
})
_TIGHTENING_HOLDS: Mapping[Tightening, Callable[[object, object], bool]] = MappingProxyType({
    Tightening.EXPRESSIONS_ADDED: lambda before, after: _expressions_kept_or_covered(before, after),
    Tightening.INDIRECT_IMPORTS_FORBIDDEN: lambda before, after: before is True and after is False,
    Tightening.MODULES_PLACED_IN_LAYERS: lambda before, after: _without_modules_unknown_to(before, after) == before,
})
STRUCK_FIELDS = ("layers", "containers", "source_modules", "forbidden_modules", "modules", "protected_modules")
UNCOMPARED_FIELDS = frozenset({"ignore_imports", "broken_contract_guidance"})
SESSION_OPTIONS = ("root_packages", "include_external_packages", "exclude_type_checking_imports")
# The (before, after) of a session option that only adds imports to the graph, so every contract judges what it judged
# before; every other change of an option loosens.
IMPORTS_ONLY_ADDED: Mapping[str, tuple[bool, bool]] = MappingProxyType({
    "include_external_packages": (False, True),
    "exclude_type_checking_imports": (True, False),
})
GRIMP_WARNING_PREFIX = "Warning: "
PANIC_MODULE = "pyo3_runtime"
MAX_ECHOED_SOURCE = 200
PACKAGE_INIT = "__init__.py"
# quality reads every source as UTF-8 (the content check and the contract refinement), so the walk judges only a
# source that it and grimp read alike. The set holds the spellings both read as UTF-8 or ASCII, so it stays a list of
# probed spellings and every other spelling is refused.
READABLE_COOKIES = frozenset({"utf-8", "utf8", "ascii", "us-ascii", "ansi_x3.4-1968"})
NUL_IN_SOURCE = "source code cannot contain null bytes"
CODING_COOKIE = re.compile(r"^[ \t\f]*#.*?coding[:=][ \t]*([-\w.]+)", re.ASCII)  # PEP 263


class ContractError(Exception):
    """The contracts cannot be judged; the message names every path repository-relative."""


class UnparsableModule(ContractError):
    """A module of a root package grimp cannot parse."""


@dataclass(frozen=True)
class Config:
    name: str
    options: UserOptions


@dataclass(frozen=True)
class ContractFields:
    """One contract as import-linter parses it: its type, its ignore expressions and every other parsed field."""

    type: str
    ignores: frozenset[ImportExpression]
    fields: Mapping[str, object]


@dataclass(frozen=True)
class SideContracts:
    """The import contracts of one side of a pull request, by name; `config` is the file holding the section, None
    when the side has none, and `adapter_contract` the name of the contract that carries the adapter id."""

    config: str | None = None
    session: Mapping[str, object] = field(default_factory=dict)
    contracts: Mapping[str, ContractFields] = field(default_factory=dict)
    adapter_contract: str | None = None


@dataclass(frozen=True)
class ContractsRead:
    """One read of the config and of the root packages: every rule of the imports area judges this one graph."""

    root: Path
    tracked: frozenset[str]
    config: Config
    contracts: tuple[Contract, ...]
    roots: Mapping[str, str]
    graph: grimp.ImportGraph
    warnings: tuple[Note, ...]


@dataclass(frozen=True)
class PullTree:
    """What git holds at the two commits of a pull request and what it renamed between them: the tracked paths of each
    commit, the head's paths of size 0, and the renames. One record, so that a listing never comes without its renames."""

    base_paths: frozenset[str]
    head_paths: frozenset[str]
    head_empty: frozenset[str]
    renames: ratchet.Renames


def read_contracts(root: Path, tracked: Sequence[str], configs: Mapping[str, str]) -> ContractsRead | None:
    """The contracts and the one import graph of the config among `configs`, None without a config.

    `configs` maps each tracked config file at the repository's top to its text.
    """
    config = find_config(configs)
    if config is None:
        return None
    if "contract_types" in config.options.session_options:
        raise ContractError(f"{config.name} sets contract_types, whose plugin modules would be imported;"
                            " only the built-in contract types are judged")
    repository = os.path.realpath(root)
    with _judging(f"the config {config.name}", repository):
        contracts = tuple(_contract(config, index, options) for index, options in enumerate(config.options.contracts_options, 1))
        roots = {package: _source_root(tracked, package) for package in config.options.session_options["root_packages"]}
        with _library_warnings(repository) as graph_warnings:
            graph = _build_graph(repository, roots, config.options)
    grimp_notes = tuple(Note(config.name, NoteEvent.WARNING, message.removeprefix(GRIMP_WARNING_PREFIX)) for message in graph_warnings)
    return ContractsRead(root, frozenset(tracked), config, contracts, roots, graph, grimp_notes)


def check_contracts(read: ContractsRead) -> RuleResult:
    """Every contract must be kept and the extent is how many it declares."""
    repository = os.path.realpath(read.root)
    results = [_check(contract, read, repository) for contract in read.contracts]
    return RuleResult([finding for result in results for finding in result.findings], len(read.contracts), False,
                      (*read.warnings, *(note for result in results for note in result.notes)))


def adapter_carrier_index(config: Config) -> int | None:
    """The position among the config's contracts of the one that carries the adapter id (a forbidden contract with the
    id), None without one; two leave it open which one is the split."""
    carriers = [
        index for index, options in enumerate(config.options.contracts_options)
        if options.get("id") == ADAPTER_CONTRACT_ID and options.get("type") == ForbiddenContract.type_name
    ]
    if len(carriers) > 1:
        raise ContractError(f"{config.name} holds {len(carriers)} contracts carrying the id {ADAPTER_CONTRACT_ID}; keep one")
    return carriers[0] if carriers else None


def find_config(configs: Mapping[str, str]) -> Config | None:
    """The one config that holds an import-linter section; two of them leave it open which one the caller runs."""
    found = [config for name, text in configs.items() if (config := _read_config(name, text)) is not None]
    if len(found) > 1:
        raise ContractError(f"{' and '.join(config.name for config in found)} each hold an import-linter config; keep one")
    return found[0] if found else None


def _read_config(name: str, text: str) -> Config | None:
    """import-linter's own reader on a copy in a private (0700) directory, so the format has one owner."""
    configuration.configure()
    with tempfile.TemporaryDirectory() as scratch:
        scratch_copy = Path(scratch, PurePosixPath(name).name)
        scratch_copy.write_text(text, encoding="utf-8")
        with _judging(f"the config {name}", scratch):
            try:
                return Config(name, use_cases.read_user_options(str(scratch_copy)))
            except FileNotFoundError:
                return None


def contracts_of(configs: Mapping[str, str]) -> SideContracts:
    """The contracts the one config among `configs` declares, none without a config; two contracts of one name on a
    side cannot be told apart, and no import graph is built."""
    config = find_config(configs)
    if config is None:
        return SideContracts()
    contracts: dict[str, ContractFields] = {}
    with _judging(f"the config {config.name}", ""):
        for index, options in enumerate(config.options.contracts_options, 1):
            contract = _contract(config, index, options)
            if contract.name in contracts:
                raise ContractError(f'import contract "{contract.name}" is declared twice in {config.name}')
            contracts[contract.name] = _fields_of(contract, options["type"])
        carrier = adapter_carrier_index(config)
        adapter = None if carrier is None else config.options.contracts_options[carrier]["name"]
        return SideContracts(config.name, _session_of(config.options), contracts, adapter)


def contract_loosenings(base: SideContracts, head: SideContracts, tree: PullTree | None = None) -> list[str]:
    """What the head loosens against the base, one sorted line each; adding a config or a contract, and removing an ignore, are none.

    A module a list lost is no loosening when `tree` shows the head no longer has it and git does not read it as renamed,
    or reads it as renamed to a module the same list at the head names at the struck name's place; a contract the head
    lacks is no loosening when one of its lists names only modules the head no longer has and git does not read as
    renamed, except an exhaustive `layers` contract while one of its containers still has a file at the head or git reads
    a file of it as renamed.
    Without a tree nothing counts as missing."""
    if base.config is None:
        return []
    if head.config is None:
        return [f"import-linter config in {base.config} removed"]
    lines = [session_loosening(option) for option in SESSION_OPTIONS
             if _session_loosened(option, base.session[option], head.session[option])]
    gone = {} if tree is None else _gone(struck_modules(base, head), head, tree)
    for name, contract in base.contracts.items():
        kept = head.contracts.get(name)
        if kept is not None:
            lines += _contract_loosenings(name, _resolved(contract, kept, gone), kept)
        elif tree is None or not _guards_nothing(contract, gone, head, tree):
            lines.append(f'import contract "{name}" removed')
    return sorted(lines + _adapter_id_loosenings(base, head))


def session_loosening(option: str) -> str:
    return f"import-linter changes {option}"


def _session_loosened(option: str, before: object, after: object) -> bool:
    return before != after and IMPORTS_ONLY_ADDED.get(option) != (before, after)


def _adapter_id_loosenings(base: SideContracts, head: SideContracts) -> list[str]:
    """The adapter id a contract that stays at the head has lost: removed, or moved to another contract of the head."""
    if base.adapter_contract not in head.contracts or head.adapter_contract == base.adapter_contract:
        return []
    line = f"import contract id {ADAPTER_CONTRACT_ID} "
    return [line + ("removed" if head.adapter_contract is None else f'moves "{base.adapter_contract}" -> "{head.adapter_contract}"')]


def struck_modules(base: SideContracts, head: SideContracts) -> frozenset[str]:
    """The module names a contract at the base lists and the head's same list lacks, every name of a contract the head
    lacks included; a wildcard is none."""
    if base.config is None or head.config is None:
        return frozenset()
    return frozenset(
        name for contract_name, contract in base.contracts.items()
        for names in _struck(contract, head.contracts.get(contract_name)).values() for name in names
    )


def _struck(base: ContractFields, head: ContractFields | None) -> dict[str, frozenset[str]]:
    """The wildcard-free names each list of `base` holds and the same list of `head` lacks; an absent `head` lists nothing."""
    if head is not None and base.type != head.type:
        return {}
    struck = {option: frozenset(name for name in _names(base, option) - _names_of(head, option) if "*" not in name)
              for option in STRUCK_FIELDS if option in base.fields}
    return {option: names for option, names in struck.items() if names}


def _names_of(contract: ContractFields | None, option: str) -> frozenset[str]:
    return frozenset() if contract is None else _names(contract, option)


def _guards_nothing(contract: ContractFields, gone: Mapping[str, str | None], head: SideContracts, tree: PullTree) -> bool:
    """Whether one list of the contract names modules and every one is deleted, none renamed; a wildcard is never `gone`.
    The `layers` list does not count for an exhaustive contract while one of its containers still has a file at the head
    or git reads a file of it as renamed: the contract still forbids every undeclared child of that container."""
    exhaustive = bool(contract.fields.get("exhaustive")) and _has_live_container(contract, head, tree)
    lists = (_names(contract, option) for option in STRUCK_FIELDS
             if option in contract.fields and not (exhaustive and option == "layers"))
    return any(names and all(name in gone and gone[name] is None for name in names) for names in lists)


def _has_live_container(contract: ContractFields, head: SideContracts, tree: PullTree) -> bool:
    heads = _placed_roots(tree.head_paths, head)
    bases = _placed_roots(tree.base_paths, head)
    return any(_has_file(tree.head_paths, _module_place(heads[package], container.expression))
               or bool(_renamed_files(tree, _module_place(bases[package], container.expression)))
               for container in contract.fields["containers"] or ()
               if (package := container.expression.partition(".")[0]) in heads and package in bases)


def _names(contract: ContractFields, option: str) -> frozenset[str]:
    """The dotted names one list holds; a layers tail is named once per container."""
    value = contract.fields[option] or ()
    if option != "layers":
        return frozenset(expression.expression for expression in value)
    return frozenset(name for layer in value for tail in layer.module_tails for name in _tail_names(contract, tail.name))


def _tail_names(contract: ContractFields, tail: str) -> list[str]:
    containers = [container.expression for container in contract.fields["containers"] or ()]
    return [f"{container}.{tail}" for container in containers] or [tail]


def _gone(struck: frozenset[str], head: SideContracts, tree: PullTree) -> Mapping[str, str | None]:
    """The struck names the head's files no longer hold, each with the module git renamed it to, None when it was
    deleted; a name outside the placed root packages is present, and a renamed name with no one renamed module is left out."""
    heads = _placed_roots(tree.head_paths, head)
    bases = _placed_roots(tree.base_paths, head)
    gone: dict[str, str | None] = {}
    for name in struck:
        package = name.partition(".")[0]
        if package not in heads or package not in bases or _has_file(tree.head_paths, _module_place(heads[package], name)):
            continue
        place = _module_place(bases[package], name)
        moves = _renamed_files(tree, place)
        if not moves:
            gone[name] = None
        elif (module := _renamed_module(place, moves, heads)) is not None:
            gone[name] = module
    return gone


def _renamed_files(tree: PullTree, place: str) -> list[tuple[str, str]]:
    """The (source, target) renames of a file of the module at `place` to a file that is not empty."""
    return [(source, target) for source, target in tree.renames.moves.items()
            if (source == f"{place}.py" or source.startswith(f"{place}/")) and target not in tree.head_empty]


def _placed_roots(paths: frozenset[str], head: SideContracts) -> dict[str, str]:
    placed = {package: _directory_of(paths, package) for package in head.session["root_packages"]}
    return {package: directory for package, directory in placed.items() if directory is not None}


def _directory_of(paths: frozenset[str], package: str) -> str | None:
    try:
        return _source_root(paths, package)
    except ContractError:
        return None


def _module_place(directory: str, name: str) -> str:
    return str(PurePosixPath(directory, *name.split(".")))


def _has_file(paths: frozenset[str], place: str) -> bool:
    return f"{place}.py" in paths or any(path.startswith(f"{place}/") for path in paths)


def _renamed_module(place: str, moves: Sequence[tuple[str, str]], heads: Mapping[str, str]) -> str | None:
    """The one module every rename source of the module at `place` names, None when a source names none or they differ."""
    candidates = {_candidate(place, source, target, heads) for source, target in moves}
    return candidates.pop() if len(candidates) == 1 else None


def _candidate(place: str, source: str, target: str, heads: Mapping[str, str]) -> str | None:
    """The module a rename target names: its own file or package for the module's own files, the folder holding the
    source's trailing parts for a file below the module's folder; the target must lie below a placed root package's
    directory at the head and every part of the name must be an identifier."""
    parts = PurePosixPath(target).parts
    depth = _root_depth(parts, heads)
    if depth is None:
        return None
    if source in (f"{place}.py", f"{place}/{PACKAGE_INIT}"):
        if parts[-1] == PACKAGE_INIT:
            module = parts[:-1]
        elif parts[-1].endswith(".py"):
            module = (*parts[:-1], parts[-1].removesuffix(".py"))
        else:
            return None
    else:
        below = PurePosixPath(source).relative_to(place).parts
        if parts[-len(below):] != below:
            return None
        module = parts[:-len(below)]
    named = module[depth:]
    return ".".join(named) if named and all(part.isidentifier() for part in named) else None


def _root_depth(parts: tuple[str, ...], heads: Mapping[str, str]) -> int | None:
    """The number of parts of the directory holding the placed root package a path lies strictly below."""
    for package, directory in heads.items():
        root = PurePosixPath(directory).parts
        if parts[:len(root) + 1] == (*root, package) and len(parts) > len(root) + 1:
            return len(root)
    return None


def _resolved(base: ContractFields, head: ContractFields, gone: Mapping[str, str | None]) -> ContractFields:
    """The base with each name the head's same list lacks and `gone` accounts for taken out when deleted and put at its
    place under the module it was renamed to, so that the comparison reads the module's position."""
    fields = dict(base.fields)
    for option, names in _struck(base, head).items():
        accounted = {name: gone[name] for name in names if name in gone}
        if accounted:
            fields[option] = _replaced(base, option, accounted)
    return ContractFields(base.type, base.ignores, fields)


def _replaced(contract: ContractFields, option: str, accounted: Mapping[str, str | None]) -> object:
    value = contract.fields[option]
    if option != "layers":
        became = (accounted.get(expression.expression, expression.expression) for expression in value)
        return type(value)(ModuleExpression(module) for module in became if module is not None)
    tails = _renamed_tails(contract, accounted)
    parts = [_with_tails(layer, tails) for layer in value]
    return [layer for layer in parts if layer.module_tails]


def _renamed_tails(contract: ContractFields, accounted: Mapping[str, str | None]) -> dict[str, str | None]:
    """The layer tails whose name in every container `accounted` covers, each with the one tail it became in all of them,
    None when it was deleted in all; any other tail is left out."""
    renamed: dict[str, str | None] = {}
    for tail in {tail.name for layer in contract.fields["layers"] for tail in layer.module_tails}:
        names = _tail_names(contract, tail)
        if not all(name in accounted for name in names):
            continue
        module = accounted[names[0]]
        became = None if module is None else module.removeprefix(names[0].removesuffix(tail))
        if all(accounted[name] == (None if became is None else name.removesuffix(tail) + became) for name in names):
            renamed[tail] = became
    return renamed


def _with_tails(layer: Layer, renamed: Mapping[str, str | None]) -> Layer:
    """A layer with its renamed tails under their new names and its deleted tails gone; one left alone has no sibling,
    so its independence says nothing."""
    tails = {replace(tail, name=renamed.get(tail.name, tail.name)) for tail in layer.module_tails
             if renamed.get(tail.name, tail.name) is not None}
    return replace(layer, module_tails=tails, is_independent=layer.is_independent or len(tails) == 1)


def _contract_loosenings(name: str, base: ContractFields, head: ContractFields) -> list[str]:
    if base.type != head.type:
        return [f'import contract "{name}" changes type']
    ignores = [f'import contract "{name}" ignores {expression}' for expression in head.ignores - base.ignores]
    changed = [f'import contract "{name}" changes {option}' for option in base.fields if _field_changed(option, base, head)]
    return ignores + changed


def _field_changed(option: str, base: ContractFields, head: ContractFields) -> bool:
    before, after = base.fields[option], head.fields[option]
    if before == after:
        return False
    tightening = TIGHTENINGS.get((base.type, option))
    return tightening is None or not _holds(tightening, before, after)


def _holds(tightening: Tightening, before: object, after: object) -> bool:
    return _TIGHTENING_HOLDS[tightening](before, after)


def _expressions_kept_or_covered(before: Iterable[ModuleExpression], after: Iterable[ModuleExpression]) -> bool:
    """Every base expression is kept, or is a plain module name one of the head's wildcard expressions matches; the match
    is import-linter's own, asked of a graph holding only those names."""
    dropped = set(before) - set(after)
    if any(expression.has_wildcard_expression() for expression in dropped):
        return False
    graph = grimp.ImportGraph()
    for expression in dropped:
        graph.add_module(expression.expression)
    covered = {module for expression in after if expression.has_wildcard_expression()
               for module in graph.find_matching_modules(expression.expression)}
    return {expression.expression for expression in dropped} <= covered


def _without_modules_unknown_to(base_layers: Sequence[Layer], head_layers: Sequence[Layer]) -> list[Layer]:
    """The head's layers without the modules the base does not name, and without the layers that leaves empty."""
    known = frozenset(tail.name for layer in base_layers for tail in layer.module_tails)
    stripped = [_known_part(layer, known) for layer in head_layers]
    return [layer for layer in stripped if layer.module_tails]


def _known_part(layer: Layer, known: frozenset[str]) -> Layer:
    """A layer's modules the base names; one left alone has no sibling, so its independence says nothing."""
    tails = {tail for tail in layer.module_tails if tail.name in known}
    return replace(layer, module_tails=tails, is_independent=layer.is_independent or len(tails) == 1)


def _fields_of(contract: Contract, contract_type: str) -> ContractFields:
    """The parsed fields, never the raw options: the INI reader injects an `id` and spells booleans as it likes."""
    names = contract._get_field_names()
    return ContractFields(contract_type, frozenset(getattr(contract, "ignore_imports", None) or ()),
                          {name: getattr(contract, name) for name in names if name not in UNCOMPARED_FIELDS})


def _include_external_packages(options: UserOptions) -> bool:
    return bool(use_cases._get_include_external_packages(options))


def _session_of(options: UserOptions) -> Mapping[str, object]:
    packages = options.session_options["root_packages"]
    return {
        "root_packages": frozenset([packages] if isinstance(packages, str) else packages),
        "include_external_packages": _include_external_packages(options),
        "exclude_type_checking_imports": use_cases._get_exclude_type_checking_imports(options),
    }


def _contract(config: Config, index: int, options: dict) -> Contract:
    if "name" not in options:
        raise ContractError(f"contract {index} in {config.name} has no name")
    name = options["name"]
    with _judging(f"contract {name} in {config.name}", ""):
        contract_class = BUILT_IN_CONTRACT_TYPES.get(options.get("type"))
        if contract_class is None:
            raise ContractError(f"contract {name} in {config.name} has type {options.get('type')}; only the built-in"
                                f" types are judged ({', '.join(BUILT_IN_CONTRACT_TYPES)})")
        try:
            return contract_class(name=name, session_options=config.options.session_options, contract_options=options)
        except InvalidContractOptions as error:
            fields = "; ".join(f"{field}: {reason}" for field, reason in error.errors.items())
            raise ContractError(f"contract {name} in {config.name} has invalid options ({fields})") from error


def _source_root(tracked: Iterable[str], package: str) -> str:
    """The one production directory holding the package's tracked __init__.py, repository-relative ("" at the top)."""
    if "." in package:
        raise ContractError(f"root package {package} is dotted; quality judges only top-level root packages")
    init = PurePosixPath(package, PACKAGE_INIT)
    directories = sorted({
        str(PurePosixPath(name).parent.parent) for name in tracked
        if PurePosixPath(name).parts[-2:] == init.parts and ratchet.area_of(name) is ratchet.Area.PRODUCTION
    })
    if len(directories) != 1:
        found = ", ".join(str(PurePosixPath(directory, package)) for directory in directories) or "none"
        raise ContractError(f"root package {package} needs exactly one tracked {init} outside the tests, found: {found}")
    return "" if directories[0] == "." else directories[0]


class _TrackedDirectoryFinder(AbstractPackageFinder):
    """Hands grimp each root package at the tracked directory IMPORT-7 decided, never where Python would find it."""

    def __init__(self, directories: Mapping[str, str]) -> None:
        self._directories = directories

    def determine_package_directories(self, package_name: str, file_system: AbstractFileSystem) -> set[str]:
        return {self._directories[package_name]}


@contextmanager
def _grimp_reading(directories: Mapping[str, str]) -> Iterator[None]:
    finder = grimp_settings.PACKAGE_FINDER
    grimp_settings.configure(PACKAGE_FINDER=_TrackedDirectoryFinder(directories))
    try:
        yield
    finally:
        grimp_settings.configure(PACKAGE_FINDER=finder)


def _build_graph(repository: str, roots: Mapping[str, str], options: UserOptions) -> grimp.ImportGraph:
    package_directories = {package: os.path.join(repository, directory, package) for package, directory in roots.items()}
    for package_directory in package_directories.values():
        init = os.path.join(package_directory, PACKAGE_INIT)
        if not os.path.lexists(init):
            raise _cannot_be_examined(repository, init, "the checkout does not hold it")
        _refuse_links_out(repository, package_directory)
    try:
        with _grimp_reading(package_directories):
            return grimp.build_graph(
                *roots,
                include_external_packages=_include_external_packages(options),
                exclude_type_checking_imports=use_cases._get_exclude_type_checking_imports(options),
                cache_dir=None,
            )
    except BaseException as error:
        # last resort for a source that changed after the walk: grimp 3.20 still panics on a FIFO and on a file name that
        # is not UTF-8, which the walk refuses. The panic type derives from BaseException and lives in `pyo3_runtime`,
        # which cannot be imported; the Rust panic hook has already written its native diagnostic (with an absolute
        # path) to fd 2 and redirecting fds would hide grimp's own output
        if type(error).__module__ != PANIC_MODULE:
            raise
        raise ContractError("grimp panicked while reading the root packages;"
                            " a source file it cannot read or decode cannot be judged") from error


def _refuse_links_out(repository: str, package_directory: str) -> None:
    """grimp walks a root package following symlinks, so this walks it the same way and refuses any directory or
    file that resolves outside the repository, any loop, and any .py file it cannot stat or read, before grimp reads
    anything."""
    real_directories: dict[str, str] = {}
    def refuse_unexaminable(error: OSError) -> None:
        raise _cannot_be_examined(repository, error.filename, error.strerror) from error

    for parent, directories, files in os.walk(package_directory, followlinks=True, onerror=refuse_unexaminable):
        real_parent = os.path.realpath(parent)
        if real_parent in _real_ancestors(real_directories, package_directory, parent):
            raise ContractError(f"{os.path.relpath(parent, repository)} links back into"
                                f" {os.path.relpath(real_parent, repository)}; a symlink loop cannot be judged")
        real_directories[parent] = real_parent
        for path in (parent, *(os.path.join(parent, name) for name in (*directories, *files))):
            if not Path(os.path.realpath(path)).is_relative_to(repository):
                raise ContractError(f"{os.path.relpath(path, repository)} links outside the repository")
        for path in (os.path.join(parent, name) for name in files if name.endswith(".py")):
            _refuse_unreadable_source(repository, path, _stat_refusing_links(repository, path))


def _stat_refusing_links(repository: str, path: str) -> os.stat_result:
    try:
        return os.stat(path)
    except OSError as error:
        if error.errno == errno.ELOOP:
            raise ContractError(f"{os.path.relpath(path, repository)} links back into"
                                f" {os.path.relpath(os.path.realpath(path), repository)};"
                                " a symlink loop cannot be judged") from error
        if error.errno == errno.ENOENT and os.path.islink(path):
            raise ContractError(f"{os.path.relpath(path, repository)} links to nothing;"
                                " a dangling link cannot be judged") from error
        raise _cannot_be_examined(repository, path, error.strerror) from error


def _refuse_unreadable_source(repository: str, path: str, file_status: os.stat_result) -> None:
    """grimp opens and decodes every .py file it finds, and a stat succeeds on a file whose mode forbids the read or
    that blocks an open (a FIFO), so all refusals come here first, in that order: file type, name, content.
    A coding cookie outside READABLE_COOKIES is refused, because quality reads every source as UTF-8."""
    if not stat.S_ISREG(file_status.st_mode):
        raise _cannot_be_examined(repository, path, "not a regular file")
    try:
        path.encode("utf-8")
    except UnicodeEncodeError as error:
        raise _cannot_be_examined(repository, path, "file name is not valid UTF-8") from error
    try:
        source = Path(path).read_bytes()
        source.decode("utf-8")
        _refuse_declared_encoding(repository, path, source)
    except UnicodeDecodeError as error:
        raise _cannot_be_examined(repository, path, "not valid UTF-8") from error
    except OSError as error:
        raise _cannot_be_examined(repository, path, error.strerror) from error


def _refuse_declared_encoding(repository: str, path: str, source: bytes) -> None:
    """detect_encoding decides whether the cookie is an error; READABLE_COOKIES decides which spelling quality reads, and
    a cookie counts on either of the first two lines, even after code, where PEP 263 would ignore it, as grimp's
    reader takes it."""
    try:
        tokenize.detect_encoding(io.BytesIO(source).readline)
    except SyntaxError as error:
        if error.msg != NUL_IN_SOURCE:  # a NUL is grimp's own syntax error to name
            raise _cannot_be_examined(repository, path, error.msg) from error
    for line in source.removeprefix(codecs.BOM_UTF8).splitlines()[:2]:
        cookie = CODING_COOKIE.match(line.decode("utf-8"))
        if cookie:
            _refuse_cookie_quality_does_not_read(repository, path, cookie.group(1))
            return


def _refuse_cookie_quality_does_not_read(repository: str, path: str, spelling: str) -> None:
    if spelling.lower() not in READABLE_COOKIES:
        accepted = ", ".join(sorted(READABLE_COOKIES))
        raise _cannot_be_examined(repository, path, f"declares {spelling}; quality reads only {accepted}")


def _real_ancestors(real_directories: Mapping[str, str], package_directory: str, directory: str) -> set[str]:
    """The resolved places of the directories os.walk passed through on its way down to `directory`."""
    ancestors = set()
    while directory != package_directory:
        directory = os.path.dirname(directory)
        ancestors.add(real_directories[directory])
    return ancestors


def _cannot_be_examined(repository: str, path: str, reason: str) -> ContractError:
    name = os.path.relpath(path, repository)
    return ContractError(f"{name} cannot be examined: {reason}")


class _CollectedWarnings(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.WARNING)
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


@contextmanager
def _library_warnings(repository: str) -> Iterator[list[str]]:
    """grimp warns through logging with absolute paths; its messages are collected, repository-relative, instead of
    reaching the terminal."""
    logger = logging.getLogger(grimp.__name__)
    collected = _CollectedWarnings()
    propagates = logger.propagate
    logger.addHandler(collected)
    logger.propagate = False
    messages: list[str] = []
    try:
        yield messages
        messages += [_relative(message, repository) for message in collected.messages]
    finally:
        logger.removeHandler(collected)
        logger.propagate = propagates


def _refined(contract: Contract, graph: grimp.ImportGraph, read: ContractsRead) -> None:
    """The contract's own copy keeps the submodules of an external package that its
    `ignore_imports` name, read from the tracked statements of the modules importing it."""
    if not isinstance(contract, ForbiddenContract):
        return
    for package, names in external_imports.named_submodules(contract.ignore_imports or (), read.roots).items():
        if not contract.as_packages:
            raise ContractError(f"contract {contract.name} ignores imports of {package} submodules and sets as_packages = false;"
                                " an import of a submodule that no line covers cannot be judged")
        if package in graph.modules:
            importers = sorted(graph.find_modules_that_directly_import(package))
            external_imports.refine(graph, package, names, {importer: _statements_of(read, importer) for importer in importers})


def _statements_of(read: ContractsRead, module: str) -> list[python_imports.Statement]:
    """The import statements of the tracked file of a module of a root package."""
    place = _module_place(read.roots[module.partition(".")[0]], module)
    path = next((path for path in (f"{place}.py", f"{place}/{PACKAGE_INIT}") if path in read.tracked), None)
    if path is None:
        raise ContractError(f"module {module} cannot be examined: git tracks no file for it")
    content = checkout_files.read_bytes(read.root, path)
    if content is None:
        raise ContractError(f"module {module} cannot be examined: the checkout holds nothing at {path}")
    try:
        return python_imports.import_statements(path, content)
    except ratchet.PARSE_ERRORS as error:
        raise UnparsableModule(_cut(f"{path} is not valid Python ({error})")) from error


def _check(contract: Contract, read: ContractsRead, repository: str) -> RuleResult:
    """Checks one contract on its own copy of the graph, as import-linter does."""
    config_name = read.config.name
    with _judging(f"contract {contract.name} in {config_name}", repository):
        own_graph = copy.deepcopy(read.graph)
        _refined(contract, own_graph, read)
        try:
            check = contract.check(own_graph, verbose=False)
        except MissingImport as stale:
            return contract_results.unmatched_ignores(contract.name, config_name, stale)
        return contract_results.judged(contract, config_name, check)


@contextmanager
def _judging(subject: str, place: str) -> Iterator[None]:
    """Any other error of import-linter or grimp is cannot judge, never a traceback, and names no path under `place`."""
    try:
        yield
    except ContractError:
        raise
    except SourceSyntaxError as error:
        raise UnparsableModule(_cut(_relative(str(error), place))) from error
    except Exception as error:  # every error import-linter or grimp raises is caller-facing: none may escape as a crash
        raise ContractError(f"{subject} cannot be judged: {type(error).__name__}: {_relative(str(error), place)}") from error


def _cut(message: str) -> str:
    """The source text a syntax error echoes is cut to a few hundred characters; the print boundary escapes what is left."""
    return message if len(message) <= MAX_ECHOED_SOURCE else message[:MAX_ECHOED_SOURCE] + "…"


def _relative(message: str, place: str) -> str:
    if not place:
        return message
    return message.replace(place + os.sep, "").replace(place, ".")
