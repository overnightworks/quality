import json

import pytest

from quality.report import FindingEvent, Unparsable
from quality.rules import Rule
from tests.support.check import check_output as run
from tests.support.check import check_result, refusals, rule_findings

RULE = Rule.VITEST_RANDOM
UNSHUFFLED = FindingEvent.UNSHUFFLED
CONFIG = "vitest.config.ts"
PACKAGE = "package.json"
FACT = "vitest runs its files and tests in a fixed order"


def package(*, dev: dict[str, str] | None = None, runtime: dict[str, str] | None = None) -> str:
    return json.dumps({"name": "x", "devDependencies": dev or {}, "dependencies": runtime or {}})


VITEST = package(dev={"vitest": "^4.0.0"})


def config(sequence: str, *, wrapper: str = "defineConfig({{{body}}})") -> str:
    """A vitest config whose root `test` object holds `sequence`."""
    body = f"test: {{ sequence: {sequence} }}"
    return f"import {{ defineConfig }} from 'vitest/config'\nexport default {wrapper.format(body=body)}\n"


def judged(build_repo, files: dict[str, str]):
    return rule_findings(build_repo(files), RULE)


def shape(findings):
    return [(finding.event, finding.path) for finding in findings]


CLEAN_SEQUENCES = [
    pytest.param("{ shuffle: true }", id="shuffle true"),
    pytest.param("{ shuffle: { files: true, tests: true } }", id="both orders"),
    pytest.param("{ shuffle: true, seed: Date.now() }", id="seed from an expression"),
    pytest.param("{ seed: seedOf(1), shuffle: true }", id="seed after a call"),
]
FLAGGED_SEQUENCES = [
    pytest.param("{ shuffle: { files: true } }", id="files only"),
    pytest.param("{ shuffle: { tests: true } }", id="tests only"),
    pytest.param("{ shuffle: false }", id="shuffle false"),
    pytest.param("{}", id="empty sequence"),
    pytest.param("{ shuffle: {} }", id="empty shuffle"),
]
FIXED_SEEDS = [
    pytest.param("{ shuffle: true, seed: 1234 }", id="fixed seed"),
    pytest.param("{ seed: 7, shuffle: { files: true, tests: true } }", id="fixed seed, both orders"),
]


@pytest.mark.parametrize("sequence", CLEAN_SEQUENCES)
def test_a_root_test_sequence_that_shuffles_both_orders_is_clean(build_repo, sequence):
    """VITEST-2, VITEST-3: `shuffle: true` or both orders under the root `test.sequence`, with a seed drawn at run time."""
    assert judged(build_repo, {PACKAGE: VITEST, CONFIG: config(sequence)}) == []


@pytest.mark.parametrize("sequence", FLAGGED_SEQUENCES)
def test_a_root_test_sequence_that_leaves_an_order_fixed_is_unshuffled_at_the_config(build_repo, sequence):
    """VITEST-2, VITEST-3: one order, no shuffle or a number seed is `unshuffled` at the config."""
    (finding,) = judged(build_repo, {PACKAGE: VITEST, CONFIG: config(sequence)})
    assert (finding.event, finding.path, finding.fact) == (UNSHUFFLED, CONFIG, FACT)
    assert finding.fix == f'set test.sequence.shuffle: true in {CONFIG}; vitest then prints Running tests with seed "N"'


@pytest.mark.parametrize("sequence", FIXED_SEEDS)
def test_a_number_seed_is_unshuffled_and_its_fix_draws_the_seed_at_run_time(build_repo, sequence):
    """VITEST-3: a number literal as `seed` fixes the order, whatever `shuffle` says."""
    (finding,) = judged(build_repo, {PACKAGE: VITEST, CONFIG: config(sequence)})
    assert (finding.event, finding.path, finding.fact) == (UNSHUFFLED, CONFIG, FACT)
    assert finding.fix == f'draw test.sequence.seed at run time or drop it in {CONFIG}; vitest then prints Running tests with seed "N"'


@pytest.mark.parametrize(
    "text",
    [
        pytest.param("export default defineConfig({ test: {} })\n", id="no sequence"),
        pytest.param("export default defineConfig({ test: { projects: [{ test: { sequence: { shuffle: true } } }] } })\n", id="only in a project"),
        pytest.param("export default defineConfig({ sequence: { shuffle: true }, test: {} })\n", id="outside the root test"),
        pytest.param("export default defineConfig({ plugins: [], test: { name: 'a' }, sequence: { shuffle: true } })\n", id="beside the root test"),
        pytest.param("export default { test: { include: [] } }\n", id="plain object"),
        pytest.param("export default defineConfig({})\n", id="no test"),
        pytest.param("export default shared\n", id="an imported config"),
        pytest.param("export default\n", id="nothing after export default"),
        pytest.param("module.exports = { test: { sequence: { shuffle: true } } }\n", id="no export default and no defineConfig"),
        pytest.param("export default { test: { sequence: {", id="an object that never closes"),
        pytest.param(
            "const other = defineConfig({ test: { sequence: { shuffle: true } } })\nexport default defineConfig({ test: {} })\n",
            id="a shuffling config that is not the exported one",
        ),
    ],
)
def test_a_sequence_that_is_not_the_root_test_sequence_counts_for_nothing(build_repo, text):
    """VITEST-2: a project's own or a misplaced `sequence` is no shuffle, and a config the file does not hold is none."""
    assert shape(judged(build_repo, {PACKAGE: VITEST, CONFIG: text})) == [(UNSHUFFLED, CONFIG)]


def test_a_file_without_export_default_reads_the_argument_of_its_first_define_config(build_repo):
    """VITEST-2: only a file without `export default` falls back to its first `defineConfig(` argument."""
    text = "module.exports = defineConfig({ test: { sequence: { shuffle: true } } })\n"
    assert judged(build_repo, {PACKAGE: VITEST, CONFIG: text}) == []


@pytest.mark.parametrize(
    ("wrapper", "clean"),
    [
        pytest.param("defineConfig({{{body}}})", True, id="object form"),
        pytest.param("defineConfig(() => ({{{body}}}))", True, id="arrow form"),
        pytest.param("defineConfig(async () => ({{{body}}}))", True, id="async arrow form"),
        pytest.param("defineConfig(({{ mode }}) => ({{{body}}}))", True, id="arrow with parameters"),
        pytest.param("{{{body}}}", True, id="plain default object"),
    ],
)
def test_every_form_of_the_exported_config_is_read(build_repo, wrapper, clean):
    """VITEST-2: the argument of `defineConfig(`, the arrow form included, and the plain `export default` object."""
    text = config("{ shuffle: true }", wrapper=wrapper)
    assert (judged(build_repo, {PACKAGE: VITEST, CONFIG: text}) == []) is clean


def test_a_project_that_turns_shuffle_off_under_a_shuffling_root_is_unshuffled(build_repo):
    """VITEST-2: any `shuffle: false` elsewhere in the file is a finding, since a vitest 5 project can turn the root's off."""
    text = (
        "export default defineConfig({ test: { sequence: { shuffle: true },"
        " projects: [{ test: { name: 'a', sequence: { shuffle: false } } }] } })\n"
    )
    assert shape(judged(build_repo, {PACKAGE: VITEST, CONFIG: text})) == [(UNSHUFFLED, CONFIG)]


def test_a_project_that_shuffles_under_a_shuffling_root_is_clean(build_repo):
    text = "export default defineConfig({ test: { sequence: { shuffle: true }, projects: [{ test: { name: 'a' } }] } })\n"
    assert judged(build_repo, {PACKAGE: VITEST, CONFIG: text}) == []


@pytest.mark.parametrize(
    "files",
    [
        pytest.param({}, id="no config"),
        pytest.param({"vite.config.ts": "export default defineConfig({ test: {} })\n"}, id="a vite config that does not shuffle"),
    ],
)
def test_a_project_without_a_shuffling_config_is_unshuffled_at_its_package_json_or_config(build_repo, files):
    """VITEST-2: the finding stands at the config, and at the package.json where no config is tracked."""
    findings = judged(build_repo, {PACKAGE: VITEST, **files})
    assert shape(findings) == [(UNSHUFFLED, next(iter(files), PACKAGE))]


def test_the_vitest_config_wins_over_the_vite_config(build_repo):
    """VITEST-1: `vitest.config` is read before `vite.config`, so a shuffle in the second leaves the first's findings."""
    files = {PACKAGE: VITEST, CONFIG: config("{}"), "vite.config.ts": config("{ shuffle: true }")}
    assert shape(judged(build_repo, files)) == [(UNSHUFFLED, CONFIG)]


def test_a_vite_config_is_read_where_no_vitest_config_is_tracked(build_repo):
    assert judged(build_repo, {PACKAGE: VITEST, "vite.config.mjs": config("{ shuffle: true }")}) == []


@pytest.mark.parametrize(
    "manifest",
    [
        pytest.param(package(), id="no dependency"),
        pytest.param(package(dev={"vite": "^7"}), id="vite only"),
        pytest.param(package(runtime={"vitest-plugin": "1"}), id="another name"),
        pytest.param(json.dumps({"name": "x", "devDependencies": ["vitest"]}), id="dependencies that are no object"),
    ],
)
def test_a_project_that_does_not_depend_on_vitest_is_clean(build_repo, manifest):
    """VITEST-1: only a package.json whose `dependencies` or `devDependencies` names `vitest` holds a vitest project."""
    assert judged(build_repo, {PACKAGE: manifest}) == []


@pytest.mark.parametrize("manifest", [package(dev={"vitest": "1"}), package(runtime={"vitest": "1"})], ids=["dev", "runtime"])
def test_either_dependency_table_makes_a_vitest_project(build_repo, manifest):
    assert shape(judged(build_repo, {PACKAGE: manifest})) == [(UNSHUFFLED, PACKAGE)]


def test_each_package_of_a_workspace_is_its_own_project(build_repo):
    """VITEST-1: the config is the first tracked one beside its own package.json."""
    files = {
        f"web/{PACKAGE}": VITEST, f"web/{CONFIG}": config("{ shuffle: true }"),
        f"api/{PACKAGE}": VITEST, f"api/{CONFIG}": config("{}"),
    }
    assert shape(judged(build_repo, files)) == [(UNSHUFFLED, f"api/{CONFIG}")]


def test_a_package_json_under_a_projects_tests_is_not_judged(build_repo):
    """VITEST-1: a fixture package under a Python project's `tests/` is no project."""
    files = {"pyproject.toml": '[project]\nname = "x"\n', f"tests/fixture/{PACKAGE}": VITEST}
    assert judged(build_repo, files) == []


@pytest.mark.parametrize(
    ("files", "reason"),
    [
        pytest.param({PACKAGE: VITEST, CONFIG: "export default { test: `a${"}, "vitest.config.ts", id="open template"),
        pytest.param({PACKAGE: VITEST, CONFIG: "/* open\nexport default {}"}, "vitest.config.ts", id="open comment"),
    ],
)
def test_a_config_that_does_not_close_is_exit_two_naming_the_file(build_repo, files, reason):
    """VITEST-4: a `ScriptSyntaxError` is a refusal naming the config."""
    assert reason in refusals(build_repo(files))[RULE]


def test_a_package_json_that_is_not_json_is_unparsable_as_naming_reports_it(build_repo):
    """VITEST-4: the shared reader refuses it as `unparsable`, naming the file."""
    verdicts = {verdict.rule: verdict for verdict in check_result(build_repo({PACKAGE: "{"})).verdicts}
    assert isinstance(verdicts[RULE].refusal, Unparsable)
    assert "package.json is not UTF-8 JSON" in str(verdicts[RULE].refusal)


def test_a_finding_is_exit_one(build_repo, capsys):
    assert run(capsys, build_repo({PACKAGE: VITEST}))[0] == 1
