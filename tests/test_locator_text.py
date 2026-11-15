import pytest

from quality import locator_text
from quality.baseline_file import BASELINE_PATH
from quality.report import FindingEvent
from quality.rules import Area, Rule
from tests.support.check import (
    assert_row_prints_finding,
    check_output,
    check_result,
    refusal_row,
    refusals,
    rows_of,
    rule_findings,
)
from tests.support.door import CHECK_WORD, door_in

PYPROJECT = (
    '[project]\nname = "app"\nversion = "0.1"\nrequires-python = ">=3.14"\n'
    'dependencies = ["overnightworks-design[catalogs]>=1"]\n'
)
UNCOVERED = {"pyproject.toml": PYPROJECT.replace("[catalogs]", "")}
CATALOGS = "frontend/src/lib/catalogs"
TEST_FILE = "tests/test_dialog.py"
FILES = (CHECK_WORD, Area.FILES.value)
HEADER = 'msgid ""\nmsgstr ""\n"Language: {language}\\n"\n\n'


def catalog(language: str, *entries: tuple[str, str, str], extra: str = "") -> str:
    """A catalog file: the entries are (msgctxt, msgid, msgstr)."""
    body = "".join(f'msgctxt "{key}"\nmsgid "{text}"\nmsgstr "{translation}"\n\n' for key, text, translation in entries)
    return HEADER.format(language=language) + body + extra


SOURCE = catalog("de", ("dialog.cancel", "Abbrechen", ""), ("dialog.title", "Einstellungen", ""))
ENGLISH = catalog("en", ("dialog.cancel", "Abbrechen", "Cancel"), ("dialog.title", "Einstellungen", "Settings"))
COVERED = {"pyproject.toml": PYPROJECT, f"{CATALOGS}/de.po": SOURCE, f"{CATALOGS}/en.po": ENGLISH}


def found(repo):
    """Each site the rule found over `repo` as (path, line, fact)."""
    return [(finding.path, finding.line, finding.fact) for finding in rule_findings(repo, Rule.LOCATOR_TEXT)]


def only_test(source: str, extra: dict[str, str] | None = None) -> dict[str, str]:
    return {**COVERED, TEST_FILE: source, **(extra or {})}


def cancel_fact(text: str = "Abbrechen") -> str:
    return f"'{text}' is the catalog text of dialog.cancel"


@pytest.mark.parametrize(
    ("source", "text", "key"),
    [
        pytest.param('page.get_by_role("button", name="Abbrechen")\n', "Abbrechen", "dialog.cancel", id="get_by_role name"),
        pytest.param('page.get_by_text("Einstellungen")\n', "Einstellungen", "dialog.title", id="get_by_text"),
        pytest.param('page.get_by_test_id("x").filter(has_text="Abbrechen")\n', "Abbrechen", "dialog.cancel", id="has_text"),
        pytest.param('expect(button).to_have_text("Abbrechen")\n', "Abbrechen", "dialog.cancel", id="to_have_text"),
        pytest.param('expect(button).to_have_accessible_name("Abbrechen")\n', "Abbrechen", "dialog.cancel", id="accessible name"),
        pytest.param('expect(page).to_have_title("Einstellungen")\n', "Einstellungen", "dialog.title", id="to_have_title"),
        pytest.param('expect(button).to_have_attribute("aria-label", "Abbrechen")\n', "Abbrechen", "dialog.cancel", id="attribute value"),
        pytest.param('expect(button).to_have_attribute(name="title", value="Abbrechen")\n', "Abbrechen", "dialog.cancel", id="value keyword"),
        pytest.param('LABEL = "Abbrechen"\npage.get_by_text(LABEL)\n', "Abbrechen", "dialog.cancel", id="module constant"),
        pytest.param('page.get_by_role("button", name=label("Abbrechen"))\n', "Abbrechen", "dialog.cancel", id="nested helper"),
        pytest.param('page.get_by_text(re.compile("^Abbrechen$"))\n', "Abbrechen", "dialog.cancel", id="anchored regex"),
        pytest.param('LABEL: str = "Abbrechen"\npage.get_by_text(LABEL)\n', "Abbrechen", "dialog.cancel", id="annotated constant"),
        pytest.param('LABEL = "Abbrechen"\ndef f():\n    LABEL = "x"\npage.get_by_text(LABEL)\n', "Abbrechen", "dialog.cancel", id="same name inside a function"),
        pytest.param('get_by_text("Abbrechen")\n', "Abbrechen", "dialog.cancel", id="bare function"),
        pytest.param('page.get_by_text("Cancel")\n', "Cancel", "dialog.cancel", id="english msgstr"),
        pytest.param('page.getByRoleButton("Abbrechen")\n', "Abbrechen", "dialog.cancel", id="camel wrapper"),
        pytest.param('expect(rows).to_have_text(["Abbrechen", "other"])\n', "Abbrechen", "dialog.cancel", id="list of texts"),
    ],
)
def test_a_catalog_text_in_a_locator_position_of_a_python_test_is_a_finding(build_repo, source, text, key):
    """LOCATOR-1 to LOCATOR-5: the fact names the text and the first key; the line is the site's."""
    repo = build_repo(only_test(source))
    ((path, line, fact),) = found(repo)
    assert (path, line, fact) == (TEST_FILE, source.count("\n"), f"'{text}' is the catalog text of {key}")


def test_a_finding_reads_as_the_rule_words_it_and_the_run_is_red(build_repo, capsys):
    """LOCATOR-6: one row per site with the event word and the fix."""
    repo = build_repo(only_test('page.get_by_text("Abbrechen")\n'))
    code, out = check_output(capsys, repo)
    (finding,) = rule_findings(repo, Rule.LOCATOR_TEXT)
    (row,) = rows_of(out, finding)
    assert_row_prints_finding(row, finding)
    assert (code, finding.event, finding.fix) == (1, FindingEvent.CATALOG_TEXT, locator_text.FIX)


@pytest.mark.parametrize(
    "source",
    [
        'assert label == "Abbrechen"\n',
        'expect(value).to_be("Abbrechen")\n',
        'page.get_by_role("button", name="Speichern")\n',
        'page.get_by_text(f"Abbrechen {n}")\n',
        'page.get_by_text("Abbrechen now")\n',
        'page.get_by_text(LABEL)\nLABEL = "Abbrechen"\nLABEL = "Other"\n',
        'page.get_by_text(re.compile("Abbrechen"))\n',
        'page.get_by_text(re.compile("Abbrechen", re.IGNORECASE))\n',
        'LABEL = "Abbrechen"\npage.get_by_text(re.compile(LABEL))\n',
        'page.get_by_text(name)\n',
        'page.getByteTimeDomainData("Abbrechen")\n',
        'expect(button).to_have_attribute("Abbrechen", "x")\n',
        'page.locator("Abbrechen")\n',
        'page.get_by_text(None)\n',
        'LABEL = "Abbrechen"\nLABEL += "!"\npage.get_by_text(LABEL)\n',
        'LABEL, OTHER = "Abbrechen", "x"\npage.get_by_text(LABEL)\n',
        'LABEL = "Abbrechen"\nfrom labels import LABEL\npage.get_by_text(LABEL)\n',
        'LABEL = "Abbrechen"\nimport labels as LABEL\npage.get_by_text(LABEL)\n',
        'LABEL = "Abbrechen"\nfor LABEL in labels:\n    pass\npage.get_by_text(LABEL)\n',
        'LABEL = "Abbrechen"\ndef LABEL():\n    pass\npage.get_by_text(LABEL)\n',
        'LABEL = "Abbrechen"\ntry:\n    pass\nexcept ValueError as LABEL:\n    pass\npage.get_by_text(LABEL)\n',
        'locators["x"]("Abbrechen")\n',
    ],
    ids=[
        "assert", "to_be", "another text", "f-string", "longer text", "constant bound twice",
        "unanchored regex", "unanchored regex with flags", "unanchored regex constant", "dynamic name", "not a locator", "attribute name", "locator selector",
        "no string", "constant extended", "constant unpacked", "constant rebound by an import", "constant rebound by an alias", "constant rebound by a loop", "constant rebound by a function", "constant rebound by a handler", "subscripted function",
    ],
)
def test_scenario_data_and_every_other_value_is_no_site(build_repo, source):
    """LOCATOR-7: only the named positions with a whole-value equal text count."""
    assert found(build_repo(only_test(source))) == []


def test_a_text_two_keys_share_names_the_first_and_counts_the_rest(build_repo):
    """LOCATOR-6: `, and N more keys`, the keys in their sort order."""
    shared = catalog("de", ("dialog.cancel", "Abbrechen", ""), ("button.cancel", "Abbrechen", ""), ("menu.cancel", "Abbrechen", ""))
    repo = build_repo(only_test('page.get_by_text("Abbrechen")\n', {f"{CATALOGS}/de.po": shared}))
    assert found(repo)[0][2] == "'Abbrechen' is the catalog text of button.cancel, and 2 more keys"


def test_a_text_one_other_key_shares_says_one_more_keys(build_repo):
    """LOCATOR-6: the word is `keys` whatever N is."""
    shared = catalog("de", ("a.cancel", "Abbrechen", ""), ("b.cancel", "Abbrechen", ""))
    repo = build_repo({**COVERED, f"{CATALOGS}/de.po": shared, f"{CATALOGS}/en.po": catalog("en"), TEST_FILE: 'page.get_by_text("Abbrechen")\n'})
    assert found(repo)[0][2] == "'Abbrechen' is the catalog text of a.cancel, and 1 more keys"


def test_a_fuzzy_entry_gives_its_msgid_and_no_translation(build_repo):
    """LOCATOR-4: gettext does not show a fuzzy msgstr, so only the msgid is surface text."""
    fuzzy = catalog("en", extra='#, fuzzy\nmsgctxt "dialog.cancel"\nmsgid "Abbrechen"\nmsgstr "Cancel"\n')
    repo = build_repo(only_test('page.get_by_text("Cancel")\npage.get_by_text("Abbrechen")\n', {f"{CATALOGS}/en.po": fuzzy}))
    assert found(repo) == [(TEST_FILE, 2, cancel_fact())]


def test_an_obsolete_entry_is_no_catalog_text(build_repo):
    obsolete = catalog("de", extra='#~ msgctxt "old.key"\n#~ msgid "Veraltet"\n#~ msgstr ""\n')
    assert found(build_repo(only_test('page.get_by_text("Veraltet")\n', {f"{CATALOGS}/de.po": obsolete}))) == []


def test_every_site_of_every_test_file_is_one_finding_in_path_and_line_order(build_repo):
    repo = build_repo(
        {
            **COVERED,
            "tests/test_b.py": 'page.get_by_text("Abbrechen")\n',
            "tests/test_a.py": 'x = 1\npage.get_by_text("Einstellungen")\npage.get_by_text("Abbrechen")\n',
        }
    )
    assert [(path, line) for path, line, _ in found(repo)] == [("tests/test_a.py", 2), ("tests/test_a.py", 3), ("tests/test_b.py", 1)]


@pytest.mark.parametrize("name", ["tests/test_a.py", "app/tests/test_a.py", "tests/unit/helpers.py"])
def test_a_python_file_under_a_tests_directory_is_read(build_repo, name):
    """LOCATOR-8."""
    assert len(found(build_repo({**COVERED, name: 'page.get_by_text("Abbrechen")\n'}))) == 1


@pytest.mark.parametrize("name", ["src/helpers.py", "test_a.py", "tests/a.js", "tests/a.ts", "tests/a.txt"])
def test_other_files_are_not_read_in_this_slice(build_repo, name):
    """LOCATOR-8: Python files under `tests/` only; TypeScript and JavaScript are not read yet."""
    assert found(build_repo({**COVERED, name: 'page.get_by_text("Abbrechen")\n'})) == []


def test_a_site_stays_red_beside_a_baseline_file(build_repo, capsys):
    """LOCATOR-6: no baseline table or ceiling turns it off."""
    repo = build_repo(only_test('page.get_by_text("Abbrechen")\n', {BASELINE_PATH: "[literal_text]\n[size]\n"}))
    assert (door_in(repo, *FILES), len(found(repo))) == (1, 1)
    capsys.readouterr()


@pytest.mark.parametrize(
    "files",
    [UNCOVERED, {}, {"app/pyproject.toml": PYPROJECT}],
    ids=["no extra", "no pyproject", "a nested project"],
)
def test_a_repository_that_is_not_covered_gets_no_row(build_repo, files):
    """LOCATOR-9: no declaration, no verdict."""
    repo = build_repo({**files, f"{CATALOGS}/de.po": SOURCE, TEST_FILE: 'page.get_by_text("Abbrechen")\n'})
    assert Rule.LOCATOR_TEXT not in {verdict.rule for area in check_result(repo).areas for verdict in area.verdicts}


def test_a_covered_repository_without_catalogs_or_tests_is_clean(build_repo):
    result = check_result(build_repo({"pyproject.toml": PYPROJECT}))
    verdict = next(verdict for area in result.areas for verdict in area.verdicts if verdict.rule is Rule.LOCATOR_TEXT)
    assert (list(verdict.findings), verdict.refusal) == ([], None)


def test_the_extent_is_the_number_of_test_files_read(build_repo):
    result = check_result(build_repo({**COVERED, "tests/test_a.py": "x = 1\n", "tests/test_b.py": "y = 2\n", "src/c.py": "z = 3\n"}))
    verdict = next(verdict for area in result.areas for verdict in area.verdicts if verdict.rule is Rule.LOCATOR_TEXT)
    assert (verdict.extent, verdict.measured_only) == (2, False)


def test_the_catalogs_directory_a_table_declares_is_read(build_repo):
    table = '[tool.design-catalogs]\ncatalogs = "app/words"\n'
    repo = build_repo(
        {
            "pyproject.toml": PYPROJECT + table,
            "app/words/de.po": SOURCE,
            f"{CATALOGS}/de.po": catalog("de", ("other.key", "Woanders", "")),
            TEST_FILE: 'page.get_by_text("Abbrechen")\npage.get_by_text("Woanders")\n',
        }
    )
    assert found(repo) == [(TEST_FILE, 1, cancel_fact())]


def test_a_catalog_in_a_subdirectory_of_the_catalogs_directory_is_not_read(build_repo):
    repo = build_repo({**COVERED, f"{CATALOGS}/old/fr.po": catalog("fr", ("x.y", "Zurück", "")), TEST_FILE: 'page.get_by_text("Zurück")\n'})
    assert found(repo) == []


def test_a_malformed_catalog_is_exit_two_naming_the_file(build_repo, capsys):
    """LOCATOR-10: design's strict reading refuses it with `name:line`."""
    repo = build_repo(only_test('page.get_by_text("Abbrechen")\n', {f"{CATALOGS}/en.po": 'msgid "unclosed\n'}))
    code, out = files_run(capsys, repo)
    assert code == 2
    assert f"{refusal_row(Area.FILES)}{CATALOGS}/en.po:" in out


def test_a_catalog_that_is_not_utf_8_is_exit_two_naming_the_file(build_repo, capsys):
    repo = build_repo(only_test("x = 1\n"))
    (repo / CATALOGS / "en.po").write_bytes(b'msgid "\xff"\nmsgstr ""\n')
    code, out = files_run(capsys, repo)
    assert code == 2
    assert f"{CATALOGS}/en.po" in out


@pytest.mark.parametrize(
    "table",
    ['[tool.design-catalogs]\ncatalogs = "/abs"\n', "[tool.design-catalogs]\npython = 3\n", '[tool.design-catalogs]\nunknown = "x"\n'],
    ids=["absolute", "python", "unknown key"],
)
def test_a_table_design_refuses_is_exit_two_naming_the_pyproject(build_repo, capsys, table):
    """LOCATOR-10, CATALOG-4."""
    repo = build_repo({**COVERED, "pyproject.toml": PYPROJECT + table, TEST_FILE: "x = 1\n"})
    code, out = files_run(capsys, repo)
    assert code == 2
    assert f"{refusal_row(Area.FILES)}pyproject.toml: tool.design-catalogs" in out
    assert Rule.LOCATOR_TEXT in refusals(repo)
    assert Rule.LITERAL_TEXT not in refusals(repo)


def test_a_catalog_symlinked_out_of_the_repository_is_exit_two_and_never_read(build_repo, capsys, elsewhere):
    """LOCATOR-10: the reader stays inside the checkout."""
    repo = build_repo(only_test("x = 1\n"))
    outside = elsewhere / "outside.po"
    outside.write_text(catalog("en", ("outside.key", "Draußen", "")))
    (repo / CATALOGS / "en.po").unlink()
    (repo / CATALOGS / "en.po").symlink_to(outside)
    code, out = files_run(capsys, repo)
    assert code == 2
    assert f"{CATALOGS}/en.po resolves outside the repository" in out
    assert "Draußen" not in out


@pytest.mark.parametrize(
    ("content", "fact"),
    [
        (b"def broken(:\n", "tests/test_dialog.py"),
        (b"x = '\xff'\n", "tests/test_dialog.py"),
    ],
    ids=["a syntax error", "not UTF-8"],
)
def test_a_test_file_python_cannot_read_is_exit_two_naming_it(build_repo, capsys, content, fact):
    """LOCATOR-10."""
    repo = build_repo(only_test("x = 1\n"))
    (repo / TEST_FILE).write_bytes(content)
    code, _ = files_run(capsys, repo)
    assert (code, fact in refusals(repo)[Rule.LOCATOR_TEXT]) == (2, True)


def files_run(capsys, repo):
    """The exit and the whole output of `quality check files` over `repo`."""
    code = door_in(repo, *FILES)
    captured = capsys.readouterr()
    return code, captured.out + captured.err
