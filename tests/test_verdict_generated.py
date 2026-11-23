"""A `[[size.generated]]` or `[[spec_citations.implemented]]` declaration is a loosening that declares itself."""

import datetime
from collections.abc import Callable
from dataclasses import dataclass

import pytest

from quality.ratchet import NO_RENAMES
from quality.sides import side_of
from quality.verdict import judge
from tests.support.generated_files import TYPES_FILE, generated_config
from tests.support.specs import foreign_entry, implemented_entry
from tests.support.verdict import declaration, gate_config, job

DATE = datetime.date(2026, 10, 30)


@dataclass(frozen=True)
class Kind:
    """One declaration that declares its own loosening: its entry as the config writes it and the line the verdict prints."""

    entry: Callable[..., str]
    first: str
    second: str
    line: str

    def what(self, path: str) -> str:
        return self.line.format(path=path)


KINDS = {
    "generated file": Kind(
        lambda path, item="#174", date=DATE, reason="written by a script": generated_config(path=path, item=item, date=date, reason=reason),
        TYPES_FILE, "frontend/src/lib/api/other.ts", "size leaves out generated file {path}",
    ),
    "implemented document": Kind(
        lambda path, item="#174", date=DATE, reason="written by a script": implemented_entry(path, item, date, reason),
        "docs/calling.md", "docs/hearing.md", "spec citation leaves {path} to its implementers",
    ),
}
EACH_KIND = pytest.mark.parametrize("kind", KINDS.values(), ids=KINDS.keys())


def judged(base: str | None, head: str, *, with_base: bool = True):
    """The verdict's loosenings as `(what, ruling)`, its unmatched declarations' `what`s, and whether it is blocked and
    green; a base of None is a base without quality/config.toml, `with_base=False` a run without one."""
    base_side = side_of(base, None, "the base") if with_base else None
    result = judge(side_of(head, None, "the head"), base_side, [job(), job("other")], {}, 1, NO_RENAMES)
    loosenings = tuple((each.what, each.declaration and each.declaration.ruling()) for each in result.loosenings)
    return loosenings, tuple(declared.what for declared in result.stale), result.blocked, result.green


def green():
    return (), (), False, True


def declared(kind: Kind, path: str, item="#174"):
    return ((kind.what(path), f"{item} of 2026-10-30"),), (), False, False


CHECKS = gate_config("checks")
CHANGES = {
    "added": lambda kind: (CHECKS, CHECKS + kind.entry(kind.first), declared(kind, kind.first)),
    "a base without config": lambda kind: (None, CHECKS + kind.entry(kind.first), declared(kind, kind.first)),
    "inherited": lambda kind: (CHECKS + kind.entry(kind.first), CHECKS + kind.entry(kind.first), green()),
    "removed": lambda kind: (CHECKS + kind.entry(kind.first), CHECKS, green()),
    "reason changed": lambda kind: (CHECKS + kind.entry(kind.first), CHECKS + kind.entry(kind.first, reason="reworded"), green()),
    "item changed": lambda kind: (CHECKS + kind.entry(kind.first), CHECKS + kind.entry(kind.first, item="#999"), green()),
    "date changed": lambda kind: (
        CHECKS + kind.entry(kind.first), CHECKS + kind.entry(kind.first, date=datetime.date(2026, 11, 1)), green()
    ),
    "path changed": lambda kind: (CHECKS + kind.entry(kind.first), CHECKS + kind.entry(kind.second), declared(kind, kind.second)),
    "a second path added": lambda kind: (
        CHECKS + kind.entry(kind.first),
        CHECKS + kind.entry(kind.first) + kind.entry(kind.second, item="#175"),
        declared(kind, kind.second, "#175"),
    ),
}


@EACH_KIND
@pytest.mark.parametrize("change", CHANGES.values(), ids=CHANGES.keys())
def test_adding_or_re_pathing_a_declaration_is_a_self_declared_loosening(kind, change):
    """VERDICT-68, VERDICT-69, CITE-18: only a head path the base does not declare loosens, bound to the head
    declaration's own item and date; inherited, removed and other-key changes are green."""
    base, head, expected = change(kind)
    assert judged(base, head) == expected


def test_a_changed_job_of_a_generated_file_loosens_nothing():
    """VERDICT-69."""
    with_types = CHECKS + generated_config()
    head = gate_config("checks", "other") + generated_config(job="other")
    assert judged(with_types, head) == green()


@EACH_KIND
def test_a_run_without_a_base_judges_no_declaration(kind):
    """VERDICT-61, CITE-18: a push judges no loosening, so a declaration leaves it green."""
    assert judged(CHECKS, CHECKS + kind.entry(kind.first), with_base=False) == green()


@EACH_KIND
def test_a_gate_loosened_entry_with_the_declared_text_matches_nothing(kind):
    """VERDICT-70, CITE-18: the declaration declares itself, so a [[gate.loosened]] entry naming it binds nothing."""
    what = kind.what(kind.first)
    head = gate_config("checks", declared=(declaration(what),)) + kind.entry(kind.first)
    assert judged(CHECKS, head) == (((what, "#174 of 2026-10-30"),), (what,), True, False)


def test_a_foreign_prefix_is_no_loosening():
    """CITE-18: an implementer's declaration loosens nothing, in an adoption or beside a base that holds none."""
    assert judged(CHECKS, CHECKS + foreign_entry("LANG")) == green()
    assert judged(None, CHECKS + foreign_entry("LANG")) == green()
