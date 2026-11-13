"""The `[literal_text]` table of the ceiling file: how `quality check` judges it and how `--fix` records and lowers it."""

from pathlib import Path

import pytest

from quality.baseline_file import BASELINE_PATH, PageKey, load_baseline
from tests.support.baselines import rendered
from tests.support.door import CHECK_WORD, DRY_RUN_WORDS, FIX_WORDS, fixed, tree_of
from tests.support.git_repositories import git, put

COVERED = {
    "pyproject.toml": '[project]\nname = "app"\nversion = "0.1"\ndependencies = ["overnightworks-design[catalogs]>=1"]\n'
}
UNCOVERED = {"pyproject.toml": '[project]\nname = "app"\nversion = "0.1"\ndependencies = ["overnightworks-design>=1"]\n'}
PAGE_A = "frontend/src/lib/A.svelte"
PAGE_B = "frontend/src/lib/B.svelte"
PAGE_C = "frontend/src/lib/C.svelte"
DEBT = (CHECK_WORD, "debt")


def page(texts: int) -> str:
    """A page with `texts` literal texts."""
    return "".join(f"<p>Text {'x' * number}</p>\n" for number in range(texts))


def literal_table(**counts: int) -> str:
    """A `[literal_text]` table, the page paths given as `A`, `B`, `C`."""
    names = {"A": PAGE_A, "B": PAGE_B, "C": PAGE_C}
    return "[literal_text]\n" + "".join(f'"{names[name]}" = {count}\n' for name, count in counts.items())


def ceiling(table: str = "", clones: bool = True) -> str:
    return rendered(f"{'[clones]' if clones else ''}\n\n{table}")


def squashed(text: str) -> str:
    """Each line with its padding between columns collapsed to one space."""
    return "\n".join(" ".join(line.split()) for line in text.splitlines())


def table_of(repo: Path) -> dict[str, int] | None:
    held = load_baseline((repo / BASELINE_PATH).read_text()).literal_texts
    return None if held is None else {key.path: count for key, count in held.items()}


def judged(capsys, repo: Path, *words: str) -> tuple[int, str]:
    code, out, _ = fixed(capsys, repo, *(words or DEBT))
    return code, squashed(out)


@pytest.mark.parametrize(
    ("pages", "table", "rows"),
    [
        pytest.param({PAGE_A: page(5)}, literal_table(A=3), ["! frontend/src/lib/A.svelte grew 5 literal texts · ceiling 3"], id="grew"),
        pytest.param(
            {PAGE_A: page(3)}, "[literal_text]\n",
            ["! frontend/src/lib/A.svelte new debt 3 literal texts · ceiling none"], id="new debt",
        ),
        pytest.param(
            {PAGE_A: page(1)}, literal_table(A=4), ["! frontend/src/lib/A.svelte can fall 1 literal text · ceiling 4"], id="can fall",
        ),
        pytest.param({}, literal_table(C=2), ["! frontend/src/lib/C.svelte can fall file is gone · ceiling 2"], id="gone page"),
    ],
)
def test_a_covered_repository_with_the_table_judges_each_page_against_its_count(build_repo, capsys, pages, table, rows):
    """LITERAL-6: a rising count or a page with text and no entry is red, a fallen count or a gone page can fall."""
    code, out = judged(capsys, build_repo({**COVERED, **pages, BASELINE_PATH: ceiling(table)}))
    assert (code, [row for row in out.splitlines() if row.startswith("! frontend")]) == (1, rows)


@pytest.mark.parametrize(
    ("page_fix", "table"),
    [
        ("fix: bring it back to 3 literal texts or fewer", literal_table(A=3)),
        ("fix: read each text through the generated module and keep it in the catalog", "[literal_text]\n"),
    ],
    ids=["grew", "new debt"],
)
def test_a_page_above_its_count_or_new_with_text_names_a_fix_that_is_not_the_fix_command(build_repo, capsys, page_fix, table):
    """LITERAL-6: the fix of a new debt is no `quality check --fix`, because the fix adds no entry to a held table."""
    _, out = judged(capsys, build_repo({**COVERED, PAGE_A: page(5), BASELINE_PATH: ceiling(table)}))
    assert page_fix in out


def test_the_clean_row_names_the_pages_the_table_holds(build_repo, capsys):
    """LITERAL-10: a held table reads enforced, with the texts and pages it holds."""
    repo = build_repo({**COVERED, PAGE_A: page(3), PAGE_B: page(1), BASELINE_PATH: ceiling(literal_table(A=3, B=1))})
    code, out = judged(capsys, repo)
    assert (code, "ceiling held · 0 entries, 0 clone pairs, 4 literal texts on 2 pages" in out) == (0, True)


def test_a_table_without_an_entry_stands_for_a_repository_that_allows_no_text(build_repo, capsys):
    """LITERAL-5: an empty table is enforced, and its clean row reads zero."""
    code, out = judged(capsys, build_repo({**COVERED, BASELINE_PATH: ceiling("[literal_text]\n")}))
    assert (code, "ceiling held · 0 entries, 0 clone pairs, 0 literal texts on 0 pages" in out) == (0, True)


def test_a_swap_of_one_text_for_another_in_a_page_that_keeps_its_count_is_not_seen(build_repo, capsys):
    """LITERAL-6: the named gap, green."""
    repo = build_repo({**COVERED, PAGE_A: "<p>One</p>\n", BASELINE_PATH: ceiling(literal_table(A=1))})
    put(repo / PAGE_A, "<p>Another</p>\n")
    assert judged(capsys, repo)[0] == 0


def test_a_tracked_page_the_disk_lacks_keeps_its_entry_unjudged_and_counts_in_the_clean_row(build_repo, capsys):
    """LITERAL-6: only a page the index no longer tracks is gone."""
    repo = build_repo({**COVERED, PAGE_A: page(2), BASELINE_PATH: ceiling(literal_table(A=2))})
    (repo / PAGE_A).unlink()
    code, out = judged(capsys, repo)
    assert (code, "2 literal texts on 1 page" in out) == (0, True)
    assert fixed(capsys, repo, *FIX_WORDS)[0] == 0
    assert table_of(repo) == {PAGE_A: 2}


@pytest.mark.parametrize("entry", ['"a.svelte" = 0', '"a.svelte" = "3"'], ids=["a zero", "a string"])
def test_an_unreadable_literal_table_refuses_the_run_naming_the_file(build_repo, capsys, entry):
    """LITERAL-5: a count that is no positive integer is exit 2 naming the ceiling file."""
    code, out = judged(capsys, build_repo({**COVERED, BASELINE_PATH: f"[literal_text]\n{entry}\n"}))
    assert (code, "quality/baseline.toml needs literal_text." in out) == (2, True)


def test_a_repository_without_the_table_measures_only_and_names_the_fix_that_records(build_repo, capsys):
    """LITERAL-10: the row keeps its words and `--fix` is now named because it records."""
    code, out = judged(capsys, build_repo({**COVERED, PAGE_A: page(3), BASELINE_PATH: ceiling()}))
    assert code == 0
    assert "3 literal texts in 1 file · partly measured only" in out
    assert "fix: quality check --fix" in out


def test_a_covered_repository_with_the_literal_table_but_no_clone_table_names_only_the_clone_part_as_unrecorded(build_repo, capsys):
    """LITERAL-10: the partly row names only the parts still unrecorded."""
    repo = build_repo({**COVERED, PAGE_A: page(3), BASELINE_PATH: ceiling("[literal_text]\n" + f'"{PAGE_A}" = 3\n', clones=False)})
    _, out = judged(capsys, repo)
    assert ("0 clone pairs · partly measured only" in out, "literal texts in" in out) == (True, False)


class TestFixRecordsTheFirstTable:
    def test_into_an_existing_ceiling_file_it_writes_the_measured_counts_sorted_and_notes_them(self, build_repo, capsys):
        """LITERAL-7, LITERAL-5: the first recording writes each page with text, sorted, and reads back enforced."""
        repo = build_repo({**COVERED, PAGE_B: page(1), PAGE_A: page(3), PAGE_C: page(0), BASELINE_PATH: ceiling()})
        code, out = judged(capsys, repo, *FIX_WORDS)
        assert (code, "✓ debt recorded 4 literal texts on 2 pages · quality/baseline.toml" in out) == (0, True)
        assert table_of(repo) == {PAGE_A: 3, PAGE_B: 1}
        assert list(load_baseline((repo / BASELINE_PATH).read_text()).literal_texts) == [PageKey(PAGE_A), PageKey(PAGE_B)]

    def test_it_writes_an_empty_table_where_no_page_holds_a_text(self, build_repo, capsys):
        """LITERAL-5, LITERAL-7: the table's presence enforces, so an empty table is written."""
        repo = build_repo({**COVERED, BASELINE_PATH: ceiling()})
        code, out = judged(capsys, repo, *FIX_WORDS)
        assert (code, "✓ debt recorded 0 literal texts on 0 pages · quality/baseline.toml" in out) == (0, True)
        assert table_of(repo) == {}

    def test_a_whole_file_first_names_the_literal_part_last(self, build_repo, capsys):
        """LITERAL-7: the first recording of the whole file counts the table in its row."""
        repo = build_repo({**COVERED, PAGE_A: page(3)})
        code, out = judged(capsys, repo, *FIX_WORDS)
        assert (code, "✓ debt recorded 0 entries, 0 clone pairs, 3 literal texts on 1 page · quality/baseline.toml is new" in out) == (0, True)
        assert table_of(repo) == {PAGE_A: 3}

    def test_a_table_git_once_held_is_not_recorded_again(self, build_repo, capsys):
        """LITERAL-7: a table git holds and the file lost refuses the fix with the restore step."""
        repo = build_repo({**COVERED, PAGE_A: page(3), BASELINE_PATH: ceiling(literal_table(A=3))})
        put(repo / BASELINE_PATH, ceiling())
        before = tree_of(repo)
        code, out = judged(capsys, repo, *FIX_WORDS)
        assert (code, "✗ debt not fixed quality/baseline.toml lost its [literal_text] table, which git holds" in out) == (2, True)
        assert tree_of(repo) == before

    def test_a_repository_that_is_not_covered_gets_no_table_and_no_row_part(self, build_repo, capsys):
        """LITERAL-7: an uncovered repository sees nothing."""
        repo = build_repo({**UNCOVERED, PAGE_A: page(3), BASELINE_PATH: ceiling()})
        code, out = judged(capsys, repo, *FIX_WORDS)
        assert (code, "literal" in out, "literal" in (repo / BASELINE_PATH).read_text()) == (0, False, False)


class TestFixLowersAndNeverRaises:
    def test_a_fallen_count_is_lowered_then_the_run_is_green(self, build_repo, capsys):
        """LITERAL-7, LITERAL-6: `can fall` is red, `--fix` prints `lowered 4 → 1 literal texts`, then it is green."""
        repo = build_repo({**COVERED, PAGE_A: page(1), BASELINE_PATH: ceiling(literal_table(A=4))})
        assert judged(capsys, repo)[0] == 1
        code, out = judged(capsys, repo, *FIX_WORDS)
        assert (code, "frontend/src/lib/A.svelte lowered 4 → 1 literal texts" in out) == (0, True)
        assert table_of(repo) == {PAGE_A: 1}
        assert judged(capsys, repo)[0] == 0

    def test_a_deleted_page_is_dropped(self, build_repo, capsys):
        """LITERAL-7, LITERAL-6: a page the index no longer tracks can fall and `--fix` drops its entry."""
        repo = build_repo({**COVERED, PAGE_A: page(2), PAGE_B: page(1), BASELINE_PATH: ceiling(literal_table(A=2, B=1))})
        git(repo, "rm", "-q", PAGE_A)
        assert "can fall file is gone · ceiling 2" in judged(capsys, repo)[1]
        code, out = judged(capsys, repo, *FIX_WORDS)
        assert (code, "frontend/src/lib/A.svelte dropped file is gone · was 2 literal texts" in out) == (0, True)
        assert table_of(repo) == {PAGE_B: 1}

    def test_a_page_that_measures_no_text_is_dropped(self, build_repo, capsys):
        """LITERAL-7: a stored entry is never zero, so the entry goes."""
        repo = build_repo({**COVERED, PAGE_A: page(2), BASELINE_PATH: ceiling(literal_table(A=2))})
        put(repo / PAGE_A, "<p>{words.none}</p>\n")
        code, out = judged(capsys, repo, *FIX_WORDS)
        assert (code, "dropped 2 → 0 literal texts" in out, table_of(repo)) == (0, True, {})

    @pytest.mark.parametrize(
        ("pages", "table", "kept"),
        [
            pytest.param({PAGE_A: page(5)}, literal_table(A=3), {PAGE_A: 3}, id="a raised count"),
            pytest.param({PAGE_A: page(3)}, "[literal_text]\n", {}, id="a new page"),
        ],
    )
    def test_a_grown_or_new_page_stays_red_and_the_fix_changes_nothing(self, build_repo, capsys, pages, table, kept):
        """LITERAL-7: `--fix` never raises an entry and never adds one."""
        repo = build_repo({**COVERED, **pages, BASELINE_PATH: ceiling(table)})
        code, _ = judged(capsys, repo, *FIX_WORDS)
        assert (code, table_of(repo)) == (1, kept)

    def test_a_page_the_scan_cannot_read_refuses_the_fix_and_writes_nothing(self, build_repo, capsys):
        """LITERAL-7, LITERAL-9: exit 2 naming the page."""
        repo = build_repo({**COVERED, PAGE_A: "<p>{ never</p>\n", BASELINE_PATH: ceiling(literal_table(A=2))})
        before = tree_of(repo)
        code, out = judged(capsys, repo, *FIX_WORDS)
        assert (code, "frontend/src/lib/A.svelte has a { that is never closed" in out, tree_of(repo)) == (2, True, before)

    def test_a_clone_recording_leaves_the_literal_table_byte_for_byte(self, build_repo, capsys):
        """LITERAL-5: tables are judged apart, so lowering one table rebuilds no other."""
        table = literal_table(A=3)
        repo = build_repo({**COVERED, PAGE_A: page(3), BASELINE_PATH: ceiling(table, clones=False)})
        assert judged(capsys, repo, *FIX_WORDS)[0] == 0
        assert (repo / BASELINE_PATH).read_text() == ceiling(table, clones=True)


class TestFixFollowsRenames:
    def test_a_renamed_page_carries_its_entry(self, build_repo, capsys):
        """LITERAL-13: a git rename moves the entry to the new path."""
        repo = build_repo({**COVERED, PAGE_A: page(3), BASELINE_PATH: ceiling(literal_table(A=3))})
        git(repo, "mv", PAGE_A, PAGE_B)
        code, out = judged(capsys, repo, *FIX_WORDS)
        assert (code, "frontend/src/lib/B.svelte moved 3 literal texts from frontend/src/lib/A.svelte" in out) == (0, True)
        assert table_of(repo) == {PAGE_B: 3}


class TestFixDryRun:
    def test_the_first_recording_is_planned_and_nothing_is_written(self, build_repo, capsys):
        """LITERAL-7, FIX-19: the dry run plans the recording, leaves the tree as it was and exits 1."""
        repo = build_repo({**COVERED, PAGE_A: page(3), BASELINE_PATH: ceiling()})
        before = tree_of(repo)
        code, out = judged(capsys, repo, *DRY_RUN_WORDS)
        assert (code, "~ debt to record 3 literal texts on 1 page · quality/baseline.toml" in out, tree_of(repo)) == (1, True, before)

    def test_a_lowering_is_planned_row_by_row_and_nothing_is_written(self, build_repo, capsys):
        """LITERAL-7, FIX-19: the dry run counts the entries to lower, names each and writes nothing."""
        repo = build_repo({**COVERED, PAGE_A: page(1), BASELINE_PATH: ceiling(literal_table(A=4))})
        before = tree_of(repo)
        code, out = judged(capsys, repo, *DRY_RUN_WORDS)
        assert (code, "~ debt 1 to lower" in out, "~ frontend/src/lib/A.svelte lower 4 → 1 literal texts" in out) == (1, True, True)
        assert tree_of(repo) == before

