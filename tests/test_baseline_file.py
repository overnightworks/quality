"""The text of quality/baseline.toml: which versions hold a clones table, what an unknown table says and how the job
limit table reads and renders."""

import re

import pytest

from quality import __version__
from quality.baseline_file import (
    BASELINE_HEADER,
    Baseline,
    JobKey,
    SizeError,
    holds_clones_table,
    load_baseline,
    render_baseline,
)


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


def test_an_unknown_table_names_the_tool_version_and_every_known_table_with_clones_and_job_minutes():
    """TIMEOUT-13, CEILING-3: an unknown table is named with the tool's version and every table it knows, `clones` and
    `job_minutes` among them."""
    with pytest.raises(
        SizeError, match=rf"unknown table \[future\]; quality {re.escape(__version__)} knows ccn, .*, clones, job_minutes$"
    ):
        load_baseline("[future]\n")


CI = ".github/workflows/ci.yml"
CI_TABLE = f'[job_minutes."{CI}"]\n'


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        pytest.param("", None, id="no table"),
        pytest.param("[job_minutes]\n", {}, id="a held empty table"),
        pytest.param(f"{CI_TABLE}e2e = 40\n", {JobKey(CI, "e2e"): 40}, id="one entry"),
        pytest.param(
            f'{CI_TABLE}e2e = 40\n"my job" = 20\n[job_minutes."other.yml"]\nz = 16\n',
            {JobKey(CI, "e2e"): 40, JobKey(CI, "my job"): 20, JobKey("other.yml", "z"): 16},
            id="several entries and a quoted key",
        ),
        pytest.param(f'{CI_TABLE}"e2e" = 40\n', {JobKey(CI, "e2e"): 40}, id="a quoted bare key"),
    ],
)
def test_the_job_minutes_table_reads_to_one_entry_per_path_and_job(text, expected):
    """TIMEOUT-12."""
    assert load_baseline(text).job_minutes == expected


@pytest.mark.parametrize(
    ("text", "where", "value"),
    [
        pytest.param(f"{CI_TABLE}e2e = 40.5\n", f"job_minutes.{CI}.e2e", "40.5", id="a float"),
        pytest.param(f"{CI_TABLE}e2e = -5\n", f"job_minutes.{CI}.e2e", "-5", id="a negative"),
        pytest.param(f"{CI_TABLE}e2e = 0\n", f"job_minutes.{CI}.e2e", "0", id="zero"),
        pytest.param(f"{CI_TABLE}e2e = true\n", f"job_minutes.{CI}.e2e", "True", id="a boolean"),
        pytest.param(f'{CI_TABLE}e2e = "40"\n', f"job_minutes.{CI}.e2e", "'40'", id="a string"),
    ],
)
def test_a_job_limit_that_is_no_positive_whole_number_is_the_malformed_file_error(text, where, value):
    """TIMEOUT-12."""
    with pytest.raises(SizeError, match=re.escape(f"needs {where} as a positive count, not {value}")):
        load_baseline(text)


@pytest.mark.parametrize(
    ("text", "where"),
    [
        pytest.param('[job_minutes]\n"ci.yml" = 40\n', "job_minutes.ci.yml", id="a path that is no table"),
        pytest.param("job_minutes = 3\n", "job_minutes", id="the table itself that is no table"),
    ],
)
def test_a_job_table_that_is_no_table_is_the_malformed_file_error(text, where):
    """TIMEOUT-12."""
    with pytest.raises(SizeError, match=re.escape(f"needs {where} as a table")):
        load_baseline(text)


def test_the_job_minutes_table_renders_after_the_size_and_clone_tables_sorted_by_path_then_job():
    """TIMEOUT-13."""
    text = (
        '[job_minutes."z.yml"]\nb = 30\na = 20\n\n[job_minutes."a.yml"]\n"my job" = 17\n\n'
        '[clones."src/a.py"]\nf = [["src/b.py", "g"]]\n\n[ccn."src/a.py"]\nf = 18\n\n[file_lines]\n"src/a.py" = 820\n'
    )
    assert render_baseline(load_baseline(text)) == (
        f'{BASELINE_HEADER}\n[ccn."src/a.py"]\n"f" = 18\n\n[file_lines]\n"src/a.py" = 820\n\n'
        '[clones."src/a.py"]\nf = [["src/b.py", "g"]]\n\n[job_minutes."a.yml"]\n"my job" = 17\n\n'
        '[job_minutes."z.yml"]\na = 20\nb = 30\n'
    )


@pytest.mark.parametrize(
    ("text", "held"),
    [("", False), ("[job_minutes]\n", True), (f"{CI_TABLE}e2e = 40\n", True)],
    ids=["no table", "a held empty table", "one entry"],
)
def test_a_held_job_table_renders_and_an_absent_one_does_not_and_the_rendering_reads_back_byte_for_byte(text, held):
    """TIMEOUT-13: a held empty table renders as `[job_minutes]` alone, and a file that renders reads back to the same
    text."""
    rendered = render_baseline(load_baseline(text))
    assert ("job_minutes" in rendered, render_baseline(load_baseline(rendered))) == (held, rendered)


def test_a_job_name_that_is_not_valid_utf8_cannot_be_rendered():
    """TIMEOUT-13."""
    baseline = Baseline({}, None, {JobKey(CI, "\ud800"): 40})
    with pytest.raises(SizeError, match="is not valid UTF-8"):
        render_baseline(baseline)
