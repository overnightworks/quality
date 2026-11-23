"""`quality check debt --fix` records the `[spec_citations]` table where git never held one and the tree is clean, drops the
held IDs that are now cited, retired or undefined, and never adds one; `--dry-run` plans the same rows."""

from pathlib import Path

import pytest

from quality.baseline_file import BASELINE_PATH, load_baseline
from tests.support.baselines import holding_spec_ids, rendered
from tests.support.door import DRY_RUN_WORDS, FIX_WORDS, digest, fixed, restore_step, tree_of
from tests.support.git_repositories import git, put
from tests.support.source_code import long_function
from tests.support.specs import SPEC, spec_with

TEST_FILE = "tests/test_a.py"
HELD = "[file_lines]\n\n[clones]\n"


def citing(*identifiers: str) -> dict[str, str]:
    tests = "".join(f'def test_{index}():\n    """{identifier}: it."""\n\n\n' for index, identifier in enumerate(identifiers))
    return {TEST_FILE: tests}


def squashed(text: str) -> str:
    return " ".join(text.split())


def written(repo: Path) -> frozenset[str] | None:
    return load_baseline((repo / BASELINE_PATH).read_text()).spec_citations


def both(capsys, repo: Path) -> tuple[tuple[int, str], tuple[int, str]]:
    """The dry run, which leaves the tree as it was, then the fix: the exit and the squashed output of each."""
    tree = tree_of(repo)
    planned_code, planned, _ = fixed(capsys, repo, *DRY_RUN_WORDS)
    assert tree_of(repo) == tree, planned
    code, done, _ = fixed(capsys, repo, *FIX_WORDS)
    return (planned_code, squashed(planned)), (code, squashed(done))


RECORDINGS = {
    "some uncited": (spec_with("- X-1: a.", "- X-2: b.", "- X-3: c."), citing("X-1"), {"X-2", "X-3"}),
    "all cited": (spec_with("- X-1: a."), citing("X-1"), set()),
    "a retired ID beside a live one": (spec_with("- X-1: a.", "- X-2 (retired)."), {}, {"X-1"}),
}


@pytest.mark.parametrize(("spec", "tests", "uncited"), RECORDINGS.values(), ids=RECORDINGS.keys())
def test_the_fix_records_the_uncited_ids_of_a_tree_that_defines_a_live_id_and_is_clean(build_repo, capsys, spec, tests, uncited):
    """CITE-11: with no table, a live ID defined, no citation undefined or retired and no ID defined twice, the table
    holds exactly the live IDs no test cites, the row counts them and the dry run plans it; a second fix changes
    nothing."""
    repo = build_repo({**spec, **tests, BASELINE_PATH: rendered(HELD)})
    planned, done = both(capsys, repo)
    noun = f"{len(uncited)} uncited spec ID{'' if len(uncited) == 1 else 's'}"
    assert (done[0], planned[0], written(repo)) == (0, 1, frozenset(uncited))
    assert f"✓ debt recorded {noun} · quality/baseline.toml" in done[1]
    assert f"~ debt to record {noun} · quality/baseline.toml" in planned[1]
    first = (repo / BASELINE_PATH).read_bytes()
    code, out, _ = fixed(capsys, repo, *FIX_WORDS)
    assert (code, (repo / BASELINE_PATH).read_bytes(), "recorded" in out) == (0, first, False)


BLOCKS_RECORDING = {
    "no live ID defined": (spec_with("- X-1 (retired)."), {}),
    "nothing defined": ({}, {}),
    "an undefined citation": (spec_with("- X-1: a."), citing("Z-1")),
    "a retired citation": (spec_with("- X-1: a.", "- X-2 (retired)."), citing("X-2")),
    "an ID defined twice": ({SPEC: "- X-1: a.\n- X-1: b.\n"}, {}),
}


@pytest.mark.parametrize(("spec", "tests"), BLOCKS_RECORDING.values(), ids=BLOCKS_RECORDING.keys())
def test_the_fix_records_nothing_while_the_tree_is_not_clean_and_the_exit_stays(build_repo, capsys, spec, tests):
    """CITE-11: no table is recorded, not even an empty one, and the report-only note stays, so a routine fix never turns
    a repository red."""
    repo = build_repo({"src/a.py": "x = 1\n", **spec, **tests, BASELINE_PATH: rendered(HELD)})
    before = digest(repo)
    planned, done = both(capsys, repo)
    assert (done[0], planned[0], digest(repo), "uncited spec ID" in done[1]) == (0, 0, before, False)


def test_a_first_fix_with_no_ceiling_file_records_the_spec_table_last_and_counts_it(build_repo, capsys):
    """CITE-11: the first recording names the uncited IDs after the entries, the pairs and the job limits."""
    repo = build_repo({"src/old.py": long_function(70), **spec_with("- X-1: a.", "- X-2: b.")})
    planned, done = both(capsys, repo)
    assert (done[0], planned[0], written(repo)) == (0, 1, frozenset({"X-1", "X-2"}))
    assert "✓ debt recorded 1 entry, 0 clone pairs, 2 uncited spec IDs · quality/baseline.toml is new" in done[1]
    assert "~ debt to record 1 entry, 0 clone pairs, 2 uncited spec IDs · quality/baseline.toml would be new" in planned[1]


def test_the_fix_drops_a_held_id_that_is_cited_retired_or_undefined_and_never_adds_one(build_repo, capsys):
    """CITE-10: each held ID now cited, retired or defined nowhere is dropped, the held uncited one stays, and a new
    uncited ID is no entry but a finding."""
    spec = spec_with("- X-1: a.", "- X-2 (retired).", "- X-3: c.", "- X-5: e.")
    repo = build_repo({**spec, **citing("X-1"), BASELINE_PATH: holding_spec_ids("X-1", "X-2", "X-3", "X-4")})
    planned, done = both(capsys, repo)
    assert (done[0], planned[0], written(repo)) == (1, 1, frozenset({"X-3"}))
    assert "ceiling lowered by 3 entries" in done[1]
    assert "3 ceiling entries would fall" in planned[1]
    assert "uncited" in done[1] and "X-5" in done[1]


def test_a_fix_with_the_table_and_nothing_to_drop_changes_nothing(build_repo, capsys):
    """CITE-10."""
    repo = build_repo({**spec_with("- X-1: a."), BASELINE_PATH: holding_spec_ids("X-1")})
    before = digest(repo)
    code, out, _ = fixed(capsys, repo, *FIX_WORDS)
    assert (code, digest(repo), "nothing to lower" in out) == (0, before, True)


UNREAD = {
    "a Markdown file that is not UTF-8": ("docs/x.md", b"\xff"),
    "a script test the lexer refuses": ("web/a.test.ts", b"const a = `\n"),
    "a Markdown file the tree lacks": ("docs/x.md", None),
    "a script test the tree lacks": ("web/a.test.ts", None),
}


@pytest.mark.parametrize(("name", "content"), UNREAD.values(), ids=UNREAD.keys())
def test_while_a_file_is_unread_the_fix_with_the_table_cannot_judge_and_writes_nothing(build_repo, capsys, name, content):
    """CITE-10: an ID defined in the unread or absent file would look undefined and be dropped, so the rule reads `cannot judge`
    naming the file, exit 2, in the dry run too."""
    repo = build_repo({**spec_with("- X-1: a.", "- X-2: b."), name: "", BASELINE_PATH: holding_spec_ids("X-1", "X-2")})
    put(repo / name, content)
    tree = tree_of(repo)
    for words in (DRY_RUN_WORDS, FIX_WORDS):
        code, out, _ = fixed(capsys, repo, *words)
        assert (code, name in out, tree_of(repo)) == (2, True, tree), out


@pytest.mark.parametrize(("name", "content"), UNREAD.values(), ids=UNREAD.keys())
def test_while_a_file_is_unread_the_fix_without_the_table_records_nothing_and_the_note_stays(build_repo, capsys, name, content):
    """CITE-10: nothing is recorded, the report-only note names the file, and the exit is as without the fix."""
    repo = build_repo({**spec_with("- X-1: a."), name: "", BASELINE_PATH: rendered(HELD)})
    put(repo / name, content)
    before = digest(repo)
    code, out, _ = fixed(capsys, repo, *FIX_WORDS)
    assert (code, digest(repo), name in out) == (0, before, True), out


def lost_by_hand(build_repo, files: dict[str, str]) -> Path:
    repo = build_repo({**files, BASELINE_PATH: holding_spec_ids("X-1")})
    put(repo / BASELINE_PATH, rendered(HELD))
    return repo


@pytest.mark.parametrize("words", [FIX_WORDS, DRY_RUN_WORDS], ids=["the fix", "the dry run"])
def test_a_table_git_holds_is_not_recorded_again_where_the_fix_would_record_it(build_repo, capsys, words):
    """CITE-11: a ceiling that lost the table although a version of it in git held one refuses the whole fix with the
    restore step, exit 2, and nothing is written, as for `[job_minutes]`."""
    repo = lost_by_hand(build_repo, spec_with("- X-1: a."))
    index, tree = git(repo, "ls-files", "--stage").stdout, tree_of(repo)
    code, out, err = fixed(capsys, repo, *words)
    assert (code, "quality/baseline.toml lost its [spec_citations] table, which git holds" in squashed(out)) == (2, True), out
    assert err.endswith(f"next: {restore_step()}\n"), err
    assert (tree_of(repo), git(repo, "ls-files", "--stage").stdout) == (tree, index)


def test_a_table_git_holds_is_not_asked_about_where_the_fix_would_not_record(build_repo, capsys):
    """CITE-11: with no live ID defined nothing would be recorded, so the question to git is not asked."""
    repo = lost_by_hand(build_repo, spec_with("- X-1 (retired)."))
    assert fixed(capsys, repo, *FIX_WORDS)[0] == 0
