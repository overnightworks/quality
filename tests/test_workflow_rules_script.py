"""What every rule that reads a step's `run:` script shares: which workflows and steps are read, how a script's lines are
read and what the door prints, over `pytest-workers` and `runtime-install`. Each rule's own facts stand in its module."""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest

from quality.report import FindingEvent, RuleResult
from quality.rules import Rule
from quality.workflow_rules import check_pytest_workers, check_runtime_install
from tests.support.check import check_output as run
from tests.support.check import (
    env_row_stands,
    event_line_fact,
    finding_rows,
    is_clean,
    judge_workflow,
    refusal_row,
    refusals,
    rule_findings,
)
from tests.support.unreadable_files import unreadable
from tests.support.workflow_scripts import BLOCK_LINE, QUOTED_LINE, WORKFLOW, block_run, steps_of

pytestmark = pytest.mark.integration

OTHER_WORKFLOW = ".github/workflows/other.yaml"


@dataclass(frozen=True)
class ScriptRule:
    """What one rule says about the scripts the shared cases build: the commands it flags, with the fact of each, and one
    it leaves alone."""

    rule: Rule
    check: Callable[[Path, list[str]], RuleResult]
    flagged: str
    event: FindingEvent
    fact: str
    second: tuple[str, FindingEvent, str]
    third: tuple[str, FindingEvent, str]
    continued: tuple[str, str]
    clean: str
    marker: str
    fix: str


SERIAL, INVALID, INSTALLS = FindingEvent.SERIAL, FindingEvent.INVALID, FindingEvent.INSTALLS
SCRIPT_RULES = [
    pytest.param(
        ScriptRule(
            Rule.PYTEST_WORKERS,
            check_pytest_workers,
            "pytest",
            SERIAL,
            "pytest runs without -n",
            ("pytest -n 0", SERIAL, "pytest runs with -n 0"),
            ("pytest -n x", INVALID, "-n count is not a number above zero or auto"),
            ("pytest -q \\", "  tests"),
            "uv run --locked pytest -n auto --cov",
            "# pytest-workers: browser tests share one stack",
            "pass -n auto, or explain on the line above: # pytest-workers: <reason>",
        ),
        id="pytest-workers",
    ),
    pytest.param(
        ScriptRule(
            Rule.RUNTIME_INSTALL,
            check_runtime_install,
            "apt-get install x",
            INSTALLS,
            "apt-get install",
            ("brew install y", INSTALLS, "brew install"),
            ("curl u | sh", INSTALLS, "a download runs as a shell script"),
            ("apt-get install \\", "  x"),
            "uv sync --locked",
            "# runtime-install: proves the install on a fresh system",
            "take the tool from the lockfile, the runner image or an image pinned by digest, "
            "or explain on the line above: # runtime-install: <reason>",
        ),
        id="runtime-install",
    ),
]


@pytest.fixture(params=SCRIPT_RULES)
def script_rule(request) -> ScriptRule:
    return request.param


@pytest.fixture
def judged(script_rule: ScriptRule):
    return judge_workflow(script_rule.rule)


@pytest.mark.parametrize("on", ["push", "schedule", "workflow_call", "pull_request"])
def test_a_workflow_is_judged_whatever_its_trigger(build_repo, script_rule, on, judged):
    """PYTEST-1, RUNTIME-1: no root is needed."""
    found = event_line_fact(judged(build_repo, block_run(script_rule.flagged, on=on)))
    assert found == [(script_rule.event, BLOCK_LINE, script_rule.fact)]


def test_a_run_is_read_as_shell_whatever_its_shell(build_repo, script_rule, judged):
    """PYTEST-1, RUNTIME-1."""
    text = steps_of() + f"      - shell: python\n        run: {script_rule.flagged}\n"
    assert event_line_fact(judged(build_repo, text)) == [(script_rule.event, QUOTED_LINE + 1, script_rule.fact)]


def test_only_the_run_of_a_step_is_read(build_repo, script_rule, judged):
    """PYTEST-1, RUNTIME-1: names, conditions, inputs, environments and the steps of `uses:` are not scripts."""
    command = script_rule.flagged
    text = steps_of() + (
        f"      - name: {command}\n        if: contains(github.ref, '{command}')\n        env:\n          CMD: {command}\n"
        "        run: 'true'\n      - uses: a/b@0123456789012345678901234567890123456789\n        with:\n"
        f"          command: {command}\n          run: {command}\n"
    )
    assert judged(build_repo, text) == []


def test_a_composite_action_is_not_read(build_repo, script_rule):
    """PYTEST-1, RUNTIME-1: the named gap."""
    action = f"name: a\nruns:\n  using: composite\n  steps:\n    - shell: bash\n      run: {script_rule.flagged}\n"
    repo = build_repo({"action.yml": action, ".github/actions/a/action.yml": action})
    assert rule_findings(repo, script_rule.rule) == []


def test_every_job_and_step_is_read_and_each_finding_names_its_file(build_repo, script_rule):
    """PYTEST-1, PYTEST-8, RUNTIME-1, RUNTIME-8."""
    (second, second_event, second_fact), (third, third_event, third_fact) = script_rule.second, script_rule.third
    text = steps_of() + f"      - run: 'true'\n      - run: {script_rule.flagged}\n  k:\n    runs-on: ubuntu-latest\n    steps:\n      - run: {second}\n"
    repo = build_repo({WORKFLOW: text, OTHER_WORKFLOW: block_run(third)})
    findings = rule_findings(repo, script_rule.rule)
    assert [(finding.path, finding.line, finding.event, finding.fact) for finding in findings] == [
        (OTHER_WORKFLOW, BLOCK_LINE, third_event, third_fact),
        (WORKFLOW, 9, script_rule.event, script_rule.fact),
        (WORKFLOW, 13, second_event, second_fact),
    ]


def test_a_step_without_a_run_a_workflow_without_jobs_and_a_name_gone_from_the_tree_are_skipped(build_repo, script_rule):
    """PYTEST-1, PYTEST-9, PYTEST-10, RUNTIME-9, RUNTIME-10: only the workflows read count toward the extent."""
    empty = ".github/workflows/empty.yml"
    repo = build_repo({WORKFLOW: steps_of() + "      - uses: a/b@v1\n", empty: "name: e\non: push\n"})
    result = script_rule.check(repo, [WORKFLOW, empty, ".github/workflows/gone.yml"])
    assert (result.findings, result.extent, script_rule.rule in refusals(repo)) == ([], 2, False)


def anchored(command: str) -> str:
    return f"x-steps: &steps\n  - run: 'true'\nx-job: &job\n  runs-on: ubuntu-latest\nx-run: &run {command}\non: push\n"


JOBS = "jobs:\n  j:\n    runs-on: ubuntu-latest\n    timeout-minutes: 15\n    steps:\n"
UNREADABLE_WORKFLOWS = {
    "yaml error": lambda command: "name: w\non: [push\n",
    "run list": lambda command: steps_of() + "      - run: [x]\n",
    "run mapping": lambda command: steps_of() + f"      - run:\n          cmd: {command}\n",
    "alias as jobs": lambda command: f"{anchored(command)}jobs: *job\n",
    "merge key at jobs": lambda command: f"{anchored(command)}jobs:\n  <<: *job\n",
    "alias as a job": lambda command: f"{anchored(command)}jobs:\n  j: *job\n",
    "merge key at a job": lambda command: f"{anchored(command)}jobs:\n  j:\n    <<: *job\n    steps: []\n",
    "alias as steps": lambda command: f"{anchored(command)}{JOBS[: -len('    steps:\n')]}    steps: *steps\n",
    "alias as a step": lambda command: f"{anchored(command)}{JOBS}      - *job\n",
    "merge key at a step": lambda command: f"{anchored(command)}{JOBS}      - <<: *job\n",
    "alias as run": lambda command: f"{anchored(command)}{JOBS}      - run: *run\n",
    "merge key at a step beside run": lambda command: f"{anchored(command)}{JOBS}      - name: t\n        <<: {{run: {command}}}\n",
    "merge key at the top": lambda command: f"{anchored(command)}<<: {{jobs: {{}}}}\n",
    "jobs is a list": lambda command: "on: push\njobs: [a]\n",
    "a job is a scalar": lambda command: "on: push\njobs:\n  j: x\n",
}


@pytest.mark.parametrize("build", UNREADABLE_WORKFLOWS.values(), ids=UNREADABLE_WORKFLOWS.keys())
def test_a_workflow_the_rule_cannot_read_is_cannot_judge_naming_the_file_while_other_rules_still_judge(
    build_repo, capsys, script_rule, build
):
    """PYTEST-9, RUNTIME-9."""
    repo = build_repo({WORKFLOW: build(script_rule.flagged), ".env": "A=1\n"})
    code, output = run(capsys, repo)
    assert code == 2, output
    assert refusal_row("workflows") in output
    assert WORKFLOW in refusals(repo)[script_rule.rule]
    assert env_row_stands(output)


@pytest.mark.parametrize("refuse", unreadable(WORKFLOW, block_run("true")))
def test_a_workflow_the_reader_refuses_is_cannot_judge_naming_the_file(build_repo, elsewhere, script_rule, refuse):
    """PYTEST-9, RUNTIME-9."""
    repo = build_repo({WORKFLOW: block_run("true")})
    refuse(repo, elsewhere)
    assert WORKFLOW in refusals(repo)[script_rule.rule]


def test_a_tracked_workflow_gone_from_the_tree_is_skipped(build_repo, script_rule):
    """PYTEST-1, RUNTIME-1."""
    repo = build_repo({WORKFLOW: block_run(script_rule.flagged)})
    (repo / WORKFLOW).unlink()
    assert (rule_findings(repo, script_rule.rule), script_rule.rule in refusals(repo)) == ([], False)


def test_a_command_split_over_lines_by_a_backslash_is_one_call_at_its_first_line(build_repo, script_rule, judged):
    """PYTEST-2, RUNTIME-2."""
    first, rest = script_rule.continued
    found = event_line_fact(judged(build_repo, block_run("echo start", first, rest, "echo done")))
    assert found == [(script_rule.event, BLOCK_LINE + 1, script_rule.fact)]


@pytest.mark.parametrize(
    ("body", "expected"),
    [("flagged", True), ("clean", False), ("marked", False)],
    ids=["a flagged line is judged", "a clean line is not", "a marker above clears it"],
)
def test_a_line_in_a_heredoc_body_is_read_as_a_shell_line(build_repo, script_rule, body, expected, judged):
    """PYTEST-13, RUNTIME-2: the reader does not parse heredocs, so a call in the text is judged; a marker above it clears it."""
    lines = {
        "flagged": [script_rule.flagged],
        "clean": [script_rule.clean],
        "marked": [script_rule.marker, script_rule.flagged],
    }[body]
    found = event_line_fact(judged(build_repo, block_run("cat <<'EOF' > run.sh", *lines, "EOF")))
    assert found == ([(script_rule.event, BLOCK_LINE + 1, script_rule.fact)] if expected else [])


def test_a_finding_prints_its_event_fact_and_its_fix(build_repo, capsys, script_rule):
    """PYTEST-8, RUNTIME-8."""
    (second, second_event, second_fact), (third, third_event, third_fact) = script_rule.second, script_rule.third
    code, output = run(capsys, build_repo({WORKFLOW: block_run(second, third)}))
    rows = finding_rows(output, f"{WORKFLOW}:{BLOCK_LINE}") + finding_rows(output, f"{WORKFLOW}:{BLOCK_LINE + 1}")
    assert code == 1, output
    assert [(row.event, row.fact, row.fix) for row in rows] == [(second_event, second_fact, script_rule.fix), (third_event, third_fact, script_rule.fix)]


def test_a_conforming_repository_is_clean_under_every_rule(build_repo, capsys, script_rule):
    """PYTEST-11, RUNTIME-10."""
    code, output = run(capsys, build_repo({WORKFLOW: block_run(script_rule.clean)}))
    assert (code, is_clean(output)) == (0, True), output


def test_a_marker_is_the_way_out_of_a_finding_through_the_door(build_repo, capsys, script_rule):
    """PYTEST-7, PYTEST-11, RUNTIME-7, RUNTIME-10."""
    code, output = run(capsys, build_repo({WORKFLOW: block_run(script_rule.marker, script_rule.flagged)}))
    assert (code, is_clean(output)) == (0, True), output
