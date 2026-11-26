from collections.abc import Callable, Iterable
from dataclasses import dataclass
from functools import partial
from operator import attrgetter

from quality import architecture
from quality.architecture import PullTree
from quality.baseline_file import BASELINE_PATH, Baseline
from quality.config import (
    Declaration,
    GateSettings,
    GeneratedFile,
    ImplementedDocument,
    LegacyMode,
)
from quality.config_allowances import LayoutAllowance, RootAllowance
from quality.config_entries import Ruling
from quality.output import SEPARATOR
from quality.ratchet import (
    Renames,
    added_pairs,
    carried_base,
    moved_path,
    named_paths,
    raised_adapter_counts,
    raised_entries,
    raised_job_limits,
)
from quality.sides import Side

SHA_PREFIX_LENGTH = 12
UNRULED_FIX = "write the operator's ruling that lets it in on the entry in quality/config.toml, its item and its date"


@dataclass(frozen=True)
class Loosening:
    """One way the head loosens the gate against its base; `what` is the raw text, before any escaping, and `fix` what
    an undeclared one needs beyond a `[[gate.loosened]]` entry (empty where that entry is the fix)."""

    what: str
    declaration: Declaration | None
    fix: str = ""


def collect_loosenings(
    head: Side, base: Side, renames: Renames, pull_tree: PullTree | None
) -> tuple[tuple[Loosening, ...], tuple[Declaration, ...]]:
    """Each loosening, bound to the head's new declaration of it; a declaration the base holds already binds nothing.
    An advisory allowance, a generated file and an implemented document declare their own loosening, so no [[gate.loosened]] entry binds one; a
    secret allowance, which carries no item or date, is declared by a [[gate.loosened]] entry like a removed job. An added
    root or layout entry declares itself by the ruling written on it and, without one, asks for it."""
    whats = [removal(name) for name in base.settings.mandatory_jobs if name not in head.settings.mandatory_jobs]
    if base.settings.legacy is LegacyMode.FAIL and head.settings.legacy is not LegacyMode.FAIL:
        whats.append(f"legacy {base.settings.legacy.value} → {head.settings.legacy.value}")
    if base.settings.sonar and not head.settings.sonar:
        whats.append("sonar true → false")
    renames = renames if pull_tree is None else pull_tree.renames
    whats += _baseline_loosenings(base.baseline, head.baseline, renames)
    whats += architecture.contract_loosenings(base.contracts, head.contracts, pull_tree)
    whats += _secret_allowance_loosenings(base.settings, head.settings)
    new = {
        declaration.what: declaration
        for declaration in head.settings.loosened
        if declaration not in base.settings.loosened
    }
    loosened = set(whats)
    stale = tuple(declaration for what, declaration in new.items() if what not in loosened)
    own = (Loosening(what, new.get(what)) for what in whats)
    return (*own, *_allowance_loosenings(base.settings, head.settings), *_self_declared_loosenings(base.settings, head.settings, renames)), stale


def _secret_allowance_loosenings(base: GateSettings, head: GateSettings) -> list[str]:
    """Each head secret allowance whose rule, file and value the base lacks, named by the first 12 hex digits of the hash."""
    inherited = set(base.secret_allowances)
    return [
        f"secret allowance adds {allowance.rule} in {allowance.file}{SEPARATOR}sha256 {allowance.secret_sha256[:SHA_PREFIX_LENGTH]}"
        for allowance in head.secret_allowances
        if allowance not in inherited
    ]


def _allowance_loosenings(base: GateSettings, head: GateSettings) -> list[Loosening]:
    """Each head allowance the base lacks, or whose `until` the head moves later, declared by its own item and date."""
    base_until = {allowance.key: allowance.until for allowance in base.advisory_allowances}
    loosenings = []
    for allowance in head.advisory_allowances:
        until = base_until.get(allowance.key)
        if until is None:
            what = f"advisory allowance adds {allowance.id} in {allowance.lock} until {allowance.until.isoformat()}"
        elif allowance.until > until:
            what = f"advisory allowance extends {allowance.id} in {allowance.lock} to {allowance.until.isoformat()}"
        else:
            continue
        loosenings.append(Loosening(what, Declaration(what, allowance.item, allowance.date, allowance.reason)))
    return loosenings


type DeclaringEntry = GeneratedFile | ImplementedDocument | RootAllowance | LayoutAllowance


@dataclass(frozen=True)
class _SelfDeclaring:
    """One kind of entry that is a loosening by itself: the line the verdict prints for an added one, filled from the
    entry's fields; where the settings hold a side's entries; the path and qualifier that tell an entry from another; the
    ruling that lets it in (None where a person has not written it); and whether git's renames move its path."""

    line: str
    entries: Callable[[GateSettings], Iterable[DeclaringEntry]]
    identity: Callable[[DeclaringEntry], tuple[str, str]]
    ruling: Callable[[DeclaringEntry], Ruling | None]
    carries_renames: bool = False


def _path_alone(entry: GeneratedFile | ImplementedDocument) -> tuple[str, str]:
    return entry.path, ""


def _own_ruling(entry: GeneratedFile | ImplementedDocument) -> Ruling:
    return Ruling(entry.item, entry.date)


_SELF_DECLARING = (
    _SelfDeclaring("size leaves out generated file {path}", lambda settings: settings.generated_files, _path_alone, _own_ruling),
    _SelfDeclaring(
        "spec citation leaves {path} to its implementers", lambda settings: settings.implemented_documents, _path_alone, _own_ruling
    ),
    _SelfDeclaring(
        "root allowance adds {entry} until {ends}", lambda settings: settings.root_allowances,
        attrgetter("identity"), attrgetter("ruling"), carries_renames=True,
    ),
    _SelfDeclaring(
        "layout allowance adds {path} for {rule} until {ends}", lambda settings: settings.layout_allowances,
        attrgetter("identity"), attrgetter("ruling"), carries_renames=True,
    ),
)


def _added(kind: _SelfDeclaring, base: GateSettings, head: GateSettings, renames: Renames) -> list[DeclaringEntry]:
    """The head's entries of one kind whose identity the base lacks; where the kind carries renames, a base entry whose
    path git renamed holds the new path unless the head still declares the old one."""
    entries = list(kind.entries(head))
    held = {kind.identity(entry) for entry in kind.entries(base)}
    if kind.carries_renames:
        kept = {kind.identity(entry)[0] for entry in entries}
        held = {(moved, qualifier) for path, qualifier in held if (moved := moved_path(path, renames, kept)) is not None}
    return [entry for entry in entries if kind.identity(entry) not in held]


def _self_declared_loosenings(base: GateSettings, head: GateSettings, renames: Renames) -> list[Loosening]:
    """Each head entry the base does not hold, of each self-declaring kind: declared by the ruling it carries, else
    undeclared with the fix that asks for one."""
    loosenings = []
    for kind in _SELF_DECLARING:
        for entry in _added(kind, base, head, renames):
            what = kind.line.format_map(vars(entry))
            ruling = kind.ruling(entry)
            loosenings.append(
                Loosening(what, None, UNRULED_FIX) if ruling is None else Loosening(what, Declaration(what, ruling.item, ruling.date, entry.reason))
            )
    return loosenings


def removal(name: str) -> str:
    return f'mandatory job "{name}" removed'


def _baseline_loosenings(base: Baseline | None, head: Baseline | None, renames: Renames) -> list[str]:
    """Without a base baseline any head one is adoption; with one, the head may only lower or drop entries, pairs and job
    limits.

    A head entry in a file git renamed is held against the base entry under the old path; it is still named, and
    declared, under its head path.

    A base `[clones]`, `[job_minutes]`, `[spec_citations]` or `[adapter_imports]` table is held the same way: the head keeps
    it, and a base without one lets the head introduce it. The tables are judged apart, so a base without one never skips
    another. A renamed test module that carries the registered mark at the head moves one adapter import count with it.
    """
    if base is None:
        return []
    if head is None:
        return [f"{BASELINE_PATH} removed"]
    base = carried_base(base, renames, named_paths(head))
    lines = [f"size baseline {entry.describe()}" for entry in raised_entries(base.size, head.size)]
    return [*lines, *(line for held in _HELD_TABLES for line in held(base, head))]


def _table_loosenings[Table](
    noun: str,
    table: Callable[[Baseline], Table | None],
    loosened: Callable[[Table, Table], list[str]],
    base: Baseline,
    head: Baseline,
) -> list[str]:
    """One held table: nothing where the base has none, the removal where the head drops it, else what the head loosens."""
    base_table, head_table = table(base), table(head)
    if base_table is None:
        return []
    if head_table is None:
        return [f"{noun} removed"]
    return [f"{noun} {change}" for change in loosened(base_table, head_table)]


_HELD_TABLES: tuple[Callable[[Baseline, Baseline], list[str]], ...] = (
    partial(
        _table_loosenings,
        "clone baseline",
        lambda baseline: baseline.clones,
        lambda base, head: [f"adds {pair.label}" for pair in added_pairs(base, head)],
    ),
    partial(
        _table_loosenings,
        "job limit baseline",
        lambda baseline: baseline.job_minutes,
        lambda base, head: [entry.describe() for entry in raised_job_limits(base, head)],
    ),
    partial(
        _table_loosenings,
        "spec citation baseline",
        lambda baseline: baseline.spec_citations,
        lambda base, head: [f"adds {identifier}" for identifier in sorted(head - base)],
    ),
    partial(
        _table_loosenings,
        "adapter import baseline",
        lambda baseline: baseline.adapter_imports,
        lambda base, head: [entry.describe() for entry in raised_adapter_counts(base, head)],
    ),
)
