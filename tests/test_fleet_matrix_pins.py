"""The `quality report` release matrix."""

import os
import re
import subprocess

import pytest

from quality.checkout import Call
from quality.fleet import (
    COLUMNS,
    MATRIX_COLUMNS,
    Declared,
    Gate,
    gate_release,
    pin_cell,
    rule_cell,
)
from quality.rules import Rule, RuleRelease, Switch, SwitchChange
from tests.support.fleet import (
    FIRST_RELEASE_COMMIT,
    FIRST_RELEASES,
    LITERAL_TEXT,
    NEWEST,
    RELEASES,
    UNRELEASED,
)
from tests.support.git_repositories import GIT_IDENTITY


def gate_call(ref: str | None) -> Call:
    """One job of `ci.yml` calling the gate at `ref`, or the repository's own file, its line naming no release."""
    return Call(ref, ".github/workflows/ci.yml", None)


def test_the_repositorys_own_workflow_file_is_self_only_in_the_gates_repository():
    """FLEET-8: `./` and `$/` name the gate's own file only where the repository is the gate's."""
    own = (gate_call(None),)
    assert pin_cell(own, "no gate", True, RELEASES) == "self"
    assert pin_cell(own, "no gate", False, RELEASES) == "no gate"


@pytest.mark.integration
def test_a_pipe_in_a_label_is_escaped_and_the_row_keeps_its_columns(fleet_report, public, remotes):
    """FLEET-44, FLEET-95: a tracked file named `a|b.py` that cannot be read puts a `|` into a reason, which both tables
    show with the `|` escaped."""
    name = public("pipe", {"docs/a.md": "text\n"})
    os.symlink("docs", remotes / name / "a|b.py")
    subprocess.run(["git", "-C", str(remotes / name), "add", "a|b.py"], check=True)
    subprocess.run(["git", "-C", str(remotes / name), *GIT_IDENTITY, "commit", "-q", "-m", "link"], check=True)
    _, output = fleet_report(name)
    lines = [line for line in output.splitlines() if line.startswith("| o/pipe |")][:2]
    assert [r"a\|b.py" in line for line in lines] == [True, True]
    assert [len(re.split(r"(?<!\\)\|", line.strip("|"))) for line in lines] == [len(COLUMNS), len(MATRIX_COLUMNS)]


CLONE_RELEASE = "d" * 40


CLONE_TABLE_PIN = "e" * 40


CLONE_RELEASES = {**FIRST_RELEASES, "v0.4.0": CLONE_RELEASE, "v0.5.0": CLONE_TABLE_PIN}


def declared(**fields) -> Declared:
    """A clone that declares nothing, with the given fields set."""
    return Declared(**{"settings": None, "has_baseline": False, "has_clone_table": False, "has_literal_table": False, "has_spec_table": False, "has_import_config": False, "has_adapter_contract": False, "has_judged_project": False,
                       "declares_catalogs": False, "unreadable": frozenset(), **fields})


CLONE = Rule.CLONE.release


@pytest.mark.parametrize("has_clone_table", [False, True], ids=["no clone table", "a clone table"])
@pytest.mark.parametrize(
    ("pin", "words"),
    [
        (CLONE_RELEASE, "report-only"),
        (NEWEST, "not in v0.3.1"),
    ],
    ids=["a pin at the clone release", "an older tag"],
)
def test_a_pinned_gate_reports_clones_whatever_the_clone_table_says(pin, words, has_clone_table):
    """FLEET-17, FLEET-18: no tag before the clone-table release enforces clone."""
    release = gate_release((gate_call(pin),), False, CLONE_RELEASES)
    assert rule_cell(CLONE, release, declared(has_clone_table=has_clone_table)) == words


@pytest.mark.parametrize(
    ("has_clone_table", "words"), [(True, "enforced"), (False, "report-only")], ids=["a clone table", "no clone table"]
)
def test_a_gate_pinned_at_the_clone_table_release_enforces_clones_with_a_clone_table(has_clone_table, words):
    """FLEET-17: v0.5.0 is the first tag that enforces the clone table."""
    release = gate_release((gate_call(CLONE_TABLE_PIN),), False, CLONE_RELEASES)
    assert rule_cell(CLONE, release, declared(has_clone_table=has_clone_table)) == words


@pytest.mark.parametrize(
    ("has_clone_table", "words"), [(True, "enforced"), (False, "report-only")], ids=["a clone table", "no clone table"]
)
def test_the_gates_own_repository_enforces_clones_with_a_clone_table(has_clone_table, words):
    """FLEET-17."""
    release = gate_release((gate_call(None),), True, CLONE_RELEASES)
    assert rule_cell(CLONE, release, declared(has_clone_table=has_clone_table)) == words


TEST_LAYOUT = Rule.TEST_LAYOUT.release


@pytest.mark.parametrize(
    ("has_judged_project", "words"), [(True, "enforced"), (False, "no judged project")], ids=["a judged project", "none"]
)
def test_main_enforces_test_layout_only_where_a_project_is_judged(has_judged_project, words):
    """FLEET-20: where the gate knows the rule, the cell says whether any project in the clone is judged."""
    release = gate_release((gate_call(None),), True, CLONE_RELEASES)
    assert rule_cell(TEST_LAYOUT, release, declared(has_judged_project=has_judged_project)) == words


@pytest.mark.parametrize(
    ("pin", "has_judged_project", "words"),
    [("v0.6.1", True, "not in v0.6.1"), ("v0.7.0", True, "enforced"), ("v0.7.0", False, "no judged project")],
    ids=["before the first release", "a judged project", "none"],
)
def test_a_pinned_gate_judges_test_layout_from_the_tag_that_first_ships_it(pin, has_judged_project, words):
    """FLEET-18, FLEET-20: v0.7.0 is the first tag that wires test-layout, for a judged project."""
    tags = {"v0.6.1": "e" * 40, "v0.7.0": "f" * 40}
    release = gate_release((gate_call(tags[pin]),), False, tags)
    assert rule_cell(TEST_LAYOUT, release, declared(has_judged_project=has_judged_project)) == words


@pytest.mark.parametrize("rule_id", ["test-import", "python-floor", "python-security", "python-print"])
def test_a_rule_judging_every_python_project_needs_no_applicability_cell(rule_id):
    """FLEET-20: the rule judges every Python project, so main enforces it with no applicability cell."""
    release = gate_release((gate_call(None),), True, CLONE_RELEASES)
    assert rule_cell(Rule(rule_id).release, release, declared()) == "enforced"


@pytest.mark.parametrize(
    ("rule_id", "before", "first"),
    [
        ("root-layout", "v0.5.1", "v0.6.0"),
        ("file-suffix", "v0.6.1", "v0.7.0"),
        ("script-name", "v0.6.1", "v0.7.0"),
        ("script-header", "v0.6.1", "v0.7.0"),
        ("test-import", "v0.6.1", "v0.7.0"),
        ("python-floor", "v0.7.0", "v0.8.0"),
        ("python-security", "v0.8.0", "v0.9.0"),
        ("python-print", "v0.8.0", "v0.9.0"),
    ],
)
def test_a_pinned_gate_has_a_rule_from_the_tag_that_first_ships_it(rule_id, before, first):
    """FLEET-18: the tags' check wires each rule from its first tag on, unconditionally."""
    tags = {before: "a" * 40, first: "b" * 40}
    rule = Rule(rule_id).release
    assert rule_cell(rule, gate_release((gate_call("a" * 40),), False, tags), declared()) == f"not in {before}"
    assert rule_cell(rule, gate_release((gate_call("b" * 40),), False, tags), declared()) == "enforced"


@pytest.mark.parametrize(("pin", "words"), [("v0.5.0", "enforced"), ("v0.4.0", "report-only")], ids=["the switch", "older"])
def test_a_later_switch_applies_from_the_tag_that_names_it(pin, words):
    """FLEET-17."""
    rule = RuleRelease("future", "v0.1.0", Switch.REPORT_ONLY, (SwitchChange("v0.5.0", Switch.CLONE_TABLE),))
    release = gate_release((gate_call("e" * 40),), False, {**FIRST_RELEASES, pin: "e" * 40})
    assert rule_cell(rule, release, declared(has_clone_table=True)) == words


def test_a_rule_without_a_release_is_not_released_on_a_pinned_gate():
    """FLEET-19."""
    unreleased = RuleRelease("future", None, Switch.REPORT_ONLY)
    release = gate_release((gate_call(NEWEST),), False, FIRST_RELEASES)
    assert rule_cell(unreleased, release, declared()) == "not released"


CATALOG_CHECK = Rule.CATALOG_CHECK.release


CATALOG_RELEASE = CATALOG_CHECK.first_release


CATALOG_PIN = "9" * 40


CATALOG_TAGS = {**FIRST_RELEASES, CATALOG_RELEASE: CATALOG_PIN}


@pytest.mark.parametrize(
    ("declares_catalogs", "words"),
    [(True, "enforced"), (False, "no catalogs declaration")],
    ids=["a catalogs declaration", "no declaration"],
)
def test_catalog_check_on_main_reads_whether_the_clone_declares_catalogs(declares_catalogs, words):
    """FLEET-98, FLEET-99: on main the cell says what the gate does with the rule, from the clone's declaration."""
    assert rule_cell(CATALOG_CHECK, Gate.MAIN, declared(declares_catalogs=declares_catalogs)) == words


CATALOG_SWITCH_PIN = "8" * 40


@pytest.mark.parametrize(
    ("pin", "declares_catalogs", "words"),
    [
        (CATALOG_PIN, True, "report-only"),
        (CATALOG_PIN, False, "report-only"),
        (CATALOG_SWITCH_PIN, True, "enforced"),
        (CATALOG_SWITCH_PIN, False, "no catalogs declaration"),
    ],
    ids=["first release with a declaration", "first release with none", "switch release with a declaration", "switch release with none"],
)
def test_catalog_check_under_a_tag_reads_report_only_until_the_release_names_the_switch(pin, declares_catalogs, words):
    """FLEET-98, FLEET-99: a tag before the switch change only reports; from it the cell reads as on main."""
    tags = {**CATALOG_TAGS, CATALOG_CHECK.switched[0].release: CATALOG_SWITCH_PIN}
    release = gate_release((gate_call(pin),), False, tags)
    assert rule_cell(CATALOG_CHECK, release, declared(declares_catalogs=declares_catalogs)) == words


@pytest.mark.parametrize(
    ("pin", "words"), [(NEWEST, "not in v0.3.1"), (None, "—"), (UNRELEASED, "release unknown")],
    ids=["a pin before the rule", "no gate", "no release"],
)
def test_catalog_check_reads_the_pin_before_the_declaration(pin, words):
    """FLEET-18, FLEET-21, FLEET-22: a pin before v0.12.0, no gate and an unknown release answer as for every rule."""
    release = gate_release(() if pin is None else (gate_call(pin),), False, CATALOG_TAGS)
    assert rule_cell(CATALOG_CHECK, release, declared(declares_catalogs=True)) == words


RENDER_PASS = Rule.RENDER_PASS.release


@pytest.mark.parametrize(
    ("declares_catalogs", "words"),
    [(True, "enforced"), (False, "no catalogs declaration")],
    ids=["a catalogs declaration", "no declaration"],
)
def test_render_pass_on_main_reads_whether_the_clone_declares_catalogs(declares_catalogs, words):
    """RENDER-1: on main the cell reads as catalog-check's does."""
    assert rule_cell(RENDER_PASS, Gate.MAIN, declared(declares_catalogs=declares_catalogs)) == words


RENDER_PASS_RELEASE_PIN = "8" * 40


@pytest.mark.parametrize(
    ("pin", "words"),
    [(NEWEST, "not in v0.3.1"), (RENDER_PASS_RELEASE_PIN, "enforced")],
    ids=["a tag below the first release", "the rule's first release"],
)
def test_render_pass_under_a_tag_reads_the_pin_against_the_rules_first_release(pin, words):
    """RENDER-1, FLEET-18: a pin before the rule's first release says so, and that release reads as main does."""
    tags = {**FIRST_RELEASES, RENDER_PASS.first_release: RENDER_PASS_RELEASE_PIN}
    release = gate_release((gate_call(pin),), False, tags)
    assert rule_cell(RENDER_PASS, release, declared(declares_catalogs=True)) == words


@pytest.mark.parametrize(
    ("declares_catalogs", "words"), [(True, "enforced"), (False, "no catalogs declaration")], ids=["a catalogs declaration", "no declaration"]
)
@pytest.mark.parametrize("has_literal_table", [True, False], ids=["a table", "no table"])
def test_literal_text_on_main_reads_whether_the_clone_declares_catalogs(declares_catalogs, has_literal_table, words):
    """FLEET-99: the cell says what the gate does with the rule, which the catalogs declaration switches on and a held
    `[literal_text]` table leaves as it is."""
    declaration = declared(declares_catalogs=declares_catalogs, has_literal_table=has_literal_table)
    assert rule_cell(LITERAL_TEXT, Gate.MAIN, declaration) == words


LOCATOR_TEXT = Rule.LOCATOR_TEXT.release


@pytest.mark.parametrize(
    ("declares_catalogs", "words"), [(True, "enforced"), (False, "no catalogs declaration")], ids=["a catalogs declaration", "no declaration"]
)
def test_locator_text_on_main_reads_whether_the_clone_declares_catalogs(declares_catalogs, words):
    """FLEET-99: the catalogs declaration switches the rule on, as for `literal-text`."""
    assert rule_cell(LOCATOR_TEXT, Gate.MAIN, declared(declares_catalogs=declares_catalogs)) == words


RULES_FIRST_RELEASED_IN_V0_21_OR_LATER = [
    Rule.LOCATOR_TEXT.release, Rule.FLEET_SOURCE.release, Rule.NAMING.release, Rule.PR_WORKFLOWS.release,
    Rule.PYTEST_RANDOM.release, Rule.LIVE_LAYER.release, Rule.VITEST_RANDOM.release,
    Rule.NODE_TEST_RANDOM.release, Rule.ADAPTER_IMPORT.release,
    Rule.INTEGRATION_MARKER.release
]


REPORT_ONLY_RULES_FIRST_RELEASED_IN_V0_23_OR_LATER = [Rule.TEST_TWIN.release, Rule.LOGGING.release, Rule.SPEC_CITATION.release]


@pytest.mark.parametrize(
    "rule_release", [*RULES_FIRST_RELEASED_IN_V0_21_OR_LATER, *REPORT_ONLY_RULES_FIRST_RELEASED_IN_V0_23_OR_LATER], ids=lambda release: release.rule
)
def test_a_rule_first_shipped_in_a_release_reads_not_in_the_tag_of_an_older_pin(rule_release):
    """FLEET-18: a gate pinned below the release that names the rule has no such rule."""
    release = gate_release((gate_call(NEWEST),), False, FIRST_RELEASES)
    assert rule_cell(rule_release, release, declared(declares_catalogs=True)) == "not in v0.3.1"


@pytest.mark.parametrize("rule_release", RULES_FIRST_RELEASED_IN_V0_21_OR_LATER, ids=lambda release: release.rule)
def test_a_rule_first_shipped_in_a_release_reads_its_switch_under_that_release(rule_release):
    """FLEET-18: a gate pinned at the release that names the rule reads the rule's switch."""
    tags = {**FIRST_RELEASES, rule_release.first_release: FIRST_RELEASE_COMMIT}
    release = gate_release((gate_call(FIRST_RELEASE_COMMIT),), False, tags)
    assert rule_cell(rule_release, release, declared(declares_catalogs=True, has_adapter_contract=True)) == "enforced"


@pytest.mark.parametrize("rule_release", REPORT_ONLY_RULES_FIRST_RELEASED_IN_V0_23_OR_LATER, ids=lambda release: release.rule)
def test_a_report_only_rule_first_shipped_in_a_release_reads_report_only_under_that_release(rule_release):
    """FLEET-18, LOGGING-6: a gate pinned at the release that names a report-only rule reads `report-only`."""
    tags = {**FIRST_RELEASES, rule_release.first_release: FIRST_RELEASE_COMMIT}
    release = gate_release((gate_call(FIRST_RELEASE_COMMIT),), False, tags)
    assert rule_cell(rule_release, release, declared(declares_catalogs=True)) == "report-only"


LITERAL_SWITCH_PIN = "7" * 40


@pytest.mark.parametrize(
    ("pin", "has_literal_table", "words"),
    [
        (FIRST_RELEASE_COMMIT, True, "report-only"),
        (FIRST_RELEASE_COMMIT, False, "report-only"),
        (LITERAL_SWITCH_PIN, True, "enforced"),
        (LITERAL_SWITCH_PIN, False, "report-only"),
    ],
    ids=["first release with a table", "first release with none", "switch release with a table", "switch release with none"],
)
def test_literal_text_under_a_tag_reads_report_only_until_the_release_names_the_switch(pin, has_literal_table, words):
    """FLEET-102: a tag before the table switch only measures; from it the cell reads the table."""
    tags = {**FIRST_RELEASES, LITERAL_TEXT.first_release: FIRST_RELEASE_COMMIT, LITERAL_TEXT.switched[0].release: LITERAL_SWITCH_PIN}
    release = gate_release((gate_call(pin),), False, tags)
    assert rule_cell(LITERAL_TEXT, release, declared(has_literal_table=has_literal_table)) == words


@pytest.mark.parametrize(
    ("declares_catalogs", "words"),
    [(True, "enforced"), (False, "no catalogs declaration")],
    ids=["a catalogs declaration", "no declaration"],
)
def test_literal_text_under_a_tag_reads_the_declaration_from_the_release_that_names_it(declares_catalogs, words):
    """FLEET-99: from the release that names the catalogs declaration the cell reads as on main."""
    declaration_release = LITERAL_TEXT.switched[-1].release
    tags = {**FIRST_RELEASES, LITERAL_TEXT.first_release: FIRST_RELEASE_COMMIT, declaration_release: LITERAL_SWITCH_PIN}
    release = gate_release((gate_call(LITERAL_SWITCH_PIN),), False, tags)
    assert rule_cell(LITERAL_TEXT, release, declared(declares_catalogs=declares_catalogs)) == words


SPEC_CITATION = Rule.SPEC_CITATION.release


@pytest.mark.parametrize(("has_spec_table", "words"), [(True, "enforced"), (False, "report-only")], ids=["a table", "no table"])
def test_spec_citation_on_main_reads_whether_the_clone_holds_the_table(has_spec_table, words):
    """FLEET-118: on main the `[spec_citations]` table switches the rule on."""
    assert rule_cell(SPEC_CITATION, Gate.MAIN, declared(has_spec_table=has_spec_table)) == words


FUTURE_SPEC_RULE = RuleRelease("future", "v0.1.0", Switch.REPORT_ONLY, (SwitchChange("v0.5.0", Switch.SPEC_TABLE),))


@pytest.mark.parametrize(
    ("rule", "tag", "has_spec_table", "words"),
    [
        (FUTURE_SPEC_RULE, "v0.4.0", True, "report-only"),
        (FUTURE_SPEC_RULE, "v0.5.0", True, "enforced"),
        (FUTURE_SPEC_RULE, "v0.5.0", False, "report-only"),
        (SPEC_CITATION, SPEC_CITATION.switched[0].release, True, "enforced"),
        (SPEC_CITATION, SPEC_CITATION.switched[0].release, False, "report-only"),
    ],
    ids=[
        "before the switch's release",
        "the switch's release with a table",
        "the switch's release with none",
        "spec-citation at its switch's release with a table",
        "spec-citation at its switch's release with none",
    ],
)
def test_a_pinned_gate_reads_the_spec_table_from_the_release_that_names_it(rule, tag, has_spec_table, words):
    """FLEET-118, FLEET-17: a tag before the switch's release only reports; from it the cell reads the table."""
    pin = "e" * 40
    release = gate_release((gate_call(pin),), False, {**FIRST_RELEASES, tag: pin})
    assert rule_cell(rule, release, declared(has_spec_table=has_spec_table)) == words
