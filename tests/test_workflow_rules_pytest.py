"""What only `pytest-workers` decides: which calls are pytest and how its worker flag is read. Every mechanic it shares with
`runtime-install` is in test_workflow_rules_script.py."""

import pytest

from quality.report import FindingEvent
from quality.rules import Rule
from tests.support.check import event_line_fact, judge_workflow
from tests.support.workflow_scripts import BLOCK_LINE, QUOTED_LINE, block_run, folded_run, quoted_run, steps_of

pytestmark = pytest.mark.integration

judged = judge_workflow(Rule.PYTEST_WORKERS)

SERIAL, INVALID = FindingEvent.SERIAL, FindingEvent.INVALID
WITHOUT_N = "pytest runs without -n"
WITH_ZERO = "pytest runs with -n 0"
BAD_COUNT = "-n count is not a number above zero or auto"
UNREADABLE = "command cannot be read to its end"
BUILT_AT_RUN_TIME = "command is built at run time, pytest -n cannot be read"
MARKER = "# pytest-workers: browser tests share one stack"


GREEN = [
    "pytest -n auto",
    'pytest -q -n auto -m "not e2e"',
    "uv run --locked pytest -q -n auto --cov",
    "uv run --no-sync --frozen --no-build pytest tests/ -n auto",
    ".venv/bin/pytest tests -q -n auto",
    "uv run --locked --project providers/qwen pytest -q -n auto x",
    "python -m pytest -n 4",
    "python3.14 -m pytest -n=4",
    "uv run python -m pytest --numprocesses=2",
    "uv run coverage run -m pytest -n auto",
    "pytest -nauto",
    "pytest -qn4",
    "pytest -qn 4",
    "pytest -qn=4",
    "pytest -n logical",
    "pytest -n 1",
    "PYTHONPATH=src pytest -n 2",
    "env A=b pytest -n 2",
    "cd x && pytest -n auto",
    "make lint; pytest -n auto",
    "x=$(pytest -n auto)",
    "pytest -n 2 | tee out",
    "if pytest -n auto; then",
    "pytest -n 0 -n auto",
    "pytest --numprocesses 3",
    "poetry run pytest -n auto",
    "uvx pytest -n auto",
    "timeout 600 pytest -n auto",
    "nice -n 5 pytest -n auto",
    "xargs pytest -n auto",
    'bash -c "pytest -n auto"',
    "uvx pytest@8 -n auto",
    "npx pytest@8 -n auto",
    "yarn pytest -n auto",
    "pytest -n " + "9" * 5000,
    'x="$(pytest -n auto)"',
    'echo "$(pytest -n auto)"',
    'x="`pytest -n auto`"',
    "$(pytest -n auto)",
    "env -u NAME pytest -n auto",
    "env -S 'pytest -n auto'",
    "python -X dev -m pytest -n auto",
    "python3.14t -m pytest -n auto",
    "sudo -E pytest -n auto",
    "pytest -n auto -- tests",
    'bash -c "pytest -n auto --x=$(echo y)"',
]


@pytest.mark.parametrize("command", GREEN)
def test_a_pytest_call_that_passes_workers_is_no_finding(build_repo, scalar, command):
    """PYTEST-2, PYTEST-3, PYTEST-5."""
    build, _ = scalar
    assert judged(build_repo, build(command)) == []


NO_CALL = [
    'echo "run pytest"',
    "pip install pytest",
    "uv pip install pytest-xdist",
    "uv add --dev pytest",
    "python -m pip install pytest",
    "cd tests/pytest_helpers",
    "cat tests/pytest_helpers/a.py",
    "./scripts/pytest_wrapper.sh",
    "# pytest",
    "echo ok # pytest",
    "make test",
    "tox -e py",
    "uv run ruff check",
    "timeout 600 xvfb-run pytest",
    "timeout 600 make test",
    "nice make",
    "xargs",
    "timeout 60",
    "bash run.sh -c 'pytest -q'",
    "bash -- -c 'pytest -q'",
    "bash script.sh",
    "bash -c",
    "xvfb-run pytest",
    "docker run img pytest",
    "pytest-cov",
    'echo "pytest -q"',
    "echo $(which pytest)",
    "sudo apt-get install pytest",
    "python -X dev tests/run.py",
    "env -u NAME ruff check",
    "python3.14tx -m pytest -q",
    "yarn dlx pytest -q",
]


@pytest.mark.parametrize("command", NO_CALL)
def test_a_word_pytest_that_starts_no_test_run_is_no_call(build_repo, scalar, command):
    """PYTEST-4."""
    build, _ = scalar
    assert judged(build_repo, build(command)) == []


SERIAL_WITHOUT_N = [
    "pytest",
    "uv run --locked pytest -q",
    "python -m pytest tests",
    'PYTEST_ADDOPTS="-n auto" pytest',
    "a && pytest -q",
    "pytest --numproc 4",
    "poetry run pytest",
    "uv run --python 3.14 pytest",
    "x=`pytest -q`",
    "echo `pytest -q` done",
    "echo `pytest -q",
    "tee out.txt <<< `pytest -q",
    "py.test -q",
    "time pytest -q",
    "exec pytest -q",
    "command pytest -q",
    "nohup pytest -q &",
    "if pytest -q; then",
    "elif pytest -q; then",
    "else pytest -q",
    "do pytest -q",
    "while pytest -q; do",
    "until pytest -q; do",
    "! pytest -q",
    "{ pytest -q; }",
    "pypy3 -m pytest -q",
    "python3.14 -m pytest -q",
    "pipenv run pytest -q",
    "pdm run pytest -q",
    "hatch run pytest -q",
    "coverage run -m pytest -q",
    "uvx pytest -q",
    "npx pytest@8 -q",
    "pnpm exec pytest -q",
    "pnpm dlx pytest -q",
    "npm exec pytest -q",
    "yarn pytest -q",
    "bunx pytest -q",
    "env -i A=b pytest -q",
    'x="$(pytest -q)"',
    'echo "result: $(pytest -q) done"',
    'x="`pytest -q`"',
    "echo '$(pytest -q)'",
    "$(pytest -q)",
    "env -u NAME pytest -q",
    "env -C dir pytest -q",
    "env --unset NAME pytest -q",
    "env --chdir dir pytest -q",
    "env -i -u NAME A=b pytest -q",
    "env -S 'pytest -q'",
    'env -S "A=1 uv run pytest -q"',
    "env -S'pytest -q'",
    "env --split-string='pytest -q'",
    "python -X dev -m pytest -q",
    "python -W error -m pytest -q",
    "uv run python -W error -m pytest -q",
    "python3.14t -m pytest -q",
    "pypy3.10 -m pytest -q",
    "sudo pytest -q",
    "sudo -E pytest -q",
    "sudo -u runner pytest -q",
    "sudo -u runner -E uv run pytest -q",
    "sudo --user=runner pytest -q",
    "sudo -n pytest -q",
    "pytest tests -- -n auto",
    "pytest -- -n auto",
    "python -m pytest tests -- -n auto",
    "timeout 600 pytest",
    "timeout -k 10 600 pytest -q",
    "timeout -s KILL 600 pytest",
    "timeout --signal=KILL 600 pytest",
    "timeout 600 uv run pytest -q",
    "nice pytest",
    "nice -n 5 pytest",
    "echo x | xargs pytest",
    "xargs -n 1 pytest",
    "sudo timeout 60 pytest",
    "time nice timeout 60 pytest",
    "nohup timeout 60 pytest &",
    'bash -c "pytest -q"',
    "sh -c 'a && pytest -q'",
    'bash -lc "pytest -q"',
    'bash -o pipefail -c "pytest -q"',
    'bash -eo pipefail -c "pytest -q"',
    'bash -O extglob -c "pytest -q"',
    'bash -c "x=$(pytest -q)"',
    "uvx pytest@8 -q",
    "uvx pytest@8.3 -q",
    "uvx -q pytest@8 -q",
    "uvx --quiet pytest@8 -q",
    "bash -c -x 'pytest -q'",
    "bash -c -o pipefail 'pytest -q'",
    "bash -c -- 'pytest -q'",
    'bash -c -- "pytest -q"',
    'bash -c - "pytest -q"',
    'bash --login -c "pytest -q"',
    'bash --rcfile f -c "pytest -q"',
    'bash +o posix -c "pytest -q"',
]


@pytest.mark.parametrize("command", SERIAL_WITHOUT_N)
def test_a_pytest_call_without_a_readable_flag_is_serial(build_repo, scalar, command):
    """PYTEST-3, PYTEST-5, PYTEST-8: every spelling of a call is read, and a missing or abbreviated flag counts as no flag, as pytest refuses an abbreviated long option."""
    build, line = scalar
    assert event_line_fact(judged(build_repo, build(command))) == [(SERIAL, line, WITHOUT_N)]


@pytest.mark.parametrize(
    "command",
    ["pytest --collect-only", "pytest --collectonly", "pytest --co -q", "uv run pytest --collect-only tests"],
)
def test_a_listing_only_call_runs_no_test_and_is_not_judged(build_repo, scalar, command):
    """PYTEST-5: a call that only lists tests needs no workers."""
    build, _ = scalar
    assert judged(build_repo, build(command)) == []


def test_a_listing_flag_after_the_separator_is_a_path_and_the_call_is_serial(build_repo, scalar):
    """PYTEST-5."""
    build, line = scalar
    assert event_line_fact(judged(build_repo, build("pytest -- --collect-only"))) == [(SERIAL, line, WITHOUT_N)]


@pytest.mark.parametrize(
    "command",
    [
        "pytest -n 0",
        "pytest -n=0",
        "pytest --numprocesses 0",
        "pytest -n auto -n 0",
        "pytest -n 00",
        "pytest -n0",
        "pytest -qn0",
        "pytest -n 0 -- -n auto",
    ],
)
def test_a_pytest_call_with_zero_workers_is_serial(build_repo, scalar, command):
    """PYTEST-5: the last of the flags counts."""
    build, line = scalar
    assert event_line_fact(judged(build_repo, build(command))) == [(SERIAL, line, WITH_ZERO)]


@pytest.mark.parametrize(
    "command",
    [
        "pytest -n $WORKERS",
        "pytest -n ${{ matrix.n }}",
        "pytest -n $(nproc)",
        'bash -c "pytest -n $(nproc)"',
        "bash -c 'pytest -n \"$(nproc)\"'",
        "pytest -n",
        "pytest -n -1",
        "pytest -n 2.5",
        "pytest -n \u0662",
        "pytest -n many",
        "pytest -nmany",
        "pytest --numprocesses=",
    ],
)
def test_a_worker_count_the_reader_cannot_read_is_invalid(build_repo, scalar, command):
    """PYTEST-5, PYTEST-8."""
    build, line = scalar
    assert event_line_fact(judged(build_repo, build(command))) == [(INVALID, line, BAD_COUNT)]


@pytest.mark.parametrize(
    "script",
    [
        pytest.param(lambda: folded_run("echo hi", "", "pytest -q"), id="folded blank line"),
        pytest.param(lambda: folded_run("echo hi", "  pytest -q"), id="folded more-indented line"),
        pytest.param(lambda: quoted_run("echo hi\npytest -q"), id="quoted newline"),
    ],
)
def test_a_line_break_kept_in_a_folded_or_quoted_value_ends_the_command(build_repo, script):
    """PYTEST-2: the shell reads a kept newline as the end of a command; the finding is at the scalar's first line."""
    findings = judged(build_repo, script())
    assert [(finding.event, finding.fact) for finding in findings] == [(SERIAL, WITHOUT_N)]


@pytest.mark.parametrize(
    "script",
    [
        pytest.param(("pytest -q \\", "  -n auto \\", "  tests"), id="the workers flag on the continued line counts"),
        pytest.param(("pytest -k ${{ github.ref }} -n auto",), id="an expression with spaces is one dynamic word"),
        pytest.param(("# pytest", "$TOOL -n auto"), id="pytest only in a comment leaves a run-time command alone"),
        pytest.param(("pytest -n 2 --a=$(x) $(y)",), id="a second substitution on a passing call's line"),
    ],
)
def test_a_script_the_reader_follows_to_a_passing_call_is_no_finding(build_repo, script):
    """PYTEST-2, PYTEST-6: the word `pytest` counts outside comments only."""
    assert judged(build_repo, block_run(*script)) == []


@pytest.mark.parametrize(
    ("script", "expected"),
    [
        pytest.param(
            ("echo a", "", "make lint && pytest -n 0 | tee out", "echo b"), [(SERIAL, BLOCK_LINE + 2, WITH_ZERO)],
            id="a call after a separator at its physical line",
        ),
        pytest.param(
            ("pytest -n 0 && pytest tests",), [(SERIAL, BLOCK_LINE, WITH_ZERO), (SERIAL, BLOCK_LINE, WITHOUT_N)], id="two calls are two findings",
        ),
        pytest.param(
            ('pytest -k "a #b" tests # -n auto', "pytest tests/a#b.py -n 0 # x"),
            [(SERIAL, BLOCK_LINE, WITHOUT_N), (SERIAL, BLOCK_LINE + 1, WITH_ZERO)],
            id="a hash inside quotes or a word is no comment",
        ),
    ],
)
def test_each_call_of_a_script_is_judged_at_its_own_line(build_repo, script, expected):
    """PYTEST-2, PYTEST-3, PYTEST-8: only a `#` that starts a word outside quotes ends the command."""
    assert event_line_fact(judged(build_repo, block_run(*script))) == expected


@pytest.mark.parametrize(
    "script",
    [
        ('echo "unbalanced pytest -n auto',),
        ("pytest -k 'unbalanced -n auto",),
        ("pytest -n auto \\", "  -k 'unbalanced"),
    ],
    ids=["double quote", "single quote", "continued"],
)
def test_a_line_that_cannot_be_split_and_holds_pytest_is_invalid_at_its_first_line(build_repo, script):
    """PYTEST-6."""
    assert event_line_fact(judged(build_repo, block_run(*script))) == [(INVALID, BLOCK_LINE, UNREADABLE)]


@pytest.mark.parametrize(
    "command",
    ["echo hi >`mktemp`", "cat <`ls`", "make 2>`mktemp` ; pytest -n 2", "cat <<`ls`"],
    ids=["output", "input", "descriptor before a call", "here-string"],
)
def test_a_redirection_glued_to_a_backtick_is_read_and_no_finding(build_repo, scalar, command):
    """PYTEST-2."""
    build, _ = scalar
    assert judged(build_repo, build(command)) == []


def test_a_call_in_a_substitution_after_a_glued_redirection_is_judged(build_repo, scalar):
    """PYTEST-2."""
    build, line = scalar
    assert event_line_fact(judged(build_repo, build("echo >`pytest -q`"))) == [(SERIAL, line, WITHOUT_N)]


def test_a_line_that_cannot_be_split_and_holds_no_pytest_is_no_finding(build_repo):
    """PYTEST-6."""
    assert judged(build_repo, block_run('echo "unbalanced')) == []


@pytest.mark.parametrize(
    ("command", "fact"),
    [
        ("timeout 60 $PYTEST -q; pytest -n auto", BUILT_AT_RUN_TIME),
        ('bash -c "pytest -q \'x"', UNREADABLE),
    ],
    ids=["run-time command behind timeout", "shell string that cannot be split"],
)
def test_a_wrapped_or_shell_call_the_reader_cannot_read_is_invalid(build_repo, scalar, command, fact):
    """PYTEST-2, PYTEST-4, PYTEST-6."""
    build, line = scalar
    assert event_line_fact(judged(build_repo, build(command))) == [(INVALID, line, fact)]


@pytest.mark.parametrize(
    "script",
    [
        ('PYTEST="uv run pytest"', "$PYTEST -n auto"),
        ("pytest -n auto", 'eval "$CMD"'),
        ("pytest -n auto", "${RUNNER} tests"),
        ("pytest -n auto", "$EXPRESSION tests"),
        ("pytest -n auto", "FLAG=1 $RUNNER tests"),
    ],
    ids=["variable", "eval", "braced variable", "dynamic word", "after an assignment"],
)
def test_a_command_built_at_run_time_in_a_run_holding_pytest_is_invalid(build_repo, script):
    """PYTEST-6."""
    findings = judged(build_repo, block_run(*script))
    assert [(finding.event, finding.fact) for finding in findings][-1] == (INVALID, BUILT_AT_RUN_TIME)
    assert findings[-1].line == BLOCK_LINE + 1


@pytest.mark.parametrize("script", [("$TOOL -n auto",), ('eval "$CMD"',), ("$(date) -q",)])
def test_a_command_built_at_run_time_in_a_run_without_pytest_is_no_finding(build_repo, script):
    """PYTEST-6: the named gap."""
    assert judged(build_repo, block_run(*script)) == []


@pytest.mark.parametrize(
    "command",
    [
        "cp $(a) $(b) dest",
        'echo "$(date)" "$(uname)"',
        "echo $(date) $(date)",
        "echo $(a)$(b)",
        "echo `a` $(b)",
        "echo `a` `b`",
        "X=`a` pytest -n 2",
        "echo 'run `pytest`'",
        'echo "run \\`pytest\\`"',
        "echo `",
        "git diff $(a) $(b) | wc",
        "echo $(( 1 + 2 )) $(( 3 ))",
        'echo X="$(a)" $(b)',
    ],
)
def test_a_second_substitution_on_a_command_line_is_no_command_word_in_a_run_holding_pytest(build_repo, command):
    """PYTEST-6: `echo $(which pytest)` is no command word and no finding."""
    assert judged(build_repo, block_run(command, "pytest -n auto")) == []


@pytest.mark.parametrize(
    ("script", "fact"),
    [
        ("$(which pytest) -q", BUILT_AT_RUN_TIME),
        ('"$(which pytest)" -q', BUILT_AT_RUN_TIME),
        ("if $(which pytest) -q; then", BUILT_AT_RUN_TIME),
        ("$(cd x && which pytest) -q", BUILT_AT_RUN_TIME),
        ("X=$(a) $(which pytest) -q", BUILT_AT_RUN_TIME),
        ("env X=$(a) $(which pytest) -q", BUILT_AT_RUN_TIME),
        ("X=`a` $(which pytest) -q", BUILT_AT_RUN_TIME),
        ("`which pytest` -q", BUILT_AT_RUN_TIME),
        ('"`which pytest`" -q', BUILT_AT_RUN_TIME),
        ("X=`a` `which pytest` -q", BUILT_AT_RUN_TIME),
        ("if `which pytest` -q; then", BUILT_AT_RUN_TIME),
        ('X="$(a)" $(which pytest) -q', BUILT_AT_RUN_TIME),
        ("env -S 'pytest -k \"x'", UNREADABLE),
    ],
    ids=[
        "command substitution",
        "quoted substitution",
        "after if",
        "after a separator",
        "after an assignment of a substitution",
        "after env and an assignment of a substitution",
        "after an assignment of a backtick substitution",
        "backtick substitution",
        "quoted backtick substitution",
        "after an assignment of a backtick substitution and a backtick substitution",
        "backtick substitution after if",
        "after an assignment of a quoted substitution",
        "unreadable env -S value",
    ],
)
def test_a_command_the_reader_cannot_follow_in_a_run_holding_pytest_is_invalid(build_repo, script, fact):
    """PYTEST-6."""
    assert event_line_fact(judged(build_repo, block_run(script))) == [(INVALID, BLOCK_LINE, fact)]


def test_a_run_time_command_before_a_pytest_call_is_invalid_and_the_call_is_judged_alone(build_repo):
    """PYTEST-6."""
    assert event_line_fact(judged(build_repo, block_run("$(date) -q", "pytest -n auto"))) == [(INVALID, BLOCK_LINE, BUILT_AT_RUN_TIME)]


@pytest.mark.parametrize(
    "script",
    [
        ("pytest -n 0 " + MARKER,),
        (MARKER, "pytest -n 0"),
        (MARKER, "pytest -n $N"),
        (MARKER, "pytest -n 0 && pytest tests"),
        (MARKER, "pytest -q \\", "  tests"),
        ("pytest -q \\", "  tests " + MARKER),
        ("echo start", MARKER, "pytest"),
        (MARKER, "# a plain comment", "pytest -n 0"),
        (MARKER, "# a plain comment", "# another", "pytest -n 0"),
        (MARKER, "# pytest-random: x", "pytest -n 0"),
        ("# pytest-random: x", MARKER, "pytest -n 0"),
        (MARKER, 'echo "unbalanced pytest'),
        ("pytest # pytest-workers:reason", ),
        ("#pytest-workers:   reason", "pytest"),
        ("# pytest-workers: ärger", "pytest"),
    ],
    ids=[
        "on its line", "above", "above an invalid count", "above two calls", "above a continued command",
        "on the last line of a continued command", "above a later line", "above a plain comment", "above two plain comments", "above another rule's marker",
        "below another rule's marker", "above an unreadable line", "no space",
        "wide spaces", "a letter of any script",
    ],
)
def test_a_marker_with_a_reason_on_or_directly_above_a_line_clears_its_calls(build_repo, script):
    """PYTEST-7."""
    assert judged(build_repo, block_run(*script)) == []


@pytest.mark.parametrize(
    ("step", "expected"),
    [
        (f"      - run: pytest {MARKER}\n", []),
        (f"      {MARKER}\n      - run: pytest\n", []),
        (f"      - run: pytest -n auto -m e2e {MARKER}\n      - run: pytest\n", [(SERIAL, QUOTED_LINE + 1, WITHOUT_N)]),
        (f"      {MARKER}\n      # a plain comment\n      - run: pytest\n      - run: pytest\n", [(SERIAL, QUOTED_LINE + 3, WITHOUT_N)]),
    ],
    ids=["after the value", "above the key", "trailing the previous step's line", "above a plain comment, not the next step"],
)
def test_a_marker_clears_a_plain_scalar_call_only_beside_or_above_its_key(build_repo, step, expected):
    """PYTEST-7: a marker on another command's line does not clear the call on the next line."""
    assert event_line_fact(judged(build_repo, steps_of() + step)) == expected


@pytest.mark.parametrize(
    "comment",
    [
        "# pytest-workers",
        "# pytest-workers:",
        "# pytest-workers: -",
        "# pytest-workers: 123",
        "# pytest-worker: x",
        "# noqa: pytest-workers - x",
    ],
    ids=["no colon", "no reason", "no letter", "digits only", "another id", "noqa form"],
)
def test_a_comment_that_is_no_marker_clears_nothing(build_repo, comment):
    """PYTEST-7."""
    assert event_line_fact(judged(build_repo, block_run(comment, "pytest"))) == [(SERIAL, BLOCK_LINE + 1, WITHOUT_N)]


@pytest.mark.parametrize(
    ("script", "line"),
    [
        ((MARKER, "echo a", "pytest"), BLOCK_LINE + 2),
        ((MARKER, "echo a", "# a plain comment", "pytest"), BLOCK_LINE + 3),
        (("echo a " + MARKER, "# a plain comment", "pytest"), BLOCK_LINE + 2),
        ((MARKER, "# pytest-random: x", "pytest -n 0", "pytest"), BLOCK_LINE + 3),
    ],
    ids=["a command between marker and call", "a command between marker and comment", "a marker trailing a command", "a run of markers clears the call below it only"],
)
def test_a_marker_outside_the_unbroken_comment_run_above_a_call_clears_nothing(build_repo, script, line):
    """PYTEST-7."""
    assert [finding.line for finding in judged(build_repo, block_run(*script))] == [line]


def test_a_marker_does_not_clear_the_call_of_the_logical_line_below_its_own(build_repo):
    """PYTEST-7: a marker covers the lines it stands on or above, not the next logical line."""
    findings = judged(build_repo, block_run(f"pytest {MARKER}", "echo a", "pytest"))
    assert event_line_fact(findings) == [(SERIAL, BLOCK_LINE + 2, WITHOUT_N)]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            steps_of() + f"      {MARKER}\n      - name: t\n        run: |\n          pytest -q\n",
            [(SERIAL, BLOCK_LINE + 1, WITHOUT_N)],
        ),
        (
            steps_of() + f"      - name: t\n        {MARKER}\n        run: | # other\n          pytest -q\n",
            [(SERIAL, BLOCK_LINE + 1, WITHOUT_N)],
        ),
        (
            steps_of() + f"      - name: t\n        run: | {MARKER}\n          pytest -q\n          pytest -n 0\n",
            [(SERIAL, BLOCK_LINE + 1, WITH_ZERO)],
        ),
    ],
    ids=["comment above the key clears nothing", "comment above a run line with a comment clears nothing", "marker on the run line covers the first line only"],
)
def test_a_marker_for_a_block_scalar_covers_its_run_line_and_the_block_only(build_repo, text, expected):
    """PYTEST-7."""
    assert event_line_fact(judged(build_repo, text)) == expected


