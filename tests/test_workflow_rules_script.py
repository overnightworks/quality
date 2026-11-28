"""What every rule that reads a step's `run:` script shares: which workflows and steps are read, how a script's lines are
read and what the door prints, over `pytest-workers`, `runtime-install`, `live-layer` and `node-test-random`. Each rule's own
facts stand in its module."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from quality.live_layer import check_live_layer
from quality.node_test_random import check_node_test_random
from quality.report import CannotJudge, Finding, FindingEvent, RuleResult
from quality.rules import Rule
from quality.workflow_rules import check_pytest_workers, check_runtime_install
from tests.support.check import check_output as run
from tests.support.check import (
    env_row_stands,
    event_line_fact,
    finding_rows,
    is_clean,
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
    it leaves alone; `marker` is the comment that clears a finding, None for a rule no comment switches off, and `files`
    the tracked files the rule needs before it reads any workflow."""

    rule: Rule
    check: Callable[[Path, list[str]], RuleResult]
    flagged: str
    event: FindingEvent
    fact: str
    second: tuple[str, FindingEvent, str]
    third: tuple[str, FindingEvent, str]
    continued: tuple[str, str]
    clean: str
    marker: str | None
    fix: str
    files: Mapping[str, str] = field(default_factory=dict)


SERIAL, INVALID, INSTALLS = FindingEvent.SERIAL, FindingEvent.INVALID, FindingEvent.INSTALLS
RUNS_LIVE, UNSHUFFLED = FindingEvent.RUNS_LIVE, FindingEvent.UNSHUFFLED
CAN_SELECT_LIVE = "pytest can select tests marked live"
FIXED_ORDER = "node --test runs its files and tests in a fixed order"
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
    pytest.param(
        ScriptRule(
            Rule.LIVE_LAYER,
            check_live_layer,
            "pytest -n auto",
            RUNS_LIVE,
            CAN_SELECT_LIVE,
            ("pytest -n auto -m e2e", RUNS_LIVE, CAN_SELECT_LIVE),
            ('pytest -n auto -m "not e2e"', RUNS_LIVE, CAN_SELECT_LIVE),
            ("pytest -n auto \\", "  tests"),
            'pytest -n auto -m "not live"',
            None,
            'add -m "not live", or "and not live" to its -m expression',
            {"tests/e2e/live/test_sign_in.py": ""},
        ),
        id="live-layer",
    ),
    pytest.param(
        ScriptRule(
            Rule.NODE_TEST_RANDOM,
            check_node_test_random,
            "node --test x.mjs",
            UNSHUFFLED,
            FIXED_ORDER,
            ("node --test y.mjs", UNSHUFFLED, FIXED_ORDER),
            ("node --test --test-random-seed=7 z.mjs", UNSHUFFLED, FIXED_ORDER),
            ("node --test \\", "  x.mjs"),
            "node --test --test-randomize --test-reporter=spec --test-reporter-destination=stdout x.mjs",
            None,
            "pass --test-randomize before the test files and drop --no-test-randomize and a fixed --test-random-seed "
            '(node 24.16 or newer); node then prints "Randomized test order seed: N"',
        ),
        id="node-test-random",
    ),
]
MARKED_RULES = [rule for rule in SCRIPT_RULES if rule.values[0].marker is not None]


@pytest.fixture(params=SCRIPT_RULES)
def script_rule(request) -> ScriptRule:
    return request.param


@pytest.fixture(params=MARKED_RULES)
def marked_rule(request) -> ScriptRule:
    return request.param


@pytest.fixture
def judged(script_rule: ScriptRule):
    return judging(script_rule)


def repository(build_repo, script_rule: ScriptRule, files: Mapping[str, str]) -> Path:
    """A repository tracking `files` beside the ones the rule needs before it reads a workflow."""
    return build_repo({**script_rule.files, **files})


def checked(repo: Path, script_rule: ScriptRule):
    """The rule's check over the files it needs and the workflow."""
    return script_rule.check(repo, [*script_rule.files, WORKFLOW])


def judging(script_rule: ScriptRule) -> Callable[..., list[Finding]]:
    """A function `(build_repo, text)` that gives the findings of the rule over a repository tracking `text` as the workflow."""

    def judge(build_repo, text: str) -> list[Finding]:
        return rule_findings(repository(build_repo, script_rule, {WORKFLOW: text}), script_rule.rule)

    return judge


@pytest.mark.parametrize("on", ["push", "schedule", "workflow_call", "pull_request"])
def test_a_workflow_is_judged_whatever_its_trigger(build_repo, script_rule, on, judged):
    """PYTEST-1, RUNTIME-1, LIVE-2, NODETEST-1: no root is needed."""
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
    repo = repository(build_repo, script_rule, {"action.yml": action, ".github/actions/a/action.yml": action})
    assert rule_findings(repo, script_rule.rule) == []


def test_every_job_and_step_is_read_and_each_finding_names_its_file(build_repo, script_rule):
    """PYTEST-1, PYTEST-8, RUNTIME-1, RUNTIME-8."""
    (second, second_event, second_fact), (third, third_event, third_fact) = script_rule.second, script_rule.third
    text = steps_of() + f"      - run: 'true'\n      - run: {script_rule.flagged}\n  k:\n    runs-on: ubuntu-latest\n    steps:\n      - run: {second}\n"
    repo = repository(build_repo, script_rule, {WORKFLOW: text, OTHER_WORKFLOW: block_run(third)})
    findings = rule_findings(repo, script_rule.rule)
    assert [(finding.path, finding.line, finding.event, finding.fact) for finding in findings] == [
        (OTHER_WORKFLOW, BLOCK_LINE, third_event, third_fact),
        (WORKFLOW, 9, script_rule.event, script_rule.fact),
        (WORKFLOW, 13, second_event, second_fact),
    ]


def test_a_step_without_a_run_a_workflow_without_jobs_and_a_name_gone_from_the_tree_are_skipped(build_repo, script_rule):
    """PYTEST-1, PYTEST-9, PYTEST-10, RUNTIME-9, RUNTIME-10, LIVE-2, NODETEST-1: only the workflows read count toward the extent."""
    empty = ".github/workflows/empty.yml"
    repo = repository(build_repo, script_rule, {WORKFLOW: steps_of() + "      - uses: a/b@v1\n", empty: "name: e\non: push\n"})
    result = script_rule.check(repo, [*script_rule.files, WORKFLOW, empty, ".github/workflows/gone.yml"])
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
def test_a_workflow_the_rule_cannot_read_is_cannot_judge_naming_the_file(build_repo, script_rule, build):
    """PYTEST-9, RUNTIME-9, LIVE-6, NODETEST-4."""
    repo = repository(build_repo, script_rule, {WORKFLOW: build(script_rule.flagged)})
    with pytest.raises(CannotJudge, match=WORKFLOW):
        checked(repo, script_rule)


def test_a_workflow_the_rule_cannot_read_is_exit_two_while_other_rules_still_judge(build_repo, capsys, script_rule):
    """PYTEST-9, RUNTIME-9, LIVE-6, NODETEST-4."""
    repo = repository(build_repo, script_rule, {WORKFLOW: UNREADABLE_WORKFLOWS["yaml error"](script_rule.flagged), ".env": "A=1\n"})
    code, output = run(capsys, repo)
    assert code == 2, output
    assert refusal_row("workflows") in output
    assert WORKFLOW in refusals(repo)[script_rule.rule]
    assert env_row_stands(output)


@pytest.mark.parametrize("refuse", unreadable(WORKFLOW, block_run("true")))
def test_a_workflow_the_reader_refuses_is_cannot_judge_naming_the_file(build_repo, elsewhere, script_rule, refuse):
    """PYTEST-9, RUNTIME-9, LIVE-6, NODETEST-4."""
    repo = repository(build_repo, script_rule, {WORKFLOW: block_run("true")})
    refuse(repo, elsewhere)
    with pytest.raises(CannotJudge, match=WORKFLOW):
        checked(repo, script_rule)


def test_a_tracked_workflow_gone_from_the_tree_is_skipped(build_repo, script_rule):
    """PYTEST-1, RUNTIME-1."""
    repo = repository(build_repo, script_rule, {WORKFLOW: block_run(script_rule.flagged)})
    (repo / WORKFLOW).unlink()
    assert (rule_findings(repo, script_rule.rule), script_rule.rule in refusals(repo)) == ([], False)


def test_a_command_split_over_lines_by_a_backslash_is_one_call_at_its_first_line(build_repo, script_rule, judged):
    """PYTEST-2, RUNTIME-2."""
    first, rest = script_rule.continued
    found = event_line_fact(judged(build_repo, block_run("echo start", first, rest, "echo done")))
    assert found == [(script_rule.event, BLOCK_LINE + 1, script_rule.fact)]


def test_every_call_of_a_script_is_judged_on_its_own(build_repo, script_rule, judged):
    """PYTEST-2, RUNTIME-2, LIVE-4, NODETEST-2: two calls, one clean, one not, give one finding at the second."""
    found = event_line_fact(judged(build_repo, block_run(script_rule.clean, script_rule.flagged)))
    assert found == [(script_rule.event, BLOCK_LINE + 1, script_rule.fact)]


@pytest.mark.parametrize(
    ("body", "expected"),
    [("flagged", True), ("clean", False)],
    ids=["a flagged line is judged", "a clean line is not"],
)
def test_a_line_in_a_heredoc_body_is_read_as_a_shell_line(build_repo, script_rule, body, expected, judged):
    """PYTEST-13, RUNTIME-2: the reader does not parse heredocs, so a call in the text is judged."""
    line = {"flagged": script_rule.flagged, "clean": script_rule.clean}[body]
    found = event_line_fact(judged(build_repo, block_run("cat <<'EOF' > run.sh", line, "EOF")))
    assert found == ([(script_rule.event, BLOCK_LINE + 1, script_rule.fact)] if expected else [])


def test_a_marker_above_a_line_in_a_heredoc_body_clears_it(build_repo, marked_rule):
    """PYTEST-13, RUNTIME-2."""
    judged = judging(marked_rule)
    assert judged(build_repo, block_run("cat <<'EOF' > run.sh", marked_rule.marker, marked_rule.flagged, "EOF")) == []


def test_a_finding_prints_its_event_fact_and_its_fix(build_repo, capsys, script_rule):
    """PYTEST-8, RUNTIME-8."""
    (second, second_event, second_fact), (third, third_event, third_fact) = script_rule.second, script_rule.third
    code, output = run(capsys, repository(build_repo, script_rule, {WORKFLOW: block_run(second, third)}))
    rows = finding_rows(output, f"{WORKFLOW}:{BLOCK_LINE}") + finding_rows(output, f"{WORKFLOW}:{BLOCK_LINE + 1}")
    assert code == 1, output
    assert [(row.event, row.fact, row.fix) for row in rows] == [(second_event, second_fact, script_rule.fix), (third_event, third_fact, script_rule.fix)]


def test_a_conforming_repository_is_clean_under_every_rule(build_repo, capsys, script_rule):
    """PYTEST-11, RUNTIME-10."""
    code, output = run(capsys, repository(build_repo, script_rule, {WORKFLOW: block_run(script_rule.clean)}))
    assert (code, is_clean(output)) == (0, True), output


def test_a_marker_is_the_way_out_of_a_finding_through_the_door(build_repo, capsys, marked_rule):
    """PYTEST-7, PYTEST-11, RUNTIME-7, RUNTIME-10."""
    code, output = run(capsys, repository(build_repo, marked_rule, {WORKFLOW: block_run(marked_rule.marker, marked_rule.flagged)}))
    assert (code, is_clean(output)) == (0, True), output
