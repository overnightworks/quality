import pytest

from quality.baseline_file import BASELINE_PATH
from quality.output import SEPARATOR
from quality.report import LiteralExtent
from quality.rules import Area, Rule
from tests.support.check import area_fact, area_row, check_output, check_result, refusal_row
from tests.support.door import CHECK_WORD, FIX_WORDS, door_in

COVERED = {
    "pyproject.toml": '[project]\nname = "app"\nversion = "0.1"\ndependencies = ["overnightworks-design[catalogs]>=1"]\n'
}
UNCOVERED = {"pyproject.toml": '[project]\nname = "app"\nversion = "0.1"\ndependencies = ["overnightworks-design>=1"]\n'}
RAW_TEXT_TAGS = ["script", "style", "textarea", "title", "iframe", "xmp", "noembed", "noframes"]
PAGES = {
    "frontend/src/a.svelte": "<h1>Log in</h1>\n<button aria-label={'Close'}>{words.close}</button>\n",
    "frontend/src/b.svelte": "<p>Welcome back</p>\n",
    "frontend/src/c.svelte": "<p>{words.none}</p>\n",
}
CLONES_TABLE = "[clones]\n"
DEBT = (CHECK_WORD, Area.DEBT.value)
FIX_COMMAND = "quality check --fix"
PARTLY = "partly measured only"


def debt_run(capsys, repo, *words):
    """The exit and stdout of `quality check debt` over `repo`."""
    code = door_in(repo, *(words or DEBT))
    return code, capsys.readouterr().out


def fix_line(out):
    return [line.strip() for line in out.splitlines() if line.strip().startswith("fix:")]


def verdict_line(out):
    return out.splitlines()[-1]


def test_a_covered_repository_without_a_ceiling_file_counts_its_literal_texts_as_measured_only(build_repo, capsys):
    """LITERAL-10, LITERAL-4: the row names the entries and the literal texts, the mode measured only, and the fix."""
    code, out = debt_run(capsys, build_repo({**COVERED, **PAGES}))
    fact = area_fact(out, Area.DEBT)
    assert (code, fact) == (0, f"0 entries over the thresholds, 3 literal texts in 3 files{SEPARATOR}measured only")
    assert fix_line(out) == [f"fix: {FIX_COMMAND}"]
    assert "debt ceiling measured, not enforced" in verdict_line(out)


@pytest.mark.parametrize("ceiling", [CLONES_TABLE, '[file_lines]\n[clones]\n'], ids=["only a clones table", "an empty size table"])
def test_a_covered_repository_with_a_clones_table_names_the_literal_part_and_the_fix_that_records_it(build_repo, capsys, ceiling):
    """LITERAL-10, LITERAL-4: recorded pairs are never called not recorded, and `--fix` records the literal texts."""
    tracked = {**COVERED, **PAGES, "frontend/src/d.svelte": "<p>{words.none}</p>\n", BASELINE_PATH: ceiling}
    code, out = debt_run(capsys, build_repo(tracked))
    assert (code, area_fact(out, Area.DEBT)) == (0, f"3 literal texts in 4 files{SEPARATOR}{PARTLY}")
    assert fix_line(out) == [f"fix: {FIX_COMMAND}"]
    assert "debt ceiling partly measured, not enforced" in verdict_line(out)


def test_a_covered_repository_with_a_ceiling_file_without_clones_names_the_pairs_and_the_literal_texts(build_repo, capsys):
    """LITERAL-10: the covered app's header-only ceiling file records no clones table, so its row names the pairs too
    and keeps the fix line."""
    code, out = debt_run(capsys, build_repo({**COVERED, **PAGES, BASELINE_PATH: "# nothing recorded yet\n"}))
    assert (code, area_fact(out, Area.DEBT)) == (0, f"0 clone pairs, 3 literal texts in 3 files{SEPARATOR}{PARTLY}")
    assert fix_line(out) == [f"fix: {FIX_COMMAND}"]
    assert "debt ceiling partly measured, not enforced" in verdict_line(out)


def test_one_literal_text_in_one_file_reads_in_the_singular(build_repo, capsys):
    """LITERAL-10: `1 literal text in 1 file`."""
    _, out = debt_run(capsys, build_repo({**COVERED, "a.svelte": "<p>Hello</p>\n", BASELINE_PATH: CLONES_TABLE}))
    assert area_fact(out, Area.DEBT).startswith("1 literal text in 1 file")


def test_a_covered_repository_without_a_svelte_file_reads_zero_in_zero_files(build_repo, capsys):
    """LITERAL-4: a repository with no Svelte file reads `0 literal texts in 0 files`."""
    _, out = debt_run(capsys, build_repo({**COVERED, BASELINE_PATH: CLONES_TABLE}))
    assert area_fact(out, Area.DEBT).startswith("0 literal texts in 0 files")


@pytest.mark.parametrize(
    "files",
    [UNCOVERED, {}, {"app/pyproject.toml": COVERED["pyproject.toml"]},
     {"pyproject.toml": '[project]\nname = "a"\nversion = "1"\n[tool.design-catalogs]\nx = 1\n'}],
    ids=["no extra", "no pyproject", "a nested project", "a catalogs table without the dependency"],
)
def test_a_repository_that_is_not_covered_prints_its_old_row_and_holds_no_literal_verdict(build_repo, capsys, files):
    """LITERAL-12, CATALOG-6: no declaration, no new part, no literal verdict and the row of before."""
    repo = build_repo({**files, **PAGES})
    code, out = debt_run(capsys, repo)
    debt = next(area for area in check_result(repo).areas if area.area is Area.DEBT)
    assert (code, area_fact(out, Area.DEBT)) == (0, f"0 entries over the thresholds{SEPARATOR}measured only")
    assert [verdict.rule for verdict in debt.verdicts] == [Rule.SIZE, Rule.CLONE, Rule.JOB_TIMEOUT]
    assert "literal" not in out


def test_every_area_judges_exactly_the_rules_it_owns_but_the_literal_rule_of_an_uncovered_repository(build_repo):
    """LITERAL-12: the area guard hides no forgotten rule."""
    judged = {area.area: {verdict.rule for verdict in area.verdicts} for area in check_result(build_repo(UNCOVERED)).areas}
    owned = {area: {rule for rule in Rule if rule.area is area} for area in Area}
    owned[Area.DEBT].discard(Rule.LITERAL_TEXT)
    assert judged == owned


def test_a_covered_repository_judges_the_literal_rule_with_its_extent_and_no_finding(build_repo):
    """LITERAL-10: the rule only measures."""
    result = check_result(build_repo({**COVERED, **PAGES}))
    verdict = next(verdict for verdict in result.verdicts if verdict.rule is Rule.LITERAL_TEXT)
    assert (verdict.findings, verdict.notes, verdict.measured_only, verdict.extent) == ((), (), True, LiteralExtent(3, 3))


def test_a_malformed_pyproject_refuses_the_debt_area_with_exit_two(build_repo, capsys):
    """LITERAL-12: the covered test reads the root `pyproject.toml` in every repository, so `debt` alone and a full run
    refuse it, and a full run refuses `workflows` beside it."""
    repo = build_repo({"pyproject.toml": "[project\n"})
    code, out = debt_run(capsys, repo)
    full_code, full = check_output(capsys, repo)
    assert (code, full_code) == (2, 2)
    assert refusal_row(Area.DEBT) in out
    assert area_row(out, Area.DEBT).glyph == "✗"
    assert [refusal_row(area) in full for area in (Area.DEBT, Area.WORKFLOWS)] == [True, True]


@pytest.mark.parametrize(
    "name",
    ["a.svelte", "frontend/src/a.svelte"],
)
def test_a_tracked_page_is_read_wherever_it_stands(build_repo, capsys, name):
    """LITERAL-1: every tracked `*.svelte` file the tree holds."""
    _, out = debt_run(capsys, build_repo({**COVERED, name: "<p>Hello</p>\n", BASELINE_PATH: CLONES_TABLE}))
    assert area_fact(out, Area.DEBT).startswith("1 literal text in 1 file")


@pytest.mark.parametrize("name", ["a.vue", "a.tsx", "a.jsx", "a.html"])
def test_other_markup_files_are_not_read(build_repo, capsys, name):
    """LITERAL-1: only Svelte pages are read."""
    _, out = debt_run(capsys, build_repo({**COVERED, name: "<p>Hello</p>\n", BASELINE_PATH: CLONES_TABLE}))
    assert area_fact(out, Area.DEBT).startswith("0 literal texts in 0 files")


def test_an_untracked_page_is_not_read(build_repo, capsys):
    """LITERAL-1: the rule reads what the repository tracks."""
    repo = build_repo({**COVERED, BASELINE_PATH: CLONES_TABLE})
    (repo / "new.svelte").write_text("<p>Hello</p>\n")
    _, out = debt_run(capsys, repo)
    assert area_fact(out, Area.DEBT).startswith("0 literal texts in 0 files")


def test_a_tracked_page_deleted_from_disk_is_not_read(build_repo, capsys):
    """LITERAL-9: a missing page is no finding."""
    repo = build_repo({**COVERED, **PAGES, BASELINE_PATH: CLONES_TABLE})
    (repo / "frontend/src/a.svelte").unlink()
    code, out = debt_run(capsys, repo)
    assert (code, area_fact(out, Area.DEBT).startswith("1 literal text in 2 files")) == (0, True)


@pytest.mark.parametrize(
    ("page", "fact"),
    [
        *[
            (f"<{tag}>\n<p>Hello</p>\n", f"frontend/src/a.svelte has a <{tag}> that is opened and never closed, so the markup after it cannot be read")
            for tag in RAW_TEXT_TAGS
        ],
        ("<p>{ never</p>\n", "frontend/src/a.svelte has a { that is never closed, so the markup after it cannot be read"),
        ("<script>\n<p>{ never</p>\n", "frontend/src/a.svelte has a { that is never closed, so the markup after it cannot be read"),
    ],
    ids=[*(f"unclosed-{tag}" for tag in RAW_TEXT_TAGS), "unclosed-hole", "unclosed-hole-after-unclosed-script"],
)
def test_a_page_the_scan_cannot_read_to_its_end_refuses_the_debt_area_naming_it(build_repo, capsys, page, fact):
    """LITERAL-9: an unclosed raw-text element or hole is exit 2, naming the file; the unclosed hole is named first."""
    tracked = {**COVERED, "frontend/src/a.svelte": page, "frontend/src/b.svelte": "<p>Hello</p>\n"}
    code, out = debt_run(capsys, build_repo(tracked))
    assert code == 2
    assert f"{refusal_row(Area.DEBT)}{fact}" in out


def test_a_page_that_is_not_utf_8_refuses_the_debt_area_naming_it(build_repo, capsys):
    """LITERAL-9: the shared reader's refusal names the file."""
    repo = build_repo({**COVERED, "frontend/src/a.svelte": "x"})
    (repo / "frontend/src/a.svelte").write_bytes(b"<p>\xff\xfe</p>")
    code, out = debt_run(capsys, repo)
    assert code == 2
    assert f"{refusal_row(Area.DEBT)}cannot read frontend/src/a.svelte" in out


def test_the_first_page_the_scan_cannot_read_stops_it(build_repo, capsys):
    """LITERAL-9: the pages are read in the tracked order and one fact prints."""
    tracked = {**COVERED, "a.svelte": "<script>\n", "b.svelte": "<p>{ never</p>\n"}
    _, out = debt_run(capsys, build_repo(tracked))
    assert "a.svelte has a <script>" in out
    assert "b.svelte" not in out


def test_fixing_the_debt_ceiling_of_a_covered_repository_records_its_literal_texts(build_repo, capsys):
    """LITERAL-10, LITERAL-7: `quality check debt --fix` writes the literal-text table, and the row then reads enforced."""
    repo = build_repo({**COVERED, **PAGES, BASELINE_PATH: CLONES_TABLE})
    assert door_in(repo, *FIX_WORDS) == 0
    capsys.readouterr()
    assert "[literal_text]" in (repo / BASELINE_PATH).read_text()
    code, out = debt_run(capsys, repo)
    assert (code, fix_line(out)) == (0, [])
    assert "ceiling held" in area_fact(out, Area.DEBT)
