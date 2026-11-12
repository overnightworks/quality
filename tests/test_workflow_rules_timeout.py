import json

import pytest

from quality.baseline_file import BASELINE_PATH
from quality.report import FindingEvent
from quality.rules import Rule
from tests.support.check import (
    area_finding_rows,
    assert_finding_vocabulary,
    is_clean,
    refusal_row,
    refusals,
    rule_findings,
)
from tests.support.check import check_output as run
from tests.support.door import PinnedJudging, door_in

MISSING, INVALID, NEW_DEBT = FindingEvent.MISSING, FindingEvent.INVALID, FindingEvent.NEW_DEBT
GREW, CAN_FALL = FindingEvent.GREW, FindingEvent.CAN_FALL
WORKFLOW = ".github/workflows/w.yml"
CALLED = ".github/workflows/x.yml"
ON_PULL_REQUEST = "on: pull_request\n"
SET_FIX = "set timeout-minutes to a number of at most 15"
LOWER_FIX = "lower timeout-minutes to 15 or less"
STEPS = "    runs-on: ubuntu-latest\n    steps:\n      - run: 'true'\n"
CONCURRENCY = (
    "concurrency:\n  group: ${{ github.workflow }}-${{ github.event.pull_request.number || github.run_id }}\n"
    "  cancel-in-progress: ${{ github.event.pull_request.number != null }}\n"
)


def job(name: str = "j", limit: str | None = "10") -> str:
    """A job whose id stands on its own line and whose `timeout-minutes`, when given, stands on the next."""
    return f"  {name}:\n" + ("" if limit is None else f"    timeout-minutes: {limit}\n") + STEPS


def workflow(on: str = ON_PULL_REQUEST, jobs: str | None = None, top: str = "") -> str:
    """A workflow whose `on:` starts on line 2 and, with no `top`, whose first job id is on line `first_job_line(on)`."""
    return f"name: w\n{on}permissions: {{}}\n{top}jobs:\n{job(limit=None) if jobs is None else jobs}"


def first_job_line(on: str = ON_PULL_REQUEST) -> int:
    return 4 + on.count("\n")


def judged(build_repo, text: str, name: str = WORKFLOW):
    return rule_findings(build_repo({name: text}), Rule.JOB_TIMEOUT)


def shape(findings):
    return [(finding.event, finding.path, finding.line, finding.fact) for finding in findings]


def missing(name: str = "j", path: str = WORKFLOW, line: int = 5):
    return (MISSING, path, line, f"job {name} has no timeout-minutes")


@pytest.mark.parametrize("limit", ["15", "1", "7.5", "15.0", "0.5"])
def test_a_literal_limit_of_at_most_fifteen_is_green(build_repo, limit):
    """TIMEOUT-5."""
    assert judged(build_repo, workflow(jobs=job(limit=limit))) == []


def test_a_job_without_a_limit_is_missing_at_its_id_with_the_fix_to_set_one(build_repo):
    """TIMEOUT-4, TIMEOUT-5, TIMEOUT-6."""
    findings = judged(build_repo, workflow())
    assert (shape(findings), [finding.fix for finding in findings]) == ([missing()], [SET_FIX])


@pytest.mark.parametrize(
    "limit",
    [
        "${{ vars.T }}", "${{ matrix.t }}", "0", "0.0", "-5", "+5", "1e1", "0x10", "ten", "", "~", "null", "[5]", "{a: 1}",
        '"10"', "'10'", "1_0", ".5", "5.", "\u0665",
    ],
    ids=[
        "variable", "matrix expression", "zero", "decimal zero", "negative", "signed", "exponent", "hex", "word", "empty",
        "tilde", "null", "sequence", "mapping", "double quoted", "single quoted", "underscore", "no integer part", "no fraction",
        "arabic-indic digit",
    ],
)
def test_a_limit_that_is_no_plain_number_above_zero_is_invalid_at_the_key_with_the_fix_to_set_one(build_repo, limit):
    """TIMEOUT-5, TIMEOUT-6."""
    findings = judged(build_repo, workflow(jobs=job(limit=limit)))
    fact = "job j has a timeout-minutes that is not a number above zero"
    assert (shape(findings), [finding.fix for finding in findings]) == ([(INVALID, WORKFLOW, 6, fact)], [SET_FIX])


@pytest.mark.parametrize("limit", ["16", "15.5", "40", "1000"])
def test_a_limit_above_fifteen_is_new_debt_naming_the_value_with_the_fix_to_lower_it(build_repo, limit):
    """TIMEOUT-5, TIMEOUT-6."""
    findings = judged(build_repo, workflow(jobs=job(limit=limit)))
    fact = f"job j has timeout-minutes {limit} · limit 15"
    assert (shape(findings), [finding.fix for finding in findings]) == ([(NEW_DEBT, WORKFLOW, 6, fact)], [LOWER_FIX])


def test_every_judged_job_is_found_in_the_files_order_and_no_fact_ends_in_a_full_stop(build_repo):
    """TIMEOUT-6."""
    findings = judged(build_repo, workflow(jobs=job("slow", "60") + job("bare", None) + job("fast", "40") + job("bad", "x")))
    assert shape(findings) == [
        (NEW_DEBT, WORKFLOW, 6, "job slow has timeout-minutes 60 · limit 15"),
        missing("bare", line=10),
        (NEW_DEBT, WORKFLOW, 15, "job fast has timeout-minutes 40 · limit 15"),
        (INVALID, WORKFLOW, 20, "job bad has a timeout-minutes that is not a number above zero"),
    ]
    for finding in findings:
        assert_finding_vocabulary(finding)


def test_a_matrix_job_is_one_job(build_repo):
    """TIMEOUT-4."""
    matrix = "  j:\n    timeout-minutes: 10\n    strategy:\n      matrix:\n        shard: [1, 2, 3]\n" + STEPS
    assert judged(build_repo, workflow(jobs=matrix)) == []
    bare = "  j:\n    strategy:\n      matrix:\n        shard: [1, 2, 3]\n" + STEPS
    assert shape(judged(build_repo, workflow(jobs=bare))) == [missing()]


def test_a_tracked_workflow_gone_from_the_tree_is_skipped_and_a_composite_action_is_not_read(build_repo):
    """TIMEOUT-1."""
    action = "name: a\non: pull_request\njobs:\n  j:\n    runs-on: ubuntu-latest\n"
    repo = build_repo({WORKFLOW: workflow(), CALLED: workflow(), ".github/actions/x/action.yml": action})
    (repo / WORKFLOW).unlink()
    assert shape(rule_findings(repo, Rule.JOB_TIMEOUT)) == [missing(path=CALLED)]


def test_a_workflow_without_jobs_has_no_job_to_judge(build_repo):
    """TIMEOUT-7."""
    repo = build_repo({WORKFLOW: f"name: w\n{ON_PULL_REQUEST}"})
    assert (rule_findings(repo, Rule.JOB_TIMEOUT), Rule.JOB_TIMEOUT in refusals(repo)) == ([], False)


def test_a_repeated_job_id_is_read_as_the_loader_reads_it(build_repo):
    """TIMEOUT-5: the last of two equal ids is the job; the gate's workflow lint refuses the repetition."""
    assert judged(build_repo, workflow(jobs=job("j", "40") + job("j", "10"))) == []


@pytest.mark.parametrize(
    "on",
    [
        "on: pull_request\n",
        "on: [push, pull_request]\n",
        "on:\n  pull_request:\n    branches: ['releases/**']\n",
        "on: pull_request_target\n",
        "on: pull_request_review\n",
        "on: push\n",
        "on:\n  push:\n",
        "on:\n  push:\n    paths: ['src/**']\n",
        "on:\n  push:\n    branches: [main]\n",
        "on:\n  push:\n    branches: main\n",
        "on:\n  push:\n    branches: ['**']\n",
        "on:\n  push:\n    branches: ['ma*']\n",
        "on:\n  push:\n    branches: ['**', '!main']\n",
        "on:\n  push:\n    branches-ignore: [develop]\n",
        "on:\n  push:\n    tags-ignore: ['v*']\n",
        "on: [workflow_dispatch, push]\n",
        "on:\n  push: ~\n",
        "on:\n  push: null\n",
        "on:\n  push: Null\n",
    ],
    ids=[
        "pull request", "list", "mapping with branch globs", "target", "review", "push", "empty push", "push with paths",
        "branch main", "branch string", "branch globstar", "branch glob", "negation", "other branches ignored",
        "tags ignored", "dispatch and push", "tilde push", "null push", "capital null push",
    ],
)
def test_a_workflow_a_pull_request_or_a_push_to_main_runs_is_judged(build_repo, on):
    """TIMEOUT-2: the shapes of `on:` that make a root."""
    assert shape(judged(build_repo, workflow(on))) == [missing(line=first_job_line(on))]


@pytest.mark.parametrize(
    "on",
    [
        "on:\n  push:\n    branches: [develop]\n",
        "on:\n  push:\n    branches: ['release/**']\n",
        "on:\n  push:\n    branches-ignore: [main]\n",
        "on:\n  push:\n    branches-ignore: ['ma*']\n",
        "on:\n  push:\n    tags: ['v*']\n",
        "on:\n  push:\n    tags: ['v*']\n    paths: ['src/**']\n",
        "on:\n  schedule:\n    - cron: '1 1 * * *'\n",
        "on: workflow_dispatch\n",
        "on: merge_group\n",
        "on:\n  workflow_run:\n    workflows: [a]\n",
        "on: workflow_call\n",
    ],
    ids=[
        "another branch", "a glob that misses main", "ignoring main", "ignoring main by glob", "tags only", "tags with paths",
        "schedule", "dispatch", "merge group", "workflow run", "call",
    ],
)
def test_a_workflow_no_pull_request_and_no_push_to_main_runs_is_outside(build_repo, on):
    """TIMEOUT-2."""
    assert judged(build_repo, workflow(on)) == []


def test_a_workflow_that_calls_nothing_and_is_no_root_is_never_judged_whatever_its_jobs(build_repo):
    """TIMEOUT-3."""
    repo = build_repo({WORKFLOW: workflow(ON_PULL_REQUEST, job(limit="10")), CALLED: workflow("on: workflow_call\n")})
    assert rule_findings(repo, Rule.JOB_TIMEOUT) == []


def calls(value: str, name: str = "caller", limit: str | None = None) -> str:
    return f"  {name}:\n" + ("" if limit is None else f"    timeout-minutes: {limit}\n") + f"    uses: {value}\n"


@pytest.mark.parametrize("prefix", ["./", "$/"])
def test_a_workflow_a_root_calls_locally_is_judged_under_its_own_path(build_repo, prefix):
    """TIMEOUT-3, TIMEOUT-4: the calling job is not judged itself, the called file is."""
    repo = build_repo({
        WORKFLOW: workflow(jobs=calls(f"{prefix}{CALLED}")),
        CALLED: workflow("on: workflow_call\n", job("inner", None)),
    })
    assert shape(rule_findings(repo, Rule.JOB_TIMEOUT)) == [missing("inner", CALLED)]


def test_the_limit_beside_a_uses_is_not_read(build_repo):
    """TIMEOUT-4."""
    repo = build_repo({
        WORKFLOW: workflow(jobs=calls(f"./{CALLED}", limit="99")),
        CALLED: workflow("on: workflow_call\n", job(limit="10")),
    })
    assert rule_findings(repo, Rule.JOB_TIMEOUT) == []


def test_a_chain_of_local_calls_is_followed_to_its_end(build_repo):
    """TIMEOUT-3."""
    repo = build_repo({
        WORKFLOW: workflow(jobs=calls(f"./{CALLED}")),
        CALLED: workflow("on: workflow_call\n", calls("./.github/workflows/y.yml")),
        ".github/workflows/y.yml": workflow("on: workflow_call\n", job("deep", None)),
    })
    assert shape(rule_findings(repo, Rule.JOB_TIMEOUT)) == [missing("deep", ".github/workflows/y.yml")]


@pytest.mark.parametrize(
    "files",
    [
        {WORKFLOW: workflow(jobs=job("j", "10") + calls(f"./{WORKFLOW}") + calls(f"$/{WORKFLOW}", "again"))},
        {
            WORKFLOW: workflow(jobs=calls(f"./{CALLED}")),
            CALLED: workflow("on: [workflow_call, pull_request]\n", job("j", "10") + calls(f"./{WORKFLOW}", "back")),
        },
    ],
    ids=["a file calling itself", "two files calling each other"],
)
def test_a_cycle_of_calls_ends_where_it_closes_and_is_no_finding(build_repo, files):
    """TIMEOUT-3."""
    assert rule_findings(build_repo(files), Rule.JOB_TIMEOUT) == []


def test_a_remote_reusable_workflow_is_not_followed(build_repo):
    """TIMEOUT-3."""
    text = workflow(jobs=calls("owner/repository/.github/workflows/x.yml@v1"))
    assert judged(build_repo, text) == []


def test_a_file_that_only_dispatches_is_not_refused_for_calls_it_cannot_follow(build_repo):
    """TIMEOUT-3: a call in a file that is not judged is not read."""
    text = workflow("on: workflow_dispatch\n", calls("./.github/workflows/gone.yml") + calls("./actions/x", "other"))
    repo = build_repo({".github/workflows/release.yml": text})
    assert (rule_findings(repo, Rule.JOB_TIMEOUT), Rule.JOB_TIMEOUT in refusals(repo)) == ([], False)


@pytest.mark.parametrize(
    "uses",
    ["./.github/workflows/gone.yml", "./actions/x", "./.github/workflows/sub/x.yml", "$/.github/workflows/gone.yaml"],
    ids=["a workflow that is not tracked", "an action", "a subfolder", "an untracked yaml by dollar"],
)
def test_a_local_call_in_a_judged_file_that_cannot_be_followed_is_cannot_judge_naming_the_caller_and_the_value(build_repo, uses):
    """TIMEOUT-3, TIMEOUT-7."""
    repo = build_repo({WORKFLOW: workflow(jobs=calls(uses))})
    reason = refusals(repo)[Rule.JOB_TIMEOUT]
    assert (WORKFLOW in reason, uses in reason) == (True, True)


def test_a_called_workflow_that_is_tracked_but_gone_from_the_tree_is_cannot_judge(build_repo):
    """TIMEOUT-3, TIMEOUT-7."""
    repo = build_repo({WORKFLOW: workflow(jobs=calls(f"./{CALLED}")), CALLED: workflow("on: workflow_call\n")})
    (repo / CALLED).unlink()
    assert CALLED in refusals(repo)[Rule.JOB_TIMEOUT]


ANCHORS = "defaults: &defaults\n  runs-on: ubuntu-latest\nlimit: &limit 10\nkey: &key timeout-minutes\npush: &push {branches: [main]}\nname_: &list [main]\n"


@pytest.mark.parametrize(
    "text",
    [
        "name: w\non: [pull_request\n",
        f"name: w\n{ON_PULL_REQUEST}jobs: [a]\n",
        f"name: w\n{ON_PULL_REQUEST}jobs:\n  j: build\n",
        f"{ANCHORS}<<: *defaults\n{ON_PULL_REQUEST}jobs:\n{job(limit='10')}",
        f"{ANCHORS}on: *limit\njobs:\n{job(limit='10')}",
        f"{ANCHORS}on: [pull_request, *limit]\njobs:\n{job(limit='10')}",
        f"{ANCHORS}on:\n  <<: {{pull_request: }}\njobs:\n{job(limit='10')}",
        f"{ANCHORS}{ON_PULL_REQUEST}jobs: &jobs\n{job(limit='10')}  again: *jobs\n",
        f"{ANCHORS}{ON_PULL_REQUEST}jobs:\n  j: *defaults\n",
        f"{ANCHORS}{ON_PULL_REQUEST}jobs:\n  <<: *defaults\n",
        f"{ANCHORS}{ON_PULL_REQUEST}jobs:\n  j:\n    <<: *defaults\n    timeout-minutes: 10\n",
        f"{ANCHORS}{ON_PULL_REQUEST}jobs:\n  j:\n    timeout-minutes: *limit\n",
        f"{ANCHORS}{ON_PULL_REQUEST}jobs:\n  j:\n    timeout-minutes:\n      <<: *defaults\n",
        f"{ANCHORS}{ON_PULL_REQUEST}jobs: *defaults\n",
        f"{ANCHORS}{ON_PULL_REQUEST}jobs:\n  j:\n    *key : 10\n",
        f"{ANCHORS}{ON_PULL_REQUEST}jobs:\n  *key :\n    timeout-minutes: 10\n",
        f"{ANCHORS}on:\n  push: *push\njobs:\n{job(limit='10')}",
        f"{ANCHORS}on:\n  push:\n    <<: *push\njobs:\n{job(limit='10')}",
        f"{ANCHORS}on:\n  push:\n    branches: *list\njobs:\n{job(limit='10')}",
        f"{ANCHORS}on:\n  push:\n    branches: [*limit]\njobs:\n{job(limit='10')}",
        f"{ANCHORS}on:\n  push:\n    branches-ignore: *list\njobs:\n{job(limit='10')}",
        f"{ANCHORS}on:\n  push:\n    branches-ignore: [*limit]\njobs:\n{job(limit='10')}",
        f"{ANCHORS}on:\n  push:\n    tags: *list\njobs:\n{job(limit='10')}",
        f"{ANCHORS}on:\n  push:\n    tags: [*limit]\njobs:\n{job(limit='10')}",
        f"{ANCHORS}on:\n  push:\n    *key : [main]\njobs:\n{job(limit='10')}",
        f"name: w\non:\n  push: main\njobs:\n{job(limit='10')}",
        f"name: w\non:\n  push: [main]\njobs:\n{job(limit='10')}",
        f"name: w\non:\n  push:\n    branches: {{a: b}}\njobs:\n{job(limit='10')}",
        f"name: w\non:\n  push:\n    branches: [[a]]\njobs:\n{job(limit='10')}",
        f"name: w\non:\n  push:\n    tags: {{a: b}}\njobs:\n{job(limit='10')}",
    ],
    ids=[
        "yaml error", "jobs as a list", "a job as a string", "merge key at the top", "alias as on", "alias in the on list",
        "merge key at on", "alias of its own mapping", "alias as a job", "merge key in jobs", "merge key in a job",
        "alias as the limit", "merge key in the limit", "alias as jobs", "alias as a key of a job", "alias as a job id", "alias as push", "merge key in push",
        "alias as branches", "alias in branches", "alias as branches-ignore", "alias in branches-ignore", "alias as tags",
        "alias in tags", "alias as a key of push", "push as a word", "push as a sequence", "branches as a mapping",
        "branches with a sequence item", "tags as a mapping",
    ],
)
def test_a_workflow_the_rule_cannot_read_is_cannot_judge_naming_the_file_while_other_rules_still_judge(build_repo, capsys, text):
    """TIMEOUT-7."""
    repo = build_repo({WORKFLOW: text, ".env": "A=1\n"})
    code, output = run(capsys, repo)
    assert code == 2, output
    assert WORKFLOW in refusals(repo)[Rule.JOB_TIMEOUT]
    assert any(".env" in line for line in output.splitlines())


def test_a_yaml_error_in_a_workflow_that_is_no_root_is_cannot_judge(build_repo):
    """TIMEOUT-7: a root is known only after the parse."""
    repo = build_repo({WORKFLOW: workflow(jobs=job(limit="10")), CALLED: "on: workflow_call\njobs: [\n"})
    assert CALLED in refusals(repo)[Rule.JOB_TIMEOUT]


def test_an_alias_in_the_jobs_of_a_workflow_that_is_not_judged_is_no_refusal(build_repo):
    """TIMEOUT-7: the jobs are read only of a judged file."""
    text = f"{ANCHORS}on: workflow_dispatch\njobs: *defaults\n"
    repo = build_repo({WORKFLOW: text})
    assert Rule.JOB_TIMEOUT not in refusals(repo)


BASELINE = {"quality/baseline.toml": ""}
LONG_FUNCTION = "def long_one():\n" + "    x = 1\n" * 61


def test_a_refused_workflow_leaves_the_size_findings_of_the_debt_area(build_repo, capsys):
    """TIMEOUT-8."""
    code, output = run(capsys, build_repo({WORKFLOW: "on: [\n", **BASELINE, "src/a.py": LONG_FUNCTION}))
    assert (code, "src/a.py:1" in output, refusal_row("debt") in output) == (2, True, True)


def test_a_refused_source_leaves_the_job_findings_of_the_debt_area(build_repo, capsys):
    """TIMEOUT-8."""
    code, output = run(capsys, build_repo({WORKFLOW: workflow(), **BASELINE, "src/bad.py": "def broken(:\n"}))
    assert (code, refusal_row("debt") in output) == (2, True)
    assert [(location, row.event) for location, row in area_finding_rows(output, "debt")] == [(f"{WORKFLOW}:5", MISSING)]


CI = ".github/workflows/ci.yml"
E2E = ".github/workflows/e2e.yml"
THREE_ROWS = {
    CI: workflow(jobs=job("tests", None) + job("e2e", "40")),
    E2E: workflow(jobs=job("shard", "${{ vars.T }}")),
}


def debt_words(printed: str) -> str:
    """What the debt row says after its area word."""
    return next(line.split("debt", 1)[1].strip() for line in printed.splitlines() if line.split()[1:2] == ["debt"])


def test_the_three_kinds_of_finding_print_in_the_debt_area_with_their_facts_and_exit_one(build_repo, capsys):
    """TIMEOUT-6."""
    code, output = run(capsys, build_repo(THREE_ROWS))
    rows = [(location, row.event, row.fact) for location, row in area_finding_rows(output, "debt")]
    assert code == 1, output
    assert rows == [
        (f"{CI}:5", MISSING, "job tests has no timeout-minutes"),
        (f"{CI}:10", NEW_DEBT, "job e2e has timeout-minutes 40 · limit 15"),
        (f"{E2E}:6", INVALID, "job shard has a timeout-minutes that is not a number above zero"),
    ]
    assert "3 findings" in debt_words(output)


def test_a_conforming_repository_is_clean_under_every_rule(build_repo, capsys):
    """TIMEOUT-5: a limit of fifteen in a pull-request workflow is clean in the whole run."""
    code, output = run(capsys, build_repo({WORKFLOW: workflow(top=CONCURRENCY, jobs=job(limit="15"))}))
    assert (code, is_clean(output)) == (0, True), output


def debt_run(capsys, repo) -> tuple[int, list[str]]:
    """The exit and the lines after the release row of `quality check debt`."""
    judging = PinnedJudging()
    code = door_in(repo, "check", "debt", clock=lambda: 0.0, judging=judging)
    return code, judging.frame(capsys.readouterr().out).splitlines()


def test_a_run_without_a_ceiling_file_and_without_findings_keeps_the_debt_row_and_says_the_ceiling_was_measured(build_repo, capsys):
    """TIMEOUT-8, TIMEOUT-10, CLOSING-8."""
    code, lines = debt_run(capsys, build_repo({WORKFLOW: workflow(top=CONCURRENCY, jobs=job(limit="15"))}))
    assert (code, "0 entries over the thresholds" in debt_words("\n".join(lines))) == (0, True)
    assert lines[-1] == "✓ clean · 1 area · debt ceiling measured, not enforced · <1s"


def test_a_finding_without_a_ceiling_file_leaves_the_closing_line_saying_the_ceiling_was_measured(build_repo, capsys):
    """TIMEOUT-8, CLOSING-8."""
    code, lines = debt_run(capsys, build_repo({WORKFLOW: workflow()}))
    assert (code, lines[-1]) == (1, "! 1 finding in 1 area · debt ceiling measured, not enforced · <1s")
    assert "1 finding" in debt_words("\n".join(lines))


def test_no_key_of_the_configuration_switches_the_rule_off(build_repo, capsys):
    """TIMEOUT-10: the configuration refuses a key that names the rule, and the rule still judges."""
    repo = build_repo({WORKFLOW: workflow(), "quality/config.toml": "[job-timeout]\nallow = true\n"})
    code, _ = run(capsys, repo)
    assert (code, shape(rule_findings(repo, Rule.JOB_TIMEOUT))) == (2, [missing()])


HAND_FIX = "delete or lower the entry in quality/baseline.toml by hand"


def held(entries: dict[str, dict[str, int]]) -> str:
    """A baseline holding a `[job_minutes]` table with `entries`, a mapping from path to job to minutes."""
    tables = "".join(
        f"[job_minutes.{json.dumps(path)}]\n" + "".join(f"{json.dumps(job)} = {minutes}\n" for job, minutes in jobs.items())
        for path, jobs in entries.items()
    )
    return tables or "[job_minutes]\n"


def outcome(findings):
    return [(finding.event, finding.path, finding.line, finding.fact, finding.fix) for finding in findings]


def new_debt(raw: str, path: str = WORKFLOW, line: int = 6):
    return (NEW_DEBT, path, line, f"job e2e has timeout-minutes {raw} · limit 15", LOWER_FIX)


def grew(raw: str, entry: int, path: str = WORKFLOW, line: int = 6):
    return (GREW, path, line, f"job e2e has timeout-minutes {raw} · ceiling {entry}", f"bring it back to {entry} or below")


def can_fall(raw: str, entry: int, path: str = WORKFLOW, line: int = 6):
    return (CAN_FALL, path, line, f"job e2e has timeout-minutes {raw} · ceiling {entry}", HAND_FIX)


def gone(entry: int, path: str = WORKFLOW, job_id: str = "e2e"):
    return (CAN_FALL, path, None, f"job {job_id} is gone · ceiling {entry}", HAND_FIX)


def no_debt(entry: int, path: str = WORKFLOW, job_id: str = "e2e"):
    return (CAN_FALL, path, None, f"job {job_id} has no debt · ceiling {entry}", HAND_FIX)


def judged_against(build_repo, text: str, entry: int | None | str):
    """The findings for one workflow `text` whose job `e2e` is held at `entry`: no baseline file (`"no file"`), a table
    without the job (`None`) or an entry."""
    files = {WORKFLOW: text}
    if entry != "no file":
        files[BASELINE_PATH] = held({} if entry is None else {WORKFLOW: {"e2e": entry}})
    return outcome(rule_findings(build_repo(files), Rule.JOB_TIMEOUT))


COMPARE = [
    pytest.param("15", None, [], id="15, a table without the job"),
    pytest.param("15", 15, [no_debt(15)], id="15, an entry of 15 is read as gone"),
    pytest.param("15", 40, [can_fall("15", 40)], id="15, an entry above"),
    pytest.param("15", 12, [no_debt(12)], id="15, an entry of 12 is gone and never grew"),
    pytest.param("16", None, [new_debt("16")], id="16, a table without the job"),
    pytest.param("16", 16, [], id="16, an entry equal"),
    pytest.param("16", 20, [can_fall("16", 20)], id="16, an entry above"),
    pytest.param("16", 15, [new_debt("16"), no_debt(15)], id="16, an entry of 15 is no entry"),
    pytest.param("17", 16, [grew("17", 16)], id="17, an entry below"),
    pytest.param("40", None, [new_debt("40")], id="40, a table without the job"),
    pytest.param("40", 40, [], id="40, an entry equal"),
    pytest.param("40", 45, [can_fall("40", 45)], id="40, an entry above"),
    pytest.param("40", 35, [grew("40", 35)], id="40, an entry below"),
    pytest.param("17.5", "no file", [new_debt("17.5")], id="17.5, no file"),
    pytest.param("17.5", None, [new_debt("17.5")], id="17.5, a table without the job"),
    pytest.param("17.5", 18, [], id="17.5, an entry of 18 holds it"),
    pytest.param("17.5", 20, [can_fall("17.5", 20)], id="17.5, an entry of 20"),
    pytest.param("17.5", 17, [grew("17.5", 17)], id="17.5, an entry of 17"),
    pytest.param("15.5", None, [new_debt("15.5")], id="15.5, a table without the job"),
    pytest.param("15.5", 16, [], id="15.5, an entry of 16 holds it"),
    pytest.param("15.5", 20, [can_fall("15.5", 20)], id="15.5, an entry of 20"),
    pytest.param("15.5", 15, [new_debt("15.5"), no_debt(15)], id="15.5, an entry of 15 is no entry"),
]


@pytest.mark.parametrize(("limit", "entry", "expected"), COMPARE)
def test_a_limit_is_judged_by_its_rounded_up_value_against_the_entry_of_its_file_and_job(build_repo, limit, entry, expected):
    """TIMEOUT-14, TIMEOUT-15, TIMEOUT-16."""
    assert judged_against(build_repo, workflow(jobs=job("e2e", limit)), entry) == expected


@pytest.mark.parametrize("entry", ["no file", None, 10, 40], ids=["no file", "no entry", "an entry of 10", "an entry of 40"])
@pytest.mark.parametrize(
    ("jobs", "expected"),
    [
        (job("e2e", None), missing("e2e")),
        (job("e2e", "x"), (INVALID, WORKFLOW, 6, "job e2e has a timeout-minutes that is not a number above zero")),
    ],
    ids=["missing", "invalid"],
)
def test_a_job_without_a_readable_limit_keeps_its_finding_and_its_entry_stands(build_repo, jobs, expected, entry):
    """TIMEOUT-15, TIMEOUT-16."""
    findings = judged_against(build_repo, workflow(jobs=jobs), entry)
    assert [finding[:4] for finding in findings] == [expected]


@pytest.mark.parametrize(
    ("entry", "expected"),
    [(40, gone(40, job_id="old")), (15, no_debt(15, job_id="old")), (10, no_debt(10, job_id="old"))],
    ids=["an entry above 15", "an entry of 15", "an entry of 10 has no debt"],
)
def test_an_entry_for_a_job_the_file_lacks_is_gone_with_no_line_or_has_no_debt_when_it_is_15_or_less(build_repo, entry, expected):
    """TIMEOUT-16."""
    repo = build_repo({WORKFLOW: workflow(jobs=job("e2e", "10")), BASELINE_PATH: held({WORKFLOW: {"old": entry}})})
    assert outcome(rule_findings(repo, Rule.JOB_TIMEOUT)) == [expected]


def test_gone_entries_follow_the_measured_findings_sorted_by_path_then_job(build_repo):
    """TIMEOUT-16."""
    entries = {CALLED: {"q": 30}, WORKFLOW: {"z": 20, "a": 25, "e2e": 35}}
    repo = build_repo({WORKFLOW: workflow(jobs=job("e2e", "40")), BASELINE_PATH: held(entries)})
    assert outcome(rule_findings(repo, Rule.JOB_TIMEOUT)) == [
        grew("40", 35),
        gone(25, job_id="a"),
        gone(20, job_id="z"),
        gone(30, CALLED, "q"),
    ]


def test_an_entry_under_a_job_that_calls_a_workflow_is_gone(build_repo):
    """TIMEOUT-16."""
    caller = workflow(jobs="  call:\n    uses: ./" + CALLED + "\n")
    called = "on: workflow_call\njobs:\n" + job("e2e", "10")
    repo = build_repo({WORKFLOW: caller, CALLED: called, BASELINE_PATH: held({WORKFLOW: {"call": 40}})})
    assert outcome(rule_findings(repo, Rule.JOB_TIMEOUT)) == [gone(40, job_id="call")]


CALLER = workflow(jobs="  call:\n    uses: ./" + CALLED + "\n")
CALLED_DEBT = "on: workflow_call\njobs:\n" + job("e2e", "40")


@pytest.mark.parametrize(
    ("entries", "expected"),
    [
        pytest.param({CALLED: {"e2e": 40}}, [], id="the file's own path holds it"),
        pytest.param({CALLED: {"e2e": 50}}, [can_fall("40", 50, CALLED, 4)], id="above, by the file's path"),
        pytest.param({CALLED: {"e2e": 30}}, [grew("40", 30, CALLED, 4)], id="below, by the file's path"),
        pytest.param({WORKFLOW: {"e2e": 40}}, [new_debt("40", CALLED, 4), gone(40)], id="the root's path holds nothing"),
        pytest.param({}, [new_debt("40", CALLED, 4)], id="no entry"),
    ],
)
def test_a_job_of_a_called_workflow_is_held_by_the_path_of_the_file_that_defines_it(build_repo, entries, expected):
    """TIMEOUT-15, TIMEOUT-16."""
    repo = build_repo({WORKFLOW: CALLER, CALLED: CALLED_DEBT, BASELINE_PATH: held(entries)})
    assert outcome(rule_findings(repo, Rule.JOB_TIMEOUT)) == expected


def test_an_entry_of_a_file_no_root_reaches_any_more_is_gone_whatever_its_value(build_repo):
    """TIMEOUT-16."""
    outside = "on: workflow_dispatch\njobs:\n" + job("e2e", "40")
    repo = build_repo({WORKFLOW: workflow(jobs=job("j", "10")), CALLED: outside, BASELINE_PATH: held({CALLED: {"e2e": 40}})})
    assert outcome(rule_findings(repo, Rule.JOB_TIMEOUT)) == [gone(40, CALLED)]


def test_a_file_no_root_reaches_with_no_entry_is_outside_the_rule(build_repo):
    """TIMEOUT-15."""
    outside = "on: workflow_dispatch\njobs:\n" + job("e2e", "40")
    repo = build_repo({WORKFLOW: workflow(jobs=job("j", "10")), CALLED: outside, BASELINE_PATH: held({})})
    assert rule_findings(repo, Rule.JOB_TIMEOUT) == []


def test_an_entry_of_a_path_the_index_does_not_track_is_gone(build_repo):
    """TIMEOUT-16."""
    repo = build_repo({WORKFLOW: workflow(jobs=job("j", "10")), BASELINE_PATH: held({".github/workflows/old.yml": {"e2e": 40}})})
    assert outcome(rule_findings(repo, Rule.JOB_TIMEOUT)) == [gone(40, ".github/workflows/old.yml")]


@pytest.mark.parametrize(
    "baseline",
    ["[job_minutes", f'[job_minutes."{WORKFLOW}"]\ne2e = 40.5\n'],
    ids=["invalid TOML", "a float entry"],
)
def test_a_malformed_baseline_makes_the_rule_cannot_judge_naming_the_file_as_size_and_clone_do(build_repo, capsys, baseline):
    """TIMEOUT-15."""
    repo = build_repo({WORKFLOW: workflow(), BASELINE_PATH: baseline})
    code, _ = run(capsys, repo)
    reasons = refusals(repo)
    assert code == 2
    assert all(BASELINE_PATH in reasons[rule] for rule in (Rule.JOB_TIMEOUT, Rule.SIZE, Rule.CLONE))


def test_a_refused_source_with_a_readable_job_table_leaves_the_job_findings_of_the_debt_area(build_repo, capsys):
    """TIMEOUT-8, TIMEOUT-15."""
    files = {WORKFLOW: workflow(jobs=job("e2e", "45")), BASELINE_PATH: held({WORKFLOW: {"e2e": 40}}), "src/bad.py": "def broken(:\n"}
    code, output = run(capsys, build_repo(files))
    assert (code, refusal_row("debt") in output) == (2, True)
    assert [(location, row.event) for location, row in area_finding_rows(output, "debt")] == [(f"{WORKFLOW}:6", GREW)]


def test_a_job_above_fifteen_is_new_debt_without_a_ceiling_file_and_the_closing_words_say_so(build_repo, capsys):
    """TIMEOUT-15."""
    code, lines = debt_run(capsys, build_repo({WORKFLOW: workflow(jobs=job("e2e", "40"))}))
    assert (code, lines[-1]) == (1, "! 1 finding in 1 area · debt ceiling measured, not enforced · <1s")


def test_the_clean_debt_row_counts_no_job_entry(build_repo, capsys):
    """TIMEOUT-15."""
    repo = build_repo({WORKFLOW: workflow(top=CONCURRENCY, jobs=job("e2e", "40")), BASELINE_PATH: "[clones]\n" + held({WORKFLOW: {"e2e": 40}})})
    code, lines = debt_run(capsys, repo)
    assert (code, "ceiling held · 0 entries, 0 clone pairs" in debt_words("\n".join(lines))) == (0, True)
