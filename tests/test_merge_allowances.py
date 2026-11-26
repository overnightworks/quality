"""The door reads an added `[[root.allow]]` or `[[layout.allow]]` entry through the verdict's one judgement."""

import pytest

from tests.support.allowances import EACH_TABLE, FIX, RULED, RULING
from tests.support.merge import GATE_CONFIG, ONE_JOB, SONAR_GREEN, FakeGh, run
from tests.support.verdict_renames import pulled

pytestmark = pytest.mark.integration


def declared_lines(table) -> str:
    return f"    ! {table.what(table.first)} · {RULED}\n        reason: stands apart\n"


def undeclared_lines(table) -> str:
    return f"    ! {table.what(table.second)}\n        {FIX}\n"


@EACH_TABLE
def test_the_door_lands_an_added_entry_with_its_ruling_as_the_verdict_prints_it(table, tmp_path, capsys):
    """VERDICT-76, VERDICT-77: a declared entry is landable, shown with its ruling and reason."""
    entry = table.entry(table.first, reason="stands apart", ruling=RULING)
    gh = pulled(tmp_path, None, None, base_config=ONE_JOB, merge_config=ONE_JOB + entry, checks=SONAR_GREEN)
    code, out, _, _ = run(gh, capsys)
    assert (code, declared_lines(table) in out) == (0, True)


@EACH_TABLE
def test_the_door_stops_at_an_added_entry_without_a_ruling_and_shows_its_fix(table, tmp_path, capsys):
    """VERDICT-77: the undeclared line stands in the stopped row with the fix beneath it, and nothing is merged, though
    a declared entry stands beside it."""
    entries = table.entry(table.first, reason="stands apart", ruling=RULING) + table.entry(table.second)
    gh = pulled(tmp_path, None, None, base_config=ONE_JOB, merge_config=ONE_JOB + entries, checks=SONAR_GREEN)
    code, out, _, _ = run(gh, capsys)
    assert (code, undeclared_lines(table) in out, gh.merge_calls) == (1, True, [])


@EACH_TABLE
def test_the_door_reads_a_base_written_before_the_keys_and_finds_no_loosening(table, capsys):
    """VERDICT-79: the pin move that writes `ends` is no loosening at the door, and no pull request file is fetched."""
    gh = FakeGh(base_config=GATE_CONFIG + table.entry(table.first, ends=None), merge_config=GATE_CONFIG + table.entry(table.first))
    code, out, _, _ = run(gh, capsys)
    assert (code, "  ✓ loosenings    none\n" in out) == (0, True)


@EACH_TABLE
def test_the_door_reads_a_file_git_moved_as_the_move_of_its_allow_entry(table, tmp_path, capsys):
    """VERDICT-81: the head entry at the path git moved a declared file to is inherited, so the door finds no loosening."""
    gh = pulled(
        tmp_path, None, None, [(table.first, table.second)],
        base_config=GATE_CONFIG + table.entry(table.first), merge_config=GATE_CONFIG + table.entry(table.second),
    )
    code, out, _, _ = run(gh, capsys)
    assert (code, "  ✓ loosenings    none\n" in out) == (0, True)


@pytest.mark.parametrize("entry", ["[[root.allow]]\nreason = 'r'\n", "[[layout.allow]]\npath = 'tests/a.py'\n"], ids=["root", "layout"])
def test_the_door_refuses_a_base_entry_without_its_identity_naming_the_base(entry, capsys):
    """VERDICT-79: the refusal sends the fix to the base branch."""
    gh = FakeGh(base_config=ONE_JOB + entry, merge_config=ONE_JOB)
    code, _, err, _ = run(gh, capsys)
    assert (code, "fix it on main first" in err, gh.merge_calls) == (2, True, [])
