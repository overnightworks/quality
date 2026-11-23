"""The adapter-import rule: a core unit test imports no adapter the repository's `core-imports-no-adapter` contract names."""

import pytest

from quality import architecture
from quality.report import CannotJudge, FindingEvent, NoteEvent, Unparsable
from quality.rules import Area, Rule
from tests.support.architecture import (
    ADAPTER_IMPORT,
    CORE_TEST,
    adapter_contract,
    adapter_findings,
    adapter_refusal,
    adapter_split,
    shop_with_adapters,
)
from tests.support.check import check_output, check_result, refusal_row, rule_notes
from tests.support.layout import CONFIG, assert_prints_as_kind, declaring
from tests.support.merge import ONE_JOB


@pytest.fixture
def red(build_repo):
    def locate(tests: dict[str, str], contract: dict | None = None, extra: dict[str, str] | None = None, **session) -> list[str]:
        repo = build_repo({**shop_with_adapters(tests, contract, **session), **(extra or {})})
        return [finding.location for finding in adapter_findings(repo)]

    return locate


@pytest.mark.parametrize(
    "files",
    [
        pytest.param({}, id="no config"),
        pytest.param({"pyproject.toml": "[project]\nname = 'shop'\n"}, id="a config without the section"),
    ],
)
def test_a_repository_without_an_import_config_is_judged_by_nothing(build_repo, files):
    """ADAPTERIMPORT-1, ADAPTERIMPORT-2: without a config the rule judges nothing and refuses nothing."""
    repo = build_repo({"src/shop/__init__.py": "", CORE_TEST: ADAPTER_IMPORT, **files})
    assert adapter_findings(repo) == []


@pytest.mark.parametrize(
    "contract",
    [
        pytest.param(adapter_split(), id="no id"),
        pytest.param(adapter_split(id="another-id"), id="another id"),
        pytest.param({"id": architecture.ADAPTER_CONTRACT_ID, "type": "independence", "modules": ["shop.core", "shop.adapters"]}, id="no forbidden contract"),
    ],
)
def test_a_config_without_the_contract_judges_nothing(red, contract):
    """ADAPTERIMPORT-1: only a `forbidden` contract with the id switches the rule on."""
    assert red({CORE_TEST: ADAPTER_IMPORT}, contract) == []


CORE_IMPORTS = [
    pytest.param(ADAPTER_IMPORT, [f"{CORE_TEST}:1"], id="a forbidden module"),
    pytest.param("import shop.adapters\n", [f"{CORE_TEST}:1"], id="a forbidden package"),
    pytest.param("from shop import adapters\n", [f"{CORE_TEST}:1"], id="a from-import of an adapter"),
    pytest.param("from shop.adapters.db import connect\n", [f"{CORE_TEST}:1"], id="a name of an adapter module"),
    pytest.param("from shop import core\n", [], id="an ancestor of an adapter is a namespace"),
    pytest.param("import shop\n", [], id="a bare import of the root package"),
    pytest.param("import shop.core.config_stores\nimport pytest\n", [], id="a core module and a third-party name"),
    pytest.param("def f():\n    import shop.adapters.db\n", [f"{CORE_TEST}:2"], id="inside a function"),
    pytest.param("from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    import shop.adapters.db\n", [f"{CORE_TEST}:3"], id="under TYPE_CHECKING"),
    pytest.param("import shop.main\n", [f"{CORE_TEST}:1"], id="a composition module that imports an adapter"),
]


@pytest.mark.parametrize(("content", "expected"), CORE_IMPORTS)
def test_a_core_test_is_red_for_an_import_that_is_or_reaches_an_adapter(red, content, expected):
    """ADAPTERIMPORT-9 to ADAPTERIMPORT-13, ADAPTERIMPORT-15: which names of a core test are red, at which line."""
    assert red({CORE_TEST: content}) == expected


def test_a_finding_names_the_adapter_a_name_is_or_reaches_and_prints_as_its_kind(build_repo, capsys):
    """ADAPTERIMPORT-16: the fact names the imported name and the forbidden module it is or reaches."""
    repo = build_repo(shop_with_adapters({CORE_TEST: "import shop.main\nimport shop.adapters.db\n"}))
    assert [(finding.event, "shop.main" in finding.fact, "shop.adapters" in finding.fact) for finding in adapter_findings(repo)] == [
        (FindingEvent.ADAPTER_IMPORT, True, True), (FindingEvent.ADAPTER_IMPORT, False, True),
    ]
    assert check_output(capsys, repo)[0] == 1


def test_a_finding_prints_as_one_row_of_its_own_kind(build_repo, capsys):
    """ADAPTERIMPORT-16, ROW-7, ROW-14: one finding at `path:line`, its fact naming the adapter."""
    repo = build_repo(shop_with_adapters({CORE_TEST: ADAPTER_IMPORT}))
    assert_prints_as_kind(capsys, repo, Rule.ADAPTER_IMPORT, FindingEvent.ADAPTER_IMPORT, "shop.adapters.db")


FOUND = [f"{CORE_TEST}:1"]


@pytest.mark.parametrize(
    ("contract", "content", "main", "expected"),
    [
        pytest.param(adapter_contract(forbidden=("uvicorn",)), "import shop.main\n", "import uvicorn.config\n", {True: FOUND, False: []},
                     id="a module that reaches a squashed external"),
        pytest.param(adapter_contract(forbidden=("agent_runtime_client",)), "import shop.main\nimport shop.core.config\n", "", {True: [], False: []},
                     id="a forbidden external nothing imports"),
        pytest.param(adapter_contract(forbidden=("psycopg",)), "import psycopg.pool\n", "", {True: FOUND, False: FOUND},
                     id="an imported external forbidden module"),
    ],
)
@pytest.mark.parametrize("external", [True, False], ids=["with external packages", "without"])
def test_a_forbidden_external_package_is_an_adapter_by_its_name_and_by_what_reaches_it(red, contract, content, main, expected, external):
    """ADAPTERIMPORT-13, ADAPTERIMPORT-14: a forbidden external package stands in the graph only as a squashed module,
    and one nothing imports is no module there at all."""
    judged = red({CORE_TEST: content}, contract, {"src/shop/main.py": main}, include_external_packages=external)
    assert judged == expected[external]


@pytest.mark.parametrize(
    ("contract", "content", "expected"),
    [
        pytest.param(adapter_contract(as_packages=True), ADAPTER_IMPORT, [f"{CORE_TEST}:1"], id="inside a forbidden package"),
        pytest.param(adapter_contract(source=("shop.core.config",), as_packages=False), ADAPTER_IMPORT, FOUND, id="inside it, not as packages"),
        pytest.param(adapter_contract(source=("shop.core.config",), as_packages=False), "import shop.adapters\n", [f"{CORE_TEST}:1"], id="the module itself, not as packages"),
        pytest.param(adapter_contract(source=("shop.core.config",), as_packages=False), "import shop.main\n", FOUND, id="a module reaching a submodule, not as packages"),
        pytest.param(adapter_contract(as_packages=False), ADAPTER_IMPORT, [], id="a subject below an exact source module"),
    ],
)
def test_as_packages_decides_only_what_lies_inside_the_core(red, contract, content, expected):
    """ADAPTERIMPORT-5, ADAPTERIMPORT-11, ADAPTERIMPORT-14: only the core is matched by prefix with `as_packages`; an adapter
    is a module and every module inside it, and its importers are found as a package."""
    assert red({CORE_TEST: content}, contract) == expected


@pytest.mark.parametrize(
    ("contract", "path", "expected"),
    [
        pytest.param(adapter_contract(), CORE_TEST, True, id="a core test"),
        pytest.param(adapter_contract(source=("shop",)), "tests/adapters/test_db.py", False, id="a subject inside an adapter package"),
        pytest.param(adapter_contract(source=("shop",), forbidden=("shop.adapters.db",)), "tests/adapters/test_db.py", False, id="a subject that is an adapter module"),
        pytest.param(adapter_contract(), "tests/test_main.py", False, id="a composition module outside the core"),
        pytest.param(adapter_contract(source=("shop",)), "tests/test_shop.py", True, id="a test named after its package, the package inside the core"),
        pytest.param(adapter_contract(), "tests/test_shop.py", False, id="a test named after its package, the package outside the core"),
        pytest.param(adapter_contract(), "tests/core/test_config_extra.py", True, id="an aspect after the subject"),
        pytest.param(adapter_contract(forbidden=("shop.core.config_stores",)), "tests/core/test_config_stores.py", False, id="the longest key is the subject"),
        pytest.param(adapter_contract(), "tests/core/test_nothing_known.py", True, id="a test that mirrors nothing has no subject"),
        pytest.param(adapter_contract(), "tests/scripts/test_tool.py", False, id="a script test"),
        pytest.param(adapter_contract(), "tests/workflows/test_gate.py", False, id="a workflow test"),
        pytest.param(adapter_contract(), "tests/e2e/test_flow.py", False, id="an end-to-end test"),
        pytest.param(adapter_contract(), "tests/core/conftest.py", False, id="a conftest"),
        pytest.param(adapter_contract(source=("shop",)), "tests/api/test_db.py", True, id="a test whose folder mirrors no package"),
    ],
)
def test_a_test_is_judged_when_its_subject_lies_in_the_core_outside_every_adapter(red, contract, path, expected):
    """ADAPTERIMPORT-4 to ADAPTERIMPORT-8: the module a test is named after decides whether it is a core test, by names alone."""
    assert red({path: ADAPTER_IMPORT}, contract) == ([f"{path}:1"] if expected else [])


def test_a_test_named_after_its_package_is_that_packages_even_beside_a_module_of_the_same_name(red):
    """ADAPTERIMPORT-4, ADAPTERIMPORT-5: `shop` the package, not `shop.shop` the module, is the subject of `test_shop.py`."""
    contract = adapter_contract(source=("shop",), forbidden=("shop.adapters", "shop.shop"))
    assert red({"tests/test_shop.py": ADAPTER_IMPORT}, contract, extra={"src/shop/shop.py": ""}) == ["tests/test_shop.py:1"]


def test_a_test_a_layout_declaration_exempts_has_no_subject_and_is_judged(red):
    """ADAPTERIMPORT-6: a `test-layout` declaration is no path allowlist for this rule."""
    path = "tests/adapters/test_db.py"
    contract = adapter_contract(source=("shop",))
    assert red({path: ADAPTER_IMPORT}, contract) == []
    declared = {CONFIG: ONE_JOB + declaring(path, rule="test-layout")}
    assert red({path: ADAPTER_IMPORT}, contract, extra=declared) == [f"{path}:1"]


def test_a_test_of_a_project_the_layout_rules_do_not_judge_is_judged(red):
    """ADAPTERIMPORT-6: a project without a package has no mirror, so its tests have no subject."""
    member = {"tool/pyproject.toml": "", "tool/tests/test_db.py": ADAPTER_IMPORT}
    assert red({}, adapter_contract(source=("shop",)), extra=member) == ["tool/tests/test_db.py:1"]


SUPPORT = "tests/support/stores.py"


@pytest.mark.parametrize(
    ("support", "test", "expected"),
    [
        pytest.param({SUPPORT: ADAPTER_IMPORT}, "from tests.support import stores\n", [f"{CORE_TEST}:1"], id="one hop"),
        pytest.param({SUPPORT: ADAPTER_IMPORT}, "import tests.support.stores\n", [f"{CORE_TEST}:1"], id="a dotted module"),
        pytest.param({SUPPORT: "import tests.support.deeper\n", "tests/support/deeper.py": ADAPTER_IMPORT}, "from tests.support import stores\n",
                     [f"{CORE_TEST}:1"], id="two hops"),
        pytest.param({SUPPORT: "import tests.support.other\n", "tests/support/other.py": "import tests.support.stores\n" + ADAPTER_IMPORT},
                     "from tests.support import stores\n", [f"{CORE_TEST}:1"], id="a cycle of support modules"),
        pytest.param({"tests/support/__init__.py": ADAPTER_IMPORT}, "import tests.support\n", [f"{CORE_TEST}:1"], id="a package initializer"),
        pytest.param({"tests/support/__init__.py": ADAPTER_IMPORT}, "from .. import support\n", [f"{CORE_TEST}:1"], id="a package initializer, relative"),
        pytest.param({SUPPORT: "import shop.core.config\n"}, "from tests.support import stores\n", [], id="a clean support module"),
        pytest.param({SUPPORT: "", "tests/support/unused.py": ADAPTER_IMPORT}, "from tests.support import stores\n", [], id="a support module no core test imports"),
    ],
)
def test_the_imports_of_the_support_modules_a_core_test_imports_are_its_own(red, support, test, expected):
    """ADAPTERIMPORT-9: support modules are followed through further support modules and judged as the test's imports."""
    findings = red({CORE_TEST: test, **support})
    assert findings == expected


def test_a_cycle_of_support_modules_is_one_finding_naming_the_module_that_imports_the_adapter(build_repo):
    """ADAPTERIMPORT-9, ADAPTERIMPORT-16: each support module is read once and the finding names the one that carried the adapter."""
    files = {
        CORE_TEST: "from tests.support import stores\n", SUPPORT: "import tests.support.other\n",
        "tests/support/other.py": "import tests.support.stores\n" + ADAPTER_IMPORT,
    }
    (finding,) = adapter_findings(build_repo(shop_with_adapters(files)))
    assert ("tests/support/other.py" in finding.fact, "shop.adapters.db" in finding.fact) == (True, True)


def test_findings_are_all_printed_sorted_by_path_and_line(red):
    """ADAPTERIMPORT-17: findings are sorted by path and line, not in the order the walk finds them."""
    tests = {
        "tests/core/test_config_stores.py": "x = 1\n" + ADAPTER_IMPORT,
        CORE_TEST: "import os\nimport shop.main\n\n" + ADAPTER_IMPORT,
    }
    assert red(tests) == [f"{CORE_TEST}:2", f"{CORE_TEST}:4", "tests/core/test_config_stores.py:2"]


@pytest.mark.parametrize(
    "contract",
    [
        pytest.param(adapter_contract(source=("shop.*",)), id="a wildcard in the source modules"),
        pytest.param(adapter_contract(forbidden=("shop.adapters.*",)), id="a wildcard in the forbidden modules"),
    ],
)
def test_a_wildcard_in_either_list_cannot_be_judged_and_refuses_only_this_rule(build_repo, contract):
    """ADAPTERIMPORT-3, ADAPTERIMPORT-20: a refusal of the rule's own leaves the import contracts judged."""
    result = check_result(build_repo(shop_with_adapters({CORE_TEST: ADAPTER_IMPORT}, contract)), named=[Area.IMPORTS])
    refusals = {verdict.rule: verdict.refusal for verdict in result.verdicts}
    assert (type(refusals[Rule.ADAPTER_IMPORT]), refusals[Rule.IMPORT_CONTRACT]) == (CannotJudge, None)
    assert "wildcard" in str(refusals[Rule.ADAPTER_IMPORT])


def test_two_contracts_carrying_the_id_cannot_be_judged(build_repo):
    """ADAPTERIMPORT-3: two contracts with the id leave it open which one is the split."""
    repo = build_repo(shop_with_adapters({CORE_TEST: ADAPTER_IMPORT}))
    (repo / "pyproject.toml").write_text(
        (repo / "pyproject.toml").read_text(encoding="utf-8") + "\n[[tool.importlinter.contracts]]\n"
        'name = "again"\nid = "core-imports-no-adapter"\ntype = "forbidden"\nsource_modules = ["shop.core"]\nforbidden_modules = ["shop.adapters"]\n',
        encoding="utf-8",
    )
    assert "carry" in str(adapter_refusal(repo))


@pytest.mark.parametrize(
    ("path", "content"),
    [
        pytest.param(CORE_TEST, "def broken(:\n", id="a core test"),
        pytest.param("tests/support/stores.py", "def broken(:\n", id="a support module a core test imports"),
        pytest.param("tests/support/__init__.py", "def broken(:\n", id="a support package initializer"),
    ],
)
def test_a_module_that_does_not_parse_makes_the_imports_row_not_checked(build_repo, capsys, path, content):
    """ADAPTERIMPORT-18: a test or support module Python cannot parse is exit 2, named."""
    tests = {CORE_TEST: "from tests.support import stores\n", "tests/support/stores.py": "", path: content}
    repo = build_repo(shop_with_adapters(tests))
    assert isinstance(adapter_refusal(repo), Unparsable)
    code, output = check_output(capsys, repo)
    assert (code, f"{refusal_row('imports')}{path} is not valid Python (" in output) == (2, True)


def test_a_config_that_cannot_be_read_refuses_both_rules_of_the_area_with_one_row(build_repo, capsys):
    """ADAPTERIMPORT-20: a read that cannot judge refuses both rules, and the area says it once."""
    contract = adapter_contract(type="custom")
    repo = build_repo(shop_with_adapters({CORE_TEST: ADAPTER_IMPORT}, contract))
    result = check_result(repo, named=[Area.IMPORTS])
    refusals = {verdict.rule: str(verdict.refusal) for verdict in result.verdicts if verdict.refusal is not None}
    assert set(refusals) == {Rule.IMPORT_CONTRACT, Rule.ADAPTER_IMPORT}
    assert len(set(refusals.values())) == 1
    code, output = check_output(capsys, repo)
    assert (code, output.count(refusal_row("imports"))) == (2, 1)


def test_the_warnings_grimp_gives_while_reading_are_printed_once_as_the_import_contracts_notes(build_repo):
    """ADAPTERIMPORT-20: the graph is built once, so a grimp warning is one note and none is the adapter rule's."""
    repo = build_repo(shop_with_adapters({CORE_TEST: ADAPTER_IMPORT}, adapter_contract(), ) | {"src/shop/some.module.py": ""})
    assert ([note.event for note in rule_notes(repo, Rule.IMPORT_CONTRACT)], rule_notes(repo, Rule.ADAPTER_IMPORT)) == ([NoteEvent.WARNING], [])


def test_a_layout_declaration_for_the_rule_is_refused_like_any_rule_outside_the_layout_rules(build_repo, capsys):
    """ADAPTERIMPORT-19: `[[layout.allow]]` names no path an adapter import is excused for."""
    config = ONE_JOB + declaring(CORE_TEST, rule="adapter-import")
    code, output = check_output(capsys, build_repo(shop_with_adapters({CORE_TEST: ADAPTER_IMPORT, CONFIG: config})))
    assert (code, "[[layout.allow]] entry 1 needs rule as one of" in output, "adapter-import," in output) == (2, True, False)
