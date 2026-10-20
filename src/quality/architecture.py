"""Judges a repository's import-linter contracts, with import-linter 2.15 as a library and never its CLI.

import-linter's own reader parses the config from a private temporary copy, grimp builds the graph with the
repository's source roots first on sys.path only while it runs and without a cache, so nothing is written to the
checkout. Caller code never runs: a plugin contract type, a dotted root package, a root package Python would find
elsewhere, a symlink out of the repository (also through a link inside it) and a symlink loop are refused before
grimp reads anything. Every message names paths repository-relative. The private import-linter helpers used here are
pinned by the exact version in pyproject.toml.
"""

import codecs
import copy
import errno
import importlib.util
import io
import logging
import os
import re
import stat
import sys
import tempfile
import tokenize
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
PANIC_MODULE = "pyo3_runtime"
MAX_ECHOED_SOURCE = 200
# Probed on grimp 3.17, whose Rust reader (not readable from Python) takes the raw lower-cased spelling of a coding
# cookie: Python's own aliases such as utf_8, U8, cp65001 or utf-8-sig make it panic.
GRIMP_ACCEPTED_COOKIES = frozenset({"utf-8", "utf8", "unicode-1-1-utf-8", "unicode11utf8", "x-unicode20utf8",
                                    "ascii", "us-ascii", "ansi_x3.4-1968"})
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
    repository = os.path.realpath(root)
    with _judging(f"the config {config.name}", repository):
        contracts = [_contract(config, index, options, repository)
                     for index, options in enumerate(config.options.contracts_options, 1)]
        roots = {package: _source_root(tracked, package) for package in config.options.session_options["root_packages"]}
        with _library_warnings(repository) as graph_notes:
            graph = _build_graph(repository, roots, config.options)
    verdicts = [_check(contract, graph, config.name, repository) for contract in contracts]
    return ContractsVerdict([finding for verdict in verdicts for finding in verdict.findings],
                            graph_notes + [note for verdict in verdicts for note in verdict.notes])


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


def _contract(config: Config, index: int, options: dict, repository: str) -> Contract:
    if "name" not in options:
        raise ContractError(f"contract {index} in {config.name} has no name")
    name = options["name"]
    with _judging(f"contract {name} in {config.name}", repository):
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
        try:
            return grimp.build_graph(
                *roots,
                include_external_packages=bool(use_cases._get_include_external_packages(options)),
                exclude_type_checking_imports=use_cases._get_exclude_type_checking_imports(options),
                cache_dir=None,
            )
        except BaseException as error:
            # last resort: _refuse_unreadable_source stops the known triggers. The panic type derives from
            # BaseException and lives in `pyo3_runtime`, which cannot be imported; the Rust panic hook has already
            # written its native diagnostic (with an absolute path) to fd 2 and redirecting fds would hide grimp's own output
            if type(error).__module__ != PANIC_MODULE:
                raise
            raise ContractError("grimp panicked while reading the root packages;"
                                " a source file it cannot read or decode cannot be judged") from error


def _refuse_links_out(repository: str, package_directory: str) -> None:
    """grimp walks a root package following symlinks, so this walks it the same way and refuses any directory or
    file that resolves outside the repository, and any loop, before grimp reads anything."""
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
        for name in files:
            path = os.path.join(parent, name)
            file_status = _stat_refusing_links(repository, path)
            if file_status is not None:
                _refuse_unreadable_source(repository, path, file_status)


def _stat_refusing_links(repository: str, path: str) -> os.stat_result | None:
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
        # a file that vanished mid-walk (a race, no link) is grimp's to report
        if error.errno != errno.ENOENT:
            raise _cannot_be_examined(repository, path, error.strerror) from error
    return None


def _refuse_unreadable_source(repository: str, path: str, file_status: os.stat_result) -> None:
    """grimp opens and decodes every .py file it finds and panics on a name or content it cannot take, and a stat
    succeeds on a file whose mode forbids the read or that blocks an open (a FIFO), so all refusals come here first,
    in that order: file type, name, content. grimp reads UTF-8 only, even when a coding cookie declares another."""
    if not path.endswith(".py"):
        return
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
    except FileNotFoundError:
        pass  # vanished mid-walk: grimp's to report, as in _stat_refusing_links
    except OSError as error:
        raise _cannot_be_examined(repository, path, error.strerror) from error


def _refuse_declared_encoding(repository: str, path: str, source: bytes) -> None:
    """detect_encoding decides whether the cookie is an error; grimp's own rule decides which cookie it can read: a
    cookie on either of the first two lines, even after code, where PEP 263 would ignore it."""
    try:
        tokenize.detect_encoding(io.BytesIO(source).readline)
    except SyntaxError as error:
        if error.msg != NUL_IN_SOURCE:  # a NUL is grimp's own syntax error to name
            raise _cannot_be_examined(repository, path, error.msg) from error
    for line in source.removeprefix(codecs.BOM_UTF8).splitlines()[:2]:
        cookie = CODING_COOKIE.match(line.decode("utf-8"))
        if cookie:
            _refuse_cookie_grimp_cannot_read(repository, path, cookie.group(1))
            return


def _refuse_cookie_grimp_cannot_read(repository: str, path: str, spelling: str) -> None:
    if spelling.lower() not in GRIMP_ACCEPTED_COOKIES:
        accepted = ", ".join(sorted(GRIMP_ACCEPTED_COOKIES))
        raise _cannot_be_examined(repository, path, f"declares {spelling}; grimp reads only {accepted}")


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
    """grimp warns through logging with absolute paths; its warnings become the rule's notes, repository-relative,
    instead of reaching the terminal."""
    logger = logging.getLogger(grimp.__name__)
    collected = _CollectedWarnings()
    propagates = logger.propagate
    logger.addHandler(collected)
    logger.propagate = False
    notes: list[str] = []
    try:
        yield notes
        notes += [f"{RULE}: note - {_relative(message, repository)}" for message in collected.messages]
    finally:
        logger.removeHandler(collected)
        logger.propagate = propagates


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
        raise ContractError(f"root package {package} is {expected}, but Python finds it {_place(found, repository)};"
                            " judging it would read the wrong package")


def _place(found: str | None, repository: str) -> str:
    """Where Python found a root package: inside, repository-relative; outside, from the sys.path entry it lies under,
    so the place is named without an absolute local path (rule 4)."""
    if found is None:
        return "without a source file"
    if Path(found).is_relative_to(repository):
        return f"at {os.path.relpath(found, repository)}"
    entry = _path_entry_holding(found)
    if entry is None:
        return "outside the repository"
    return f"outside the repository at {os.path.relpath(found, entry.parent)}"


def _path_entry_holding(found: str) -> Path | None:
    """The deepest sys.path entry a file lies under, which is the one its package was found in."""
    entries = [Path(os.path.realpath(entry)) for entry in sys.path if entry]
    holding = [entry for entry in entries if Path(found).is_relative_to(entry)]
    return max(holding, key=lambda entry: len(entry.parts), default=None)


def _check(contract: Contract, graph: grimp.ImportGraph, config_name: str, repository: str) -> ContractsVerdict:
    """Checks one contract on its own copy of the graph, as import-linter does."""
    with _judging(f"contract {contract.name} in {config_name}", repository):
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
        broken = Finding(
            RULE, config_name, None, f"contract {contract.name} is broken:", BROKEN_FIX, detail=_rendered(contract, check)
        )
        return ContractsVerdict([broken], notes)


def _unmatched(message: str) -> str:
    match = UNMATCHED_IGNORE.fullmatch(message)
    return message if match is None else f"ignores {match['expression']}, which no import matches"


def _rendered(contract: Contract, check: ContractCheck) -> tuple[str, ...]:
    """The broken contract as import-linter renders it, as plain text, one entry per line."""
    with output.console.capture() as captured:
        contract.render_broken_contract(check)
    return tuple(ANSI_ESCAPE.sub("", captured.get()).strip().split("\n"))


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
    return message.replace(place + os.sep, "").replace(place, ".")
