"""A renamed test module that carries the registered mark at the head moves one adapter import count with it: the verdict
and the door read the mark and the registration from the head commit."""

import subprocess

import pytest

from quality.baseline_file import render_baseline
from tests.support.architecture import pytest_markers
from tests.support.baselines import adapter_table, ceiling
from tests.support.git_repositories import put
from tests.support.merge import GATE_CONFIG, drive
from tests.support.verdict import verdict_of
from tests.support.verdict_renames import commit_all, moved_in_head, pulled

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


def moved_unreadable(pull_request, base, head, unreadable):
    """The base commit holds `OLD` as a symlink or as bytes no UTF-8 text is; the head commit moves it, registers the mark."""
    repo, commit_base = pull_request
    put(repo / OLD, None)
    (repo / OLD).parent.mkdir(parents=True, exist_ok=True)
    if unreadable == "a symlink":
        put(repo / "tests/data.py", MODULE)
        (repo / OLD).symlink_to("../data.py")
    else:
        put(repo / OLD, MODULE.encode() + b"\xff\n")
    commit_base(base)
    subprocess.run(["git", "-C", str(repo), "mv", OLD, NEW], check=True)
    put(repo / "quality/baseline.toml", head)
    put(repo / "pyproject.toml", REGISTERED)
    commit_all(repo, "head moves the module")


@pytest.mark.parametrize("unreadable", ["a symlink", "bytes that are no UTF-8 text"])
@pytest.mark.parametrize(("base", "code", "line"), [
    pytest.param(render_baseline(ceiling({}, None)), 0, None, id="a base without the table judges as it did before the contract"),
    pytest.param(adapter_table({"tests/core": 1}), 1, "adapter import baseline adds tests 1", id="a base with the table shows the raise"),
])
def test_a_moved_module_the_verdict_cannot_read_is_never_refused_for_it(capsys, pull_request, unreadable, base, code, line):
    """ADAPTERIMPORT-27, ADAPTERIMPORT-29: the marks are read only where the base holds the table, and a module that
    cannot be read carries no count, so the pull request is judged and the raise shows."""
    moved_unreadable(pull_request, base, adapter_table({"tests": 1}), unreadable)
    actual, output = verdict_of(pull_request[0], capsys)
    assert (actual, line is None or line in output, "cannot read" in output) == (code, True, False)
