import json

import pytest

from quality.report import FindingEvent
from quality.rules import Rule
from quality.workflow_rules import UNREADABLE_FACT
from tests.support.check import check_output, check_result, rule_findings, rule_notes

WORKFLOW = ".github/workflows/ci.yml"
WORKFLOWS_DIRECTORY = ".github/workflows"
MISSING_FACT = "no workflow step runs design-catalogs --check on a pull request, directly or through make"
RUN_TIME_FACT = "command is built at run time, design-catalogs --check cannot be read"
FIX = (
    "add a step to a pull-request workflow, or a make target that make lint runs: "
    "uv run design-catalogs --check, from the repository root"
)
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
MISSING = (WORKFLOWS_DIRECTORY, None, FindingEvent.MISSING, MISSING_FACT)
MISSING_LINE = f"! {WORKFLOWS_DIRECTORY} missing {MISSING_FACT}"
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


def block_workflow(*script: str, on: str = "pull_request") -> str:
    """A workflow with one step whose `run: |` holds the script lines, one per file line."""
    return HEAD.format(on=on) + "      - name: t\n        run: |\n" + "".join(f"          {line}\n" for line in script)


def line_of(text: str, needle: str) -> int:
    """The file line (from 1) of the first line of `text` that holds `needle`."""
    return next(number for number, line in enumerate(text.splitlines(), start=1) if needle in line)


def rows(repo):
    """The `catalog-check` findings as path, line, event and fact."""
    return [(found.path, found.line, found.event, found.fact) for found in rule_findings(repo, Rule.CATALOG_CHECK)]


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
def test_a_covered_repository_without_a_call_gets_one_missing_finding_and_exit_one(build_repo, capsys, body):
    """CATALOG-1, CATALOG-5, CATALOG-10, CATALOG-25: the declaration in any of the three places, with the name spelt another
    way; one row at the workflows folder with no line, its fix, no note beside it."""
    repo = build_repo({"pyproject.toml": pyproject(body), WORKFLOW: workflow("make lint")})
    code, out = check_output(capsys, repo)
    assert code == 1
    assert rows(repo) == [MISSING]
    assert rule_findings(repo, Rule.CATALOG_CHECK)[0].fix == FIX
    assert rule_notes(repo, Rule.CATALOG_CHECK) == []
    assert MISSING_LINE in normalised(out)
    assert f"fix: {FIX}" in normalised(out)
    assert "! workflows 1 finding" in normalised(out)


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
    """CATALOG-2, CATALOG-3, CATALOG-6: no row, and no refusal from the rule, whatever its workflows hold."""
    files = {**files, WORKFLOW: files.get(WORKFLOW, workflow("make lint"))}
    repo = build_repo(files)
    assert rows(repo) == []
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
    """CATALOG-7, CATALOG-24: the forms the shared shell reader reads as a call of a named tool are clean."""
    repo = build_repo({"pyproject.toml": pyproject(DECLARED), WORKFLOW: workflow(run)})
    assert rows(repo) == []
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
def test_no_counting_call_gives_the_missing_finding(build_repo, run):
    """CATALOG-5, CATALOG-7, CATALOG-9, CATALOG-25: a call through make with no Makefile, a word in an echo, the check without
    its flag, a script and tox."""
    repo = build_repo({"pyproject.toml": pyproject(DECLARED), WORKFLOW: workflow(run)})
    assert rows(repo) == [MISSING]


def test_a_composite_or_reusable_call_is_no_call(build_repo):
    """CATALOG-9, CATALOG-25: a step that calls an action or a workflow through `uses:` reads as no call."""
    uses = (
        HEAD.format(on="pull_request")
        + "      - uses: a/b@0123456789012345678901234567890123456789\n        with:\n          run: design-catalogs --check\n"
    )
    repo = build_repo({"pyproject.toml": pyproject(DECLARED), WORKFLOW: uses})
    assert rows(repo) == [MISSING]


@pytest.mark.parametrize(
    ("step_lines", "on", "expect_missing"),
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
def test_the_step_conditions_decide_whether_the_call_counts(build_repo, step_lines, on, expect_missing):
    """CATALOG-8, CATALOG-24, CATALOG-25, CATALOG-31: the trigger, continue-on-error, the working directory; a step if is a
    named gap that still counts."""
    repo = build_repo(
        {"pyproject.toml": pyproject(DECLARED), WORKFLOW: workflow("design-catalogs --check", *step_lines, on=on)}
    )
    assert rows(repo) == ([MISSING] if expect_missing else [])


def test_or_true_still_counts(build_repo):
    """CATALOG-8, CATALOG-31: `|| true` is a named gap; the call still counts."""
    repo = build_repo({"pyproject.toml": pyproject(DECLARED), WORKFLOW: workflow("design-catalogs --check || true")})
    assert rows(repo) == []


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


def test_a_make_goal_that_reaches_the_check_in_the_makefile_is_clean(build_repo, capsys):
    """CATALOG-12, CATALOG-14 to CATALOG-18, CATALOG-21, CATALOG-24: `lint` reaches `words-check` through `$(MAKE)`, and the
    check's line is the same reading as a direct call: no row and exit 0."""
    repo = build_repo(
        {
            "pyproject.toml": pyproject(DECLARED),
            WORKFLOW: workflow("make lint"),
            MAKEFILE: COVERED_MAKEFILE,
            CONFIG: ROOT_MAKEFILE_ALLOWANCE,
        }
    )
    code, _ = check_output(capsys, repo)
    assert (code, rows(repo)) == (0, [])


@pytest.mark.parametrize(
    "run",
    [
        pytest.param("make test", id="the goal is not in the file"),
        pytest.param("make -C sub lint", id="make -C reads another directory"),
        pytest.param("make -j4 lint", id="a flag makes no call of make"),
    ],
)
def test_a_make_goal_that_does_not_reach_the_check_gets_the_missing_finding(build_repo, capsys, run):
    """CATALOG-5, CATALOG-12, CATALOG-22, CATALOG-25: a goal that does not reach the check, or a make call with a flag, which
    is no call of make, gets the finding, its fact word for word, and exit 1."""
    repo = build_repo(
        {
            "pyproject.toml": pyproject(DECLARED),
            WORKFLOW: workflow(run),
            MAKEFILE: COVERED_MAKEFILE,
            CONFIG: ROOT_MAKEFILE_ALLOWANCE,
        }
    )
    code, out = check_output(capsys, repo)
    assert code == 1
    assert rows(repo) == [MISSING]
    assert MISSING_LINE in normalised(out)


def test_a_recipe_line_whose_command_is_a_make_variable_and_names_the_generator_is_invalid_at_its_makefile_line(
    build_repo, capsys
):
    """CATALOG-22, CATALOG-27, CATALOG-28: `lint` reaches `$(UV) run design-catalogs --check`; the line is no call and is built
    at run time, so it is an `invalid` row at `Makefile` and its line, with the fix and exit 1, and no `missing` beside it."""
    makefile = "lint:\n\t$(UV) run design-catalogs --check\n"
    repo = build_repo({"pyproject.toml": pyproject(DECLARED), WORKFLOW: workflow("make lint"), MAKEFILE: makefile})
    code, out = check_output(capsys, repo)
    expected = (MAKEFILE, line_of(makefile, "$(UV)"), FindingEvent.INVALID, RUN_TIME_FACT)
    assert (code, rows(repo)) == (1, [expected])
    assert rule_findings(repo, Rule.CATALOG_CHECK)[0].fix == FIX
    assert f"! Makefile:{expected[1]} invalid {RUN_TIME_FACT}" in normalised(out)


def test_a_gnumakefile_beside_the_makefile_makes_the_whole_reading_no_call(build_repo):
    """CATALOG-13, CATALOG-25: make reads `GNUmakefile` first, so a tracked one beside the `Makefile` gives the finding."""
    repo = build_repo(
        {
            "pyproject.toml": pyproject(DECLARED),
            WORKFLOW: workflow("make lint"),
            MAKEFILE: COVERED_MAKEFILE,
            GNU_MAKEFILE: "all:\n\tuv run design-catalogs --check\n",
        }
    )
    assert rows(repo) == [MISSING]


def test_an_untracked_makefile_is_no_call(build_repo):
    """CATALOG-13, CATALOG-25: only the tracked root `Makefile` is read; one on disk that git does not track gives the finding."""
    repo = build_repo({"pyproject.toml": pyproject(DECLARED), WORKFLOW: workflow("make lint")})
    (repo / MAKEFILE).write_text(COVERED_MAKEFILE)
    assert rows(repo) == [MISSING]


def test_a_make_step_with_continue_on_error_gives_the_missing_finding(build_repo):
    """CATALOG-8, CATALOG-21, CATALOG-25: a step with `continue-on-error: true` does not count, even when its goal reaches the check."""
    repo = build_repo(
        {
            "pyproject.toml": pyproject(DECLARED),
            WORKFLOW: workflow("make lint", "continue-on-error: true"),
            MAKEFILE: COVERED_MAKEFILE,
        }
    )
    assert rows(repo) == [MISSING]


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
    assert rows(repo) == []


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
    assert rows(repo) == []


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


def steps_workflow(*runs: str) -> str:
    """A workflow with one step per script, each a single `run:` line."""
    return HEAD.format(on="pull_request") + "".join(f"      - run: {json.dumps(run)}\n" for run in runs)


def covered(build_repo, workflow_text: str, **files: str):
    return build_repo({"pyproject.toml": pyproject(DECLARED), WORKFLOW: workflow_text, **files})


def workflow_row(text: str, event: FindingEvent, fact: str, needle: str) -> tuple:
    return (WORKFLOW, line_of(text, needle), event, fact)


@pytest.mark.parametrize(
    ("run", "invalid"),
    [
        pytest.param('design-catalogs --check "unterminated', True, id="an unterminated quote beside the word"),
        pytest.param('python -m design.catalogs.command "--check', True, id="the module word, unterminated"),
        pytest.param('echo "unterminated', False, id="an unterminated quote without the word"),
        pytest.param('my-design-catalogs "unterminated', False, id="the word inside a longer name"),
    ],
)
def test_a_line_the_reader_refuses_is_invalid_only_when_its_own_text_names_the_generator(build_repo, run, invalid):
    """CATALOG-24, CATALOG-25, CATALOG-26, CATALOG-28: the refused line is an `invalid` row at the workflow and the line, and
    no `missing` stands beside it; without the word, or with the word only inside a longer name, the line is no problem and
    the repository is `missing`."""
    text = workflow(run)
    repo = covered(build_repo, text)
    expected = workflow_row(text, FindingEvent.INVALID, UNREADABLE_FACT, "run:")
    assert rows(repo) == ([expected] if invalid else [MISSING])


def test_an_invalid_finding_is_exit_one_through_the_door_with_the_fix(build_repo, capsys):
    """CATALOG-10, CATALOG-26, CATALOG-28: the row names the workflow and the first line of the refused logical line, and the
    fix of the missing finding follows it."""
    text = block_workflow("echo start", 'design-catalogs --check "unterminated')
    repo = covered(build_repo, text)
    code, out = check_output(capsys, repo)
    line = line_of(text, "design-catalogs")
    assert code == 1
    assert f"! {WORKFLOW}:{line} invalid {UNREADABLE_FACT}" in normalised(out)
    assert f"fix: {FIX}" in normalised(out)


@pytest.mark.parametrize(
    ("script", "invalid"),
    [
        pytest.param(("design-catalogs --version", "$TOOL --check"), True, id="the script names the generator"),
        pytest.param(("$TOOL --check",), False, id="the script never names it"),
        pytest.param(("echo design-catalogs", "eval \"$X\""), True, id="eval beside the word"),
    ],
)
def test_a_command_built_at_run_time_is_invalid_when_the_whole_script_names_the_generator(build_repo, script, invalid):
    """CATALOG-27, CATALOG-28: the word is tested on the script as a whole, not on the line that is built at run time."""
    text = block_workflow(*script)
    repo = covered(build_repo, text)
    expected = (WORKFLOW, line_of(text, script[-1]), FindingEvent.INVALID, RUN_TIME_FACT)
    assert rows(repo) == ([expected] if invalid else [MISSING])


def test_the_word_in_another_step_does_not_make_a_run_time_command_invalid(build_repo):
    """CATALOG-27: "the script" is one step's `run:`, so a step that names the generator does not turn another step's line."""
    repo = covered(build_repo, steps_workflow("echo design-catalogs", "$TOOL --check"))
    assert rows(repo) == [MISSING]


def test_a_counting_call_in_any_step_leaves_an_invalid_line_without_a_row(build_repo):
    """CATALOG-24: any counted step with a call makes the rule clean, whatever else the workflows hold."""
    repo = covered(build_repo, steps_workflow('design-catalogs --check "unterminated', "design-catalogs --check"))
    assert rows(repo) == []


def test_a_goal_that_reaches_the_check_leaves_an_invalid_line_without_a_row(build_repo):
    """CATALOG-24: a call through make counts like a direct one for the verdict."""
    repo = covered(
        build_repo, steps_workflow('design-catalogs --check "unterminated', "make lint"), **{MAKEFILE: COVERED_MAKEFILE}
    )
    assert rows(repo) == []


def test_an_invalid_line_and_a_goal_without_a_call_give_the_invalid_row_only(build_repo):
    """CATALOG-24: `missing` is the verdict only when nothing is `invalid`."""
    text = steps_workflow('design-catalogs --check "unterminated', "make test")
    repo = covered(build_repo, text, **{MAKEFILE: "test:\n\techo tested\n"})
    assert rows(repo) == [workflow_row(text, FindingEvent.INVALID, UNREADABLE_FACT, "unterminated")]


def test_an_unreadable_recipe_line_that_names_the_generator_is_invalid_at_its_makefile_line(build_repo):
    """CATALOG-26, CATALOG-28: a reached recipe line is read as a script line, and stands at `Makefile` and its first line."""
    makefile = "lint:\n\techo start\n\tdesign-catalogs --check \"unterminated\n"
    repo = covered(build_repo, workflow("make lint"), **{MAKEFILE: makefile})
    assert rows(repo) == [(MAKEFILE, line_of(makefile, "design-catalogs"), FindingEvent.INVALID, UNREADABLE_FACT)]


def test_a_recipe_naming_the_generator_without_the_check_is_missing_not_invalid(build_repo):
    """CATALOG-24, CATALOG-27: `$(MAKE) words-check` is the make substitution, no command built at run time."""
    makefile = ".PHONY: lint words-check\nlint:\n\t$(MAKE) words-check\nwords-check:\n\tuv run design-catalogs\n"
    repo = covered(build_repo, workflow("make lint"), **{MAKEFILE: makefile})
    assert rows(repo) == [MISSING]


def test_every_make_variable_command_of_a_recipe_that_names_the_generator_is_invalid(build_repo):
    """CATALOG-27: the reached recipes are one text, so once a line names the generator each variable command is a row, in the
    order the scan reaches them."""
    makefile = "lint:\n\techo design-catalogs --check\n\t$(UV) run ruff\n\t$(UV) run pytest\n"
    repo = covered(build_repo, workflow("make lint"), **{MAKEFILE: makefile})
    assert rows(repo) == [
        (MAKEFILE, line_of(makefile, "ruff"), FindingEvent.INVALID, RUN_TIME_FACT),
        (MAKEFILE, line_of(makefile, "pytest"), FindingEvent.INVALID, RUN_TIME_FACT),
    ]


def test_an_invalid_line_of_a_workflow_stands_before_one_of_the_makefile(build_repo):
    """CATALOG-28: workflow findings in the walk's order, then the `Makefile`'s."""
    makefile = "lint:\n\t$(UV) run design-catalogs --check\n"
    text = steps_workflow('design-catalogs "unterminated', "make lint")
    repo = covered(build_repo, text, **{MAKEFILE: makefile})
    assert rows(repo) == [
        workflow_row(text, FindingEvent.INVALID, UNREADABLE_FACT, "unterminated"),
        (MAKEFILE, line_of(makefile, "$(UV)"), FindingEvent.INVALID, RUN_TIME_FACT),
    ]


@pytest.mark.parametrize(
    ("run", "refuses"),
    [("make lint", True), ("design-catalogs --check", False)],
    ids=["the goal is the only route", "a direct call beside it"],
)
def test_a_makefile_symlinked_out_of_the_repository_is_exit_two_only_when_it_is_read(
    build_repo, elsewhere, capsys, run, refuses
):
    """CATALOG-23, CATALOG-24: the file is read only when no counted step calls the check directly; a link out of the
    repository is refused naming `Makefile`."""
    repo = covered(build_repo, workflow(run), **{MAKEFILE: COVERED_MAKEFILE, CONFIG: ROOT_MAKEFILE_ALLOWANCE})
    outside = elsewhere / MAKEFILE
    outside.write_text(COVERED_MAKEFILE)
    (repo / MAKEFILE).unlink()
    (repo / MAKEFILE).symlink_to(outside)
    refusal = catalog_verdict(repo).refusal
    assert (refusal is not None) is refuses
    if refusal is not None:
        assert MAKEFILE in str(refusal)
        code, _ = check_output(capsys, repo)
        assert code == 2


@pytest.mark.parametrize(
    ("text", "files"),
    [
        pytest.param(block_workflow("cat <<EOF", "design-catalogs --check", "EOF"), {}, id="a here-document body line"),
        pytest.param(workflow("if false; then design-catalogs --check; fi"), {}, id="a call inside if false"),
        pytest.param(workflow("cd sub && design-catalogs --check"), {}, id="a call behind cd sub"),
        pytest.param(workflow("uv run --with design-catalogs --check"), {}, id="the generator as an option value of uv run"),
        pytest.param(workflow("design-catalogs --check || true"), {}, id="|| true in a step"),
        pytest.param(
            workflow("make lint"), {MAKEFILE: "lint:\n\tdesign-catalogs --check || true\n"}, id="|| true in a recipe line"
        ),
        pytest.param(workflow("design-catalogs --check", on="pull_request_review"), {}, id="pull_request_review as trigger"),
        pytest.param(
            workflow("design-catalogs --check", on="{pull_request: {branches: [release]}}"), {}, id="a trigger filter"
        ),
        pytest.param(
            workflow("design-catalogs --check").replace(
                "    runs-on: ubuntu-latest\n", "    if: false\n    runs-on: ubuntu-latest\n"
            ),
            {},
            id="a job-level if",
        ),
        pytest.param(workflow("design-catalogs --check", "shell: bash"), {}, id="a step shell"),
        pytest.param(
            workflow("design-catalogs --check").replace(
                "jobs:\n", "defaults:\n  run:\n    working-directory: sub\njobs:\n"
            ),
            {},
            id="a default working directory",
        ),
        pytest.param(
            workflow("make lint"), {MAKEFILE: "SHELL = /bin/zsh\nlint:\n\tdesign-catalogs --check\n"}, id="a SHELL assignment"
        ),
        pytest.param(
            workflow("make lint"),
            {MAKEFILE: ".SHELLFLAGS := -ec\nlint:\n\tdesign-catalogs --check\n"},
            id="a .SHELLFLAGS assignment",
        ),
        pytest.param(
            workflow("make lint"),
            {MAKEFILE: "lint:\n\tdesign-catalogs --check\n", "lint": "a tracked file of the target's name\n"},
            id="a target that is not .PHONY while a file of its name is tracked",
        ),
        pytest.param(
            workflow("make lint"),
            {MAKEFILE: "lint:\n\tdesign-catalogs --check\nlint: ; echo hi\n"},
            id="a ; recipe after a tab recipe of the same target",
        ),
    ],
)
def test_the_named_gaps_that_err_green_read_as_a_counting_call(build_repo, text, files):
    """CATALOG-31: each form the docs name as a gap that errs green is clean."""
    assert rows(covered(build_repo, text, **files)) == []
