"""A renamed test module that carries the registered mark at the head moves one adapter import count with it: the verdict
and the door read the mark and the registration from the head commit."""

import pytest

from tests.support.architecture import pytest_markers
from tests.support.baselines import adapter_table
from tests.support.merge import GATE_CONFIG, drive
from tests.support.verdict import verdict_of
from tests.support.verdict_renames import moved_in_head, pulled

OLD, NEW = "tests/core/test_config.py", "tests/test_config.py"
MARK = "pytestmark = pytest.mark.imports_adapter\n"
MODULE = "".join(f"value_{number} = {number}\n" for number in range(30))
REGISTERED = pytest_markers("imports_adapter: reads a real service")
UNREGISTERED = pytest_markers("unit: fast")

def moved(counts: dict[str, int], pyproject: str) -> tuple[str, str, dict[str, str]]:
    """The base table, the head table and the head's registration, of a module that moves out of `tests/core`."""
    return adapter_table({"tests/core": 1}), adapter_table(counts), {"pyproject.toml": pyproject}


@pytest.mark.parametrize(("counts", "pyproject", "code", "line"), [
    pytest.param({"tests": 1}, REGISTERED, 0, None, id="the count moves with the module"),
    pytest.param({"tests": 2}, REGISTERED, 1, "adapter import baseline raises tests 1 -> 2", id="a raise beyond the carried count"),
    pytest.param({"tests": 1}, UNREGISTERED, 1, "adapter import baseline adds tests 1", id="the head does not register the mark"),
])
def test_the_verdict_carries_a_count_with_a_renamed_module_that_holds_the_registered_mark_at_the_head(capsys, pull_request, counts, pyproject, code, line):
    """BASELINE-12, ADAPTERIMPORT-29: the carried count is no loosening, a count above it is a raise, an add where the
    head does not register the mark."""
    base, head, files = moved(counts, pyproject)
    moved_in_head(pull_request, base, head, MODULE + MARK, old=OLD, new=NEW, base_files={OLD: MODULE}, head_files=files)
    actual, output = verdict_of(pull_request[0], capsys)
    assert (actual, line is None or line in output) == (code, True)
    assert (line is None) == ("adapter import baseline" not in output)


@pytest.mark.integration
@pytest.mark.parametrize(("counts", "pyproject", "code", "line"), [
    pytest.param({"tests": 1}, REGISTERED, 0, None, id="the count moves with the module"),
    pytest.param({"tests": 2}, REGISTERED, 1, "adapter import baseline raises tests 1 -> 2", id="a raise beyond the carried count"),
])
def test_the_door_reads_the_mark_from_the_merge_commit_as_the_verdict_does(capsys, tmp_path, counts, pyproject, code, line):
    """BASELINE-19, ADAPTERIMPORT-29: the door judges the move from the pull request's own two commits."""
    base, head, _ = moved(counts, pyproject)
    gh = pulled(
        tmp_path, base, head, [(OLD, NEW)], base_config=GATE_CONFIG, merge_config=GATE_CONFIG,
        base_files={OLD: MODULE}, merge_files={NEW: MODULE + MARK, "pyproject.toml": pyproject},
    )
    assert drive(gh)[0] == code
    assert (line is None or line in capsys.readouterr().out) is True
