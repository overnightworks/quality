import pytest

from quality import literal_text
from quality.baseline_file import BASELINE_PATH
from quality.report import FindingEvent
from quality.rules import Area, Rule
from tests.support.check import (
    area_row,
    assert_row_prints_finding,
    check_output,
    check_result,
    refusal_row,
    refusals,
    rows_of,
    rule_findings,
)
from tests.support.door import CHECK_WORD, door_in

COVERED = {
    "pyproject.toml": '[project]\nname = "overnightworks-app"\nversion = "0.1"\nrequires-python = ">=3.14"\ndependencies = ["overnightworks-design[catalogs]>=1"]\n'
}
UNCOVERED = {"pyproject.toml": '[project]\nname = "overnightworks-app"\nversion = "0.1"\nrequires-python = ">=3.14"\ndependencies = ["overnightworks-design>=1"]\n'}
RAW_TEXT_TAGS = ["script", "style", "textarea", "title", "iframe", "xmp", "noembed", "noframes"]
PAGES = {
    "frontend/src/a.svelte": "<h1>Log in</h1>\n<button aria-label={'Close'}>{words.close}</button>\n",
    "frontend/src/b.svelte": "<p>Welcome back</p>\n",
    "frontend/src/c.svelte": "<p>{words.none}</p>\n",
}
TEXTS = [("frontend/src/a.svelte", 1, "'Log in'"), ("frontend/src/a.svelte", 2, "'Close'"), ("frontend/src/b.svelte", 1, "'Welcome back'")]
FILES = (CHECK_WORD, Area.FILES.value)
PYPROJECT_REASON = "pyproject.toml is not UTF-8 TOML that Python reads"


@pytest.fixture
def fixture_origin() -> str:
    return "overnightworks/app"


def files_run(capsys, repo):
    """The exit and stdout of `quality check files` over `repo`."""
    code = door_in(repo, *FILES)
    return code, capsys.readouterr().out


def found(repo):
    """Each literal text the rule found over `repo` as (path, line, fact)."""
    return [(finding.path, finding.line, finding.fact) for finding in rule_findings(repo, Rule.LITERAL_TEXT)]


@pytest.mark.parametrize(
    "ceiling",
    [None, "[literal_text]\n", '[literal_text]\n"frontend/src/a.svelte" = 9\n"frontend/src/b.svelte" = 9\n'],
    ids=["no ceiling file", "an empty table", "a table holding more than the pages"],
)
def test_every_literal_text_of_a_covered_repository_is_a_red_finding_whatever_the_ceiling_file_holds(build_repo, capsys, ceiling):
    """LITERAL-14: one finding per text at its page and line, with no table and no exemption turning it off."""
    repo = build_repo({**COVERED, **PAGES, **({} if ceiling is None else {BASELINE_PATH: ceiling})})
    code, out = files_run(capsys, repo)
    findings = rule_findings(repo, Rule.LITERAL_TEXT)
    assert (code, found(repo)) == (1, TEXTS)
    assert {(finding.event, finding.fix) for finding in findings} == {(FindingEvent.LITERAL_TEXT, literal_text.FIX)}
    for finding in findings:
        (row,) = rows_of(out, finding)
        assert_row_prints_finding(row, finding)


def test_a_covered_repository_whose_pages_read_every_word_through_a_catalog_is_clean(build_repo, capsys):
    """LITERAL-14: a page without a literal text gives no finding."""
    code, out = files_run(capsys, build_repo({**COVERED, "src/a.svelte": "<p>{words.none}</p>\n"}))
    assert (code, area_row(out, Area.FILES).glyph) == (0, "✓")


@pytest.mark.parametrize(
    "files",
    [UNCOVERED, {}, {"app/pyproject.toml": COVERED["pyproject.toml"]},
     {"pyproject.toml": '[project]\nname = "overnightworks-app"\nversion = "1"\n[tool.design-catalogs]\nx = 1\n'}],
    ids=["no extra", "no pyproject", "a nested project", "a catalogs table without the dependency"],
)
def test_a_repository_that_is_not_covered_sees_no_literal_row_and_its_debt_area_is_unchanged(build_repo, capsys, files):
    """LITERAL-15, CATALOG-6: no declaration, no literal verdict, and `debt` judges exactly `size`, `clone`, `job-timeout`, `test-twin` and `spec-citation`."""
    repo = build_repo({**files, **PAGES})
    code = door_in(repo, CHECK_WORD, Area.DEBT.value)
    out = capsys.readouterr().out
    areas = {area.area: [verdict.rule for verdict in area.verdicts] for area in check_result(repo).areas}
    assert (code, Rule.LITERAL_TEXT in areas[Area.FILES], areas[Area.DEBT]) == (0, False, [Rule.SIZE, Rule.CLONE, Rule.JOB_TIMEOUT, Rule.TEST_TWIN, Rule.SPEC_CITATION])
    assert "literal" not in out


def test_every_area_judges_exactly_the_rules_it_owns_but_the_covered_only_rules_of_an_uncovered_repository(build_repo):
    """LITERAL-15: the area guard hides no forgotten rule."""
    judged = {area.area: {verdict.rule for verdict in area.verdicts} for area in check_result(build_repo(UNCOVERED)).areas}
    owned = {area: {rule for rule in Rule if rule.area is area} for area in Area}
    owned[Area.FILES] -= {Rule.LITERAL_TEXT, Rule.LOCATOR_TEXT}
    owned[Area.CODE] -= {Rule.LOGGING}
    assert judged == owned


def test_the_literal_rule_of_a_covered_repository_stands_in_the_files_area_and_reads_its_pages(build_repo):
    """LITERAL-14: the rule belongs to `files`, and its extent is the pages it read."""
    result = check_result(build_repo({**COVERED, **PAGES}))
    files = next(area for area in result.areas if area.area is Area.FILES)
    verdict = next(verdict for verdict in files.verdicts if verdict.rule is Rule.LITERAL_TEXT)
    assert (verdict.measured_only, verdict.extent) == (False, len(PAGES))


@pytest.mark.parametrize(
    "name",
    ["a.svelte", "frontend/src/a.svelte"],
)
def test_a_tracked_page_is_read_wherever_it_stands(build_repo, name):
    """LITERAL-1: every tracked `*.svelte` file the tree holds."""
    assert found(build_repo({**COVERED, name: "<p>Hello</p>\n"})) == [(name, 1, "'Hello'")]


@pytest.mark.parametrize("name", ["a.vue", "a.tsx", "a.jsx", "a.html"])
def test_other_markup_files_are_not_read(build_repo, name):
    """LITERAL-1: only Svelte pages are read."""
    assert found(build_repo({**COVERED, name: "<p>Hello</p>\n"})) == []


def test_an_untracked_page_is_not_read(build_repo):
    """LITERAL-1: the rule reads what the repository tracks."""
    repo = build_repo(COVERED)
    (repo / "new.svelte").write_text("<p>Hello</p>\n")
    assert found(repo) == []


def test_a_tracked_page_deleted_from_disk_is_not_read(build_repo):
    """LITERAL-9: a missing page is no finding."""
    repo = build_repo({**COVERED, **PAGES})
    (repo / "frontend/src/a.svelte").unlink()
    assert found(repo) == TEXTS[2:]


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
def test_a_page_the_scan_cannot_read_to_its_end_refuses_the_files_area_naming_it(build_repo, capsys, page, fact):
    """LITERAL-9: an unclosed raw-text element or hole is exit 2, naming the file; the unclosed hole is named first."""
    tracked = {**COVERED, "frontend/src/a.svelte": page, "frontend/src/b.svelte": "<p>Hello</p>\n"}
    code, out = files_run(capsys, build_repo(tracked))
    assert code == 2
    assert f"{refusal_row(Area.FILES)}{fact}" in out


def test_a_page_that_is_not_utf_8_refuses_the_files_area_naming_it(build_repo, capsys):
    """LITERAL-9: the shared reader's refusal names the file."""
    repo = build_repo({**COVERED, "frontend/src/a.svelte": "x"})
    (repo / "frontend/src/a.svelte").write_bytes(b"<p>\xff\xfe</p>")
    code, out = files_run(capsys, repo)
    assert code == 2
    assert f"{refusal_row(Area.FILES)}cannot read frontend/src/a.svelte" in out


def test_the_first_page_the_scan_cannot_read_stops_it(build_repo, capsys):
    """LITERAL-9: the pages are read in the tracked order and one fact prints."""
    tracked = {**COVERED, "src/a.svelte": "<script>\n", "src/b.svelte": "<p>{ never</p>\n"}
    _, out = files_run(capsys, build_repo(tracked))
    assert "src/a.svelte has a <script>" in out
    assert "src/b.svelte" not in out


@pytest.mark.parametrize(
    "table", ["[tool]\ndesign-catalogs = 1\n", '[tool.design-catalogs]\ncatalogs = "/abs"\n'], ids=["no table", "bad path"]
)
def test_a_bad_catalogs_declaration_leaves_the_literal_texts_of_a_covered_repository_found(build_repo, table):
    """RENDER-2: the catalogs directory is read only for the render pass, so the literal-text rule finds as before."""
    assert found(build_repo({"pyproject.toml": COVERED["pyproject.toml"] + table, **PAGES})) == TEXTS


@pytest.mark.parametrize("with_pages", [True, False], ids=["with pages", "without pages"])
@pytest.mark.parametrize("body", [b"[project\n", b'[project]\nname = "\xff"\n'], ids=["invalid TOML", "not UTF-8"])
def test_a_broken_root_pyproject_gives_the_files_area_one_reason_through_every_rule_that_reads_it(build_repo, capsys, body, with_pages):
    """LITERAL-15, REFUSAL-4: `python-floor` and `literal-text` refuse with one wording, which the row stands once."""
    repo = build_repo({**COVERED, **(PAGES if with_pages else {})})
    (repo / "pyproject.toml").write_bytes(body)
    code, out = files_run(capsys, repo)
    reasons = refusals(repo)
    assert code == 2
    assert reasons[Rule.PYTHON_FLOOR] == reasons[Rule.LITERAL_TEXT]
    assert reasons[Rule.LITERAL_TEXT].startswith(PYPROJECT_REASON)
    assert out.count(PYPROJECT_REASON) == 1


def test_a_root_pyproject_the_filesystem_refuses_gives_the_files_area_one_reason(build_repo, capsys, elsewhere):
    """LITERAL-15, REFUSAL-4: a read error is worded as the shared reader words it, for both rules."""
    repo = build_repo(COVERED)
    outside = elsewhere / "outside.toml"
    outside.write_text("[project]\n")
    (repo / "pyproject.toml").unlink()
    (repo / "pyproject.toml").symlink_to(outside)
    code, out = files_run(capsys, repo)
    reasons = refusals(repo)
    assert (code, reasons[Rule.PYTHON_FLOOR] == reasons[Rule.LITERAL_TEXT]) == (2, True)
    assert out.count("pyproject.toml resolves outside the repository") == 1


def test_a_broken_root_pyproject_is_exit_two_in_a_full_run_for_the_files_and_workflows_areas(build_repo, capsys):
    """LITERAL-15: `files` and `workflows` both refuse it, and `debt` no longer does."""
    repo = build_repo({"pyproject.toml": "[project\n"})
    debt_code = door_in(repo, CHECK_WORD, Area.DEBT.value)
    capsys.readouterr()
    full_code, full = check_output(capsys, repo)
    reasons = refusals(repo)
    assert (debt_code, full_code) == (0, 2)
    assert [refusal_row(area) in full for area in (Area.FILES, Area.WORKFLOWS, Area.DEBT)] == [True, True, False]
    assert {reasons[rule].startswith(PYPROJECT_REASON) for rule in (Rule.CATALOG_CHECK, Rule.RENDER_PASS)} == {True}
