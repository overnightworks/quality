"""The pr-workflows rule: every workflow a pull request triggers is the gate's caller, CodeQL's, or declared with a reason."""

import pytest

from quality.config import CONFIG_PATH, WORKFLOW_ALLOWANCE_HEADING
from quality.report import Example, FindingEvent
from quality.rules import Rule
from tests.support.check import (
    assert_row_prints_finding,
    check_output,
    check_result,
    refusal_row,
    refusals,
    rows_of,
    rule_findings,
)

WORKFLOW = ".github/workflows/preview.yml"
OTHER = ".github/workflows/e2e.yml"
CALLER = ".github/workflows/ci.yml"
COMMIT = "a" * 40
LOCAL_GATE = "./.github/workflows/gate.yml"
PINNED_GATE = f"overnightworks/quality/.github/workflows/gate.yml@{COMMIT}"
PINNED_CODEQL = f"overnightworks/quality/.github/workflows/codeql.yml@{COMMIT}"
DECLARATION = f'{WORKFLOW_ALLOWANCE_HEADING}\nfile = "{WORKFLOW}"\nreason = "it deploys the preview"\n'


@pytest.fixture
def fixture_origin() -> str:
    return "overnightworks/app"


def workflow(on: str, *uses: str) -> str:
    """A workflow with the trigger text `on` and one job per `uses:` value, or one job with a step when none is given."""
    jobs = "".join(f"  call{number}:\n    uses: {target}\n" for number, target in enumerate(uses))
    step = "  j:\n    runs-on: ubuntu-latest\n    timeout-minutes: 15\n    steps:\n      - run: echo\n"
    return f"on: {on}\npermissions: {{}}\nconcurrency:\n  group: g\n  cancel-in-progress: false\njobs:\n{jobs or step}"


def judged(repo):
    return [(finding.path, finding.line, finding.event) for finding in rule_findings(repo, Rule.PR_WORKFLOWS)]


UNLISTED = [(WORKFLOW, None, FindingEvent.UNLISTED)]


@pytest.mark.parametrize(
    "on",
    ["pull_request", "[pull_request, push]", "{pull_request: {branches: [main]}, push: }", "pull_request_target",
     "{pull_request_target: }", "pull_request_review"],
    ids=["string", "list", "mapping", "target string", "target mapping", "review"],
)
def test_a_workflow_a_pull_request_triggers_that_is_no_caller_and_not_declared_is_unlisted(build_repo, on):
    """PRWORKFLOW-1: R4's sentence, in every shape of `on:` and every pull-request event."""
    assert judged(build_repo({WORKFLOW: workflow(on)})) == UNLISTED


def test_the_finding_names_both_ways_out_and_the_block_to_copy(build_repo):
    """PRWORKFLOW-2."""
    (finding,) = rule_findings(build_repo({WORKFLOW: workflow("pull_request")}), Rule.PR_WORKFLOWS)
    assert finding.fix == Example(
        "move its jobs behind the gate's caller, or declare it with a reason",
        (WORKFLOW_ALLOWANCE_HEADING, f'file = "{WORKFLOW}"'),
    )


def test_the_door_prints_the_finding_with_its_fix_and_exits_one(build_repo, capsys):
    """PRWORKFLOW-2: the red row at the workflow carries the label and the block to copy as its fix."""
    repo = build_repo({WORKFLOW: workflow("pull_request")})
    code, output = check_output(capsys, repo)
    (finding,) = rule_findings(repo, Rule.PR_WORKFLOWS)
    (row,) = rows_of(output, finding)
    assert_row_prints_finding(row, finding)
    assert code == 1


@pytest.mark.parametrize("on", ["push", "workflow_call", "merge_group", "workflow_dispatch", "{push: {branches: [main]}}"])
def test_a_workflow_no_pull_request_triggers_is_outside_the_rule(build_repo, on):
    """PRWORKFLOW-4: `merge_group`, `workflow_call` and `push` alone are named gaps, not roots."""
    assert judged(build_repo({WORKFLOW: workflow(on)})) == []


@pytest.mark.parametrize(
    "uses",
    [(LOCAL_GATE,), (PINNED_GATE,), (PINNED_CODEQL,), (PINNED_GATE, PINNED_CODEQL), (LOCAL_GATE, "./.github/workflows/other.yml")],
    ids=["local gate", "pinned gate", "pinned CodeQL", "both in one file", "gate beside another call"],
)
def test_the_gates_caller_and_codeqls_are_listed_by_what_they_call(build_repo, uses):
    """PRWORKFLOW-1."""
    assert judged(build_repo({CALLER: workflow("pull_request", *uses)})) == []


def test_a_workflow_that_calls_another_workflow_is_no_caller(build_repo):
    """PRWORKFLOW-1: only a call of the gate's or CodeQL's file lists a workflow."""
    assert judged(build_repo({WORKFLOW: workflow("pull_request", "./.github/workflows/e2e.yml")})) == UNLISTED


def test_only_the_undeclared_workflows_are_findings_sorted_by_path(build_repo):
    """PRWORKFLOW-1: one finding per workflow, the extent counts every pull-request workflow."""
    repo = build_repo({
        WORKFLOW: workflow("pull_request"), OTHER: workflow("pull_request_target"), CALLER: workflow("pull_request", LOCAL_GATE),
        ".github/workflows/nightly.yml": workflow("schedule"),
    })
    (verdict,) = (verdict for verdict in check_result(repo).verdicts if verdict.rule is Rule.PR_WORKFLOWS)
    assert ([finding.path for finding in verdict.findings], verdict.extent) == ([OTHER, WORKFLOW], 3)


def test_a_declared_workflow_is_clean(build_repo):
    """PRWORKFLOW-1: a declaration with its reason needs no ruling."""
    assert judged(build_repo({WORKFLOW: workflow("pull_request"), CONFIG_PATH: DECLARATION})) == []


@pytest.mark.parametrize(
    "files",
    [
        {WORKFLOW: workflow("push")},
        {},
        {CALLER: workflow("pull_request", LOCAL_GATE)},
        {CALLER: workflow("pull_request", PINNED_CODEQL)},
    ],
    ids=["push only", "absent", "the gate's caller", "CodeQL's caller"],
)
def test_a_declaration_that_names_no_other_pull_request_workflow_is_unused(build_repo, files):
    """PRWORKFLOW-5."""
    repo = build_repo({**files, CONFIG_PATH: DECLARATION.replace(WORKFLOW, next(iter(files), WORKFLOW))})
    (finding,) = rule_findings(repo, Rule.PR_WORKFLOWS)
    assert (finding.event, finding.line, finding.fix) == (
        FindingEvent.UNUSED, None, f"delete its {WORKFLOW_ALLOWANCE_HEADING} entry from {CONFIG_PATH}",
    )


ANCHORS = "defaults: &defaults\n  jobs: {}\non_: &on pull_request\nlist_: &list [pull_request]\nkey_: &key on\n"


@pytest.mark.parametrize(
    "text",
    [
        "on: [pull_request\n",
        f"{ANCHORS}on: *on\n",
        f"{ANCHORS}on: [push, *on]\n",
        f"{ANCHORS}on: *list\n",
        f"{ANCHORS}on:\n  <<: {{pull_request: }}\n",
        f"{ANCHORS}<<: *defaults\non: pull_request\n",
        f"{ANCHORS}*key : pull_request\n",
    ],
    ids=["yaml error", "alias as on", "alias in the on list", "alias of the on list", "merge key at on", "merge key at the top", "alias as the key"],
)
def test_a_workflow_quality_cannot_read_is_exit_two_naming_the_file(build_repo, capsys, text):
    """PRWORKFLOW-6: an alias or a merge key that can hide the triggers, and a YAML quality cannot read."""
    repo = build_repo({WORKFLOW: text})
    code, output = check_output(capsys, repo)
    assert (code, refusal_row("workflows") in output, WORKFLOW in refusals(repo)[Rule.PR_WORKFLOWS]) == (2, True, True)
