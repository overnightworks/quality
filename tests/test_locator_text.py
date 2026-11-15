from dataclasses import dataclass

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


@dataclass(frozen=True)
class Scenario:
    """A covered app: a German source catalog, an English one and a test file, with the generator's declaration."""

    directory: str = "frontend/src/lib/catalogs"
    test_file: str = "tests/test_dialog.py"
    script_file: str = "src/dialog.test.ts"

    def pyproject(self, *, covered: bool = True, table: str = "") -> str:
        extra = "[catalogs]" if covered else ""
        return (
            '[project]\nname = "app"\nversion = "0.1"\nrequires-python = ">=3.14"\n'
            f'dependencies = ["overnightworks-design{extra}>=1"]\n{table}'
        )

    def catalog(self, language: str, *entries: tuple[str, str, str], extra: str = "") -> str:
        """A catalog file: the entries are (msgctxt, msgid, msgstr)."""
        header = f'msgid ""\nmsgstr ""\n"Language: {language}\\n"\n\n'
        body = "".join(f'msgctxt "{key}"\nmsgid "{text}"\nmsgstr "{translation}"\n\n' for key, text, translation in entries)
        return header + body + extra

    def catalog_path(self, language: str) -> str:
        return f"{self.directory}/{language}.po"

    def source_catalog(self) -> str:
        return self.catalog(
            "de",
            ("dialog.cancel", "Abbrechen", ""),
            ("dialog.title", "Einstellungen", ""),
            ("dialog.done", "Fertig.", ""),
            ("dialog.dont", "Don't", ""),
            ("dialog.size", "Größe", ""),
        )

    def covered(self) -> dict[str, str]:
        english = self.catalog("en", ("dialog.cancel", "Abbrechen", "Cancel"), ("dialog.title", "Einstellungen", "Settings"))
        return {
            "pyproject.toml": self.pyproject(),
            self.catalog_path("de"): self.source_catalog(),
            self.catalog_path("en"): english,
        }

    def only_test(self, source: str, extra: dict[str, str] | None = None) -> dict[str, str]:
        return {**self.covered(), self.test_file: source, **(extra or {})}

    def only_script(self, source: str, name: str | None = None) -> dict[str, str]:
        return {**self.covered(), name or self.script_file: source}

    def not_covered(self, kind: str) -> dict[str, str]:
        """The pyproject files of a repository the rule does not cover, by what keeps it out."""
        return {
            "no extra": {"pyproject.toml": self.pyproject(covered=False)},
            "no pyproject": {},
            "a nested project": {"app/pyproject.toml": self.pyproject()},
        }[kind]


@pytest.fixture
def scenario() -> Scenario:
    return Scenario()


def found(repo):
    """Each site the rule found over `repo` as (path, line, fact)."""
    return [(finding.path, finding.line, finding.fact) for finding in rule_findings(repo, Rule.LOCATOR_TEXT)]


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
        pytest.param('page.get_by_text("x").filter(has_not_text="Abbrechen")\n', "Abbrechen", "dialog.cancel", id="has_not_text"),
        pytest.param('expect(button).to_have_accessible_description("Abbrechen")\n', "Abbrechen", "dialog.cancel", id="accessible description"),
    ],
)
def test_a_catalog_text_in_a_locator_position_of_a_python_test_is_a_finding(build_repo, scenario, source, text, key):
    """LOCATOR-1 to LOCATOR-5: the fact names the text and the first key; the line is the site's."""
    repo = build_repo(scenario.only_test(source))
    ((path, line, fact),) = found(repo)
    assert (path, line, fact) == (scenario.test_file, source.count("\n"), f"'{text}' is the catalog text of {key}")


def test_a_finding_reads_as_the_rule_words_it_and_the_run_is_red(build_repo, scenario, capsys):
    """LOCATOR-6: one row per site with the event word and the fix."""
    repo = build_repo(scenario.only_test('page.get_by_text("Abbrechen")\n'))
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
        'page.get_by_role("Abbrechen")\n',
        'page.getByRole(role="Abbrechen")\n',
        'page.get_by_test_id("Abbrechen")\n',
        'page.getByTestId("Abbrechen")\n',
        'page.get_by_text("^Abbrechen$")\n',
        'expect(button).to_have_attribute("data-state", "Abbrechen")\n',
        'expect(button).to_have_attribute(name="data-state", value="Abbrechen")\n',
        'expect(button).to_have_attribute(attribute, "Abbrechen")\n',
    ],
    ids=[
        "assert", "to_be", "another text", "f-string", "longer text", "constant bound twice",
        "unanchored regex", "unanchored regex with flags", "unanchored regex constant", "dynamic name", "not a locator", "attribute name", "locator selector",
        "no string", "constant extended", "constant unpacked", "constant rebound by an import", "constant rebound by an alias", "constant rebound by a loop", "constant rebound by a function", "constant rebound by a handler", "subscripted function",
        "role argument", "role keyword", "test id", "camelCase test id",
        "a plain string is no regex", "data attribute", "data attribute keyword", "attribute name unknown",
    ],
)
def test_scenario_data_and_every_other_value_is_no_site(build_repo, scenario, source):
    """LOCATOR-7: only the named positions with a whole-value equal text count."""
    assert found(build_repo(scenario.only_test(source))) == []


def test_a_text_two_keys_share_names_the_first_and_counts_the_rest(build_repo, scenario):
    """LOCATOR-6: `, and N more keys`, the keys in their sort order."""
    shared = scenario.catalog("de", ("dialog.cancel", "Abbrechen", ""), ("button.cancel", "Abbrechen", ""), ("menu.cancel", "Abbrechen", ""))
    repo = build_repo(scenario.only_test('page.get_by_text("Abbrechen")\n', {scenario.catalog_path("de"): shared}))
    assert found(repo)[0][2] == "'Abbrechen' is the catalog text of button.cancel, and 2 more keys"


def test_a_text_one_other_key_shares_says_one_more_keys(build_repo, scenario):
    """LOCATOR-6: the word is `keys` whatever N is."""
    shared = scenario.catalog("de", ("a.cancel", "Abbrechen", ""), ("b.cancel", "Abbrechen", ""))
    repo = build_repo({**scenario.covered(), scenario.catalog_path("de"): shared, scenario.catalog_path("en"): scenario.catalog("en"), scenario.test_file: 'page.get_by_text("Abbrechen")\n'})
    assert found(repo)[0][2] == "'Abbrechen' is the catalog text of a.cancel, and 1 more keys"


def test_a_fuzzy_entry_gives_its_msgid_and_no_translation(build_repo, scenario):
    """LOCATOR-4: gettext does not show a fuzzy msgstr, so only the msgid is surface text."""
    fuzzy = scenario.catalog("en", extra='#, fuzzy\nmsgctxt "dialog.cancel"\nmsgid "Abbrechen"\nmsgstr "Cancel"\n')
    repo = build_repo(scenario.only_test('page.get_by_text("Cancel")\npage.get_by_text("Abbrechen")\n', {scenario.catalog_path("en"): fuzzy}))
    assert found(repo) == [(scenario.test_file, 2, cancel_fact())]


def test_an_obsolete_entry_is_no_catalog_text(build_repo, scenario):
    obsolete = scenario.catalog("de", extra='#~ msgctxt "old.key"\n#~ msgid "Veraltet"\n#~ msgstr ""\n')
    assert found(build_repo(scenario.only_test('page.get_by_text("Veraltet")\n', {scenario.catalog_path("de"): obsolete}))) == []


def test_every_site_of_every_test_file_is_one_finding_in_path_and_line_order(build_repo, scenario):
    repo = build_repo(
        {
            **scenario.covered(),
            "tests/test_b.py": 'page.get_by_text("Abbrechen")\n',
            "tests/test_a.py": 'x = 1\npage.get_by_text("Einstellungen")\npage.get_by_text("Abbrechen")\n',
        }
    )
    assert [(path, line) for path, line, _ in found(repo)] == [("tests/test_a.py", 2), ("tests/test_a.py", 3), ("tests/test_b.py", 1)]


@pytest.mark.parametrize("name", ["tests/test_a.py", "app/tests/test_a.py", "tests/unit/helpers.py"])
def test_a_python_file_under_a_tests_directory_is_read(build_repo, scenario, name):
    """LOCATOR-8."""
    assert len(found(build_repo({**scenario.covered(), name: 'page.get_by_text("Abbrechen")\n'}))) == 1


@pytest.mark.parametrize("name", ["src/helpers.py", "test_a.py", "tests/a.txt", "src/lib/app.ts", "x.test.tsx", "x.test.mjs", "x.test.json", "tests/a.js"])
def test_other_files_are_not_read(build_repo, scenario, name):
    """LOCATOR-8: Python files under `tests/`, and the TypeScript and JavaScript test files named below."""
    assert found(build_repo({**scenario.covered(), name: 'page.get_by_text("Abbrechen")\npage.getByText("Abbrechen")\n'})) == []


@pytest.mark.parametrize("name", ["x.test.ts", "x.spec.js", "x.test.browser.ts", "x.spec.integration.js", "src/a.spec.ts", "e2e/helpers.ts", "app/e2e/deep/helpers.js", "test-utils/a.ts", "src/test-utils/b.js"])
def test_a_script_test_file_is_read(build_repo, scenario, name):
    """LOCATOR-8: a tracked `*.ts` or `*.js` named `*.test.*` or `*.spec.*`, or under `e2e/` or `test-utils/`."""
    assert found(build_repo(scenario.only_script("page.getByText('Abbrechen');\n", name))) == [(name, 1, cancel_fact())]


def test_a_site_stays_red_beside_a_baseline_file(build_repo, scenario):
    """LOCATOR-6: no baseline table or ceiling turns it off."""
    repo = build_repo(scenario.only_test('page.get_by_text("Abbrechen")\n', {BASELINE_PATH: "[literal_text]\n[size]\n"}))
    assert (door_in(repo, CHECK_WORD, Area.FILES.value), len(found(repo))) == (1, 1)


@pytest.mark.parametrize("kind", ["no extra", "no pyproject", "a nested project"])
def test_a_repository_that_is_not_covered_gets_no_row(build_repo, scenario, kind):
    """LOCATOR-9: no declaration, no verdict."""
    repo = build_repo({**scenario.not_covered(kind), scenario.catalog_path("de"): scenario.source_catalog(), scenario.test_file: 'page.get_by_text("Abbrechen")\n'})
    assert Rule.LOCATOR_TEXT not in {verdict.rule for area in check_result(repo).areas for verdict in area.verdicts}


def test_a_covered_repository_without_catalogs_or_tests_is_clean(build_repo, scenario):
    result = check_result(build_repo({"pyproject.toml": scenario.pyproject()}))
    verdict = next(verdict for area in result.areas for verdict in area.verdicts if verdict.rule is Rule.LOCATOR_TEXT)
    assert (list(verdict.findings), verdict.refusal) == ([], None)


def test_the_extent_is_the_number_of_test_files_read(build_repo, scenario):
    result = check_result(build_repo({**scenario.covered(), "tests/test_a.py": "x = 1\n", "tests/test_b.py": "y = 2\n", "src/c.py": "z = 3\n"}))
    verdict = next(verdict for area in result.areas for verdict in area.verdicts if verdict.rule is Rule.LOCATOR_TEXT)
    assert (verdict.extent, verdict.measured_only) == (2, False)


def test_the_catalogs_directory_a_table_declares_is_read(build_repo, scenario):
    table = '[tool.design-catalogs]\ncatalogs = "app/words"\n'
    repo = build_repo(
        {
            "pyproject.toml": scenario.pyproject(table=table),
            "app/words/de.po": scenario.source_catalog(),
            scenario.catalog_path("de"): scenario.catalog("de", ("other.key", "Woanders", "")),
            scenario.test_file: 'page.get_by_text("Abbrechen")\npage.get_by_text("Woanders")\n',
        }
    )
    assert found(repo) == [(scenario.test_file, 1, cancel_fact())]


def test_a_catalog_in_a_subdirectory_of_the_catalogs_directory_is_not_read(build_repo, scenario):
    repo = build_repo({**scenario.covered(), f"{scenario.directory}/old/fr.po": scenario.catalog("fr", ("x.y", "Zurück", "")), scenario.test_file: 'page.get_by_text("Zurück")\n'})
    assert found(repo) == []


def test_a_malformed_catalog_is_exit_two_naming_the_file(build_repo, scenario, capsys):
    """LOCATOR-10: design's strict reading refuses it with `name:line`."""
    repo = build_repo(scenario.only_test('page.get_by_text("Abbrechen")\n', {scenario.catalog_path("en"): 'msgid "unclosed\n'}))
    code, out = files_run(capsys, repo)
    assert code == 2
    assert f"{refusal_row(Area.FILES)}{scenario.catalog_path('en')}:" in out


def test_a_catalog_that_is_not_utf_8_is_exit_two_naming_the_file(build_repo, scenario, capsys):
    repo = build_repo(scenario.only_test("x = 1\n"))
    (repo / scenario.catalog_path("en")).write_bytes(b'msgid "\xff"\nmsgstr ""\n')
    code, out = files_run(capsys, repo)
    assert code == 2
    assert scenario.catalog_path("en") in out


@pytest.mark.parametrize(
    "table",
    ['[tool.design-catalogs]\ncatalogs = "/abs"\n', "[tool.design-catalogs]\npython = 3\n", '[tool.design-catalogs]\nunknown = "x"\n'],
    ids=["absolute", "python", "unknown key"],
)
def test_a_table_design_refuses_is_exit_two_naming_the_pyproject(build_repo, scenario, capsys, table):
    """LOCATOR-10, CATALOG-4."""
    repo = build_repo({**scenario.covered(), "pyproject.toml": scenario.pyproject(table=table), scenario.test_file: "x = 1\n"})
    code, out = files_run(capsys, repo)
    assert code == 2
    assert f"{refusal_row(Area.FILES)}pyproject.toml: tool.design-catalogs" in out
    assert Rule.LOCATOR_TEXT in refusals(repo)
    assert Rule.LITERAL_TEXT not in refusals(repo)


def test_a_catalog_symlinked_out_of_the_repository_is_exit_two_and_never_read(build_repo, scenario, capsys, elsewhere):
    """LOCATOR-10: the reader stays inside the checkout."""
    repo = build_repo(scenario.only_test("x = 1\n"))
    outside = elsewhere / "outside.po"
    outside.write_text(scenario.catalog("en", ("outside.key", "Draußen", "")))
    (repo / scenario.catalog_path("en")).unlink()
    (repo / scenario.catalog_path("en")).symlink_to(outside)
    code, out = files_run(capsys, repo)
    assert code == 2
    assert f"{scenario.catalog_path('en')} resolves outside the repository" in out
    assert "Draußen" not in out


@pytest.mark.parametrize("content", [b"def broken(:\n", b"x = '\xff'\n"], ids=["a syntax error", "not UTF-8"])
def test_a_test_file_python_cannot_read_is_exit_two_naming_it(build_repo, scenario, capsys, content):
    """LOCATOR-10."""
    repo = build_repo(scenario.only_test("x = 1\n"))
    (repo / scenario.test_file).write_bytes(content)
    code, _ = files_run(capsys, repo)
    assert (code, scenario.test_file in refusals(repo)[Rule.LOCATOR_TEXT]) == (2, True)


def test_a_very_long_operator_chain_is_read_and_the_sites_after_it_are_found(build_repo, scenario, capsys):
    """LOCATOR-10: a tree deeper than the interpreter recurses does not refuse the rule."""
    chain = "X = " + " + ".join(['"a"'] * 8000)
    repo = build_repo(scenario.only_test(f'{chain}\npage.get_by_text("Abbrechen")\n'))
    code, out = files_run(capsys, repo)
    assert (code, cancel_fact() in out) == (1, True)

@pytest.mark.parametrize(
    ("source", "text", "key"),
    [
        pytest.param("page.getByRole('button', { name: 'Abbrechen' });\n", "Abbrechen", "dialog.cancel", id="getByRole name"),
        pytest.param('page.getByRole("button", { name: "Abbrechen", exact: true });\n', "Abbrechen", "dialog.cancel", id="double quotes and options"),
        pytest.param("page.getByText('Einstellungen');\n", "Einstellungen", "dialog.title", id="getByText"),
        pytest.param("screen.getByLabelText('Abbrechen');\n", "Abbrechen", "dialog.cancel", id="a wrapper of testing-library"),
        pytest.param("page.getByRoleButton('Abbrechen');\n", "Abbrechen", "dialog.cancel", id="an app wrapper"),
        pytest.param("page.getByText('Cancel');\n", "Cancel", "dialog.cancel", id="english msgstr"),
        pytest.param("page.locator('li').filter({ hasText: 'Abbrechen' });\n", "Abbrechen", "dialog.cancel", id="hasText"),
        pytest.param("page.locator('li').filter({ hasNotText: 'Abbrechen' });\n", "Abbrechen", "dialog.cancel", id="hasNotText"),
        pytest.param("expect(button).toHaveText('Abbrechen');\n", "Abbrechen", "dialog.cancel", id="toHaveText"),
        pytest.param("expect(button).toHaveAccessibleName('Abbrechen');\n", "Abbrechen", "dialog.cancel", id="toHaveAccessibleName"),
        pytest.param("expect(button).toHaveAccessibleDescription('Abbrechen');\n", "Abbrechen", "dialog.cancel", id="toHaveAccessibleDescription"),
        pytest.param("expect(page).toHaveTitle('Einstellungen');\n", "Einstellungen", "dialog.title", id="toHaveTitle"),
        pytest.param("expect(rows).toHaveText(['Abbrechen', 'other']);\n", "Abbrechen", "dialog.cancel", id="array of texts"),
        pytest.param("expect(button).toHaveAttribute('aria-label', 'Abbrechen');\n", "Abbrechen", "dialog.cancel", id="read attribute value"),
        pytest.param("page.getByText(`Abbrechen`);\n", "Abbrechen", "dialog.cancel", id="hole-free template"),
        pytest.param("const LABEL = 'Abbrechen';\npage.getByText(LABEL);\n", "Abbrechen", "dialog.cancel", id="top-level const"),
        pytest.param("export const LABEL = 'Abbrechen'\npage.getByText(LABEL)\n", "Abbrechen", "dialog.cancel", id="exported const without semicolon"),
        pytest.param("const LABEL = 'Abbrechen' as const;\npage.getByText(LABEL);\n", "Abbrechen", "dialog.cancel", id="const as const"),
        pytest.param("const LABEL = `Abbrechen`;\npage.getByRole('button', { name: LABEL });\n", "Abbrechen", "dialog.cancel", id="template const as name"),
        pytest.param("page.getByText(/^Abbrechen$/);\n", "Abbrechen", "dialog.cancel", id="anchored regex"),
        pytest.param("page.getByText(/^Abbrechen$/i);\n", "Abbrechen", "dialog.cancel", id="anchored regex with a flag"),
        pytest.param("page.getByText(/^Fertig\\.$/);\n", "Fertig.", "dialog.done", id="anchored regex with an escape"),
        pytest.param("page.getByText(new RegExp('^Abbrechen$'));\n", "Abbrechen", "dialog.cancel", id="new RegExp"),
        pytest.param("page.getByText(RegExp(\"^Abbrechen$\", 'i'));\n", "Abbrechen", "dialog.cancel", id="RegExp with a flag argument"),
        pytest.param("page.getByText('Don\\'t');\n", "Don't", "dialog.dont", id="escaped quote"),
        pytest.param("page.getByText(\"Don't\");\n", "Don't", "dialog.dont", id="quote of the other kind"),
        pytest.param("page.getByText('Gr\\u00f6\\u{df}e');\n", "Größe", "dialog.size", id="unicode escapes"),
        pytest.param("page.getByText('Gr\\xf6\\xdfe');\n", "Größe", "dialog.size", id="hex escapes"),
        pytest.param("page.getByRole('button', { name: label('Abbrechen') });\n", "Abbrechen", "dialog.cancel", id="nested helper"),
        pytest.param("const m = `x ${page.getByText('Abbrechen')} y`;\n", "Abbrechen", "dialog.cancel", id="inside a template hole"),
    ],
)
def test_a_catalog_text_in_a_locator_position_of_a_script_test_is_a_finding(build_repo, scenario, source, text, key):
    """LOCATOR-1 to LOCATOR-5: the same positions as Python's, in TypeScript and JavaScript syntax."""
    repo = build_repo(scenario.only_script(source))
    ((path, line, fact),) = found(repo)
    assert (path, line, fact) == (scenario.script_file, source.count("\n"), f"'{text}' is the catalog text of {key}")


def test_a_line_continuation_in_a_string_is_no_part_of_its_text_and_the_line_is_where_it_starts(build_repo, scenario):
    """LOCATOR-2, LOCATOR-6."""
    source = "x = 1;\npage.getByText('Abbre\\\nchen');\n"
    assert found(build_repo(scenario.only_script(source))) == [(scenario.script_file, 2, cancel_fact())]


def test_each_literal_of_a_multi_line_call_is_a_finding_at_its_own_line(build_repo, scenario):
    """LOCATOR-6: the line is the literal's, not the call's."""
    source = "page.getByRole(\n  'button',\n  {\n    name: 'Abbrechen',\n  },\n);\nexpect(x).toHaveText(\n  'Einstellungen',\n);\n"
    assert [line for _, line, _ in found(build_repo(scenario.only_script(source)))] == [4, 8]

@pytest.mark.parametrize(
    "source",
    [
        "page.getByText(`Abbrechen ${n}`);\n",
        "page.getByText(/Abbrechen/);\n",
        "page.getByText(/^Abbrechen/);\n",
        "page.getByText(/Abbrechen$/);\n",
        "page.getByText(/^Abbrechen$|^Einstellungen$/);\n",
        "page.getByText(new RegExp('Abbrechen'));\n",
        "const LABEL = 'Abbrechen';\npage.getByText(new RegExp(LABEL));\n",
        "page.getByText('^Abbrechen$');\n",
        "// page.getByText('Abbrechen');\n",
        "/* page.getByText('Abbrechen');\n   page.getByText('Abbrechen'); */\n",
        "page.getByText(words['Abbrechen']);\n",
        "page.getByText(ok ? 'Abbrechen' : 'x');\n",
        "page.getByText(ok ? 'x' : 'Abbrechen');\n",
        "page.getByText(text => text === 'Abbrechen');\n",
        "page.getByText('Abbrechen' + suffix);\n",
        "page.getByRole('Abbrechen');\n",
        "page.getByRole('button', { name: 'Speichern' });\n",
        "page.getByTestId('Abbrechen');\n",
        "expect(value).toBe('Abbrechen');\n",
        "expect(button).toHaveAttribute('data-state', 'Abbrechen');\n",
        "expect(button).toHaveAttribute(attribute, 'Abbrechen');\n",
        "expect(button).toHaveAttribute('Abbrechen', 'x');\n",
        "expect(button).toHaveValue('Abbrechen');\n",
        "const x = 'Abbrechen';\nlet LABEL = 'Abbrechen';\npage.getByText(LABEL);\n",
        "var LABEL = 'Abbrechen';\npage.getByText(LABEL);\n",
        "const LABEL = 'Abbrechen';\nconst LABEL = 'Other';\npage.getByText(LABEL);\n",
        "const LABEL = 'Abbrechen';\nfunction LABEL() {}\npage.getByText(LABEL);\n",
        "const LABEL = 'Abbrechen';\nclass LABEL {}\npage.getByText(LABEL);\n",
        "test('x', () => {\n  const LABEL = 'Abbrechen';\n  page.getByText(LABEL);\n});\n",
        "const LABEL = 'Abbrechen' + 'x';\npage.getByText(LABEL);\n",
        "const LABEL = 'Abbrechen',\n  OTHER = 'x';\npage.getByText(LABEL);\n",
        "const LABEL: string = 'Abbrechen';\npage.getByText(LABEL);\n",
        "page.getByText(name);\n",
        "page.locator('Abbrechen');\n",
        "page.getByteTimeDomainData('Abbrechen');\n",
        "page.getByText(`${label}`);\n",
    ],
    ids=[
        "template with a hole", "unanchored regex", "regex anchored at the start only", "regex anchored at the end only",
        "alternation", "unanchored RegExp", "RegExp of a constant", "a plain string is no regex", "line comment", "block comment",
        "index", "ternary branch", "ternary else branch", "arrow comparison", "concatenation", "role", "another text", "test id", "toBe",
        "data attribute", "attribute name unknown", "attribute name is the text", "toHaveValue", "let", "var", "const declared twice",
        "const and function", "const and class", "function-local const", "const concatenation", "const list", "typed const",
        "dynamic name", "locator selector", "not a locator", "hole alone",
    ],
)
def test_scenario_data_and_every_other_script_value_is_no_site(build_repo, scenario, source):
    """LOCATOR-7: only the named positions with a whole-value equal text count."""
    assert found(build_repo(scenario.only_script(source))) == []


@pytest.mark.parametrize(
    "prefix",
    [
        "const r = /'/;\n",
        "// don't\n",
        "const r = /[/]/;\n",
        "const half = total / 2;\nconst third = total / 3;\n",
        "if (ok) { x = a / b; }\nconst y = (a + b) / 2 / 'x'.length;\n",
        "const text = `a ${b / 2} c`;\n",
        "const s = '`';\n",
        "const html = `<a href=\"x\">${f({ a: 1 })}</a>`;\n",
        "}\n}\n",
        "foo((({\n",
        "﻿#!/usr/bin/env node\nclass A { #x = 1; }\n",
        "const é = 'ü€';\n",
        "const x = 1_000 / 2;\n",
        "return /x/.test(y);\n",
        "/'/.test(y);\n",
        "const text = `${a} and ${b} and ${'}'}`;\n",
        "const s = `${value}${/'/.test(value)}`;\n",
        "const nested = " + "[" * 20000 + "]" * 20000 + ";\n",
    ],
    ids=[
        "quote in a regex", "quote in a comment", "slash in a class", "divisions", "division after parentheses",
        "division in a template hole", "backtick in a string", "braces in a template hole", "stray braces", "unbalanced brackets", "private field and hashbang",
        "non-ascii", "number separator", "regex after return", "regex first in the file", "template with several holes", "regex starting a later hole", "deep nesting",
    ],
)
def test_a_valid_file_is_read_to_its_last_site_whatever_comes_first(build_repo, scenario, prefix):
    """LOCATOR-10, LOCATOR-11: nothing here refuses the rule, and the site after it is found."""
    source = prefix + "page.getByText('Abbrechen');\n"
    assert found(build_repo(scenario.only_script(source))) == [(scenario.script_file, source.count("\n"), cancel_fact())]


@pytest.mark.parametrize("terminator", ["\r", "\r\n", "\u2028"])
def test_a_line_is_counted_by_every_terminator_the_lexer_knows(build_repo, scenario, terminator):
    """LOCATOR-11: a literal's line is the line it starts on, whatever ends the lines before it."""
    repo = build_repo(scenario.only_script("x = 1\n"))
    source = terminator.join(["x = 1;", "// note", "page.getByText('Abbrechen');", ""])
    (repo / scenario.script_file).write_bytes(source.encode())
    assert found(repo) == [(scenario.script_file, 3, cancel_fact())]


@pytest.mark.parametrize(
    ("content", "line"),
    [
        pytest.param(b"x = 1\nconst a = 'abc\nfoo();\n", 2, id="string reaching a newline"),
        pytest.param(b"x = 1\nconst a = \"abc", 2, id="string reaching the end"),
        pytest.param(b"x = 1\nconst a = 'abc\\\n", 2, id="continued string reaching the end"),
        pytest.param(b"x = 1\ny = 2\nconst a = `abc", 3, id="unclosed template"),
        pytest.param(b"x = 1\nconst a = `abc ${b", 2, id="unclosed hole"),
        pytest.param(b"x = 1\nconst a = `abc ${ {b} ", 2, id="hole with an unclosed brace"),
        pytest.param(b"x = 1\nconst r = /abc\nfoo();\n", 2, id="regex reaching a newline"),
        pytest.param(b"x = 1\nconst r = /abc", 2, id="regex reaching the end"),
        pytest.param(b"x = 1\nconst r = /[abc/;\n", 2, id="regex class reaching the end of the line"),
        pytest.param(b"x = 1\n\n/* never closed\nfoo();\n", 3, id="unclosed block comment"),
    ],
)
def test_a_script_test_file_that_cannot_be_read_is_exit_two_naming_it_and_the_line(build_repo, scenario, capsys, content, line):
    """LOCATOR-10: the refusal names the file and the line of the construct that does not close."""
    repo = build_repo(scenario.only_script("x = 1\n"))
    (repo / scenario.script_file).write_bytes(content)
    code, _ = files_run(capsys, repo)
    reason = refusals(repo)[Rule.LOCATOR_TEXT]
    assert (code, scenario.script_file in reason, f"line {line}" in reason) == (2, True, True)


def test_a_script_test_file_that_is_not_utf_8_is_exit_two_naming_it(build_repo, scenario, capsys):
    """LOCATOR-10."""
    repo = build_repo(scenario.only_script("x = 1\n"))
    (repo / scenario.script_file).write_bytes(b"const a = '\xff';\n")
    code, _ = files_run(capsys, repo)
    assert (code, scenario.script_file in refusals(repo)[Rule.LOCATOR_TEXT]) == (2, True)


def files_run(capsys, repo):
    """The exit and the whole output of `quality check files` over `repo`."""
    code = door_in(repo, CHECK_WORD, Area.FILES.value)
    captured = capsys.readouterr()
    return code, captured.out + captured.err
