"""#174 sentence 2: a [[size.generated]] declaration is a loosening that declares itself."""

import datetime

import pytest

from quality.ratchet import NO_RENAMES
from quality.sides import side_of
from quality.verdict import judge
from tests.support.generated_files import TYPES_FILE, generated_config
from tests.support.verdict import declaration, gate_config, job

OTHER_FILE = "frontend/src/lib/api/other.ts"
WITH_TYPES = gate_config("checks") + generated_config()


def judged(base: str | None, head: str, *, with_base: bool = True):
    """The verdict's loosenings as `(what, ruling)`, its unmatched declarations' `what`s, and whether it is blocked and
    green; a base of None is a base without quality/config.toml, `with_base=False` a run without one."""
    base_side = side_of(base, None, "the base") if with_base else None
    result = judge(side_of(head, None, "the head"), base_side, [job(), job("other")], {}, 1, NO_RENAMES)
    loosenings = tuple((each.what, each.declaration and each.declaration.ruling()) for each in result.loosenings)
    return loosenings, tuple(declared.what for declared in result.stale), result.blocked, result.green


def green():
    return (), (), False, True


def declared(path=TYPES_FILE, item="#174"):
    return ((f"size leaves out generated file {path}", f"{item} of 2026-10-30"),), (), False, False


@pytest.mark.parametrize(
    ("base", "head", "expected"),
    [
        (gate_config("checks"), WITH_TYPES, declared()),
        (None, WITH_TYPES, declared()),
        (WITH_TYPES, WITH_TYPES, green()),
        (WITH_TYPES, gate_config("checks"), green()),
        (WITH_TYPES, gate_config("checks", "other") + generated_config(job="other"), green()),
        (WITH_TYPES, gate_config("checks") + generated_config(reason="reworded"), green()),
        (WITH_TYPES, gate_config("checks") + generated_config(item="#999"), green()),
        (WITH_TYPES, gate_config("checks") + generated_config(date=datetime.date(2026, 11, 1)), green()),
        (WITH_TYPES, gate_config("checks") + generated_config(path=OTHER_FILE), declared(OTHER_FILE)),
        (WITH_TYPES, WITH_TYPES + generated_config(path=OTHER_FILE, item="#175"), declared(OTHER_FILE, "#175")),
    ],
    ids=[
        "added", "a base without config", "inherited", "removed", "job changed", "reason changed", "item changed",
        "date changed", "path changed", "a second file added",
    ],
)
def test_adding_or_re_pathing_a_declaration_is_a_self_declared_loosening(base, head, expected):
    """#174 sentence 2, acceptance 5: only a head path the base does not declare loosens, bound to the head
    declaration's own item and date; inherited, removed and other-key changes are green."""
    assert judged(base, head) == expected


def test_a_run_without_a_base_judges_no_generated_file():
    """#174 sentence 2: a push judges no loosening, so a declaration leaves it green."""
    assert judged(gate_config("checks"), WITH_TYPES, with_base=False) == green()


def test_a_gate_loosened_entry_with_the_generated_text_matches_nothing():
    """#174 sentence 2: the declaration declares itself, so a [[gate.loosened]] entry naming it binds nothing."""
    what = f"size leaves out generated file {TYPES_FILE}"
    head = gate_config("checks", declared=(declaration(what),)) + generated_config()
    assert judged(gate_config("checks"), head) == (((what, "#174 of 2026-10-30"),), (what,), True, False)
