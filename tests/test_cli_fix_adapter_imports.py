"""`quality check debt --fix` lowers and drops the held `[adapter_imports]` counts, never raises or adds one, records the
table where none exists and git never held one, and follows a renamed marked module; `--dry-run` plans the same rows."""

from pathlib import Path

import pytest

from quality.baseline_file import BASELINE_PATH, load_baseline
from quality.check import Fix
from quality.rules import Area, Rule
from tests.support.architecture import adapter_contract, adapter_split, shop_with_adapters
from tests.support.baselines import CONFLICT_MARKERS, adapter_table, rendered
from tests.support.check import check_result
from tests.support.door import (
    DRY_RUN_WORDS,
    FIX_WORDS,
    assert_refused_with_restore_step,
    both,
    digest,
    fixed,
    squashed,
    tree_of,
)
from tests.support.git_repositories import git, put

REGISTERED = ("imports_adapter: reads a real service",)
MARKED = "import pytest\n\npytestmark = pytest.mark.imports_adapter\nimport shop.adapters.db\n"
UNUSED = "import pytest\n\npytestmark = pytest.mark.imports_adapter\nimport shop.core.config\n"
HELD = "[file_lines]\n\n[clones]\n"
CORE_TEST, STORES_TEST, DEEP_TEST = "tests/core/test_config.py", "tests/core/test_config_stores.py", "tests/core/deep/test_nothing_known.py"


def shop(tests: dict[str, str], held: str = HELD, **options) -> dict[str, str]:
    """A `shop` repository holding `tests` and a ceiling of `held`; its `pyproject.toml` registers the mark."""
    return {**shop_with_adapters(tests, markers=REGISTERED, **options), BASELINE_PATH: rendered(held)}


def written(repo: Path) -> dict[str, int] | None:
    return load_baseline((repo / BASELINE_PATH).read_text()).adapter_imports


def test_the_fix_lowers_and_drops_held_counts_and_adds_no_directory(build_repo, capsys):
    """ADAPTERIMPORT-28: a held count falls to its measured one, goes where it measures 0, and a directory the table
    does not hold is no entry."""
    held = HELD + adapter_table({"tests/core": 3, "tests/old": 1})
    repo = build_repo(shop({CORE_TEST: MARKED, STORES_TEST: MARKED, DEEP_TEST: MARKED}, held))
    planned, done = both(capsys, repo)
    assert (planned[0], done[0], written(repo)) == (1, 0, {"tests/core": 2})
    assert "ceiling lowered by 2 entries" in done[1]
    assert "2 ceiling entries would fall" in planned[1]


def test_the_fix_never_raises_a_held_count(build_repo, capsys):
    """ADAPTERIMPORT-28: a measured count above the held one leaves the file as it is."""
    repo = build_repo(shop({CORE_TEST: MARKED, STORES_TEST: MARKED}, HELD + adapter_table({"tests/core": 1})))
    before = digest(repo)
    code, out, _ = fixed(capsys, repo, *FIX_WORDS)
    assert (code, digest(repo), "nothing to lower" in out) == (0, before, True)


def test_the_fix_records_the_measured_counts_where_the_file_has_no_table_and_git_never_held_one(build_repo, capsys):
    """ADAPTERIMPORT-28: the row counts the exceptions, the dry run plans it and a second fix changes nothing."""
    repo = build_repo(shop({CORE_TEST: MARKED, STORES_TEST: MARKED, DEEP_TEST: MARKED}))
    planned, done = both(capsys, repo)
    assert (planned[0], done[0], written(repo)) == (1, 0, {"tests/core": 2, "tests/core/deep": 1})
    assert "✓ debt recorded 3 adapter import exceptions · quality/baseline.toml" in done[1]
    assert "~ debt to record 3 adapter import exceptions · quality/baseline.toml" in planned[1]
    first = (repo / BASELINE_PATH).read_bytes()
    code, out, _ = fixed(capsys, repo, *FIX_WORDS)
    assert (code, (repo / BASELINE_PATH).read_bytes(), "recorded" in out) == (0, first, False)


def test_a_first_fix_with_no_ceiling_file_counts_the_adapter_exceptions_in_its_row(build_repo, capsys):
    repo = build_repo({key: value for key, value in shop({CORE_TEST: MARKED}).items() if key != BASELINE_PATH})
    planned, done = both(capsys, repo)
    assert (planned[0], done[0], written(repo)) == (1, 0, {"tests/core": 1})
    assert "✓ debt recorded 0 entries, 0 clone pairs, 1 adapter import exception · quality/baseline.toml is new" in done[1]
    assert "~ debt to record 0 entries, 0 clone pairs, 1 adapter import exception · quality/baseline.toml would be new" in planned[1]


@pytest.mark.parametrize("tests", [{CORE_TEST: UNUSED}, {}], ids=["every mark unused", "no mark"])
def test_the_fix_records_no_table_while_no_mark_exempts_anything(build_repo, capsys, tests):
    """ADAPTERIMPORT-28: a count of 0 records nothing, not even an empty table."""
    repo = build_repo(shop(tests))
    before = digest(repo)
    planned, done = both(capsys, repo)
    assert (planned[0], done[0], digest(repo)) == (0, 0, before)


def test_the_fix_leaves_the_table_alone_where_no_contract_carries_the_id(build_repo, capsys):
    """ADAPTERIMPORT-27."""
    repo = build_repo(shop({CORE_TEST: MARKED}, HELD + adapter_table({"tests/core": 5, "tests/old": 1}), contract=adapter_split()))
    before = digest(repo)
    code, out, _ = fixed(capsys, repo, *FIX_WORDS)
    assert (code, digest(repo), "nothing to lower" in out) == (0, before, True)


def lost_by_hand(build_repo) -> Path:
    repo = build_repo(shop({CORE_TEST: MARKED}, HELD + adapter_table({"tests/core": 1})))
    put(repo / BASELINE_PATH, rendered(HELD))
    return repo


@pytest.mark.parametrize("words", [FIX_WORDS, DRY_RUN_WORDS], ids=["the fix", "the dry run"])
def test_a_table_git_holds_is_not_recorded_again(build_repo, capsys, words):
    """ADAPTERIMPORT-28, FIX-12: a ceiling that lost the table although git held one refuses the whole fix with the
    restore step, exit 2, and writes nothing."""
    repo = lost_by_hand(build_repo)
    assert_refused_with_restore_step(capsys, repo, words, "quality/baseline.toml lost its [adapter_imports] table, which git holds")


def unreadable_in_the_past(build_repo, content: bytes) -> Path:
    """A repository whose last commit holds a readable baseline without the table, beneath one no parser reads."""
    repo = build_repo(shop({CORE_TEST: MARKED}))
    for text in (content, rendered(HELD)):
        put(repo / BASELINE_PATH, text)
        git(repo, "add", "-f", BASELINE_PATH)
        git(repo, "commit", "-q", "-m", "write the ceiling")
    return repo


def test_an_unreadable_version_that_never_names_the_table_lets_the_fix_record_it(build_repo, capsys):
    """FIX-46: a version no parser reads, with conflict markers and no `adapter_imports`, holds no table."""
    repo = unreadable_in_the_past(build_repo, CONFLICT_MARKERS)
    planned, done = both(capsys, repo)
    assert (planned[0], done[0], written(repo)) == (1, 0, {"tests/core": 1}), (planned, done)


@pytest.mark.parametrize("words", [FIX_WORDS, DRY_RUN_WORDS], ids=["the fix", "the dry run"])
def test_an_unreadable_version_that_names_the_table_refuses_the_fix(build_repo, capsys, words):
    """FIX-13: a version no parser reads whose bytes name `adapter_imports` refuses with the table's name and exit 2."""
    repo = unreadable_in_the_past(build_repo, CONFLICT_MARKERS + b"\xff[adapter_imports]\n")
    assert_refused_with_restore_step(capsys, repo, words, "a version of quality/baseline.toml in git cannot be read and names adapter_imports")


@pytest.mark.parametrize("words", [FIX_WORDS, DRY_RUN_WORDS], ids=["the fix", "the dry run"])
def test_a_contract_read_that_cannot_judge_refuses_the_fix_while_the_table_is_held(build_repo, capsys, words):
    """ADAPTERIMPORT-28: the held counts cannot be lowered against a read that cannot be judged, so nothing is written."""
    held = HELD + adapter_table({"tests/core": 2})
    repo = build_repo(shop({CORE_TEST: MARKED}, held, contract=adapter_contract(type="custom")))
    tree = tree_of(repo)
    code, _, _ = fixed(capsys, repo, *words)
    assert (code, tree_of(repo)) == (2, tree)


def test_a_dry_run_judges_the_planned_table(build_repo):
    """ADAPTERIMPORT-26: under `--fix --dry-run` the adapter rule judges the table the fix would write, so a count the fix
    lowers is no `can fall`."""
    repo = build_repo(shop({CORE_TEST: MARKED}, HELD + adapter_table({"tests/core": 3})))
    areas = [Area.DEBT, Area.IMPORTS]

    def adapter_events(fix):
        verdict = next(verdict for verdict in check_result(repo, named=areas, fix=fix).verdicts if verdict.rule is Rule.ADAPTER_IMPORT)
        return [finding.event.value for finding in verdict.findings]

    assert (adapter_events(None), adapter_events(Fix.DRY_RUN)) == (["can fall"], [])


def test_a_directory_moved_whole_carries_its_count_in_the_fix(pull_request, capsys):
    """FIX-34, ADAPTERIMPORT-28: a marked module the index renames out of a directory takes one count with it, as `moved`."""
    repo, commit_base = pull_request
    for name, text in shop({CORE_TEST: MARKED}, HELD + adapter_table({"tests/core": 1})).items():
        put(repo / name, text)
    commit_base(rendered(HELD + adapter_table({"tests/core": 1})))
    git(repo, "mv", CORE_TEST, "tests/test_config.py")
    code, out, _ = fixed(capsys, repo, *FIX_WORDS)
    assert (code, written(repo), "debt moved 1 entry · quality/baseline.toml" in squashed(out)) == (0, {"tests": 1}, True), out
