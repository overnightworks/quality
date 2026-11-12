import json
import os
from collections.abc import Callable
from pathlib import Path

import pytest

from quality.report import FindingEvent
from quality.rules import Rule
from quality.workflow_rules import check_pytest_workers
from tests.support.check import check_output as run
from tests.support.check import env_row_stands, finding_rows, is_clean, refusal_row, refusals, rule_findings

SERIAL, INVALID = FindingEvent.SERIAL, FindingEvent.INVALID
WORKFLOW = ".github/workflows/w.yml"
WITHOUT_N = "pytest runs without -n"
WITH_ZERO = "pytest runs with -n 0"
BAD_COUNT = "-n count is not a number above zero or auto"
UNREADABLE = "command cannot be read to its end"
BUILT_AT_RUN_TIME = "command is built at run time, pytest -n cannot be read"
FIX = "pass -n auto, or explain on the line above: # pytest-workers: <reason>"
MARKER = "# pytest-workers: browser tests share one stack"
BLOCK_LINE = 10
QUOTED_LINE = 8


def head(on: str = "push") -> str:
    """A workflow up to its `steps:` key (line 7); the next line is line 8."""
    return f"on: {on}\npermissions: {{}}\njobs:\n  j:\n    runs-on: ubuntu-latest\n    timeout-minutes: 15\n    steps:\n"


def block_run(*lines: str, on: str = "push") -> str:
    """One step whose `run: |` is line 9 and whose script starts on line 10."""
    return head(on) + "      - name: t\n        run: |\n" + "".join(f"          {line}\n" for line in lines)


def quoted_run(line: str, on: str = "push") -> str:
    """One step whose quoted `run:` value is line 8."""
    return head(on) + f"      - run: {json.dumps(line)}\n"


def folded_run(*lines: str, on: str = "push") -> str:
    """One step whose `run: >` is line 9 and whose value starts on line 10."""
    return head(on) + "      - name: t\n        run: >\n" + "".join(f"          {line}\n" for line in lines)


@pytest.fixture(params=[(block_run, BLOCK_LINE), (quoted_run, QUOTED_LINE)], ids=["literal block", "quoted"])
def scalar(request) -> tuple[Callable[[str], str], int]:
    """How a one-line script stands in a workflow, and the line of its finding."""
    return request.param


def judged(build_repo, text: str):
    return rule_findings(build_repo({WORKFLOW: text}), Rule.PYTEST_WORKERS)


def shape(findings):
    return [(finding.event, finding.line, finding.fact) for finding in findings]


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
    "pytest -qn4",
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
    """PYTEST-3, PYTEST-5, PYTEST-8: every spelling of a call is read, and a missing, abbreviated or glued flag counts as no flag."""
    build, line = scalar
    assert shape(judged(build_repo, build(command))) == [(SERIAL, line, WITHOUT_N)]


@pytest.mark.parametrize(
    "command",
    [
        "pytest -n 0",
        "pytest -n=0",
        "pytest --numprocesses 0",
        "pytest -n auto -n 0",
        "pytest -n 00",
        "pytest -n0",
        "pytest -n 0 -- -n auto",
    ],
)
def test_a_pytest_call_with_zero_workers_is_serial(build_repo, scalar, command):
    """PYTEST-5: the last of the flags counts."""
    build, line = scalar
    assert shape(judged(build_repo, build(command))) == [(SERIAL, line, WITH_ZERO)]


@pytest.mark.parametrize(
    "command",
    [
        "pytest -n $WORKERS",
        "pytest -n ${{ matrix.n }}",
        "pytest -n $(nproc)",
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
    assert shape(judged(build_repo, build(command))) == [(INVALID, line, BAD_COUNT)]


def test_a_command_split_over_lines_by_a_backslash_is_one_call_at_its_first_line(build_repo):
    """PYTEST-2."""
    findings = judged(build_repo, block_run("echo start", "pytest -q \\", "  tests", "echo done"))
    assert shape(findings) == [(SERIAL, BLOCK_LINE + 1, WITHOUT_N)]


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


def test_the_workers_flag_on_the_continued_line_counts(build_repo):
    """PYTEST-2."""
    assert judged(build_repo, block_run("pytest -q \\", "  -n auto \\", "  tests")) == []


def test_a_call_after_a_separator_is_found_at_its_physical_line(build_repo):
    """PYTEST-2, PYTEST-3."""
    findings = judged(build_repo, block_run("echo a", "", "make lint && pytest -n 0 | tee out", "echo b"))
    assert shape(findings) == [(SERIAL, BLOCK_LINE + 2, WITH_ZERO)]


def test_two_calls_are_two_findings(build_repo):
    """PYTEST-8."""
    findings = judged(build_repo, block_run("pytest -n 0 && pytest tests"))
    assert shape(findings) == [(SERIAL, BLOCK_LINE, WITH_ZERO), (SERIAL, BLOCK_LINE, WITHOUT_N)]


def test_a_hash_inside_quotes_or_a_word_is_no_comment(build_repo):
    """PYTEST-2: only a `#` that starts a word outside quotes ends the command."""
    findings = judged(build_repo, block_run('pytest -k "a #b" tests # -n auto', "pytest tests/a#b.py -n 0 # x"))
    assert shape(findings) == [(SERIAL, BLOCK_LINE, WITHOUT_N), (SERIAL, BLOCK_LINE + 1, WITH_ZERO)]


def test_an_expression_becomes_one_dynamic_word(build_repo):
    """PYTEST-2: `${{ }}` with spaces is one word, so the words after it keep their place."""
    assert judged(build_repo, block_run("pytest -k ${{ github.ref }} -n auto")) == []


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
    assert shape(judged(build_repo, block_run(*script))) == [(INVALID, BLOCK_LINE, UNREADABLE)]


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
    assert shape(judged(build_repo, build("echo >`pytest -q`"))) == [(SERIAL, line, WITHOUT_N)]


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
    assert shape(judged(build_repo, build(command))) == [(INVALID, line, fact)]


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


def test_a_command_built_at_run_time_with_pytest_only_in_a_comment_is_no_finding(build_repo):
    """PYTEST-6: the word counts outside comments only."""
    assert judged(build_repo, block_run("# pytest", "$TOOL -n auto")) == []


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


def test_a_second_substitution_on_a_passing_calls_line_leaves_the_call_green(build_repo):
    """PYTEST-6."""
    assert judged(build_repo, block_run("pytest -n 2 --a=$(x) $(y)")) == []


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
    assert shape(judged(build_repo, block_run(script))) == [(INVALID, BLOCK_LINE, fact)]


def test_a_run_time_command_before_a_pytest_call_is_invalid_and_the_call_is_judged_alone(build_repo):
    """PYTEST-6."""
    assert shape(judged(build_repo, block_run("$(date) -q", "pytest -n auto"))) == [(INVALID, BLOCK_LINE, BUILT_AT_RUN_TIME)]


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
        (MARKER, 'echo "unbalanced pytest'),
        ("pytest # pytest-workers:reason", ),
        ("#pytest-workers:   reason", "pytest"),
        ("# pytest-workers: ärger", "pytest"),
    ],
    ids=[
        "on its line", "above", "above an invalid count", "above two calls", "above a continued command",
        "on the last line of a continued command", "above a later line", "above an unreadable line", "no space",
        "wide spaces", "a letter of any script",
    ],
)
def test_a_marker_with_a_reason_on_or_directly_above_a_line_clears_its_calls(build_repo, script):
    """PYTEST-7."""
    assert judged(build_repo, block_run(*script)) == []


@pytest.mark.parametrize("on_run_line", [True, False], ids=["after the value", "above the key"])
def test_a_marker_clears_a_plain_scalar_call_beside_or_above_its_key(build_repo, on_run_line):
    """PYTEST-7."""
    step = f"      - run: pytest {MARKER}\n" if on_run_line else f"      {MARKER}\n      - run: pytest\n"
    assert judged(build_repo, head() + step) == []


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
    assert shape(judged(build_repo, block_run(comment, "pytest"))) == [(SERIAL, BLOCK_LINE + 1, WITHOUT_N)]


def test_a_marker_two_lines_above_clears_nothing(build_repo):
    """PYTEST-7."""
    findings = judged(build_repo, block_run(MARKER, "echo a", "pytest"))
    assert shape(findings) == [(SERIAL, BLOCK_LINE + 2, WITHOUT_N)]


def test_a_marker_does_not_clear_the_call_of_the_logical_line_below_its_own(build_repo):
    """PYTEST-7: a marker covers the lines it stands on or above, not the next logical line."""
    findings = judged(build_repo, block_run(f"pytest {MARKER}", "echo a", "pytest"))
    assert shape(findings) == [(SERIAL, BLOCK_LINE + 2, WITHOUT_N)]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            head() + f"      {MARKER}\n      - name: t\n        run: |\n          pytest -q\n",
            [(SERIAL, BLOCK_LINE + 1, WITHOUT_N)],
        ),
        (
            head() + f"      - name: t\n        run: | {MARKER}\n          pytest -q\n          pytest -n 0\n",
            [(SERIAL, BLOCK_LINE + 1, WITH_ZERO)],
        ),
    ],
    ids=["comment above the key clears nothing", "marker on the run line covers the first line only"],
)
def test_a_marker_for_a_block_scalar_covers_its_run_line_and_the_block_only(build_repo, text, expected):
    """PYTEST-7."""
    assert shape(judged(build_repo, text)) == expected


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (["pytest -q"], [(SERIAL, BLOCK_LINE + 1, WITHOUT_N)]),
        (["pytest -n auto"], []),
        ([MARKER, "pytest -q"], []),
    ],
    ids=["serial", "with workers", "marker above"],
)
def test_a_pytest_line_in_a_heredoc_body_is_read_as_a_shell_line(build_repo, body, expected):
    """PYTEST-13: the reader does not parse heredocs, so a call in the text is judged; a marker above it clears it."""
    assert shape(judged(build_repo, block_run("cat <<'EOF' > run.sh", *body, "EOF"))) == expected


def test_the_extent_is_the_number_of_workflows_read(build_repo):
    """PYTEST-10: a tracked name gone from the tree is not read."""
    other = ".github/workflows/other.yaml"
    repo = build_repo({WORKFLOW: block_run("pytest -n auto"), other: block_run("pytest -n auto")})
    result = check_pytest_workers(repo, [WORKFLOW, other, ".github/workflows/gone.yml"])
    assert result.extent == 2


@pytest.mark.parametrize("on", ["push", "schedule", "workflow_call", "pull_request"])
def test_a_workflow_is_judged_whatever_its_trigger(build_repo, on):
    """PYTEST-1: no root is needed."""
    assert shape(judged(build_repo, block_run("pytest", on=on))) == [(SERIAL, BLOCK_LINE, WITHOUT_N)]


def test_a_run_is_read_as_shell_whatever_its_shell(build_repo):
    """PYTEST-1."""
    text = head() + "      - shell: python\n        run: pytest\n"
    assert shape(judged(build_repo, text)) == [(SERIAL, QUOTED_LINE + 1, WITHOUT_N)]


def test_only_the_run_of_a_step_is_read(build_repo):
    """PYTEST-1: names, conditions, inputs and environments are not scripts."""
    step = (
        "      - name: pytest\n        if: contains(github.ref, 'pytest')\n        env:\n          CMD: pytest\n"
        "        run: 'true'\n      - uses: a/b@0123456789012345678901234567890123456789\n        with:\n          run: pytest\n"
        "          args: pytest -n 0\n"
    )
    assert judged(build_repo, head() + step) == []


def test_every_job_and_step_is_read_and_each_finding_names_its_file(build_repo):
    """PYTEST-1, PYTEST-8."""
    text = head() + "      - run: 'true'\n      - run: pytest\n  k:\n    runs-on: ubuntu-latest\n    steps:\n      - run: pytest -n 0\n"
    repo = build_repo({WORKFLOW: text, ".github/workflows/other.yaml": block_run("pytest")})
    findings = rule_findings(repo, Rule.PYTEST_WORKERS)
    assert [(finding.path, finding.line, finding.fact) for finding in findings] == [
        (".github/workflows/other.yaml", BLOCK_LINE, WITHOUT_N),
        (WORKFLOW, 9, WITHOUT_N),
        (WORKFLOW, 13, WITH_ZERO),
    ]


def test_a_composite_action_is_not_read(build_repo):
    """PYTEST-1: the named gap."""
    action = "name: a\nruns:\n  using: composite\n  steps:\n    - shell: bash\n      run: pytest\n"
    repo = build_repo({"action.yml": action, ".github/actions/a/action.yml": action})
    assert rule_findings(repo, Rule.PYTEST_WORKERS) == []


def test_a_step_without_a_run_and_a_workflow_without_jobs_are_skipped(build_repo):
    """PYTEST-9."""
    repo = build_repo({WORKFLOW: head() + "      - uses: a/b@v1\n", ".github/workflows/empty.yml": "name: e\non: push\n"})
    assert (rule_findings(repo, Rule.PYTEST_WORKERS), Rule.PYTEST_WORKERS in refusals(repo)) == ([], False)


def test_a_tracked_workflow_gone_from_the_tree_is_skipped(build_repo):
    """PYTEST-1."""
    repo = build_repo({WORKFLOW: block_run("pytest")})
    (repo / WORKFLOW).unlink()
    assert (rule_findings(repo, Rule.PYTEST_WORKERS), Rule.PYTEST_WORKERS in refusals(repo)) == ([], False)


def test_a_finding_prints_its_event_fact_and_the_flag_and_the_marker_as_its_fix(build_repo, capsys):
    """PYTEST-8."""
    code, output = run(capsys, build_repo({WORKFLOW: block_run("pytest -n 0", "pytest -n x")}))
    rows = finding_rows(output, f"{WORKFLOW}:{BLOCK_LINE}") + finding_rows(output, f"{WORKFLOW}:{BLOCK_LINE + 1}")
    assert code == 1, output
    assert [(row.event, row.fact, row.fix) for row in rows] == [(SERIAL, WITH_ZERO, FIX), (INVALID, BAD_COUNT, FIX)]


def test_a_conforming_repository_is_clean_under_every_rule(build_repo, capsys):
    """PYTEST-11."""
    code, output = run(capsys, build_repo({WORKFLOW: block_run("uv run --locked pytest -q -n auto --cov")}))
    assert (code, is_clean(output)) == (0, True), output


def test_a_marker_is_the_way_out_of_a_serial_run_through_the_door(build_repo, capsys):
    """PYTEST-7, PYTEST-11."""
    code, output = run(capsys, build_repo({WORKFLOW: block_run(MARKER, "pytest -p no:cacheprovider -m e2e")}))
    assert (code, is_clean(output)) == (0, True), output


ANCHORS = "x-steps: &steps\n  - run: 'true'\nx-job: &job\n  runs-on: ubuntu-latest\nx-run: &run pytest\n"
JOBS = "jobs:\n  j:\n    runs-on: ubuntu-latest\n    timeout-minutes: 15\n    steps:\n"


@pytest.mark.parametrize(
    "text",
    [
        "name: w\non: [push\n",
        head() + "      - run: [pytest]\n",
        head() + "      - run:\n          cmd: pytest\n",
        f"{ANCHORS}on: push\njobs: *job\n",
        f"{ANCHORS}on: push\njobs:\n  <<: *job\n",
        f"{ANCHORS}on: push\njobs:\n  j: *job\n",
        f"{ANCHORS}on: push\njobs:\n  j:\n    <<: *job\n    steps: []\n",
        f"{ANCHORS}on: push\n{JOBS[:-len('    steps:\n')]}    steps: *steps\n",
        f"{ANCHORS}on: push\n{JOBS}      - *job\n",
        f"{ANCHORS}on: push\n{JOBS}      - <<: *job\n",
        f"{ANCHORS}on: push\n{JOBS}      - run: *run\n",
        f"{ANCHORS}on: push\n{JOBS}      - name: t\n        <<: {{run: pytest}}\n",
        f"{ANCHORS}<<: {{jobs: {{}}}}\non: push\n",
    ],
    ids=[
        "yaml error", "run list", "run mapping", "alias as jobs", "merge key at jobs", "alias as a job",
        "merge key at a job", "alias as steps", "alias as a step", "merge key at a step", "alias as run",
        "merge key at a step beside run", "merge key at the top",
    ],
)
def test_a_workflow_the_rule_cannot_read_is_cannot_judge_naming_the_file_while_other_rules_still_judge(build_repo, capsys, text):
    """PYTEST-9."""
    repo = build_repo({WORKFLOW: text, ".env": "A=1\n"})
    code, output = run(capsys, repo)
    assert code == 2, output
    assert refusal_row("workflows") in output
    assert WORKFLOW in refusals(repo)[Rule.PYTEST_WORKERS]
    assert env_row_stands(output)


def refuse_by_mode(repo: Path, _elsewhere: Path) -> None:
    (repo / WORKFLOW).chmod(0)


def refuse_by_link(repo: Path, elsewhere: Path) -> None:
    (elsewhere / "outside.yml").write_text(block_run("pytest"))
    (repo / WORKFLOW).unlink()
    (repo / WORKFLOW).symlink_to(elsewhere / "outside.yml")


def refuse_by_encoding(repo: Path, _elsewhere: Path) -> None:
    (repo / WORKFLOW).write_bytes(b"on: push\n# \xff\xfe\n")


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
    """PYTEST-9."""
    repo = build_repo({WORKFLOW: block_run("pytest")})
    refuse(repo, elsewhere)
    assert WORKFLOW in refusals(repo)[Rule.PYTEST_WORKERS]
