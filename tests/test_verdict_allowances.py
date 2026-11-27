"""An added `[[root.allow]]` or `[[layout.allow]]` entry is a loosening the verdict judges: the one judgement of both tables."""

import datetime

import pytest

from quality.config_allowances import ConfigSide
from quality.ratchet import NO_RENAMES, Renames
from quality.sides import UnusableConfig, read_base, side_of
from quality.verdict import judge, loosening_lines
from tests.support.allowances import EACH_TABLE, FIX, RULED, RULING, TABLES, Table
from tests.support.git_repositories import head_commit
from tests.support.layout import declaring, root_declaring
from tests.support.verdict import declaration, gate_config, job

CHECKS = gate_config("checks")


def judged(base: str | None, head: str, renames: Renames = NO_RENAMES):
    """What the verdict prints under its loosenings row as `(text, lines below it)`, its unmatched declarations' `what`s
    and whether it is blocked and green; a base of None is a base without quality/config.toml."""
    result = judge(
        side_of(head, None, "the head", side=ConfigSide.HEAD),
        side_of(base, None, "the base", side=ConfigSide.BASE),
        [job()], {}, 1, renames,
    )
    printed = tuple((line.text, line.below) for line in loosening_lines(result))
    return printed, tuple(stale.what for stale in result.stale), result.blocked, result.green


def green():
    return (), (), False, True


def declared(table: Table, name: str, ends: str = "#2"):
    """The only line is the entry's, declared by its own ruling: red, and only the operator's door lands it."""
    return (((f"{table.what(name, ends)} · {RULED}", ("reason: stands apart",)),), (), False, False)


def undeclared(table: Table, *names: str):
    """Each line is the entry's, with the fix for the missing ruling beneath it: red, and nothing lands it."""
    return (tuple((table.what(name), (FIX,)) for name in names), (), True, False)


@EACH_TABLE
@pytest.mark.parametrize("ruled", [True, False], ids=["with its ruling", "without a ruling"])
def test_an_added_entry_is_red_declared_by_its_ruling_or_undeclared_with_a_fix(table, ruled):
    """VERDICT-76, VERDICT-77: a head entry whose identity the base lacks prints its line, declared with its own item and
    date and its reason beneath, or undeclared with the fix beneath."""
    entry = table.entry(table.first, reason="stands apart", ruling=RULING if ruled else None)
    expected = declared(table, table.first) if ruled else undeclared(table, table.first)
    assert judged(CHECKS, CHECKS + entry) == expected


CHANGES = {
    "inherited": lambda table: (table.entry(table.first), table.entry(table.first), green()),
    "a pin move that writes ends": lambda table: (table.entry(table.first, ends=None), table.entry(table.first), green()),
    "a changed reason": lambda table: (table.entry(table.first), table.entry(table.first, reason="reworded"), green()),
    "a changed ends": lambda table: (table.entry(table.first), table.entry(table.first, ends="#3"), green()),
    "a ruling written": lambda table: (table.entry(table.first), table.entry(table.first, ruling=RULING), green()),
    "a changed ruling": lambda table: (
        table.entry(table.first, ruling=("#8", datetime.date(2026, 11, 1))), table.entry(table.first, ruling=RULING), green()
    ),
    "a deleted entry": lambda table: (table.entry(table.first), "", green()),
    "a second entry added": lambda table: (
        table.entry(table.first), table.entry(table.first) + table.entry(table.second), undeclared(table, table.second)
    ),
}


@EACH_TABLE
@pytest.mark.parametrize("change", CHANGES.values(), ids=CHANGES.keys())
def test_only_an_identity_the_base_lacks_loosens(table, change):
    """VERDICT-76, VERDICT-78: an inherited entry, a pin move, a changed reason, ends or ruling and a deletion loosen
    nothing."""
    base, head, expected = change(table)
    assert judged(CHECKS + base, CHECKS + head) == expected


BASES_READ_BY_IDENTITY = {
    "written before the keys": Table.written_before_the_keys,
    "declaring one identity twice": lambda table: table.entry(table.first) * 2,
}


@EACH_TABLE
@pytest.mark.integration
@pytest.mark.parametrize("base", BASES_READ_BY_IDENTITY.values(), ids=BASES_READ_BY_IDENTITY.keys())
def test_the_base_commit_is_read_by_identity_only(table, base, build_repo):
    """VERDICT-79: the verdict's own base reader, `sides.read_base`, reads only each entry's identity, so a base this
    release's head reading refuses is read, and the pull request that repairs it is green."""
    repo = build_repo({"quality/config.toml": CHECKS + base(table)})
    head = side_of(CHECKS + table.entry(table.first), None, "the head", side=ConfigSide.HEAD)
    result = judge(head, read_base(repo, head_commit(repo)), [job()], {}, 1, NO_RENAMES)
    assert (result.blocked, result.green) == (False, True)


def test_a_layout_entry_for_another_rule_of_the_same_path_is_an_add():
    """SUFFIX-20, VERDICT-78: the identity of a layout entry is its rule and its path."""
    table = TABLES["layout"]
    other_rule = declaring(table.first, rule="script-name")
    expected = ((("layout allowance adds tests/a.py for test-layout until #2", (FIX,)),), (), True, False)
    assert judged(CHECKS + other_rule, CHECKS + other_rule + table.entry(table.first)) == expected


@EACH_TABLE
def test_an_adoption_adds_every_entry(table):
    """VERDICT-76: a base without quality/config.toml holds no entry."""
    head = CHECKS + table.entry(table.first) + table.entry(table.second)
    assert judged(None, head) == undeclared(table, table.first, table.second)


@EACH_TABLE
def test_a_run_without_a_base_judges_no_entry(table):
    """VERDICT-61: a push judges no loosening."""
    head = side_of(CHECKS + table.entry(table.first), None, "the head", side=ConfigSide.HEAD)
    assert judge(head, None, [job()], {}, 1, NO_RENAMES).green


@EACH_TABLE
def test_a_gate_loosened_entry_with_the_printed_text_matches_nothing(table):
    """VERDICT-80: an added entry declares itself, so a [[gate.loosened]] entry naming its line binds nothing and is red."""
    what = table.what(table.first)
    head = CHECKS + declaration(what) + table.entry(table.first)
    assert judged(CHECKS, head) == ((((what, (FIX,)),), (what,), True, False))


@EACH_TABLE
def test_a_file_git_renamed_moves_its_entry(table):
    """VERDICT-81: the head entry at the new path is inherited, whichever table holds it."""
    renames = Renames({table.first: table.second}, frozenset({table.second}))
    assert judged(CHECKS + table.entry(table.first), CHECKS + table.entry(table.second), renames) == green()


@EACH_TABLE
def test_an_entry_the_base_holds_at_a_rename_target_stays_inherited(table):
    """VERDICT-78: an inherited entry is never a loosening, whether or not git lists a rename onto its path."""
    renames = Renames({table.first: table.second}, frozenset({table.second}))
    both = CHECKS + table.entry(table.second)
    assert judged(both, both, renames) == green()


@EACH_TABLE
def test_a_head_that_still_declares_the_old_name_adds_the_new_one(table):
    """VERDICT-81: the entry moves rather than being copied."""
    renames = Renames({table.first: table.second}, frozenset({table.second}))
    head = CHECKS + table.entry(table.first) + table.entry(table.second)
    assert judged(CHECKS + table.entry(table.first), head, renames) == undeclared(table, table.second)


def test_a_root_directory_declared_under_a_new_name_is_an_add():
    """VERDICT-81: git reports file moves only, so a moved directory is not read as a rename."""
    renames = Renames({"frontend/app.js": "web/app.js"}, frozenset({"web/app.js"}))
    expected = undeclared(TABLES["root"], "web/")
    assert judged(CHECKS + root_declaring("frontend/"), CHECKS + root_declaring("web/"), renames) == expected


REFUSED_BASES = {
    "a root entry without its name": "[[root.allow]]\nreason = 'r'\n",
    "a root entry that is no text": "[[root.allow]]\nentry = 3\n",
    "a layout entry without its path": "[[layout.allow]]\nrule = 'test-layout'\n",
    "a layout entry without its rule": "[[layout.allow]]\npath = 'tests/a.py'\n",
    "a layout path that is no text": "[[layout.allow]]\npath = 3\nrule = 'test-layout'\n",
    "a layout rule that is no text": "[[layout.allow]]\npath = 'tests/a.py'\nrule = 3\n",
}


@pytest.mark.parametrize("entry", REFUSED_BASES.values(), ids=REFUSED_BASES.keys())
def test_a_base_entry_without_its_identity_is_refused_naming_the_base(entry):
    """VERDICT-79: only the identity is read, and a base lacking it cannot be judged."""
    with pytest.raises(UnusableConfig) as refused:
        side_of(CHECKS + entry, None, "the base", side=ConfigSide.BASE)
    assert refused.value.where == "the base"


TABLE_REFUSED_BASES = {
    "a root allow that is no list": "[root]\nallow = 3\n",
    "a layout allow that is no list": "[layout]\nallow = 3\n",
    "a root table with an unknown key": "[root]\nunknown = 1\n",
    "a layout table with an unknown key": "[layout]\nunknown = 1\n",
    "a root table that is no table": "root = 3\n",
}


@pytest.mark.parametrize("entry", [*REFUSED_BASES.values(), *TABLE_REFUSED_BASES.values()], ids=[*REFUSED_BASES, *TABLE_REFUSED_BASES])
def test_a_refused_base_shows_no_example_with_an_ends_key(entry):
    """VERDICT-79: the base is read by identity only, so no example of its refusal asks for `ends`."""
    with pytest.raises(UnusableConfig) as refused:
        side_of(CHECKS + entry, None, "the base", side=ConfigSide.BASE)
    assert not any(line.startswith("ends") for line in refused.value.error.example or ())


@pytest.mark.parametrize("entry", TABLE_REFUSED_BASES.values(), ids=TABLE_REFUSED_BASES.keys())
def test_a_base_whose_allow_table_cannot_be_read_is_refused_naming_the_base(entry):
    """VERDICT-79: a malformed `[root]` or `[layout]` table is refused on the base too."""
    with pytest.raises(UnusableConfig) as refused:
        side_of(CHECKS + entry, None, "the base", side=ConfigSide.BASE)
    assert refused.value.where == "the base"
