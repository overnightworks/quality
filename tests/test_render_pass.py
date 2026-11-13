import pytest

from quality.report import FindingEvent
from quality.rules import Rule
from tests.support.check import check_output, rule_findings

CONFIG = "quality/config.toml"
WORKFLOW = ".github/workflows/ci.yml"
DEFAULT_DIRECTORY = "frontend/src/lib/catalogs"
DECLARED = 'dependencies = ["overnightworks-design[catalogs]>=1"]'
GATE = '[gate]\nmandatory_jobs = ["checks", "Render pages"]\n'
LANGUAGES = '[languages]\nrender_pass = "Render pages"\n'
SETTING_FIX = "name the job that renders each page once per language in [languages] render_pass"


def pyproject(body: str = DECLARED, table: str = "") -> str:
    return f'[project]\nname = "app"\nversion = "0.1"\nrequires-python = ">=3.14"\n{body}\n{table}'


def workflow(*jobs: str) -> str:
    """A workflow whose jobs are the given `job_id: name` pairs; a name of `-` leaves the job without one."""
    body = "".join(
        f"  {identifier}:\n    runs-on: ubuntu-latest\n    timeout-minutes: 15\n"
        + ("" if label == "-" else f"    name: {label}\n")
        + "    steps:\n      - run: echo\n"
        for identifier, label in (job.split(": ", 1) for job in jobs)
    )
    return f"on: push\npermissions: {{}}\njobs:\n{body}"


def catalogs(directory: str = DEFAULT_DIRECTORY, *names: str) -> dict[str, str]:
    return {f"{directory}/{name}.po": "" for name in (names or ("de", "en"))}


def covered(extra: dict[str, str] | None = None, table: str = "", config: str | None = GATE + LANGUAGES) -> dict[str, str]:
    """A covered repository: two catalogs in the default directory, a workflow whose job is named `Render pages`."""
    files = {"pyproject.toml": pyproject(table=table), WORKFLOW: workflow("render: Render pages", "checks: -")}
    files |= catalogs() | (extra or {})
    if config is not None:
        files[CONFIG] = config
    return files


def rows(repo):
    return [(found.path, found.line, found.event, found.fact, found.fix) for found in rule_findings(repo, Rule.RENDER_PASS)]


def missing(fact: str, fix: str) -> list:
    return [(CONFIG, None, FindingEvent.MISSING, fact, fix)]


def test_a_covered_repository_with_two_catalogs_and_no_declaration_is_missing(build_repo, capsys):
    """RENDER-1, RENDER-3: no [languages] render_pass is a missing row at the config, and the run is red."""
    repo = build_repo(covered(config=GATE))
    assert rows(repo) == missing("2 catalogs and no render pass declared", SETTING_FIX)
    assert check_output(capsys, repo)[0] == 1


def test_a_covered_repository_without_a_config_file_is_missing_the_declaration(build_repo):
    """RENDER-3: a missing quality/config.toml yields the first finding."""
    assert rows(build_repo(covered(config=None))) == missing("2 catalogs and no render pass declared", SETTING_FIX)


def test_a_declared_job_no_tracked_workflow_holds_is_missing(build_repo):
    repo = build_repo(covered(config=GATE + '[languages]\nrender_pass = "Render everything"\n'))
    assert rows(repo) == missing('render pass "Render everything" is no job of a tracked workflow', "name a job a tracked workflow holds")


def test_a_declared_job_the_gate_does_not_require_is_missing(build_repo):
    repo = build_repo(covered(config='[gate]\nmandatory_jobs = ["checks"]\n' + LANGUAGES))
    assert rows(repo) == missing(
        'render pass "Render pages" is not a mandatory job', 'add "Render pages" to [gate] mandatory_jobs'
    )


def test_a_malformed_mandatory_jobs_list_leaves_the_declared_job_not_mandatory(build_repo):
    repo = build_repo(covered(config='[gate]\nmandatory_jobs = "checks"\n' + LANGUAGES))
    assert [row[3] for row in rows(repo)] == ['render pass "Render pages" is not a mandatory job']


def test_the_first_unmet_condition_alone_is_reported(build_repo):
    """Sentence 4: an undeclared pass that no job could satisfy gets the first finding only."""
    repo = build_repo(covered(extra={WORKFLOW: workflow("other: Other")}, config='[gate]\nmandatory_jobs = ["checks"]\n'))
    assert [row[3] for row in rows(repo)] == ["2 catalogs and no render pass declared"]


def test_the_id_of_a_named_job_is_no_name_the_run_lists(build_repo):
    """Sentence 5: a job with a `name:` is listed by it, not by its id."""
    repo = build_repo(covered(config='[gate]\nmandatory_jobs = ["render"]\n[languages]\nrender_pass = "render"\n'))
    assert [row[3] for row in rows(repo)] == ['render pass "render" is no job of a tracked workflow']


def test_a_job_without_a_name_is_held_by_its_id(build_repo):
    repo = build_repo(covered(config=GATE + '[languages]\nrender_pass = "checks"\n'))
    assert rows(repo) == []


def test_a_name_that_holds_an_expression_matches_no_declaration(build_repo):
    expression = "${{ matrix.language }}"
    repo = build_repo(
        covered(
            extra={WORKFLOW: workflow(f"render: {expression}")},
            config=f'[gate]\nmandatory_jobs = ["{expression}"]\n[languages]\nrender_pass = "{expression}"\n',
        )
    )
    assert [row[3] for row in rows(repo)] == [f'render pass "{expression}" is no job of a tracked workflow']


def test_a_job_of_any_tracked_workflow_counts(build_repo):
    repo = build_repo(covered(extra={WORKFLOW: workflow("checks: -"), ".github/workflows/pages.yml": workflow("render: Render pages")}))
    assert rows(repo) == []


def test_all_three_held_has_no_row_and_the_run_is_clean_of_the_rule(build_repo, capsys):
    repo = build_repo(covered())
    assert rows(repo) == []
    assert "render-pass" not in check_output(capsys, repo)[1]


@pytest.mark.parametrize(
    "files",
    [
        pytest.param({"pyproject.toml": pyproject("dependencies = []"), **catalogs()}, id="not covered"),
        pytest.param({"pyproject.toml": pyproject(), **catalogs(DEFAULT_DIRECTORY, "de")}, id="one catalog"),
        pytest.param({"pyproject.toml": pyproject()}, id="no catalog"),
        pytest.param({"pyproject.toml": pyproject(), **catalogs("slidev/catalogs")}, id="catalogs outside the directory"),
        pytest.param({"pyproject.toml": pyproject(), **catalogs(DEFAULT_DIRECTORY, "de"), f"{DEFAULT_DIRECTORY}/more/fr.po": ""}, id="nested file"),
        pytest.param({"pyproject.toml": pyproject(), **catalogs(DEFAULT_DIRECTORY, "de"), f"{DEFAULT_DIRECTORY}/en.pot": ""}, id="another suffix"),
    ],
)
def test_a_repository_with_fewer_than_two_catalogs_in_the_directory_is_not_judged(build_repo, files):
    """RENDER-1, RENDER-2: no row and no workflow read, even where the workflow could not be read."""
    repo = build_repo({**files, WORKFLOW: "on: [\n"})
    assert rows(repo) == []
    assert rule_findings(repo, Rule.RENDER_PASS) == []


def test_a_declared_directory_holds_the_catalogs(build_repo):
    """RENDER-2: `[tool.design-catalogs] catalogs` replaces the default, normalised."""
    table = '[tool.design-catalogs]\ncatalogs = "./app/words/../catalogs/"\n'
    repo = build_repo(
        {"pyproject.toml": pyproject(table=table), **catalogs("app/catalogs"), WORKFLOW: workflow("render: Render pages"), CONFIG: GATE}
    )
    assert [row[3] for row in rows(repo)] == ["2 catalogs and no render pass declared"]


def test_catalogs_in_the_default_directory_do_not_count_when_another_is_declared(build_repo):
    table = '[tool.design-catalogs]\ncatalogs = "app/catalogs"\n'
    assert rows(build_repo({"pyproject.toml": pyproject(table=table), **catalogs(), CONFIG: GATE})) == []


@pytest.mark.parametrize(
    "table",
    [
        '[tool.design-catalogs]\ncatalogs = "/abs"',
        '[tool.design-catalogs]\ncatalogs = ""',
        '[tool.design-catalogs]\ncatalogs = "."',
        '[tool.design-catalogs]\ncatalogs = "../out"',
        '[tool.design-catalogs]\ncatalogs = "a/../../out"',
        "[tool.design-catalogs]\ncatalogs = 3",
        "[tool]\ndesign-catalogs = 1",
    ],
    ids=["absolute", "empty", "dot", "parent", "escaping", "no string", "no table"],
)
def test_an_invalid_catalogs_path_in_a_covered_repository_is_exit_two_naming_the_pyproject(build_repo, capsys, table):
    """RENDER-2."""
    repo = build_repo({"pyproject.toml": pyproject(table=table), **catalogs(), CONFIG: GATE + LANGUAGES})
    code, out = check_output(capsys, repo)
    assert (code, "pyproject.toml" in out) == (2, True)


def test_an_invalid_catalogs_path_in_an_uncovered_repository_is_no_exit(build_repo, capsys):
    """RENDER-2: the path is read only where the repository is covered."""
    repo = build_repo({"pyproject.toml": pyproject("dependencies = []", '[tool.design-catalogs]\ncatalogs = "/abs"\n')})
    assert check_output(capsys, repo)[0] != 2
