import json

import pytest

from quality.cli import AreaEvent
from quality.report import Note, NoteEvent
from quality.rules import Rule
from tests.support.check import area_row, check_output, check_result, rule_findings, rule_notes

WORKFLOW = ".github/workflows/ci.yml"
NOT_CALLED_FACT = "no workflow step runs design-catalogs --check on a pull request, directly or through make"
MAKEFILE = "Makefile"
GNU_MAKEFILE = "GNUmakefile"
CONFIG = "quality/config.toml"
ROOT_MAKEFILE_ALLOWANCE = (
    '[[root.allow]]\nentry = "Makefile"\nreason = "the root Makefile holds the make goals of the workflows"\n'
)
COVERED_MAKEFILE = (
    ".PHONY: lint wire-check words-check\n"
    "lint: wire-check words-check\n"
    "\t$(MAKE) wire-check\n"
    "\t$(MAKE) words-check\n"
    "wire-check:\n"
    "\techo wired\n"
    "words-check:\n"
    "\tuv run design-catalogs --check\n"
)
NOT_CALLED = Note(".github/workflows", NoteEvent.NOT_CALLED, NOT_CALLED_FACT)
NOT_CALLED_LINE = f"· .github/workflows not called {NOT_CALLED_FACT}"
DECLARED = 'dependencies = ["overnightworks-design[catalogs]>=1"]'
HEAD = (
    "on: {on}\npermissions: {{}}\n"
    "concurrency:\n  group: ${{{{ github.workflow }}}}-${{{{ github.event.pull_request.number || github.run_id }}}}\n"
    "  cancel-in-progress: ${{{{ github.event.pull_request.number != null }}}}\n"
    "jobs:\n  j:\n    runs-on: ubuntu-latest\n    timeout-minutes: 15\n    steps:\n"
)


def pyproject(body: str) -> str:
    return f'[project]\nname = "app"\nversion = "0.1"\nrequires-python = ">=3.14"\n{body}\n'


def workflow(run: str, *step_lines: str, on: str = "pull_request") -> str:
    """A workflow with one step whose `run:` is `run`, and the step's extra keys above it."""
    step = (
        "      - name: t\n" + "".join(f"        {line}\n" for line in step_lines) + f"        run: {json.dumps(run)}\n"
    )
    return HEAD.format(on=on) + step


def catalog_verdict(repo):
    return next(verdict for verdict in check_result(repo).verdicts if verdict.rule is Rule.CATALOG_CHECK)


def normalised(out: str) -> list[str]:
    return [" ".join(line.split()) for line in out.splitlines()]


@pytest.mark.parametrize(
    "body",
    [
        pytest.param(DECLARED, id="project dependencies"),
        pytest.param(
            '[project.optional-dependencies]\nci = ["overnightworks-design[catalogs]>=1"]',
            id="optional-dependencies group",
        ),
        pytest.param('[dependency-groups]\ndev = ["overnightworks-design[catalogs]>=1"]', id="dependency-groups group"),
        pytest.param(
            'dependencies = ["Overnightworks_Design[Catalogs]==1.2"]', id="name and extra in another spelling"
        ),
        pytest.param(
            'dependencies = ["overnightworks-design[catalogs]; python_version >= \'3.12\'", "requests"]',
            id="a marker beside the entry",
        ),
        pytest.param(
            '[dependency-groups]\ndev = [{include-group = "lint"}, "overnightworks-design[catalogs]>=1"]',
            id="an include-group entry beside it",
        ),
    ],
)
def test_a_covered_repository_without_a_direct_call_gets_the_note_and_exit_zero(build_repo, capsys, body):
    """CATALOG-1, CATALOG-5: the declaration in any of the three places, with the name spelt another way; no note row beside
    a finding, and the workflows row stays clean."""
    repo = build_repo({"pyproject.toml": pyproject(body), WORKFLOW: workflow("make lint")})
    code, out = check_output(capsys, repo)
    assert code == 0
    assert rule_notes(repo, Rule.CATALOG_CHECK) == [NOT_CALLED]
    assert rule_findings(repo, Rule.CATALOG_CHECK) == []
    assert NOT_CALLED_LINE in normalised(out)
    assert area_row(out, "workflows").event is AreaEvent.CLEAN


@pytest.mark.parametrize(
    ("files", "id_"),
    [
        pytest.param({"pyproject.toml": pyproject("dependencies = []")}, "no dependency"),
        pytest.param({"pyproject.toml": pyproject('dependencies = ["overnightworks-design>=1"]')}, "the extra missing"),
        pytest.param(
            {"pyproject.toml": pyproject('dependencies = ["overnightworks-design[docs]>=1"]')}, "another extra"
        ),
        pytest.param(
            {"pyproject.toml": pyproject("dependencies = []\n\n[tool.design-catalogs]\nenabled = true")},
            "the table without the declaration",
        ),
        pytest.param(
            {
                "pyproject.toml": pyproject(
                    'dependencies = []\n\n[tool.uv]\ndev-dependencies = ["overnightworks-design[catalogs]>=1"]'
                )
            },
            "uv's legacy dev-dependencies",
        ),
        pytest.param(
            {WORKFLOW: workflow("uvx --from 'overnightworks-design[catalogs]' design-catalogs --check")},
            "no declaration, a direct call",
        ),
        pytest.param({}, "no pyproject.toml"),
    ],
)
def test_a_repository_that_does_not_declare_the_dependency_sees_nothing(build_repo, files, id_):
    """CATALOG-2, CATALOG-3, CATALOG-6: no note, and no refusal from the rule, whatever its workflows hold."""
    files = {**files, WORKFLOW: files.get(WORKFLOW, workflow("make lint"))}
    repo = build_repo(files)
    assert rule_notes(repo, Rule.CATALOG_CHECK) == []
    assert catalog_verdict(repo).refusal is None


def test_an_unreadable_workflow_does_not_refuse_an_uncovered_repository(build_repo):
    """CATALOG-3: no workflow is read for a repository that is not covered."""
    repo = build_repo({"pyproject.toml": pyproject("dependencies = []"), ".github/workflows/broken.yml": "on: [\n"})
    assert catalog_verdict(repo).refusal is None


@pytest.mark.parametrize(
    "run",
    [
        "design-catalogs --check",
        "uv run design-catalogs --check",
        "uvx --from 'overnightworks-design[catalogs]' design-catalogs --check",
        "env CI=1 design-catalogs --check",
        "sudo design-catalogs --check",
        "time design-catalogs --check",
        "FOO=1 design-catalogs --check",
        "timeout 60 design-catalogs --check",
        "sh -c 'design-catalogs --check'",
        "pnpm exec design-catalogs --check",
        "/usr/local/bin/design-catalogs --check",
        "design-catalogs \\\n  --check",
        "true && design-catalogs --check",
        "python -m design.catalogs.command --check",
    ],
)
def test_a_direct_call_of_the_check_in_any_reader_form_counts(build_repo, run):
    """CATALOG-7: the forms the shared shell reader reads as a call of a named tool."""
    repo = build_repo({"pyproject.toml": pyproject(DECLARED), WORKFLOW: workflow(run)})
    assert rule_notes(repo, Rule.CATALOG_CHECK) == []
    assert catalog_verdict(repo).refusal is None


@pytest.mark.parametrize(
    "run",
    [
        "make lint",
        "echo design-catalogs --check",
        "design-catalogs",
        "./scripts/check.sh",
        "tox -e lint",
    ],
)
def test_no_counting_call_gives_the_note(build_repo, run):
    """CATALOG-5, CATALOG-7, CATALOG-9: a call through make with no Makefile, a word in an echo, the check without its flag, a script and tox."""
    repo = build_repo({"pyproject.toml": pyproject(DECLARED), WORKFLOW: workflow(run)})
    assert rule_notes(repo, Rule.CATALOG_CHECK) == [NOT_CALLED]


def test_a_composite_or_reusable_call_is_no_call(build_repo):
    """CATALOG-9: a step that calls an action or a workflow through `uses:` reads as no call."""
    uses = (
        HEAD.format(on="pull_request")
        + "      - uses: a/b@0123456789012345678901234567890123456789\n        with:\n          run: design-catalogs --check\n"
    )
    repo = build_repo({"pyproject.toml": pyproject(DECLARED), WORKFLOW: uses})
    assert rule_notes(repo, Rule.CATALOG_CHECK) == [NOT_CALLED]


@pytest.mark.parametrize(
    ("step_lines", "on", "expect_note"),
    [
        pytest.param(("continue-on-error: true",), "pull_request", True, id="continue-on-error true"),
        pytest.param(("continue-on-error: false",), "pull_request", False, id="continue-on-error false"),
        pytest.param((), "push", True, id="push only"),
        pytest.param((), "pull_request_target", False, id="pull_request_target counts"),
        pytest.param(("working-directory: web",), "pull_request", True, id="another working directory"),
        pytest.param(("working-directory: .",), "pull_request", False, id="the root working directory"),
        pytest.param(
            ("if: github.event_name == 'pull_request'",), "pull_request", False, id="a step if counts (named gap)"
        ),
    ],
)
def test_the_step_conditions_decide_whether_the_call_counts(build_repo, step_lines, on, expect_note):
    """CATALOG-8: the trigger, continue-on-error, the working directory; a step if is a named gap that still counts."""
    repo = build_repo(
        {"pyproject.toml": pyproject(DECLARED), WORKFLOW: workflow("design-catalogs --check", *step_lines, on=on)}
    )
    assert (rule_notes(repo, Rule.CATALOG_CHECK) == [NOT_CALLED]) is expect_note


def test_or_true_still_counts(build_repo):
    """CATALOG-8: `|| true` is a named gap; the call still counts."""
    repo = build_repo({"pyproject.toml": pyproject(DECLARED), WORKFLOW: workflow("design-catalogs --check || true")})
    assert rule_notes(repo, Rule.CATALOG_CHECK) == []


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        pytest.param(None, "pyproject.toml is not UTF-8", id="not UTF-8"),
        pytest.param("[project\n", "pyproject.toml is not TOML", id="invalid TOML"),
        pytest.param(
            'dependencies = "overnightworks-design[catalogs]"',
            "pyproject.toml has a dependency list that is no list",
            id="list is a string",
        ),
        pytest.param(
            "dependencies = [3]", "pyproject.toml has an entry that is no requirement", id="entry is an integer"
        ),
        pytest.param(
            'dependencies = ["overnightworks-design[catalogs"]',
            "pyproject.toml has an invalid requirement",
            id="invalid requirement",
        ),
        pytest.param(
            f'{DECLARED}\n[dependency-groups]\ndev = ["!!bad"]',
            "pyproject.toml has a requirement quality cannot read",
            id="invalid requirement after the covered entry",
        ),
    ],
)
def test_a_pyproject_quality_cannot_read_is_exit_two(build_repo, body, reason):
    """CATALOG-4: exit 2 naming pyproject.toml, also in a repository that turns out not to be covered."""
    repo = build_repo({"pyproject.toml": pyproject(body or DECLARED), WORKFLOW: workflow("make lint")})
    if body is None:
        (repo / "pyproject.toml").write_bytes(b'[project]\nname = "\xff"\n')
    refusal = catalog_verdict(repo).refusal
    assert refusal is not None, reason
    assert "pyproject.toml" in str(refusal)


@pytest.mark.parametrize(
    "workflow_text",
    [
        pytest.param("on: pull_request\njobs: [\n", id="unparsable YAML"),
        pytest.param(
            HEAD.format(on="pull_request") + "      - name: t\n        run:\n          a: b\n", id="run is a mapping"
        ),
        pytest.param(
            "on: pull_request\nx: &steps\n  - run: make lint\njobs:\n  j:\n    runs-on: ubuntu-latest\n    steps: *steps\n",
            id="an alias hides the steps",
        ),
        pytest.param(
            "on: pull_request\njobs:\n  j:\n    runs-on: ubuntu-latest\n    <<: {steps: []}\n",
            id="a merge key hides the job",
        ),
    ],
)
def test_a_workflow_the_rule_cannot_read_in_a_covered_repository_is_exit_two(build_repo, workflow_text):
    """CATALOG-11: a workflow that cannot be read, a run that is no string, an alias or a merge key that can hide a step."""
    repo = build_repo({"pyproject.toml": pyproject(DECLARED), WORKFLOW: workflow_text})
    refusal = catalog_verdict(repo).refusal
    assert refusal is not None
    assert WORKFLOW in str(refusal)


def test_a_covered_repository_with_an_unreadable_workflow_exits_two_through_the_door(build_repo, capsys):
    """CATALOG-11: the exit code of the door, not only the verdict."""
    repo = build_repo({"pyproject.toml": pyproject(DECLARED), WORKFLOW: "on: [\n"})
    code, _ = check_output(capsys, repo)
    assert code == 2


def test_a_make_goal_that_reaches_the_check_in_the_makefile_gets_no_note(build_repo):
    """CATALOG-5, CATALOG-12, CATALOG-14 to CATALOG-18, CATALOG-21: `lint` reaches `words-check` through `$(MAKE)`, and the
    check's line is the same reading as a direct call."""
    repo = build_repo(
        {"pyproject.toml": pyproject(DECLARED), WORKFLOW: workflow("make lint"), MAKEFILE: COVERED_MAKEFILE}
    )
    assert rule_notes(repo, Rule.CATALOG_CHECK) == []


@pytest.mark.parametrize(
    "run",
    [
        pytest.param("make test", id="the goal is not in the file"),
        pytest.param("make -C sub lint", id="make -C reads another directory"),
        pytest.param("make -j4 lint", id="a flag makes no call of make"),
    ],
)
def test_a_make_goal_that_does_not_reach_the_check_gets_the_note_with_the_new_fact(build_repo, capsys, run):
    """CATALOG-5, CATALOG-12: a goal that does not reach the check, or a make call with a flag, which is no call of make, gets
    the note, its fact word for word, and exit 0."""
    repo = build_repo(
        {
            "pyproject.toml": pyproject(DECLARED),
            WORKFLOW: workflow(run),
            MAKEFILE: COVERED_MAKEFILE,
            CONFIG: ROOT_MAKEFILE_ALLOWANCE,
        }
    )
    code, out = check_output(capsys, repo)
    assert code == 0
    assert rule_notes(repo, Rule.CATALOG_CHECK) == [NOT_CALLED]
    assert NOT_CALLED_LINE in normalised(out)


def test_a_recipe_line_whose_command_is_built_at_run_time_gets_the_note(build_repo):
    """CATALOG-5, CATALOG-22: `lint` reaches a line whose command word is a make variable; that line is no call, so the note prints."""
    repo = build_repo(
        {
            "pyproject.toml": pyproject(DECLARED),
            WORKFLOW: workflow("make lint"),
            MAKEFILE: "lint:\n\t$(UV) run design-catalogs --check\n",
        }
    )
    assert rule_notes(repo, Rule.CATALOG_CHECK) == [NOT_CALLED]


def test_a_gnumakefile_beside_the_makefile_makes_the_whole_reading_no_call(build_repo):
    """CATALOG-13: make reads `GNUmakefile` first, so a tracked one beside the `Makefile` gives the note."""
    repo = build_repo(
        {
            "pyproject.toml": pyproject(DECLARED),
            WORKFLOW: workflow("make lint"),
            MAKEFILE: COVERED_MAKEFILE,
            GNU_MAKEFILE: "all:\n\tuv run design-catalogs --check\n",
        }
    )
    assert rule_notes(repo, Rule.CATALOG_CHECK) == [NOT_CALLED]


def test_an_untracked_makefile_is_no_call(build_repo):
    """CATALOG-13: only the tracked root `Makefile` is read; one on disk that git does not track gives the note."""
    repo = build_repo({"pyproject.toml": pyproject(DECLARED), WORKFLOW: workflow("make lint")})
    (repo / MAKEFILE).write_text(COVERED_MAKEFILE)
    assert rule_notes(repo, Rule.CATALOG_CHECK) == [NOT_CALLED]


def test_a_make_step_with_continue_on_error_gives_the_note(build_repo):
    """CATALOG-8, CATALOG-21: a step with `continue-on-error: true` does not count, even when its goal reaches the check."""
    repo = build_repo(
        {
            "pyproject.toml": pyproject(DECLARED),
            WORKFLOW: workflow("make lint", "continue-on-error: true"),
            MAKEFILE: COVERED_MAKEFILE,
        }
    )
    assert rule_notes(repo, Rule.CATALOG_CHECK) == [NOT_CALLED]


def test_a_direct_call_beside_a_non_utf8_makefile_is_no_refusal(build_repo):
    """CATALOG-23: the Makefile is read only when no counted step calls the generator directly."""
    repo = build_repo(
        {
            "pyproject.toml": pyproject(DECLARED),
            WORKFLOW: workflow("design-catalogs --check"),
            MAKEFILE: COVERED_MAKEFILE,
        }
    )
    (repo / MAKEFILE).write_bytes(b"\xff\n")
    assert catalog_verdict(repo).refusal is None
    assert rule_notes(repo, Rule.CATALOG_CHECK) == []


def test_a_make_goal_beside_a_non_utf8_makefile_is_exit_two_naming_the_makefile(build_repo, capsys):
    """CATALOG-23: a counted make step with no direct call reads the Makefile; one that is not UTF-8 is exit 2."""
    repo = build_repo(
        {"pyproject.toml": pyproject(DECLARED), WORKFLOW: workflow("make lint"), MAKEFILE: COVERED_MAKEFILE}
    )
    (repo / MAKEFILE).write_bytes(b"\xff\n")
    refusal = catalog_verdict(repo).refusal
    assert refusal is not None
    assert "Makefile" in str(refusal)
    code, _ = check_output(capsys, repo)
    assert code == 2


def test_a_non_utf8_makefile_of_an_uncovered_repository_is_no_refusal(build_repo):
    """CATALOG-3, CATALOG-23: a repository that does not declare the generator reads no Makefile."""
    repo = build_repo(
        {"pyproject.toml": pyproject("dependencies = []"), WORKFLOW: workflow("make lint"), MAKEFILE: COVERED_MAKEFILE}
    )
    (repo / MAKEFILE).write_bytes(b"\xff\n")
    assert catalog_verdict(repo).refusal is None
    assert rule_notes(repo, Rule.CATALOG_CHECK) == []


@pytest.mark.parametrize(
    ("run", "recipe", "rule"),
    [
        pytest.param("make test", "\tpytest -q", Rule.PYTEST_WORKERS, id="pytest-workers"),
        pytest.param("make install", "\tapt-get install -y x", Rule.RUNTIME_INSTALL, id="runtime-install"),
    ],
)
def test_the_other_rules_still_read_a_make_call_as_their_named_gap(build_repo, run, recipe, rule):
    """CATALOG-21: `make` stays a gap of pytest-workers and runtime-install; their finding is not read from a Makefile recipe."""
    repo = build_repo({WORKFLOW: workflow(run), MAKEFILE: f"test:\n{recipe}\ninstall:\n{recipe}\n"})
    assert rule_findings(repo, rule) == []
