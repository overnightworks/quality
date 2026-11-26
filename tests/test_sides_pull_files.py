"""The files git renamed are read only where they can change the judgement."""

import pytest

from quality.config_allowances import ConfigSide
from quality.ratchet import NO_RENAMES, Renames
from quality.sides import pull_files, side_of
from tests.support.layout import declaring, root_declaring
from tests.support.verdict import gate_config

CHECKS = gate_config("checks")
LISTED = Renames({"tests/a.py": "tests/b.py"}, frozenset({"tests/b.py"}))
BOTH_TABLES = root_declaring("specs/") + declaring("tests/a.py", rule="test-layout")


def never_read(marks: bool):
    raise AssertionError("a file read that no entry of the pull request needs")


ADDED = {
    "an added root entry": (CHECKS, CHECKS + root_declaring("specs/"), True),
    "an added layout entry": (CHECKS, CHECKS + declaring("tests/a.py", rule="test-layout"), True),
    "a layout entry of a new rule": (
        CHECKS + declaring("tests/a.py", rule="script-name"), CHECKS + declaring("tests/a.py", rule="test-layout"), True
    ),
    "an adoption of entries": (None, CHECKS + BOTH_TABLES, True),
    "no entry": (CHECKS, CHECKS, False),
    "inherited entries of both tables": (CHECKS + BOTH_TABLES, CHECKS + BOTH_TABLES, False),
    "a pin move writing ends": (CHECKS + BOTH_TABLES.replace("ends", "unread"), CHECKS + BOTH_TABLES, False),
    "deleted entries": (CHECKS + BOTH_TABLES, CHECKS, False),
}


@pytest.mark.parametrize(("base", "head", "listed"), ADDED.values(), ids=ADDED.keys())
def test_the_renames_are_read_only_where_the_head_holds_an_added_entry(base, head, listed):
    """VERDICT-81: a pull request that adds no root or layout entry reads and fetches nothing more than before."""
    base_side = side_of(base, None, "the base", side=ConfigSide.BASE)
    head_side = side_of(head, None, "the head", side=ConfigSide.HEAD)
    tree, renames = pull_files(base_side, head_side, never_read, lambda marks: LISTED if listed else never_read(marks))
    assert (tree, renames) == (None, LISTED if listed else NO_RENAMES)
