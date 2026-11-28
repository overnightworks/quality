
import pytest

from quality.report import FindingEvent
from quality.rules import Rule
from tests.support.check import check_output as run
from tests.support.check import check_result, refusals, rule_findings
from tests.support.workflow_scripts import WORKFLOW, block_run

pytestmark = pytest.mark.integration

RULE = Rule.LIVE_LAYER
RUNS_LIVE, INVALID = FindingEvent.RUNS_LIVE, FindingEvent.INVALID
LIVE_TEST = "tests/e2e/live/test_sign_in.py"
LIVE = {LIVE_TEST: ""}
SCRIPT_LINE = 10
CAN_SELECT = "pytest can select tests marked live"
UNREADABLE_EXPRESSION = "-m expression cannot be read"
FIX = 'add -m "not live", or "and not live" to its -m expression'
MARKER = "# live-layer: the journeys run on the operator's machine"


def judged(build_repo, call: str, extra: dict[str, str] | None = None):
    """The findings of live-layer over a workflow holding the call plus extra files."""
    return rule_findings(build_repo({**LIVE, WORKFLOW: block_run(*call.split("\n")), **(extra or {})}), RULE)


CLEAN_CALLS = [
    pytest.param('pytest -m "e2e and not live"', id="narrowed"),
    pytest.param('pytest -m "not live"', id="excluded"),
    pytest.param('pytest -m "not (live or e2e)"', id="negated group"),
    pytest.param("pytest -mnot\\ live", id="glued"),
    pytest.param('uv run pytest -n auto -m "not live"', id="through a runner"),
    pytest.param('python -m pytest -m "not live"', id="through the interpreter"),
    pytest.param('pytest -m live -m "not live"', id="the last of two counts"),
    pytest.param('pytest -m "not live" -- -m live', id="a -m after -- is a path"),
    pytest.param("pytest --collect-only", id="listing only"),
    pytest.param('pytest --co -m live', id="listing with a live -m"),
    pytest.param("echo pytest", id="no call"),
]
RUNS_LIVE_CALLS = [
    pytest.param("pytest -n auto", id="no -m"),
    pytest.param("pytest -m e2e", id="another marker"),
    pytest.param('pytest -m "not e2e"', id="negated another marker"),
    pytest.param('pytest -m "live or x"', id="or another marker"),
    pytest.param('pytest -m "not live" -m e2e', id="the last of two counts"),
    pytest.param('pytest -m ""', id="empty selects all"),
    pytest.param('pytest -m "live(site=\'x\') or not live(site=\'x\')"', id="calls with arguments are other names"),
    pytest.param('pytest -- -m "not live"', id="a -m after -- is a path"),
    pytest.param("pytest --markers", id="markers is no -m"),
]


@pytest.mark.parametrize("call", CLEAN_CALLS)
def test_a_call_that_cannot_select_the_live_layer_is_clean(build_repo, call):
    """LIVE-2, LIVE-3: a call whose last -m is false whenever live is true, or that only lists tests, passes."""
    assert judged(build_repo, call) == []


@pytest.mark.parametrize("call", RUNS_LIVE_CALLS)
def test_a_call_that_can_select_the_live_layer_runs_live_at_its_first_line(build_repo, call):
    """LIVE-4: the finding is at the call's line with the fact and the fix the rule words."""
    (finding,) = judged(build_repo, call)
    assert (finding.event, finding.path, finding.line, finding.fact, finding.fix) == (RUNS_LIVE, WORKFLOW, SCRIPT_LINE, CAN_SELECT, FIX)


def test_addopts_do_not_clear_a_call(build_repo):
    """LIVE-3: pytest reads addopts before the call's own words, so the call's own -m decides."""
    pyproject = '[project]\nname = "x"\n\n[tool.pytest.ini_options]\naddopts = \'-m "not live"\'\n'
    assert len(judged(build_repo, "pytest -n auto", {"pyproject.toml": pyproject})) == 1


def test_pytest_addopts_in_the_environment_do_not_clear_a_call(build_repo):
    """LIVE-3: PYTEST_ADDOPTS is read before the call's words too."""
    assert len(judged(build_repo, "PYTEST_ADDOPTS='-m \"not live\"' pytest -n auto")) == 1


@pytest.mark.parametrize(
    "call",
    [
        pytest.param('pytest -m "${{ matrix.marker }}"', id="workflow expression"),
        pytest.param('pytest -m "$MARKERS"', id="variable"),
        pytest.param('pytest -m "$(echo not live)"', id="substitution"),
        pytest.param('pytest -m "not live and"', id="not an expression"),
        pytest.param('pytest -m "a b"', id="two names"),
    ],
)
def test_an_expression_built_at_run_time_or_outside_the_grammar_is_invalid(build_repo, call):
    """LIVE-5: a -m the rule cannot read is invalid, not clean."""
    (finding,) = judged(build_repo, call)
    assert (finding.event, finding.line, finding.fact) == (INVALID, SCRIPT_LINE, UNREADABLE_EXPRESSION)


def test_a_line_the_shell_reader_cannot_finish_is_invalid_as_in_the_other_pytest_rules(build_repo):
    """LIVE-5: an unterminated quote in a pytest line is `invalid` with the reader's own fact."""
    (finding,) = judged(build_repo, 'pytest -m "not live')
    assert (finding.event, finding.fact) == (INVALID, "command cannot be read to its end")


def test_a_pytest_call_built_at_run_time_is_invalid(build_repo):
    """LIVE-5: a command word chosen at run time hides whether it calls pytest."""
    findings = judged(build_repo, 'timeout 60 $PYTEST -q; pytest -m "not live"')
    assert [(finding.event, finding.fact) for finding in findings] == [(INVALID, "command is built at run time, pytest -m cannot be read")]


def test_no_marker_clears_a_finding(build_repo):
    """LIVE-7: no comment switches the rule off."""
    repo = build_repo({**LIVE, WORKFLOW: block_run(MARKER, "pytest -n auto")})
    assert [finding.event for finding in rule_findings(repo, RULE)] == [RUNS_LIVE]


@pytest.mark.parametrize("trigger", ["push", "{schedule: [{cron: '0 3 * * *'}]}", "pull_request", "workflow_dispatch"])
def test_a_workflow_is_judged_whatever_triggers_it(build_repo, trigger):
    """LIVE-2: a push, scheduled or manual run is CI too."""
    repo = build_repo({**LIVE, WORKFLOW: block_run("pytest -n auto", on=trigger)})
    assert [finding.event for finding in rule_findings(repo, RULE)] == [RUNS_LIVE]


def test_every_pytest_call_of_a_script_is_judged_on_its_own(build_repo):
    """LIVE-4: two calls, one clean, one not, give one finding at the second."""
    (finding,) = judged(build_repo, 'pytest -m "not live"\npytest -m e2e')
    assert finding.line == SCRIPT_LINE + 1


def test_the_extent_is_the_number_of_workflows_read(build_repo):
    """LIVE-2: the rule's extent counts workflows."""
    repo = build_repo({**LIVE, WORKFLOW: block_run("pytest -m 'not live'"), ".github/workflows/x.yml": block_run("echo", on="{schedule: [{cron: '0 3 * * *'}]}")})
    (verdict,) = (verdict for verdict in check_result(repo).verdicts if verdict.rule is RULE)
    assert (verdict.findings, verdict.extent) == ((), 2)


@pytest.mark.parametrize(
    "files",
    [
        pytest.param({}, id="no tests folder"),
        pytest.param({"tests/e2e/test_x.py": ""}, id="end-to-end without live"),
        pytest.param({"tests/live/test_x.py": ""}, id="live outside end-to-end"),
        pytest.param({"tests/unit/e2e/live/test_x.py": ""}, id="deeper than tests"),
        pytest.param({"docs/tests/e2e/live/test_x.py": ""}, id="in no judged project"),
    ],
)
def test_a_repository_without_a_live_layer_is_clean_at_extent_zero_and_reads_no_workflow(build_repo, files):
    """LIVE-1: no tracked path under tests/e2e/live/ means no live layer; a workflow no -m guards is not read."""
    repo = build_repo({**files, WORKFLOW: block_run("pytest -n auto")})
    (verdict,) = (verdict for verdict in check_result(repo).verdicts if verdict.rule is RULE)
    assert (verdict.findings, verdict.extent) == ((), 0)


def test_a_workflow_the_rule_would_read_is_not_read_without_a_live_layer(build_repo):
    """LIVE-1: an unreadable workflow is no exit 2 of this rule while no live layer exists."""
    assert RULE not in refusals(build_repo({WORKFLOW: "jobs: [\n"}))


@pytest.mark.parametrize(
    "files",
    [
        pytest.param({LIVE_TEST: ""}, id="at the root"),
        pytest.param({"svc/pyproject.toml": '[project]\nname = "svc"\n', "svc/tests/e2e/live/test_x.py": ""}, id="in a judged project"),
        pytest.param({"tests/e2e/live/journeys/deep/test_x.py": ""}, id="in a subfolder"),
    ],
)
def test_a_tracked_path_under_a_live_folder_makes_a_live_layer(build_repo, files):
    """LIVE-1: the root's tests/ and a judged project's tests/ each hold the live layer."""
    repo = build_repo({**files, WORKFLOW: block_run("pytest -n auto")})
    assert [finding.event for finding in rule_findings(repo, RULE)] == [RUNS_LIVE]


def test_a_workflow_the_rule_cannot_read_refuses_it_naming_the_file(build_repo):
    """LIVE-6: the exit-2 cases are pytest-workers'."""
    assert ".github/workflows/w.yml" in refusals(build_repo({**LIVE, WORKFLOW: "jobs: [\n"}))[RULE]


def test_a_finding_is_exit_one(build_repo, capsys):
    """LIVE-4: the door fails on a live call."""
    assert run(capsys, build_repo({**LIVE, WORKFLOW: block_run("pytest")}))[0] == 1


@pytest.mark.parametrize("option", ['-k "not live"', "--deselect tests/e2e/live", "--ignore tests/e2e/live"])
def test_an_exclusion_by_keyword_deselect_or_ignore_is_no_exclusion(build_repo, option):
    """LIVE-8: only -m clears a call; the stricter reading is fixed."""
    assert [finding.event for finding in judged(build_repo, f"pytest {option}")] == [RUNS_LIVE]
