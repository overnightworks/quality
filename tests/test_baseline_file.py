"""The text of quality/baseline.toml: which versions hold a clones table, what an unknown table says and how the job
limit table reads and renders."""

import re

import pytest

from quality import __version__
from quality.baseline_file import (
    BASELINE_HEADER,
    JobKey,
    SizeError,
    holds_table,
    load_baseline,
    render_baseline,
)
from tests.support.baselines import CONFLICT_MARKERS, ceiling

SPELLINGS = [
    pytest.param("[{name}]\n", True, id="a header"),
    pytest.param("[ {name} ]\n", True, id="a header with spaces"),
    pytest.param("{name} = {{}}\n", True, id="an inline table"),
    pytest.param('["{name}"]\n', True, id="a quoted header"),
    pytest.param('["{name}"."a.yml"]\n', True, id="a dotted key"),
    pytest.param("[function_lines]\n{name} = 5\n", False, id="a function named so"),
    pytest.param('[file_lines]\n"{name}" = 120\n', False, id="a file named so"),
    pytest.param("# {name}\n[file_lines]\n", False, id="a comment naming it"),
]


@pytest.mark.parametrize("name", ["clones", "job_minutes", "spec_citations", "adapter_imports"])
@pytest.mark.parametrize(("spelling", "held"), SPELLINGS)
def test_a_version_holds_a_table_if_it_parses_so(name, spelling, held):
    """FIX-12, TIMEOUT-28: a version of the ceiling holds a table where it parses as one, in any spelling, and not
    where a function or file is merely named so or a comment mentions it."""
    assert holds_table(spelling.format(name=name).encode(), name) is held


@pytest.mark.parametrize(
    ("held", "asked"),
    [("clones", "job_minutes"), ("job_minutes", "clones"), ("clones", "spec_citations"), ("spec_citations", "clones"), ("clones", "adapter_imports"), ("adapter_imports", "clones")],
)
def test_a_version_holding_one_table_does_not_hold_the_other(held, asked):
    """TIMEOUT-28: the table asked about is the only one that counts."""
    assert holds_table(f"[{held}]\n".encode(), asked) is False


UNREAD_NAMING = [
    pytest.param("[{name}", id="not TOML"),
    pytest.param("[{name}]\n[file_lines\n", id="a header and a broken line"),
    pytest.param("\xff[{name}]", id="not UTF-8"),
]
UNREAD_NOT_NAMING = [
    pytest.param(CONFLICT_MARKERS, id="conflict markers"),
    pytest.param(b"\xff\xfe", id="not UTF-8"),
]


@pytest.mark.parametrize("name", ["clones", "job_minutes", "spec_citations", "adapter_imports"])
@pytest.mark.parametrize("text", UNREAD_NAMING)
def test_a_version_no_parser_reads_that_names_the_table_raises(text, name):
    """FIX-13, TIMEOUT-28: a version of the ceiling in git that no parser reads and whose bytes name the table raises the
    error that names the table, instead of reading as held or absent."""
    version = text.format(name=name).encode("latin-1")
    with pytest.raises(SizeError, match=f"a version of quality/baseline.toml in git cannot be read and names {name}"):
        holds_table(version, name)


@pytest.mark.parametrize("name", ["clones", "job_minutes", "spec_citations", "adapter_imports"])
@pytest.mark.parametrize("version", UNREAD_NOT_NAMING)
def test_a_version_no_parser_reads_that_never_names_the_table_holds_nothing(version, name):
    """FIX-46: a version no TOML parser reads whose bytes never name the table does not hold it."""
    assert holds_table(version, name) is False


def test_an_unknown_table_names_the_tool_version_and_every_known_table_with_the_held_lists():
    """TIMEOUT-13, CEILING-3, CITE-13, ADAPTERIMPORT-30: an unknown table is named with the tool's version and every table it knows,
    `clones`, `job_minutes`, `spec_citations` and `adapter_imports` among them."""
    with pytest.raises(
        SizeError, match=rf"unknown table \[future\]; quality {re.escape(__version__)} knows ccn, .*, clones, job_minutes, spec_citations, adapter_imports$"
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
        pytest.param(f"{CI_TABLE}e2e = 40.5\n", f'job_minutes."{CI}".e2e', "40.5", id="a float"),
        pytest.param(f"{CI_TABLE}e2e = -5\n", f'job_minutes."{CI}".e2e', "-5", id="a negative"),
        pytest.param(f"{CI_TABLE}e2e = 0\n", f'job_minutes."{CI}".e2e', "0", id="zero"),
        pytest.param(f"{CI_TABLE}e2e = true\n", f'job_minutes."{CI}".e2e', "True", id="a boolean"),
        pytest.param(f'{CI_TABLE}e2e = "40"\n', f'job_minutes."{CI}".e2e', "'40'", id="a string"),
        pytest.param(f'{CI_TABLE}"my job" = 0\n', f'job_minutes."{CI}"."my job"', "0", id="a job id that needs quotes"),
    ],
)
def test_a_job_limit_that_is_no_positive_whole_number_is_the_malformed_file_error(text, where, value):
    """TIMEOUT-12, TIMEOUT-33: the table, the path and the job are joined as a TOML dotted key, the path and a job id
    that is no bare key quoted."""
    with pytest.raises(SizeError, match=re.escape(f"needs {where} as a positive count, not {value}")):
        load_baseline(text)


@pytest.mark.parametrize(
    ("text", "where"),
    [
        pytest.param('[job_minutes]\n"ci.yml" = 40\n', 'job_minutes."ci.yml"', id="a path that is no table"),
        pytest.param("job_minutes = 3\n", "job_minutes", id="the table itself that is no table"),
    ],
)
def test_a_job_table_that_is_no_table_is_the_malformed_file_error(text, where):
    """TIMEOUT-12, TIMEOUT-33."""
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
    baseline = ceiling({}, None, {JobKey(CI, "\ud800"): 40})
    with pytest.raises(SizeError, match="is not valid UTF-8"):
        render_baseline(baseline)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        pytest.param("", None, id="no table"),
        pytest.param("[spec_citations]\n", frozenset(), id="a held empty table"),
        pytest.param("[spec_citations]\nuncited = []\n", frozenset(), id="an empty list"),
        pytest.param('[spec_citations]\nuncited = ["ST-01", "ST-02"]\n', frozenset({"ST-01", "ST-02"}), id="two IDs"),
        pytest.param('[spec_citations]\nuncited = ["ST-01", "ST-01"]\n', frozenset({"ST-01"}), id="a repeated ID"),
    ],
)
def test_the_spec_citations_table_reads_to_the_set_of_held_ids(text, expected):
    """CITE-7: the uncited IDs are the table's one key, a set."""
    assert load_baseline(text).spec_citations == expected


@pytest.mark.parametrize(
    ("text", "needs"),
    [
        pytest.param("spec_citations = 3\n", "spec_citations as a table", id="the table itself that is no table"),
        pytest.param('[spec_citations]\nuncited = "ST-01"\n', "spec_citations.uncited as a list of strings", id="a string"),
        pytest.param("[spec_citations]\nuncited = [1]\n", "spec_citations.uncited as a list of strings", id="a number in the list"),
        pytest.param("[spec_citations]\nother = []\n", "spec_citations to hold only uncited", id="another key"),
    ],
)
def test_a_spec_citations_table_that_is_not_a_list_of_ids_is_the_malformed_file_error(text, needs):
    """CITE-7."""
    with pytest.raises(SizeError, match=re.escape(f"needs {needs}")):
        load_baseline(text)


def test_the_spec_citations_table_renders_last_with_the_ids_sorted_one_per_line():
    """CITE-7: the table follows the job tables, and its IDs stand sorted, one per line."""
    text = '[spec_citations]\nuncited = ["ST-10", "AB-2", "ST-01"]\n\n[job_minutes."a.yml"]\nz = 16\n\n[file_lines]\n"src/a.py" = 820\n'
    assert render_baseline(load_baseline(text)) == (
        f'{BASELINE_HEADER}\n[file_lines]\n"src/a.py" = 820\n\n[job_minutes."a.yml"]\nz = 16\n\n'
        '[spec_citations]\nuncited = [\n    "AB-2",\n    "ST-01",\n    "ST-10",\n]\n'
    )


@pytest.mark.parametrize(
    ("text", "rendered_table"),
    [("", ""), ("[spec_citations]\n", "[spec_citations]\n"), ('[spec_citations]\nuncited = ["X-1"]\n', '[spec_citations]\nuncited = [\n    "X-1",\n]\n')],
    ids=["no table", "a held empty table", "one ID"],
)
def test_a_held_spec_table_renders_and_an_absent_one_does_not_and_the_rendering_reads_back_byte_for_byte(text, rendered_table):
    """CITE-7: an empty held table renders as its header alone, and a file that renders reads back to the same text."""
    rendered = render_baseline(load_baseline(text))
    assert (rendered.removeprefix(BASELINE_HEADER).strip(), render_baseline(load_baseline(rendered))) == (rendered_table.strip(), rendered)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        pytest.param("", None, id="no table"),
        pytest.param("[adapter_imports]\n", {}, id="a held empty table"),
        pytest.param('[adapter_imports]\n"tests/api" = 49\n"tests/app" = 1\n', {"tests/api": 49, "tests/app": 1}, id="two directories"),
    ],
)
def test_the_adapter_imports_table_reads_to_the_count_of_each_directory(text, expected):
    """ADAPTERIMPORT-26: the keys are test directories and the values are positive counts."""
    assert load_baseline(text).adapter_imports == expected


@pytest.mark.parametrize(
    ("text", "needs"),
    [
        pytest.param("adapter_imports = 3\n", "adapter_imports as a table", id="the table itself that is no table"),
        pytest.param('[adapter_imports]\n"tests/api" = 0\n', "adapter_imports.tests/api as a positive count, not 0", id="zero"),
        pytest.param('[adapter_imports]\n"tests/api" = "2"\n', "adapter_imports.tests/api as a positive count", id="a string"),
        pytest.param('[adapter_imports."tests/api"]\nx = 1\n', "adapter_imports.tests/api as a positive count", id="a table per directory"),
    ],
)
def test_an_adapter_imports_table_that_is_not_a_map_of_counts_is_the_malformed_file_error(text, needs):
    with pytest.raises(SizeError, match=re.escape(f"needs {needs}")):
        load_baseline(text)


def test_the_adapter_imports_table_renders_after_the_spec_citations_with_the_directories_sorted():
    text = '[adapter_imports]\n"tests/b" = 2\n"tests/a" = 1\n\n[spec_citations]\n\n[file_lines]\n"src/a.py" = 820\n'
    assert render_baseline(load_baseline(text)) == (
        f'{BASELINE_HEADER}\n[file_lines]\n"src/a.py" = 820\n\n[spec_citations]\n\n[adapter_imports]\n"tests/a" = 1\n"tests/b" = 2\n'
    )


@pytest.mark.parametrize(
    ("text", "rendered_table"),
    [("", ""), ("[adapter_imports]\n", "[adapter_imports]\n"), ('[adapter_imports]\n"tests/a" = 1\n', '[adapter_imports]\n"tests/a" = 1\n')],
    ids=["no table", "a held empty table", "one directory"],
)
def test_a_held_adapter_table_renders_and_an_absent_one_does_not_and_the_rendering_reads_back_byte_for_byte(text, rendered_table):
    rendered = render_baseline(load_baseline(text))
    assert (rendered.removeprefix(BASELINE_HEADER).strip(), render_baseline(load_baseline(rendered))) == (rendered_table.strip(), rendered)
