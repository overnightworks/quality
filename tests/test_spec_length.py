from pathlib import Path

import pytest

from quality import checkout, spec_length
from quality.baseline_file import BASELINE_PATH
from quality.report import FindingEvent
from quality.rules import Area, Rule
from tests.support.baselines import DEBT_BASELINE_STATES
from tests.support.check import assert_row_prints_finding, check_result, finding_rows, rule_findings
from tests.support.door import CHECK_WORD, door_in
from tests.support.git_repositories import git, put, shallow_clone
from tests.support.specs import SPEC, spec_with

OVER = spec_length.WORD_LIMIT + 1
AT = spec_length.WORD_LIMIT
DEBT = [Area.DEBT]
ORIGIN_MAIN = checkout.DEFAULT_BRANCH_REF
SHORT = "- Y-1: short."


def words(count: int) -> str:
    return " ".join(["word"] * count)


def rule(identifier: str, count: int) -> str:
    """A list item that defines `identifier` with exactly `count` words."""
    return f"- {identifier}: {words(count)}"


def change(repo: Path, files: dict[str, str | None], message: str = "change") -> None:
    """Writes `files` into `repo` (None deletes one) and commits them."""
    for name, content in files.items():
        put(repo / name, content)
    git(repo, "add", "-A", "-f", "--", *files)
    git(repo, "commit", "-q", "-m", message)


@pytest.fixture
def branched(build_repo):
    """`branched(base, changes, committed=True)`: a repository whose `origin/main` is the commit that tracks `base`, with
    `changes` (a name to its new text, None to delete it) committed on top or, where `committed` is False, left in the work tree."""

    def build(base: dict[str, str], changes: dict[str, str | None] | None = None, committed: bool = True) -> Path:
        repo = build_repo(base)
        git(repo, "update-ref", ORIGIN_MAIN, "HEAD")
        if changes and committed:
            change(repo, changes)
        for name, content in ({} if committed else changes or {}).items():
            put(repo / name, content)
        return repo

    return build


def judged(repo: Path, capsys: pytest.CaptureFixture[str]) -> tuple[int, str, list[tuple[str, int | None, str]]]:
    """The exit and output of `quality check debt` over `repo` and the length findings it holds as (path, line, fact)."""
    code = door_in(repo, CHECK_WORD, Area.DEBT.value)
    findings = rule_findings(repo, Rule.SPEC_LENGTH, DEBT)
    return code, capsys.readouterr().out, [(finding.path, finding.line, finding.fact) for finding in findings]


def too_long(*rules: tuple[str, int], line: int = 1) -> list[tuple[str, int, str]]:
    """The findings of rules `(id, words)` that stand one line apart in the spec file from `line` on."""
    return [(SPEC, line + number, spec_length.too_long_fact(identifier, count)) for number, (identifier, count) in enumerate(rules)]


@pytest.mark.integration
@pytest.mark.parametrize(("count", "red"), [(OVER, True), (AT, False)], ids=["over the limit", "at the limit"])
def test_a_rule_a_change_adds_is_too_long_over_the_limit_and_clean_at_it(branched, capsys, count, red):
    """SPECLENGTH-1: a rule over the limit is a `too long` finding at its definition, one at the limit is none."""
    repo = branched(spec_with(SHORT), spec_with(SHORT, rule("X-1", count)))
    code, printed, found = judged(repo, capsys)
    assert (code, found) == ((1, too_long(("X-1", count), line=2)) if red else (0, []))
    if red:
        finding = rule_findings(repo, Rule.SPEC_LENGTH, DEBT)[0]
        assert (finding.event, finding.fix) == (FindingEvent.TOO_LONG, spec_length.FIX)
        assert_row_prints_finding(finding_rows(printed, finding.location)[0], finding)


@pytest.mark.integration
def test_an_edited_rule_over_the_limit_is_a_finding_and_its_untouched_neighbour_is_none(branched, capsys):
    """SPECLENGTH-3: a rule whose text differs from the base's under its ID counts, a long one that no change touches does not."""
    base = spec_with(rule("X-1", OVER), "", rule("X-2", OVER))
    repo = branched(base, spec_with(rule("X-1", OVER + 1), "", rule("X-2", OVER)))
    code, _, found = judged(repo, capsys)
    assert (code, found) == (1, too_long(("X-1", OVER + 1)))


@pytest.mark.integration
@pytest.mark.parametrize(
    ("lines", "expected"),
    [
        pytest.param(["| ID | Text |", "| --- | --- |", f"| X-1 | {words(OVER)} |", f"| X-2 | {words(AT)} |"], [("X-1", OVER)], id="a table row counts alone by its cells after the ID"),
        pytest.param(["| ID | Text |", "| --- | --- |", f"| X-1 | {words(AT // 2 + 5)} |", f"| X-2 | {words(AT // 2 + 5)} |"], [], id="two table rows are two rules"),
        pytest.param([f"- X-1: {words(OVER - 3)} `a b c`"], [("X-1", OVER)], id="inline code counts by its tokens"),
        pytest.param([f"- X-1: {words(OVER - 2)} [two words](https://example.test/page)"], [("X-1", OVER)], id="a link counts by its visible words"),
        pytest.param([f"- X-1: **Title.** {words(OVER - 1)}"], [("X-1", OVER)], id="a bold title counts as written"),
        pytest.param([rule("X-1", 20), f"  - {words(OVER - 20)}"], [("X-1", OVER)], id="a nested item without an ID belongs to its rule"),
        pytest.param([rule("X-1", 20), "", f"  {words(OVER - 20)}"], [("X-1", OVER)], id="a paragraph after a blank line belongs to its rule"),
        pytest.param([rule("X-1", AT), f"  - X-2: {words(OVER)}"], [("X-2", OVER)], id="a nested item with an ID is a rule of its own"),
        pytest.param([rule("X-1", AT), "  ```", f"  {words(5)}", "  ```"], [], id="a fenced block counts for no rule"),
        pytest.param(["- X-1 (retired).", f"  {words(OVER)}"], [], id="a retired rule's text counts for no rule"),
        pytest.param(["# Heading", f"  {words(OVER)}"], [], id="text outside every rule counts for no rule"),
    ],
)
def test_a_rule_counts_the_words_of_its_text_and_each_table_row_is_one_rule(branched, capsys, lines, expected):
    """SPECLENGTH-4, SPECLENGTH-5, SPECLENGTH-6: a rule is its item with what stands under it or its row's cells after the ID, and a
    word is a token with a letter or a digit."""
    repo = branched(spec_with(SHORT), spec_with(SHORT, *lines))
    assert [fact for _, _, fact in judged(repo, capsys)[2]] == [spec_length.too_long_fact(identifier, count) for identifier, count in expected]


LONG = rule("X-1", OVER)


@pytest.mark.integration
@pytest.mark.parametrize(
    ("base", "head", "expected"),
    [
        pytest.param(spec_with(LONG), spec_with(f"- X-1: {words(10)}", f"  {words(OVER - 10)}"), [], id="re-wrapped"),
        pytest.param({"docs/a.md": f"{LONG}\n"}, {"docs/a.md": "", "docs/b.md": f"{LONG}\n"}, [], id="moved to another file"),
        pytest.param(spec_with(LONG), spec_with(rule("X-2", OVER)), [("X-2", OVER)], id="renumbered"),
        pytest.param(spec_with(LONG), spec_with("- X-1 (retired)."), [], id="retired"),
    ],
)
def test_a_rule_with_the_base_text_under_its_id_is_untouched_wherever_it_stands(branched, capsys, base, head, expected):
    """SPECLENGTH-3: the same words under the same ID touch nothing, so a re-wrap, a move and a retirement are clean and a renumbering is a new rule."""
    assert judged(branched(base, head), capsys)[2] == too_long(*expected)


@pytest.mark.integration
@pytest.mark.parametrize(
    ("changes", "committed", "base", "expected"),
    [
        pytest.param(spec_with(SHORT, rule("X-1", OVER)), True, spec_with(SHORT), 1, id="committed on the branch"),
        pytest.param(spec_with(SHORT, rule("X-1", OVER)), False, spec_with(SHORT), 1, id="uncommitted in the work tree"),
        pytest.param(None, True, spec_with(SHORT, rule("X-1", OVER)), 0, id="main's own commit"),
    ],
)
def test_a_change_is_what_the_tree_holds_beyond_the_merge_base_with_origin_main_uncommitted_edits_included(branched, capsys, changes, committed, base, expected):
    """SPECLENGTH-2: the work tree against the merge base of HEAD and origin/main, so main's own commit changes nothing."""
    assert judged(branched(base, changes, committed), capsys)[0] == expected


@pytest.mark.integration
@pytest.mark.parametrize("merged", [False, True], ids=["a local run on the branch", "the pull request's merge commit"])
def test_what_main_changed_after_the_branch_point_is_not_counted_and_the_branchs_own_edit_is(build_repo, capsys, merged):
    """SPECLENGTH-2: the base is the merge base, which is main's tip in a merge commit, never origin/main's tip as a local run sees it."""
    repo = build_repo({"docs/a.md": f"{rule('R-1', OVER)}\n", "docs/b.md": f"{SHORT}\n"})
    git(repo, "branch", "-M", "trunk")
    git(repo, "checkout", "-q", "-b", "feature")
    change(repo, {"docs/b.md": f"{rule('B-1', OVER)}\n"})
    git(repo, "checkout", "-q", "trunk")
    change(repo, {"docs/a.md": f"{rule('R-1', OVER + 5)}\n"})
    git(repo, "update-ref", ORIGIN_MAIN, "trunk")
    git(repo, "checkout", "-q", "feature")
    if merged:
        git(repo, "merge", "-q", "--no-ff", "-m", "merge", "trunk")
    code, _, found = judged(repo, capsys)
    assert (code, found) == (1, [("docs/b.md", 1, spec_length.too_long_fact("B-1", OVER))])


def a_shallow_clone_that_does_not_reach_the_merge_base(build_repo, elsewhere) -> Path:
    origin = build_repo(spec_with(SHORT))
    git(origin, "branch", "-M", "main")
    git(origin, "checkout", "-q", "-b", "feature")
    change(origin, spec_with(SHORT, rule("X-1", OVER)))
    git(origin, "checkout", "-q", "main")
    change(origin, spec_with(SHORT, "- Y-2: short."))
    git(origin, "checkout", "-q", "feature")
    clone = shallow_clone(origin, elsewhere)
    git(clone, "fetch", "-q", "--depth", "1", "origin", f"main:{ORIGIN_MAIN}")
    return clone


def a_shallow_clone_on_main_itself(build_repo, elsewhere) -> Path:
    origin = build_repo(spec_with(SHORT, rule("X-1", OVER)))
    git(origin, "branch", "-M", "main")
    return shallow_clone(origin, elsewhere)


@pytest.mark.integration
@pytest.mark.parametrize(
    ("start", "step"),
    [
        pytest.param(lambda build_repo, elsewhere: build_repo(spec_with(rule("X-1", OVER))), checkout.FETCH_DEFAULT_BRANCH_STEP, id="no origin/main ref"),
        pytest.param(a_shallow_clone_that_does_not_reach_the_merge_base, checkout.UNSHALLOW_STEP, id="a shallow clone that does not reach the merge base"),
    ],
)
def test_a_long_rule_and_no_merge_base_with_origin_main_cannot_be_judged_and_names_the_next_step(build_repo, elsewhere, capsys, start, step):
    """SPECLENGTH-7, SPECLENGTH-8: exit 2 with the refusal and the step that fetches the base, never a pass that judged nothing."""
    repo = start(build_repo, elsewhere)
    refusal = next(verdict.refusal for verdict in check_result(repo, named=DEBT).verdicts if verdict.rule is Rule.SPEC_LENGTH)
    assert (door_in(repo, CHECK_WORD, Area.DEBT.value), refusal.reason.fact, refusal.reason.next_steps) == (2, spec_length.NO_BASE_FACT, (step,))


@pytest.mark.integration
@pytest.mark.parametrize(
    "start",
    [
        pytest.param(lambda build_repo, elsewhere: build_repo(spec_with(SHORT)), id="no long rule asks git for nothing"),
        pytest.param(a_shallow_clone_on_main_itself, id="a depth-1 clone on main's own commit"),
    ],
)
def test_a_tree_with_no_long_rule_or_on_main_itself_needs_no_fetch_and_is_clean(build_repo, elsewhere, start):
    """SPECLENGTH-9: the base is asked only where a live rule is over the limit, and on main's own commit it is HEAD."""
    repo = start(build_repo, elsewhere)
    assert (door_in(repo, CHECK_WORD, Area.DEBT.value), rule_findings(repo, Rule.SPEC_LENGTH, DEBT)) == (0, [])


@pytest.mark.integration
@pytest.mark.parametrize("state", ["a baseline held", "no baseline"])
def test_the_rule_is_red_without_a_spec_citation_table_and_without_a_baseline_file(branched, capsys, state):
    """SPECLENGTH-10: it needs no table and no config, so it judges every repository the same."""
    baseline = DEBT_BASELINE_STATES[state]
    files = {} if baseline is None else {BASELINE_PATH: baseline}
    repo = branched({**spec_with(SHORT), **files}, spec_with(SHORT, rule("X-1", OVER)))
    code, _, found = judged(repo, capsys)
    assert (code, found) == (1, too_long(("X-1", OVER), line=2))
