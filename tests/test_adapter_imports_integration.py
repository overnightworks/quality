"""The integration-marker rule: a test module outside the core that reaches an adapter carries the registered `integration` mark."""

import pytest

from quality import suites
from quality.report import FindingEvent, Unparsable
from quality.rules import Area, Rule
from tests.support.architecture import (
    ADAPTER_IMPORT,
    CORE_TEST,
    adapter_contract,
    adapter_findings,
    adapter_split,
    imports_findings,
    shop_with_adapters,
)
from tests.support.check import check_output, check_result
from tests.support.layout import CONFIG, assert_prints_as_kind, declaring
from tests.support.merge import ONE_JOB

NON_CORE = "tests/test_main.py"
SUPPORT = "tests/support/stores.py"
REGISTERED = ("integration: reaches a real adapter",)
FIX = "put pytest.mark.integration in the module's one direct pytestmark binding"
REGISTER = "register integration in [tool.pytest.ini_options] markers"
BOUND = ADAPTER_IMPORT + "import pytest\n\n\n"
MARK = "pytestmark = pytest.mark.integration\n\n\n"
TEST_A = "def test_a(): ...\n"
TEST_B = "\n\ndef test_b(): ...\n"
MARKED_TEST = "@pytest.mark.integration\n" + TEST_A
INHERITED = "class TestDb(Base): ...\n"
SHARED = "class ShopCase(Base): ...\n"
FACT = "imports shop.adapters · "
NO_TEST = "no test read and the module is not marked integration"
BROKEN = "def broken(:\n"


@pytest.fixture
def unmarked(build_repo):
    def judge(tests: dict[str, str], contract: dict | None = None, markers: tuple[str, ...] = REGISTERED, **session):
        repo = build_repo(shop_with_adapters(tests, contract, markers=markers, **session))
        return imports_findings(repo, Rule.INTEGRATION_MARKER)

    return judge


def read(findings) -> list[tuple[str, str, str]]:
    return [(finding.location, finding.fact, finding.fix) for finding in findings]


@pytest.mark.parametrize(
    "files",
    [
        pytest.param({}, id="no config"),
        pytest.param({"pyproject.toml": "[project]\nname = 'shop'\n"}, id="a config without the section"),
    ],
)
def test_a_repository_without_an_import_config_is_judged_by_nothing(build_repo, files):
    """INTEGRATION-1: without a config the rule judges nothing and refuses nothing."""
    repo = build_repo({"src/shop/__init__.py": "", NON_CORE: ADAPTER_IMPORT, **files})
    assert imports_findings(repo, Rule.INTEGRATION_MARKER) == []


@pytest.mark.parametrize(
    "contract",
    [
        pytest.param(adapter_split(), id="no id"),
        pytest.param(adapter_split(id="another-id"), id="another id"),
    ],
)
def test_a_config_without_the_contract_judges_nothing_and_reads_no_file(unmarked, contract):
    """INTEGRATION-1: a test module that does not parse refuses nothing where no contract carries the id."""
    assert unmarked({NON_CORE: ADAPTER_IMPORT, "tests/test_shop.py": BROKEN}, contract) == []


@pytest.mark.parametrize(
    ("contract", "path", "judged"),
    [
        pytest.param(adapter_contract(), NON_CORE, True, id="a composition module outside the core"),
        pytest.param(adapter_contract(source=("shop",)), "tests/adapters/test_db.py", True, id="a subject inside an adapter package"),
        pytest.param(adapter_contract(), CORE_TEST, False, id="a core test"),
        pytest.param(adapter_contract(source=("shop",)), "tests/api/test_db.py", False, id="a test whose folder mirrors no package"),
        pytest.param(adapter_contract(), "tests/scripts/test_tool.py", False, id="a script test"),
        pytest.param(adapter_contract(), "tests/workflows/test_gate.py", False, id="a workflow test"),
        pytest.param(adapter_contract(), "tests/e2e/test_flow.py", False, id="an end-to-end test"),
        pytest.param(adapter_contract(), "tests/conftest.py", False, id="a conftest"),
    ],
)
def test_the_rule_judges_the_test_modules_adapter_import_leaves_out(unmarked, contract, path, judged):
    """INTEGRATION-2: a core test belongs to `adapter-import` alone; scripts, workflows, e2e tests and conftests are not judged."""
    assert [finding.path for finding in unmarked({path: BOUND + TEST_A}, contract)] == ([path] if judged else [])


def test_a_declared_test_module_without_a_subject_is_a_core_test_and_not_judged(unmarked):
    """INTEGRATION-2: a test with no subject is a core test (ADAPTERIMPORT-6), so no declaration moves it here."""
    path = "tests/adapters/test_db.py"
    declared = {CONFIG: ONE_JOB + declaring(path, rule="test-layout")}
    assert unmarked({path: BOUND + TEST_A, **declared}, adapter_contract(source=("shop",))) == []


BOUND_IMPORTS = [
    pytest.param(ADAPTER_IMPORT, True, id="a forbidden module"),
    pytest.param("from shop import adapters\n", True, id="a from-import of an adapter"),
    pytest.param("import shop.main\n", True, id="a composition module that reaches an adapter"),
    pytest.param("def f():\n    import shop.adapters.db\n", True, id="inside a function"),
    pytest.param("from shop import core\n", False, id="an ancestor of an adapter is a namespace"),
    pytest.param("import shop.core.config\nimport pytest\n", False, id="a core module and a third-party name"),
]


@pytest.mark.parametrize(("imports", "bound"), BOUND_IMPORTS)
def test_a_module_is_adapter_bound_by_an_import_that_is_or_reaches_an_adapter(unmarked, imports, bound):
    """INTEGRATION-3: the names ADAPTERIMPORT-9 to 15 call red bind the module."""
    assert [finding.path for finding in unmarked({NON_CORE: imports + "\n\n" + TEST_A})] == ([NON_CORE] if bound else [])


@pytest.mark.parametrize(
    ("support", "tests", "bound"),
    [
        pytest.param({SUPPORT: ADAPTER_IMPORT}, ["from tests.support import stores\n" + TEST_A], [0], id="one hop"),
        pytest.param({SUPPORT: ADAPTER_IMPORT}, ["import tests.support.stores\n" + TEST_A, "import os\n" + TEST_A], [0], id="a test that imports none stays clean"),
        pytest.param({SUPPORT: ADAPTER_IMPORT}, ["from tests.support import stores\n" + TEST_A] * 2, [0, 1], id="every test that uses the support module"),
        pytest.param({SUPPORT: "import shop.core.config\n"}, ["from tests.support import stores\n" + TEST_A], [], id="a clean support module"),
        pytest.param({SUPPORT: "", "tests/support/unused.py": ADAPTER_IMPORT}, ["from tests.support import stores\n" + TEST_A], [], id="a support module no test imports"),
    ],
)
def test_a_support_module_that_imports_an_adapter_binds_every_non_core_test_that_uses_it(unmarked, support, tests, bound):
    """INTEGRATION-3: the imports of the support modules a test imports are its own."""
    paths = ["tests/test_main.py", "tests/test_shop.py"]
    named = dict(zip(paths, tests, strict=False))
    assert [finding.path for finding in unmarked({**support, **named})] == [paths[index] for index in bound]


MARKED_FORMS = [
    pytest.param(MARK + TEST_A, None, id="a bare module mark"),
    pytest.param("pytestmark = pytest.mark.integration()\n\n\n" + TEST_A, None, id="a called module mark"),
    pytest.param("pytestmark = [pytest.mark.slow, pytest.mark.integration]\n\n\n" + TEST_A, None, id="a mark in a module list"),
    pytest.param("layer = pytest.mark.integration\npytestmark = layer\n\n\n" + TEST_A, None, id="a module-level alias"),
    pytest.param(MARKED_TEST + "\n\n" + MARKED_TEST.replace("test_a", "test_b"), None, id="a mark on each test"),
    pytest.param("@pytest.mark.integration\nclass TestDb:\n    def test_a(self): ...\n", None, id="a mark on the enclosing class"),
    pytest.param("class TestDb:\n    pytestmark = pytest.mark.integration\n\n    def test_a(self): ...\n", None, id="a class pytestmark"),
    pytest.param(MARKED_TEST + TEST_B, "1 of 2", id="a mark on one of two tests"),
    pytest.param(
        '@pytest.mark.parametrize("x", [pytest.param(1, marks=pytest.mark.integration)])\ndef test_a(x): ...\n', "1 of 1",
        id="a mark of a parameter set",
    ),
    pytest.param(MARK + MARK + TEST_A, "1 of 1", id="a module that binds pytestmark twice"),
    pytest.param("if True:\n    pytestmark = pytest.mark.integration\n\n\n" + TEST_A, "1 of 1", id="a conditional module mark"),
    pytest.param("pytestmark = pytest.mark.slow\n\n\n" + TEST_A, "1 of 1", id="another mark"),
]


@pytest.mark.parametrize(("body", "tail"), MARKED_FORMS)
def test_an_adapter_bound_module_is_clean_when_every_test_carries_the_mark_in_a_form_the_report_reads(unmarked, body, tail):
    """INTEGRATION-4: the module is clean when the mark reaches every test; a mark only a run applies counts for none."""
    expected = [] if tail is None else [(f"{NON_CORE}:1", f"{FACT}{tail} tests not marked integration", FIX)]
    assert read(unmarked({NON_CORE: BOUND + body})) == expected


def counted(k_of_n: str) -> str:
    return f"{k_of_n} tests not marked integration"


UNSEEN = [
    pytest.param(INHERITED, counted("1 of 1"), id="an inherited suite"),
    pytest.param("@pytest.mark.integration\n" + INHERITED, None, id="an inherited suite marked by its decorator"),
    pytest.param("class TestDb(Base):\n    pytestmark = pytest.mark.integration\n", None, id="an inherited suite marked by its pytestmark"),
    pytest.param(MARK + INHERITED, None, id="an inherited suite marked by the module"),
    pytest.param(MARKED_TEST + "\n\n" + INHERITED, counted("1 of 2"), id="an inherited suite beside a marked test"),
    pytest.param("class TestDb(object):\n    def test_a(self): ...\n", counted("1 of 1"), id="object is no base"),
    pytest.param(SHARED, counted("1 of 1"), id="an inherited suite whose name is no test class"),
    pytest.param("@pytest.mark.integration\n" + SHARED, None, id="an inherited suite whose name is no test class, marked by its decorator"),
    pytest.param(MARK + SHARED, None, id="an inherited suite whose name is no test class, marked by the module"),
    pytest.param(MARKED_TEST + "\n\n" + SHARED, counted("1 of 2"), id="a helper class with a base beside a marked test"),
    pytest.param("def make():\n    class Fake(Base): ...\n\n\n" + MARKED_TEST, None, id="a class with a base inside a function is no test class"),
    pytest.param(
        "class TestOuter:\n    @pytest.mark.integration\n    def test_a(self): ...\n\n    class TestInner(Base): ...\n", counted("1 of 2"),
        id="a nested inherited suite beside a marked test",
    ),
    pytest.param("from shop.core.config import test_round\n\n\n" + MARKED_TEST, counted("1 of 2"), id="an imported test beside a marked test"),
    pytest.param("from shop.core.config import test_round\n\n\n" + MARK + TEST_A, None, id="an imported test marked by the module"),
    pytest.param("from shop.core.config import make as test_make\n", counted("1 of 1"), id="an import renamed to a test name"),
    pytest.param("from shop.core.config import test_make as make\n", NO_TEST, id="an import renamed away from a test name"),
    pytest.param("from shop.core.config import TestRound\n", counted("1 of 1"), id="an imported test class"),
    pytest.param("from shop.core.config import *\n", counted("1 of 1"), id="a star import"),
    pytest.param(MARK + "from shop.core.config import *\n", None, id="a star import marked by the module"),
    pytest.param("@pytest.mark.integration\nclass TestDb:\n    def test_a(self): ...\n\n\n" + INHERITED, counted("1 of 2"), id="a decorator on another class"),
]


@pytest.mark.parametrize(("body", "fact"), UNSEEN)
def test_a_test_the_source_does_not_show_counts_as_one_more_that_only_a_reaching_mark_marks(unmarked, body, fact):
    """INTEGRATION-5: inherited suites, whatever their class is named, and imported tests stand beside the tests the reader sees."""
    expected = [] if fact is None else [(f"{NON_CORE}:1", FACT + fact, FIX)]
    assert read(unmarked({NON_CORE: BOUND + body})) == expected


def test_a_test_the_source_does_not_show_moves_no_count_of_the_report():
    """INTEGRATION-5, FLEET-120: the report counts the tests it reads, not the inherited or imported ones."""
    module = "from shop.core.config import *\nfrom shop.core.config import test_round\n\n\nclass TestDb(Base): ...\n\n\n" + TEST_A
    counted = suites.count_suites([NON_CORE], {NON_CORE: module.encode()})
    assert sum(counted.layers.values()) == 1


MODULE_ONLY = [
    pytest.param(BOUND, None, FACT + NO_TEST, id="a module with no test"),
    pytest.param(MARK + BOUND, None, None, id="a module with no test, marked"),
    pytest.param(BOUND, ("unit: fast",), FACT + NO_TEST, id="a module with no test, unregistered"),
]


@pytest.mark.parametrize(("module", "markers", "fact"), MODULE_ONLY)
def test_an_adapter_bound_module_with_no_test_is_clean_only_when_its_own_pytestmark_carries_the_mark(unmarked, module, markers, fact):
    """INTEGRATION-6: a module in which no test is read or counted needs the module's own mark."""
    findings = unmarked({NON_CORE: module}, markers=markers or REGISTERED)
    assert [finding.fact for finding in findings] == ([] if fact is None else [fact])


@pytest.mark.parametrize(
    ("markers", "tests"),
    [
        pytest.param(("unit: fast",), MARKED_TEST + "\n\n" + MARKED_TEST.replace("test_a", "test_b"), id="a registration of another mark"),
        pytest.param((), MARKED_TEST + "\n\n" + MARKED_TEST.replace("test_a", "test_b"), id="no registration"),
    ],
)
def test_without_the_registration_no_mark_reaches_any_test_and_the_fix_registers_it(unmarked, markers, tests):
    """INTEGRATION-7: the fact counts every test and the fix is the registration."""
    findings = unmarked({NON_CORE: BOUND + tests}, markers=markers)
    assert read(findings) == [(f"{NON_CORE}:1", f"{FACT}2 of 2 tests not marked integration", REGISTER)]


def test_a_mark_is_registered_by_the_pyproject_of_its_own_project(unmarked):
    """INTEGRATION-7: the registration is the `pyproject.toml` of the project whose tests the module is in."""
    member = {
        "tool/pyproject.toml": "", "tool/src/tool/__init__.py": "", "tool/src/tool/db.py": "",
        "tool/tests/test_db.py": BOUND + MARKED_TEST,
    }
    (finding,) = unmarked(member)
    assert (finding.location, finding.fix) == ("tool/tests/test_db.py:1", REGISTER)


def test_one_finding_stands_at_the_first_red_statement_and_names_every_adapter_the_module_reaches(build_repo):
    """INTEGRATION-8: one finding per module, at the line of its first red statement."""
    web = {"src/shop/web/__init__.py": "", "src/shop/web/views.py": ""}
    module = "import pytest\n\nimport shop.core.config\nimport shop.web.views\nimport shop.adapters.db\n\n\n" + TEST_A
    forbidden = adapter_contract(forbidden=("shop.adapters", "shop.web"))
    repo = build_repo(shop_with_adapters({NON_CORE: module, **web}, forbidden, markers=REGISTERED))
    (finding,) = imports_findings(repo, Rule.INTEGRATION_MARKER)
    assert (finding.location, finding.fact) == (f"{NON_CORE}:4", "imports shop.adapters, shop.web · 1 of 1 tests not marked integration")


def test_a_finding_names_the_support_module_that_carried_the_adapter_and_prints_as_its_kind(build_repo, capsys):
    """INTEGRATION-8: the fact is the words ADAPTERIMPORT-16 gives, then the count; the row is an `unmarked` one."""
    tests = {NON_CORE: "from tests.support import stores\n" + TEST_A, SUPPORT: ADAPTER_IMPORT}
    repo = build_repo(shop_with_adapters(tests, markers=REGISTERED))
    finding = assert_prints_as_kind(capsys, repo, Rule.INTEGRATION_MARKER, FindingEvent.UNMARKED, "shop.adapters")
    assert (finding.fact, finding.fix) == (f"imports shop.adapters (through {SUPPORT}) · 1 of 1 tests not marked integration", FIX)


@pytest.mark.parametrize("body", [pytest.param(MARK + TEST_A, id="a marked module"), pytest.param(TEST_A, id="an unmarked one")])
def test_a_module_that_reaches_no_adapter_is_no_finding_marked_or_not(unmarked, body):
    """INTEGRATION-9: the mark on a module that is not adapter-bound is no finding of this rule."""
    assert unmarked({NON_CORE: "import pytest\nimport shop.core.config\n\n\n" + body}) == []


@pytest.mark.parametrize(
    ("tests", "refused"),
    [
        pytest.param({NON_CORE: BROKEN}, {Rule.INTEGRATION_MARKER}, id="a non-core test"),
        pytest.param({CORE_TEST: BROKEN}, {Rule.ADAPTER_IMPORT}, id="a core test"),
        pytest.param({CORE_TEST: "from tests.support import stores\n", SUPPORT: BROKEN}, {Rule.ADAPTER_IMPORT}, id="a support module only a core test imports"),
        pytest.param({NON_CORE: "from tests.support import stores\n", SUPPORT: BROKEN}, {Rule.INTEGRATION_MARKER}, id="a support module only a non-core test imports"),
        pytest.param(
            {CORE_TEST: "from tests.support import stores\n", NON_CORE: "from tests.support import stores\n", SUPPORT: BROKEN},
            {Rule.ADAPTER_IMPORT, Rule.INTEGRATION_MARKER}, id="a support module both rules reach",
        ),
        pytest.param({SUPPORT: BROKEN}, set(), id="a support module nothing imports"),
    ],
)
def test_a_module_that_does_not_parse_refuses_the_rules_it_is_or_is_carried_by_and_only_those(build_repo, tests, refused):
    """INTEGRATION-10: one read serves both rules, and each rule refuses for the modules it judges."""
    verdicts = check_result(build_repo(shop_with_adapters(tests, markers=REGISTERED)), named=[Area.IMPORTS]).verdicts
    assert {verdict.rule for verdict in verdicts if verdict.refusal is not None} == refused
    assert all(isinstance(verdict.refusal, Unparsable) for verdict in verdicts if verdict.refusal is not None)


@pytest.mark.parametrize(
    ("tests", "judged_rule", "judged_path"),
    [
        pytest.param({CORE_TEST: ADAPTER_IMPORT, NON_CORE: BROKEN}, Rule.ADAPTER_IMPORT, CORE_TEST, id="a non-core test that does not parse"),
        pytest.param({CORE_TEST: BROKEN, NON_CORE: BOUND + TEST_A}, Rule.INTEGRATION_MARKER, NON_CORE, id="a core test that does not parse"),
    ],
)
def test_one_rules_refusal_never_silences_the_other_rule(build_repo, capsys, tests, judged_rule, judged_path):
    """INTEGRATION-10: the rule a module does not refuse keeps its findings, and the run is exit 2."""
    repo = build_repo(shop_with_adapters(tests, markers=REGISTERED))
    assert [finding.path for finding in imports_findings(repo, judged_rule)] == [judged_path]
    assert check_output(capsys, repo)[0] == 2


def test_a_layout_declaration_for_the_rule_is_refused_like_any_rule_outside_the_layout_rules(build_repo, capsys):
    """INTEGRATION-11: `[[layout.allow]]` names no path an unmarked module is excused for."""
    config = ONE_JOB + declaring(NON_CORE, rule="integration-marker")
    code, output = check_output(capsys, build_repo(shop_with_adapters({NON_CORE: BOUND + TEST_A, CONFIG: config})))
    assert (code, "[[layout.allow]] entry 1 needs rule as one of" in output, "integration-marker," in output) == (2, True, False)


def test_the_integration_mark_exempts_nothing_from_adapter_import(build_repo):
    """INTEGRATION-12: a core test that imports an adapter is red marked or not."""
    repo = build_repo(shop_with_adapters({CORE_TEST: "import pytest\n" + MARK + ADAPTER_IMPORT}, markers=REGISTERED))
    assert [finding.event for finding in adapter_findings(repo)] == [FindingEvent.ADAPTER_IMPORT]


def test_the_adapter_mark_exempts_nothing_from_the_integration_marker(build_repo):
    """INTEGRATION-12: `imports_adapter` marks a core test only, and a module of this rule is red under it."""
    module = BOUND + "pytestmark = pytest.mark.imports_adapter\n\n\n" + TEST_A
    repo = build_repo(shop_with_adapters({NON_CORE: module}, markers=(*REGISTERED, "imports_adapter: reads a real service")))
    assert [finding.path for finding in imports_findings(repo, Rule.INTEGRATION_MARKER)] == [NON_CORE]
    assert [finding.event for finding in adapter_findings(repo)] == [FindingEvent.UNUSED]
