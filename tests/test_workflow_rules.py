import json
import os
from pathlib import Path

import pytest

from quality.report import FindingEvent
from quality.rules import Rule
from tests.support.check import check_output as run
from tests.support.check import env_row_stands, is_clean, refusal_row, refusals, rule_findings

MISSING, SHARED_GROUP, CANCELS_MAIN = FindingEvent.MISSING, FindingEvent.SHARED_GROUP, FindingEvent.CANCELS_MAIN
WORKFLOW = ".github/workflows/w.yml"
GROUP = "${{ github.workflow }}-${{ github.event.pull_request.number || github.run_id }}"
GROUP_BY_EVENT = "${{ github.workflow }}-${{ github.event_name }}-${{ github.event.pull_request.number || github.run_id }}"
FLAG = "${{ github.event.pull_request.number != null }}"
FLAG_BY_EVENT_NAME = "${{ github.event_name == 'pull_request' }}"
NO_GROUP = "no group"
NO_CONCURRENCY = "no concurrency"
NO_FLAG = "no cancel-in-progress"
NOT_LISTED_GROUP = "group is not a listed form"
NOT_LISTED_FLAG = "cancel-in-progress is not a listed form"
JOBS = "permissions: {}\njobs:\n  j:\n    runs-on: ubuntu-latest\n    timeout-minutes: 15\n    steps:\n      - run: 'true'\n"
ON_PULL_REQUEST = "on: pull_request\n"
ON_COMBINED = "on: [pull_request, pull_request_review]\n"


def block(group: str, flag: str) -> str:
    return f"concurrency:\n  group: {group}\n  cancel-in-progress: {flag}\n"


def workflow(on: str = ON_PULL_REQUEST, concurrency: str = "") -> str:
    """A workflow whose `on:` is one line (line 2) and whose `concurrency` block starts on line 3."""
    return f"name: w\n{on}{concurrency}{JOBS}"


def fix_lines(group: str) -> tuple[str, ...]:
    return ("concurrency:", f"  group: {group}", f"  cancel-in-progress: {FLAG}")


def judged(build_repo, text: str):
    return rule_findings(build_repo({WORKFLOW: text}), Rule.PR_CONCURRENCY)


def shape(findings):
    return [(finding.event, finding.line, finding.fact) for finding in findings]


@pytest.mark.parametrize(
    "on",
    [
        "on: pull_request\n",
        "on: pull_request_target\n",
        "on: pull_request_review\n",
        "on: [push, pull_request]\n",
        "on:\n  push:\n    branches: [main]\n  pull_request:\n    branches: ['releases/**']\n",
        "on:\n  pull_request_target:\n    types: [opened]\n",
    ],
    ids=["string", "target", "review", "list", "mapping with branch globs", "mapping with types"],
)
def test_a_pull_request_workflow_without_concurrency_is_missing(build_repo, on):
    """CONCURRENCY-1, CONCURRENCY-5."""
    assert shape(judged(build_repo, workflow(on))) == [(MISSING, None, NO_CONCURRENCY)]


@pytest.mark.parametrize(
    "on",
    [
        "",
        "on: push\n",
        "on:\n  push:\n    branches: [main]\n",
        "on:\n  push:\n    tags: ['v*']\n",
        "on:\n  schedule:\n    - cron: '1 1 * * *'\n",
        "on: workflow_dispatch\n",
        "on: merge_group\n",
        "on: workflow_call\n",
    ],
    ids=["no on", "push", "push to main", "tags only", "schedule", "dispatch", "merge group", "call"],
)
def test_a_workflow_no_pull_request_triggers_is_outside_in_every_state_of_concurrency(build_repo, on):
    """CONCURRENCY-1."""
    repo = build_repo({
        ".github/workflows/bare.yml": workflow(on),
        ".github/workflows/true.yml": workflow(on, block("fixed", "true")),
        ".github/workflows/listed.yml": workflow(on, block(GROUP, FLAG)),
    })
    assert rule_findings(repo, Rule.PR_CONCURRENCY) == []


@pytest.mark.parametrize(
    "on",
    [ON_PULL_REQUEST, "on: pull_request_review\n", ON_COMBINED, "on: [pull_request, push]\n"],
    ids=["pull request", "review", "pull request and review", "pull request and push"],
)
def test_the_canonical_block_passes_for_every_trigger_set(build_repo, capsys, on):
    """CONCURRENCY-3, CONCURRENCY-4, CONCURRENCY-7: the repository is clean under every rule."""
    code, output = run(capsys, build_repo({WORKFLOW: workflow(on, block(GROUP, FLAG))}))
    assert (code, is_clean(output)) == (0, True), output


@pytest.mark.parametrize("on", ["on: pull_request_target\n", "on: [pull_request, pull_request_target]\n"])
def test_the_canonical_block_passes_for_a_workflow_with_the_target_trigger(build_repo, on):
    """CONCURRENCY-3, CONCURRENCY-4."""
    assert judged(build_repo, workflow(on, block(GROUP, FLAG))) == []


@pytest.mark.parametrize(
    ("group", "flag"),
    [
        (GROUP_BY_EVENT, FLAG),
        (GROUP, FLAG_BY_EVENT_NAME),
        (GROUP_BY_EVENT, FLAG_BY_EVENT_NAME),
        (f'"{GROUP}"', f'"{FLAG}"'),
        (f"'{GROUP}'", f"'{FLAG}'"),
        (f">-\n    {GROUP}", f">-\n    {FLAG}"),
        (
            "${{github.workflow}}-${{github.event.pull_request.number||github.run_id}}",
            "${{github.event.pull_request.number!=null}}",
        ),
        (
            "${{   github.workflow   }}-${{ github.event.pull_request.number   ||\tgithub.run_id }}",
            "${{ github.event.pull_request.number   !=   null }}",
        ),
        ("${{ github.workflow }}-${{ github.event_name }}-${{github.event.pull_request.number||github.run_id}}",
         "${{ github.event_name=='pull_request' }}"),
    ],
    ids=[
        "group by event", "flag by event name", "both by event", "double quotes", "single quotes", "folded block",
        "no spaces", "wide spaces", "event forms without spaces",
    ],
)
def test_each_listed_form_passes_whatever_its_quoting_and_whitespace(build_repo, group, flag):
    """CONCURRENCY-3: G1 or G2 with F1 or F2 in a workflow that has only the pull_request trigger."""
    assert judged(build_repo, workflow(ON_PULL_REQUEST, block(group, flag))) == []


def test_the_group_without_the_event_name_passes_in_a_workflow_that_combines_triggers(build_repo):
    """CONCURRENCY-4: that effect of the group is named and not judged."""
    assert judged(build_repo, workflow(ON_COMBINED, block(GROUP, FLAG))) == []


@pytest.mark.parametrize("on", ["on: pull_request_target\n", "on: pull_request_review\n", ON_COMBINED])
def test_the_flag_by_event_name_cancels_main_in_a_workflow_with_another_pull_request_trigger(build_repo, on):
    """CONCURRENCY-3: it leaves those runs uncancelled."""
    findings = judged(build_repo, workflow(on, block(GROUP_BY_EVENT, FLAG_BY_EVENT_NAME)))
    assert shape(findings) == [(CANCELS_MAIN, 5, NOT_LISTED_FLAG)]


@pytest.mark.parametrize(
    ("group", "fact"),
    [
        ("fixed", NOT_LISTED_GROUP),
        ("${{ github.event.pull_request.number || github.run_id }}", NOT_LISTED_GROUP),
        ("${{ github.workflow }}-${{ github.run_id }}", NOT_LISTED_GROUP),
        ("${{ github.workflow }}-${{ github.event.number || github.run_id }}", NOT_LISTED_GROUP),
        ("${{ github.workflow }}-${{ github.sha }}", f"{NOT_LISTED_GROUP} (falls back to github.sha)"),
        ("${{ github.workflow }}-${{ github.ref }}", f"{NOT_LISTED_GROUP} (falls back to github.ref)"),
        ("${{ github.workflow }}-${{ github.head_ref }}", f"{NOT_LISTED_GROUP} (falls back to github.head_ref)"),
        ("${{ github.workflow }}-${{ github.ref_name }}", f"{NOT_LISTED_GROUP} (falls back to github.ref_name)"),
        (
            "${{ github.workflow }}-${{ github.event.pull_request.number || github.sha }}",
            f"{NOT_LISTED_GROUP} (falls back to github.sha)",
        ),
        ("${{ github.workflow }}--${{ github.event.pull_request.number || github.run_id }}", NOT_LISTED_GROUP),
    ],
    ids=[
        "constant", "no workflow part", "run id only", "an equivalent number", "sha", "ref", "head ref", "ref name",
        "number or sha", "another separator",
    ],
)
def test_a_group_outside_the_list_is_a_shared_group_naming_the_fallback_it_holds(build_repo, group, fact):
    """CONCURRENCY-3, CONCURRENCY-5."""
    assert shape(judged(build_repo, workflow(ON_PULL_REQUEST, block(group, FLAG)))) == [(SHARED_GROUP, 4, fact)]


@pytest.mark.parametrize(
    "flag",
    [
        "true",
        "false",
        "",
        "'true'",
        "${{ github.event_name == 'pull_request' || true }}",
        "${{ github.event.pull_request != null }}",
        "${{ !github.event.pull_request.number }}",
        "${{ github.event_name == 'push' }}",
    ],
    ids=["true", "false", "empty", "quoted true", "or true", "an equivalent flag", "negated number", "push"],
)
def test_a_flag_outside_the_list_cancels_main(build_repo, flag):
    """CONCURRENCY-3, CONCURRENCY-5."""
    assert shape(judged(build_repo, workflow(ON_PULL_REQUEST, block(GROUP, flag)))) == [(CANCELS_MAIN, 5, NOT_LISTED_FLAG)]


def test_a_group_and_a_flag_outside_the_list_are_both_found(build_repo):
    """CONCURRENCY-5."""
    findings = judged(build_repo, workflow(ON_PULL_REQUEST, block("fixed", "true")))
    assert shape(findings) == [(SHARED_GROUP, 4, NOT_LISTED_GROUP), (CANCELS_MAIN, 5, NOT_LISTED_FLAG)]


@pytest.mark.parametrize(
    "concurrency",
    [
        f"concurrency:\n  group: {GROUP}\n",
        f"concurrency: {GROUP}\n",
    ],
    ids=["group only", "scalar group"],
)
def test_a_concurrency_with_a_group_and_no_flag_is_missing(build_repo, concurrency):
    """CONCURRENCY-5: a group with no flag, a scalar included."""
    findings = judged(build_repo, workflow(ON_PULL_REQUEST, concurrency))
    assert shape(findings) == [(MISSING, None, NO_FLAG)]


def test_a_job_level_concurrency_does_not_count(build_repo):
    """CONCURRENCY-2."""
    jobs = f"jobs:\n  j:\n    runs-on: ubuntu-latest\n    concurrency:\n      group: {GROUP}\n      cancel-in-progress: {FLAG}\n    steps:\n      - run: 'true'\n"
    findings = judged(build_repo, f"name: w\n{ON_PULL_REQUEST}{jobs}")
    assert shape(findings) == [(MISSING, None, NO_CONCURRENCY)]


@pytest.mark.parametrize(
    ("on", "concurrency", "group"),
    [
        (ON_PULL_REQUEST, "", GROUP),
        ("on: pull_request_target\n", "", GROUP),
        ("on: pull_request_review\n", "", GROUP),
        (ON_COMBINED, "", GROUP_BY_EVENT),
        ("on: [pull_request_target, pull_request_review]\n", "", GROUP_BY_EVENT),
        ("on: [pull_request, pull_request_target]\n", block("fixed", "true"), GROUP_BY_EVENT),
        (ON_PULL_REQUEST, block("fixed", "true"), GROUP),
    ],
    ids=["pull request", "target", "review", "combined", "target and review", "combined with findings", "findings"],
)
def test_every_finding_shows_the_canonical_block_with_the_group_of_the_triggers_as_its_fix(build_repo, on, concurrency, group):
    """CONCURRENCY-4, CONCURRENCY-5: the group by event name for a workflow that names more than one pull-request trigger."""
    findings = judged(build_repo, workflow(on, concurrency))
    assert findings
    assert [finding.fix.lines for finding in findings] == [fix_lines(group)] * len(findings)


def test_the_printed_fix_label_ends_in_exactly_one_colon(build_repo, capsys):
    """CONCURRENCY-5: the label carries no colon of its own, because the printer adds one."""
    repo = build_repo({WORKFLOW: workflow()})
    [finding] = rule_findings(repo, Rule.PR_CONCURRENCY)
    _, output = run(capsys, repo)
    printed = [line.strip() for line in output.splitlines() if finding.fix.label in line]
    assert not finding.fix.label.endswith(":")
    assert (printed, "::" in output) == ([f"fix: {finding.fix.label}:"], False)


def test_only_the_workflows_a_pull_request_triggers_are_found_and_each_is_named(build_repo):
    """CONCURRENCY-1, CONCURRENCY-5."""
    repo = build_repo({
        ".github/workflows/a.yml": workflow(),
        ".github/workflows/b.yaml": workflow("on: push\n"),
        ".github/workflows/c.yml": workflow(ON_PULL_REQUEST, block(GROUP, FLAG)),
    })
    assert [finding.path for finding in rule_findings(repo, Rule.PR_CONCURRENCY)] == [".github/workflows/a.yml"]


ANCHORS = f"defaults: &defaults\n  group: {GROUP}\n  cancel-in-progress: {FLAG}\ngroup: &group {GROUP}\nflag: &flag '{FLAG}'\non_: &on pull_request\n"
ALIASED_KEY = "key: &key concurrency\n"


@pytest.mark.parametrize(
    "text",
    [
        "name: w\non: [pull_request\n",
        f"name: w\n{ON_PULL_REQUEST}concurrency:\n  group: [a, b]\n  cancel-in-progress: {FLAG}\n",
        f"name: w\n{ON_PULL_REQUEST}concurrency:\n  group:\n    a: b\n  cancel-in-progress: {FLAG}\n",
        f"name: w\n{ON_PULL_REQUEST}concurrency:\n  group: {GROUP}\n  cancel-in-progress: [true]\n",
        f"name: w\n{ON_PULL_REQUEST}concurrency:\n  group: {GROUP}\n  cancel-in-progress:\n    a: b\n",
        f"name: w\n{ON_PULL_REQUEST}concurrency: [a]\n",
        f"{ANCHORS}<<: *defaults\n{ON_PULL_REQUEST}",
        f"{ANCHORS}on: *on\n",
        f"{ANCHORS}on: [push, *on]\n",
        f"{ANCHORS}on:\n  <<: {{pull_request: }}\n",
        f"{ANCHORS}{ON_PULL_REQUEST}concurrency: *defaults\n",
        f"{ANCHORS}{ON_PULL_REQUEST}concurrency:\n  <<: *defaults\n",
        f"{ANCHORS}{ON_PULL_REQUEST}concurrency:\n  group: *group\n  cancel-in-progress: {FLAG}\n",
        f"{ANCHORS}{ON_PULL_REQUEST}concurrency:\n  group: {GROUP}\n  cancel-in-progress: *flag\n",
        f"{ALIASED_KEY}{ON_PULL_REQUEST}*key :\n  group: {GROUP}\n  cancel-in-progress: {FLAG}\n",
        f"{ALIASED_KEY}on:\n  pull_request:\n  *key :\n",
        f"{ALIASED_KEY}{ON_PULL_REQUEST}concurrency:\n  group: {GROUP}\n  *key : {FLAG}\n",
        f"name: w\n{ON_PULL_REQUEST}concurrency: &c\n  group: {GROUP}\n  cancel-in-progress: {FLAG}\n  again: *c\n",
    ],
    ids=[
        "yaml error", "group list", "group mapping", "flag list", "flag mapping", "concurrency list", "merge key at the top",
        "alias as on", "alias in the on list", "merge key at on", "alias as concurrency", "merge key at concurrency",
        "alias as group", "alias as flag", "alias as the key at the top", "alias as the key in on", "alias as the key in concurrency",
        "alias of its own mapping",
    ],
)
def test_a_workflow_the_rule_cannot_read_is_cannot_judge_naming_the_file_while_other_rules_still_judge(build_repo, capsys, text):
    """CONCURRENCY-6."""
    repo = build_repo({WORKFLOW: text, ".env": "A=1\n"})
    code, output = run(capsys, repo)
    assert code == 2, output
    assert refusal_row("workflows") in output
    assert Rule.PR_CONCURRENCY in refusals(repo)
    assert WORKFLOW in refusals(repo)[Rule.PR_CONCURRENCY]
    assert env_row_stands(output)


def refuse_by_mode(repo: Path, _elsewhere: Path) -> None:
    (repo / WORKFLOW).chmod(0)


def refuse_by_link(repo: Path, elsewhere: Path) -> None:
    (elsewhere / "outside.yml").write_text(workflow())
    (repo / WORKFLOW).unlink()
    (repo / WORKFLOW).symlink_to(elsewhere / "outside.yml")


def refuse_by_encoding(repo: Path, _elsewhere: Path) -> None:
    (repo / WORKFLOW).write_bytes(b"on: pull_request\n# \xff\xfe\n")


@pytest.mark.parametrize(
    "refuse",
    [
        pytest.param(
            refuse_by_mode, id="mode 0", marks=pytest.mark.skipif(os.geteuid() == 0, reason="root reads a mode-0 file")
        ),
        pytest.param(refuse_by_link, id="link out of the repository"),
        pytest.param(refuse_by_encoding, id="text that is not UTF-8"),
    ],
)
def test_a_workflow_the_reader_refuses_is_cannot_judge_naming_the_file(build_repo, elsewhere, refuse):
    """CONCURRENCY-6."""
    repo = build_repo({WORKFLOW: workflow()})
    refuse(repo, elsewhere)
    assert WORKFLOW in refusals(repo)[Rule.PR_CONCURRENCY]


def test_a_tracked_workflow_gone_from_the_tree_is_skipped(build_repo):
    """CONCURRENCY-6."""
    repo = build_repo({WORKFLOW: workflow()})
    (repo / WORKFLOW).unlink()
    assert (rule_findings(repo, Rule.PR_CONCURRENCY), Rule.PR_CONCURRENCY in refusals(repo)) == ([], False)


def quoted(text: str) -> str:
    """A YAML double-quoted scalar that spells every non-ASCII character as an escape."""
    return json.dumps(text)


@pytest.mark.parametrize("space", ["\u00a0", "\u2003", "\u0085", "\u3000"], ids=["no-break", "em space", "next line", "ideographic"])
def test_whitespace_that_is_not_ascii_inside_an_expression_keeps_a_form_outside_the_list(build_repo, space):
    """CONCURRENCY-3: only space, tab, carriage return and line feed fold."""
    group = GROUP.replace("{{ github.workflow }}", f"{{{{{space}github.workflow{space}}}}}")
    flag = FLAG.replace("{{ ", f"{{{{{space}")
    assert shape(judged(build_repo, workflow(ON_PULL_REQUEST, block(quoted(group), FLAG)))) == [(SHARED_GROUP, 4, NOT_LISTED_GROUP)]
    assert shape(judged(build_repo, workflow(ON_PULL_REQUEST, block(GROUP, quoted(flag))))) == [(CANCELS_MAIN, 5, NOT_LISTED_FLAG)]


@pytest.mark.parametrize("header", ["|", ">", "|+", ">+", "|-", ">-"])
def test_a_block_scalar_is_read_by_its_content_whatever_its_final_line_breaks(build_repo, header):
    """CONCURRENCY-3."""
    group = f"{header}\n    {GROUP}\n\n"
    flag = f"{header}\n    {FLAG}\n\n"
    assert judged(build_repo, workflow(ON_PULL_REQUEST, block(group, FLAG))) == []
    assert judged(build_repo, workflow(ON_PULL_REQUEST, block(GROUP, flag))) == []


@pytest.mark.parametrize("header", ["|", ">", "|+", ">-"])
def test_a_block_scalar_with_an_unlisted_text_is_still_found(build_repo, header):
    """CONCURRENCY-3."""
    assert shape(judged(build_repo, workflow(ON_PULL_REQUEST, block(f"{header}\n    fixed\n", FLAG)))) == [(SHARED_GROUP, 4, NOT_LISTED_GROUP)]
    assert shape(judged(build_repo, workflow(ON_PULL_REQUEST, block(GROUP, f"{header}\n    true\n")))) == [(CANCELS_MAIN, 5, NOT_LISTED_FLAG)]


def test_a_quoted_scalar_with_an_escaped_line_break_stays_outside_the_list(build_repo):
    """CONCURRENCY-3: only a block scalar's own final line break is dropped."""
    assert shape(judged(build_repo, workflow(ON_PULL_REQUEST, block(f'"{GROUP}\\n"', FLAG)))) == [(SHARED_GROUP, 4, NOT_LISTED_GROUP)]
    assert shape(judged(build_repo, workflow(ON_PULL_REQUEST, block(GROUP, f'"{FLAG}\\n"')))) == [(CANCELS_MAIN, 5, NOT_LISTED_FLAG)]


@pytest.mark.parametrize(
    ("concurrency", "expected"),
    [
        (f"concurrency:\n  cancel-in-progress: {FLAG}\n", [(SHARED_GROUP, 3, NO_GROUP)]),
        ("concurrency: {}\n", [(SHARED_GROUP, 3, NO_GROUP), (MISSING, None, NO_FLAG)]),
    ],
    ids=["flag only", "empty mapping"],
)
def test_a_concurrency_mapping_without_a_group_is_a_shared_group_at_the_concurrency_key(build_repo, concurrency, expected):
    """CONCURRENCY-5."""
    assert shape(judged(build_repo, workflow(ON_PULL_REQUEST, concurrency))) == expected
