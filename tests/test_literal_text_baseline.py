"""The `[literal_text]` table of an earlier release in the ceiling file: it is read and ignored, and `--fix` rewrites it away."""

import pytest

from quality.baseline_file import BASELINE_PATH, load_baseline, render_baseline
from tests.support.baselines import rendered
from tests.support.door import CHECK_WORD, FIX_WORDS, door_in

COVERED = {
    "pyproject.toml": '[project]\nname = "app"\nversion = "0.1"\ndependencies = ["overnightworks-design[catalogs]>=1"]\n'
}
PAGE = "frontend/src/lib/A.svelte"
CLONES = "[clones]\n"
TABLES = {
    "an empty table": "[literal_text]\n",
    "a populated table": f'[literal_text]\n"{PAGE}" = 3\n',
    "a count of zero": f'[literal_text]\n"{PAGE}" = 0\n',
    "a count that is a string": f'[literal_text]\n"{PAGE}" = "3"\n',
    "a table that is none": "literal_text = 3\n",
}


@pytest.mark.parametrize("table", TABLES.values(), ids=TABLES.keys())
def test_a_literal_text_table_loads_as_no_table_whatever_it_holds(table):
    """LITERAL-16: the table is read and ignored, malformed or not, and the baseline renders without it."""
    assert (load_baseline(table), render_baseline(load_baseline(table))) == (load_baseline(""), render_baseline(load_baseline("")))


@pytest.mark.parametrize("table", TABLES.values(), ids=TABLES.keys())
def test_a_run_over_a_ceiling_file_holding_the_table_judges_as_without_it(build_repo, capsys, table):
    """LITERAL-16: the table is no exit 2 and no finding, and the debt verdict is the one of the file without it."""
    held = door_in(build_repo({**COVERED, BASELINE_PATH: table + CLONES}), CHECK_WORD, "debt")
    capsys.readouterr()
    plain = door_in(build_repo({**COVERED, BASELINE_PATH: CLONES}), CHECK_WORD, "debt")
    assert (held, plain) == (0, 0)


@pytest.mark.parametrize("table", TABLES.values(), ids=TABLES.keys())
def test_the_fix_rewrites_the_ceiling_file_without_the_table_and_a_second_fix_changes_nothing(build_repo, capsys, table):
    """LITERAL-16, FIX-16: `--fix` writes the file as it renders without the table; the same command again leaves it."""
    repo = build_repo({**COVERED, BASELINE_PATH: table + CLONES})
    assert door_in(repo, *FIX_WORDS) == 0
    first = (repo / BASELINE_PATH).read_text()
    out = capsys.readouterr().out
    assert door_in(repo, *FIX_WORDS) == 0
    assert (first, "rewritten" in out, (repo / BASELINE_PATH).read_text()) == (rendered(CLONES), True, first)
