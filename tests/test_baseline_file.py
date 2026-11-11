"""The text of quality/baseline.toml: which versions hold a clones table and what an unknown table says."""

import re

import pytest

from quality import __version__
from quality.baseline_file import SizeError, holds_clones_table, load_baseline


@pytest.mark.parametrize(
    ("version", "held"),
    [
        pytest.param(b"[clones]\n", True, id="[clones]"),
        pytest.param(b"[ clones ]\n", True, id="[ clones ]"),
        pytest.param(b"clones = {}\n", True, id="clones = {}"),
        pytest.param(b'["clones"]\n', True, id='["clones"]'),
        pytest.param(b'[function_lines]\nclones = {}\n', False, id="a function named clones"),
        pytest.param(b'[file_lines]\n"clones" = 120\n', False, id="a file named clones"),
    ],
)
def test_a_version_holds_clones_if_it_parses_so(version, held):
    """FIX-12: a version of the ceiling holds a clones table where it parses as one, in any spelling, and not where a
    function or file is merely named `clones`."""
    assert holds_clones_table(version) is held


@pytest.mark.parametrize("version", [b"[clones", b"\xff\xfe"], ids=["[clones", "not UTF-8"])
def test_a_version_no_parser_reads_shows_nothing(version):
    """FIX-13: a version of the ceiling in git that no parser reads raises the error that names it, instead of
    reading as a table held or absent."""
    with pytest.raises(SizeError, match="a version of quality/baseline.toml in git cannot be read"):
        holds_clones_table(version)


def test_an_unknown_table_names_the_tool_version_and_every_known_table_with_clones():
    """CEILING-3: an unknown table is named with the tool's version and every table it knows, `clones` among them."""
    with pytest.raises(SizeError, match=rf"unknown table \[future\]; quality {re.escape(__version__)} knows ccn, .*, clones$"):
        load_baseline("[future]\n")
