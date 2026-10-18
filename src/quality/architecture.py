"""Judges a repository's import-linter contracts, with import-linter 2.15 as a library and never its CLI.

import-linter's own reader parses the config from a private temporary copy, grimp builds the graph with the
repository's source roots first on sys.path only while it runs and without a cache, so nothing is written to the
checkout. Caller code never runs: a plugin contract type, a dotted root package, a root package Python would find
elsewhere, a symlink out of the repository (also through a link inside it) and a symlink loop are refused before
grimp reads anything. Every message names paths repository-relative. The private import-linter helpers used here are
pinned by the exact version in pyproject.toml.
"""

import copy
import importlib.util
import os
import re
import sys
import tempfile
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import grimp
from grimp.exceptions import SourceSyntaxError
from importlinter import configuration
from importlinter.application import output, use_cases
from importlinter.application.user_options import UserOptions
from importlinter.contracts.acyclic_siblings import AcyclicSiblingsContract
from importlinter.contracts.forbidden import ForbiddenContract
from importlinter.contracts.independence import IndependenceContract
from importlinter.contracts.layers import LayersContract
from importlinter.contracts.protected import ProtectedContract
from importlinter.domain.contract import Contract, ContractCheck, InvalidContractOptions
from importlinter.domain.helpers import MissingImport

from quality import ratchet
from quality.report import Finding

RULE = "import-contract"
CONFIG_FILES = (".importlinter", "setup.cfg", "pyproject.toml")
NO_CONFIG_REPORT = f"{RULE}: report only (no import-linter config)"
BUILT_IN_CONTRACT_TYPES: Mapping[str, type[Contract]] = {
    "forbidden": ForbiddenContract,
    "layers": LayersContract,
    "independence": IndependenceContract,
    "protected": ProtectedContract,
    "acyclic_siblings": AcyclicSiblingsContract,
}
UNMATCHED_IGNORE = re.compile(r"No matches for ignored import (?P<expression>.+)\.")
ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
BROKEN_FIX = "change the import or the contract."
STALE_IGNORE_FIX = "delete the entry."


class ContractError(Exception):
    """The contracts cannot be judged; the message names every path repository-relative."""


class UnparsableModule(ContractError):
    """A module of a root package grimp cannot parse."""


@dataclass(frozen=True)
class Config:
    name: str
    options: UserOptions


@dataclass(frozen=True)
class ContractsVerdict:
    findings: list[Finding]
    notes: list[str]


def check_contracts(root: Path, tracked: Sequence[str], configs: Mapping[str, str]) -> ContractsVerdict:
    """Report only without a config; with one, every contract must be kept.

    `configs` maps each tracked config file at the repository's top to its text.
    """
    config = find_config(configs)
    if config is None:
        return ContractsVerdict([], [NO_CONFIG_REPORT])
    if "contract_types" in config.options.session_options:
        raise ContractError(f"{config.name} sets contract_types, whose plugin modules would be imported;"
                            " only the built-in contract types are judged")
    contracts = [_contract(config, index, options) for index, options in enumerate(config.options.contracts_options, 1)]
    roots = {package: _source_root(tracked, package) for package in config.options.session_options["root_packages"]}
    repository = os.path.realpath(root)
    with _judging(f"the config {config.name}", repository):
        graph = _build_graph(repository, roots, config.options)
    verdicts = [_check(contract, graph, config.name, repository) for contract in contracts]
    return ContractsVerdict([finding for verdict in verdicts for finding in verdict.findings],
                            [note for verdict in verdicts for note in verdict.notes])


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


def _contract(config: Config, index: int, options: dict) -> Contract:
    if "name" not in options:
        raise ContractError(f"contract {index} in {config.name} has no name")
    name = options["name"]
    contract_class = BUILT_IN_CONTRACT_TYPES.get(options.get("type"))
    if contract_class is None:
        raise ContractError(f"contract {name} in {config.name} has type {options.get('type')}; only the built-in"
                            f" types are judged ({', '.join(BUILT_IN_CONTRACT_TYPES)})")
    try:
        return contract_class(name=name, session_options=config.options.session_options, contract_options=options)
    except InvalidContractOptions as error:
        fields = "; ".join(f"{field}: {reason}" for field, reason in error.errors.items())
        raise ContractError(f"contract {name} in {config.name} has invalid options ({fields})") from error


def _source_root(tracked: Sequence[str], package: str) -> str:
    """The one production directory holding the package's tracked __init__.py, repository-relative ("" at the top)."""
    if "." in package:
        raise ContractError(f"root package {package} is dotted; finding it would import {package.rpartition('.')[0]}")
    init = PurePosixPath(package, "__init__.py")
    directories = sorted({
        str(PurePosixPath(name).parent.parent) for name in tracked
        if PurePosixPath(name).parts[-2:] == init.parts and ratchet.area_of(name) is ratchet.Area.PRODUCTION
    })
    if len(directories) != 1:
        found = ", ".join(str(PurePosixPath(directory, package)) for directory in directories) or "none"
        raise ContractError(f"root package {package} needs exactly one tracked {init} outside the tests, found: {found}")
    return "" if directories[0] == "." else directories[0]


def _build_graph(repository: str, roots: Mapping[str, str], options: UserOptions) -> grimp.ImportGraph:
    for package, directory in roots.items():
        _refuse_links_out(repository, os.path.join(repository, directory, package))
    with _first_on_path([os.path.join(repository, directory) for directory in dict.fromkeys(roots.values())]):
        for package, directory in roots.items():
            _refuse_foreign_location(repository, package, directory)
        return grimp.build_graph(
            *roots,
            include_external_packages=bool(use_cases._get_include_external_packages(options)),
            exclude_type_checking_imports=use_cases._get_exclude_type_checking_imports(options),
            cache_dir=None,
        )


def _refuse_links_out(repository: str, package_directory: str) -> None:
    """grimp walks a root package following symlinks, so this walks it the same way and refuses any directory or
    file that resolves outside the repository, and any loop, before grimp reads anything."""
    real_directories: dict[str, str] = {}
    for parent, directories, files in os.walk(package_directory, followlinks=True, onerror=_raise):
        real_parent = os.path.realpath(parent)
        if real_parent in _real_ancestors(real_directories, package_directory, parent):
            raise ContractError(f"{os.path.relpath(parent, repository)} links back into"
                                f" {os.path.relpath(real_parent, repository)}; a symlink loop cannot be judged")
        real_directories[parent] = real_parent
        for path in (parent, *(os.path.join(parent, name) for name in (*directories, *files))):
            if not Path(os.path.realpath(path)).is_relative_to(repository):
                raise ContractError(f"{os.path.relpath(path, repository)} links outside the repository")


def _real_ancestors(real_directories: Mapping[str, str], package_directory: str, directory: str) -> set[str]:
    """The resolved places of the directories os.walk passed through on its way down to `directory`."""
    ancestors = set()
    while directory != package_directory:
        directory = os.path.dirname(directory)
        ancestors.add(real_directories[directory])
    return ancestors


def _raise(error: OSError) -> None:
    raise error


@contextmanager
def _first_on_path(directories: list[str]) -> Iterator[None]:
    saved = list(sys.path)
    sys.path[:0] = directories
    try:
        yield
    finally:
        sys.path[:] = saved


def _refuse_foreign_location(repository: str, package: str, directory: str) -> None:
    """A name the interpreter already imported (or finds before the roots) would make grimp read another package.

    The collision is named rather than worked around: there is no custom finder, and sys.modules stays as it is.
    """
    expected = os.path.join(directory, package, "__init__.py")
    spec = importlib.util.find_spec(package)
    found = os.path.realpath(spec.origin) if spec is not None and spec.origin and os.path.isabs(spec.origin) else None
    if found != os.path.join(repository, expected):
        place = os.path.relpath(found, repository) if found and Path(found).is_relative_to(repository) else None
        raise ContractError(f"root package {package} is {expected}, but Python finds it"
                            f" {place or 'outside the repository'}; judging it would read the wrong package")


def _check(contract: Contract, graph: grimp.ImportGraph, config_name: str, repository: str) -> ContractsVerdict:
    """Checks one contract on its own copy of the graph, as import-linter does."""
    with _judging(f"contract {contract.name}", repository):
        try:
            check = contract.check(copy.deepcopy(graph), verbose=False)
        except MissingImport as stale:
            return ContractsVerdict([
                Finding(RULE, config_name, None, f"contract {contract.name} {_unmatched(line)}", STALE_IGNORE_FIX)
                for line in str(stale).splitlines()
            ], [])
        notes = [f"{RULE}: note - contract {contract.name} {_unmatched(warning)}" for warning in check.warnings]
        if check.kept:
            return ContractsVerdict([], notes)
        broken = f"contract {contract.name} is broken:\n{_rendered(contract, check)}"
        return ContractsVerdict([Finding(RULE, config_name, None, broken, BROKEN_FIX)], notes)


def _unmatched(message: str) -> str:
    match = UNMATCHED_IGNORE.fullmatch(message)
    return message if match is None else f"ignores {match['expression']}, which no import matches"


def _rendered(contract: Contract, check: ContractCheck) -> str:
    """The broken contract as import-linter renders it, as plain text."""
    with output.console.capture() as captured:
        contract.render_broken_contract(check)
    return ANSI_ESCAPE.sub("", captured.get()).strip()


@contextmanager
def _judging(subject: str, place: str) -> Iterator[None]:
    """Any other error of import-linter or grimp is cannot judge, never a traceback, and names no path under `place`."""
    try:
        yield
    except ContractError:
        raise
    except SourceSyntaxError as error:
        raise UnparsableModule(_relative(str(error), place)) from error
    except Exception as error:  # every error import-linter or grimp raises is caller-facing: none may escape as a crash
        raise ContractError(f"{subject} cannot be judged: {type(error).__name__}: {_relative(str(error), place)}") from error


def _relative(message: str, place: str) -> str:
    return message.replace(place + os.sep, "").replace(place, ".")
