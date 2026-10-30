"""#174 sentence 2: a [[size.generated]] declaration is a loosening that declares itself."""

import datetime

import pytest

from quality.ratchet import NO_RENAMES
from quality.verdict import judge, side_of
from tests.support.generated_files import TYPES_FILE, generated_config
from tests.support.verdict import ONLY_DECLARED, declaration, gate_config, job

DECLARED = f"loosened (declared, #174 of 2026-10-30): size leaves out generated file {TYPES_FILE}"
OTHER_FILE = "frontend/src/lib/api/other.ts"
GREEN = ["verdict: green"]
WITH_TYPES = gate_config("checks") + generated_config()


def verdict_lines(base: str | None, head: str, *, with_base: bool = True) -> list[str]:
    """The verdict's lines; a base of None is a base without quality/config.toml, `with_base=False` a run without one."""
    base_side = side_of(base, None, "the base") if with_base else None
    result = judge(side_of(head, None, "the head"), base_side, [job(), job("other")], {}, 1, NO_RENAMES)
    return list(result.lines())


@pytest.mark.parametrize(
    ("base", "head", "expected"),
    [
        (gate_config("checks"), WITH_TYPES, [DECLARED, ONLY_DECLARED]),
        (None, WITH_TYPES, [DECLARED, ONLY_DECLARED]),
        (WITH_TYPES, WITH_TYPES, GREEN),
        (WITH_TYPES, gate_config("checks"), GREEN),
        (WITH_TYPES, gate_config("checks", "other") + generated_config(job="other"), GREEN),
        (WITH_TYPES, gate_config("checks") + generated_config(reason="reworded"), GREEN),
        (WITH_TYPES, gate_config("checks") + generated_config(item="#999"), GREEN),
        (WITH_TYPES, gate_config("checks") + generated_config(date=datetime.date(2026, 11, 1)), GREEN),
        (WITH_TYPES, gate_config("checks") + generated_config(path=OTHER_FILE),
         [DECLARED.replace(TYPES_FILE, OTHER_FILE), ONLY_DECLARED]),
        (WITH_TYPES, WITH_TYPES + generated_config(path=OTHER_FILE, item="#175"),
         ["loosened (declared, #175 of 2026-10-30): size leaves out generated file " + OTHER_FILE, ONLY_DECLARED]),
    ],
    ids=[
        "added", "a base without config", "inherited", "removed", "job changed", "reason changed", "item changed",
        "date changed", "path changed", "a second file added",
    ],
)
def test_adding_or_re_pathing_a_declaration_is_a_self_declared_loosening(base, head, expected):
    """#174 sentence 2, acceptance 5: only a head path the base does not declare loosens, bound to the head
    declaration's own item and date; inherited, removed and other-key changes are green."""
    assert verdict_lines(base, head) == expected


def test_a_run_without_a_base_judges_no_generated_file():
    """#174 sentence 2: a push judges no loosening, so a declaration leaves it green."""
    assert verdict_lines(gate_config("checks"), WITH_TYPES, with_base=False) == GREEN


def test_a_gate_loosened_entry_with_the_generated_text_matches_nothing():
    """#174 sentence 2: the declaration declares itself, so a [[gate.loosened]] entry naming it binds nothing."""
    what = f"size leaves out generated file {TYPES_FILE}"
    head = gate_config("checks", declared=(declaration(what),)) + generated_config()
    assert verdict_lines(gate_config("checks"), head) == [DECLARED, f"declared loosening matches nothing: {what}", "verdict: red"]
