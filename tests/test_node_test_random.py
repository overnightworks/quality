import pytest

from quality.report import FindingEvent
from quality.rules import Rule
from tests.support.check import check_output as run
from tests.support.check import check_result, refusals, rule_findings

RULE = Rule.NODE_TEST_RANDOM
UNSHUFFLED, UNSEEDED, INVALID = FindingEvent.UNSHUFFLED, FindingEvent.UNSEEDED, FindingEvent.INVALID
WORKFLOW = ".github/workflows/w.yml"
SCRIPT_LINE = 10
FIXED_ORDER = "node --test runs its files and tests in a fixed order"
NO_SEED = "no reporter of the call prints the seed to the log"
UNREADABLE = "command cannot be read to its end"
RUN_TIME = "command is built at run time, node --test cannot be read"
SHUFFLE_FIX = (
    'pass --test-randomize before the test files and drop --no-test-randomize and a fixed --test-random-seed (node 24.16 or newer); '
    'node then prints "Randomized test order seed: N"'
)
SEED_FIX = 'add --test-reporter=spec --test-reporter-destination=stdout so the log prints "Randomized test order seed: N"'
MARKER = "# node-test-random: the order is fixed on purpose"
HEAD = "on: push\npermissions: {}\njobs:\n  j:\n    runs-on: ubuntu-latest\n    timeout-minutes: 15\n    steps:\n"


def workflow(*lines: str, style: str = "|") -> str:
    """One step whose `run:` is line 9 and whose script starts on line 10."""
    return HEAD + f"      - name: t\n        run: {style}\n" + "".join(f"          {line}\n" for line in lines)


def judged(build_repo, call: str, style: str = "|"):
    return rule_findings(build_repo({WORKFLOW: workflow(*call.split("\n"), style=style)}), RULE)


CLEAN_CALLS = [
    pytest.param("node --test-randomize --test x.mjs", id="randomize before the files"),
    pytest.param("node --test --test-randomize=false x.mjs", id="randomize with a value counts as set"),
    pytest.param("node --test --no-test-randomize --test-randomize x.mjs", id="randomize after no-randomize"),
    pytest.param("node --test --test-random-seed=${{ github.run_id }} x.mjs", id="seed from an expression"),
    pytest.param("node --test --test-random-seed=$SEED x.mjs", id="seed from a variable"),
    pytest.param("node --test --test-random-seed $(date +%s) x.mjs", id="seed from a substitution"),
    pytest.param("node --test --test-randomize --test-reporter=spec --test-reporter-destination=stdout x.mjs", id="spec to stdout"),
    pytest.param(
        "node --test --test-randomize --test-reporter=spec --test-reporter-destination=stdout "
        "--test-reporter=lcov --test-reporter-destination=lcov.info x.mjs",
        id="spec to stdout with lcov to a file",
    ),
    pytest.param("node --test --test-randomize --test-reporter=spec x.mjs", id="spec without a destination"),
    pytest.param("node --test --test-randomize --test-reporter=tap --test-reporter-destination=stderr x.mjs", id="tap to stderr"),
    pytest.param("node --test --test-randomize --test-reporter=junit x.mjs", id="junit"),
    pytest.param("node script.mjs", id="no --test"),
    pytest.param("node --test-randomize script.mjs", id="randomize without --test"),
    pytest.param("echo node --test", id="no call"),
    pytest.param("node --test --test-randomize x.mjs -- --no-test-randomize", id="no-randomize after -- is not read"),
]
UNSHUFFLED_CALLS = [
    pytest.param("node --test x.mjs", id="plain"),
    pytest.param("node --test --test-randomize --no-test-randomize x.mjs", id="no-randomize last"),
    pytest.param("node --test --test-random-seed=7 x.mjs", id="fixed seed with ="),
    pytest.param("node --test --test-random-seed 7 x.mjs", id="fixed seed as next word"),
    pytest.param("node --test -- --test-randomize", id="randomize after -- is not read"),
    pytest.param("pnpm exec node --test x.mjs", id="through pnpm exec"),
    pytest.param("npx node --test x.mjs", id="through npx"),
    pytest.param("env A=1 node --test x.mjs", id="through env"),
    pytest.param("A=1 node --test x.mjs", id="after an assignment"),
    pytest.param("bash -c 'node --test x.mjs'", id="in a shell string"),
    pytest.param("node --test --test-reporter=dot x.mjs", id="a dot reporter does not hide the fixed order"),
]
UNSEEDED_CALLS = [
    pytest.param("--test-reporter=dot", id="dot"),
    pytest.param("--test-reporter=lcov --test-reporter-destination=lcov.info", id="lcov alone"),
    pytest.param("--test-reporter=spec --test-reporter-destination=out.txt", id="spec to a file"),
    pytest.param("--test-reporter=./reporter.mjs", id="a reporter module"),
    pytest.param("--test-reporter=spec --test-reporter-destination=out.txt --test-reporter=dot", id="the destination pairs by position"),
]


@pytest.mark.parametrize("call", CLEAN_CALLS)
def test_a_call_that_shuffles_with_its_seed_printed_is_clean(build_repo, call):
    """NODETEST-1, NODETEST-2, NODETEST-3: a call that shuffles and prints its seed, or is no node test call, passes."""
    assert judged(build_repo, call) == []


@pytest.mark.parametrize("call", UNSHUFFLED_CALLS)
def test_a_call_that_does_not_shuffle_is_unshuffled_at_its_first_line(build_repo, call):
    """NODETEST-2: the finding is at the call's line with the fact and the fix the rule words."""
    findings = judged(build_repo, call)
    assert [(finding.event, finding.path, finding.line) for finding in findings] == [(UNSHUFFLED, WORKFLOW, SCRIPT_LINE)]
    assert (findings[0].fact, findings[0].fix) == (FIXED_ORDER, SHUFFLE_FIX)


def test_a_folded_scalar_call_is_unshuffled_at_its_first_content_line(build_repo):
    """NODETEST-2: a call in a `>-` scalar is reported at the scalar's first content line."""
    (finding,) = judged(build_repo, "node --test\nx.mjs", style=">-")
    assert (finding.event, finding.line) == (UNSHUFFLED, SCRIPT_LINE)


@pytest.mark.parametrize("reporters", UNSEEDED_CALLS)
def test_a_call_whose_reporters_print_no_seed_is_unseeded(build_repo, reporters):
    """NODETEST-3: a shuffling call with no spec, tap or junit reporter to stdout or stderr is unseeded."""
    (finding,) = judged(build_repo, f"node --test --test-randomize {reporters} x.mjs")
    assert (finding.event, finding.line, finding.fact, finding.fix) == (UNSEEDED, SCRIPT_LINE, NO_SEED, SEED_FIX)


def test_an_unreadable_line_holding_node_test_is_invalid(build_repo):
    """NODETEST-4: an unterminated quote in a line with `node` and `--test` is `invalid` with the reader's fact."""
    (finding,) = judged(build_repo, "node --test 'x.mjs")
    assert (finding.event, finding.fact) == (INVALID, UNREADABLE)


def test_an_unreadable_line_without_the_words_is_nothing(build_repo):
    """NODETEST-4: an unreadable line that holds no node test call is no finding."""
    assert judged(build_repo, "echo 'x") == []


def test_a_command_built_at_run_time_in_a_script_holding_node_test_is_invalid(build_repo):
    """NODETEST-4: a command word chosen at run time hides whether it calls node --test."""
    findings = judged(build_repo, "$RUNNER --coverage; node --test --test-randomize x.mjs")
    assert [(finding.event, finding.fact) for finding in findings] == [(INVALID, RUN_TIME)]


def test_every_call_of_a_script_is_judged_on_its_own(build_repo):
    """NODETEST-2: two calls, one clean, one not, give one finding at the second."""
    (finding,) = judged(build_repo, "node --test --test-randomize x.mjs\nnode --test y.mjs")
    assert finding.line == SCRIPT_LINE + 1


def test_no_marker_clears_a_finding(build_repo):
    """NODETEST-5: no comment switches the rule off."""
    repo = build_repo({WORKFLOW: workflow(MARKER, "node --test x.mjs")})
    assert [finding.event for finding in rule_findings(repo, RULE)] == [UNSHUFFLED]


def test_a_repository_without_a_call_is_clean_and_its_extent_is_its_workflows(build_repo):
    """NODETEST-1: the extent is the number of workflows read."""
    repo = build_repo({WORKFLOW: workflow("echo hi")})
    (verdict,) = (verdict for verdict in check_result(repo).verdicts if verdict.rule is RULE)
    assert (verdict.findings, verdict.extent) == ((), 1)


def test_an_unreadable_workflow_is_exit_two_naming_the_file(build_repo):
    """NODETEST-4: a workflow that cannot be read refuses, as pytest-workers does."""
    assert WORKFLOW in refusals(build_repo({WORKFLOW: "jobs: [\n"}))[RULE]


def test_a_finding_is_exit_one(build_repo, capsys):
    """NODETEST-2: the rule's finding fails the run."""
    assert run(capsys, build_repo({WORKFLOW: workflow("node --test x.mjs")}))[0] == 1
