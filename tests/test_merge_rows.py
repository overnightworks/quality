"""The rows the operator's door prints before its prompt and the pair it prints when a step stops it."""

import contextlib
import io

import pytest

from quality import cli
from tests.support.merge import (
    ABSENT,
    B_REMOVED,
    GATE_CONFIG,
    LEGACY_RED,
    ONE_JOB,
    REPO,
    SHA,
    SONAR_GREEN,
    TWO_JOBS,
    FakeGh,
    changed_nothing,
    declaring,
    drive,
    no_network,
    not_confirmed_pair,
    prompt,
    sonar,
)
from tests.support.terminal import screen


def frame(*lines):
    return "".join(f"{line}\n" for line in lines)


def pull_row():
    return "  · #7            open           head aaaaaaa · into main"


def not_json_row(subject):
    return f"  ✗ {subject:<14}not judged     Expecting value: line 1 column 1 (char 0)"


def not_json_answer(step):
    return {"raw_answers": {step: "not json"}}


def loosenings_none():
    return "  ✓ loosenings    none"


def legacy_row():
    return "  ! sonar         4 legacy findings on main"


def checks_row(red="quality / sonar and quality / verdict are"):
    return f"  ✓ checks        ok             only {red} red"


def verdict_only_checks_row():
    return checks_row("quality / verdict is")


def refused_pair():
    return frame(
        "error: not merged · the operator's merge lands only declared loosenings and legacy findings · nothing changed",
        "next: gh pr checks 7 --repo acme/widgets",
    )


def not_judged_pair():
    return frame(
        "error: could not judge · nothing changed",
        "next: fix what the ✗ row names, then run quality emergency-merge acme/widgets 7 again",
    )


def run(gh, capsys, **arguments):
    code, answers = drive(gh, **arguments)
    streams = capsys.readouterr()
    return code, streams.out, streams.err, answers


@pytest.mark.parametrize(
    ("terminal", "repository", "pair"),
    [
        (False, REPO, frame(
            "error: not merged · the operator's merge needs a terminal on stdin · nothing changed",
            "next: the operator runs it in his own terminal",
        )),
        (True, "not-a-repo", frame(
            "error: not merged · 'not-a-repo' is not <owner>/<repo> · nothing changed",
            "next: name the repository as <owner>/<repo>",
        )),
        (False, "not-a-repo", frame(
            "error: not merged · 'not-a-repo' is not <owner>/<repo> · nothing changed",
            "next: name the repository as <owner>/<repo>",
        )),
    ],
    ids=["no terminal", "a malformed name", "a malformed name and no terminal"],
)
def test_a_refusal_before_any_read_is_the_pair_alone(terminal, repository, pair, capsys):
    gh = FakeGh()
    code, out, err, _ = run(gh, capsys, terminal=terminal, repo=repository)
    assert (code, out, err, gh.calls) == (1, "", pair, [])


@pytest.mark.parametrize(
    ("gh_kwargs", "row", "pair"),
    [
        ({"draft": True}, "  ! #7            draft          head aaaaaaa · into main", frame(
            "error: not merged · a draft does not land · nothing changed", "next: gh pr ready 7 --repo acme/widgets",
        )),
        ({"draft": True, "body": ""}, "  ! #7            draft          head aaaaaaa · into main", frame(
            "error: not merged · a draft does not land · nothing changed", "next: gh pr ready 7 --repo acme/widgets",
        )),
        ({"draft": True, "state": "closed"}, "  ! #7            closed         head aaaaaaa · into main", frame(
            "error: not merged · #7 is closed · nothing changed", "next: gh pr view 7 --repo acme/widgets",
        )),
        ({"state": "closed", "body": ""}, "  ! #7            closed         head aaaaaaa · into main", frame(
            "error: not merged · #7 is closed · nothing changed", "next: gh pr view 7 --repo acme/widgets",
        )),
    ],
    ids=["draft", "a draft without a landing line", "a closed draft", "closed without a landing line"],
)
def test_a_draft_stops_the_door_before_its_body_or_any_check_is_read(gh_kwargs, row, pair, capsys):
    gh = FakeGh(**gh_kwargs)
    code, out, err, answers = run(gh, capsys)
    assert (code, out, err, answers) == (1, frame(row), pair, [])
    assert gh.requests == [(("api", f"repos/{REPO}"), None), (("api", f"repos/{REPO}/pulls/7"), None)]
    changed_nothing(gh)


def test_the_door_asks_for_the_head_commits_checks_and_statuses_and_the_login_by_these_requests(capsys):
    gh = FakeGh()
    assert run(gh, capsys)[0] == 0
    assert gh.requests[:4] == [
        (("api", f"repos/{REPO}"), None),
        (("api", f"repos/{REPO}/pulls/7"), None),
        (("api", "--paginate", f"repos/{REPO}/commits/{SHA}/check-runs?per_page=100", "--jq", ".check_runs[]"), None),
        (("api", "--paginate", f"repos/{REPO}/commits/{SHA}/status?per_page=100", "--jq", ".statuses[]"), None),
    ]
    assert [request for request in gh.requests if request[0][:2] == ("api", "user")] == [(("api", "user", "--jq", ".login"), None)]


@pytest.mark.parametrize("draft", [ABSENT, None, "false"], ids=["missing", "null", "a string"])
def test_a_draft_flag_that_is_no_boolean_cannot_be_judged(draft, capsys):
    gh = FakeGh(draft=draft)
    code, out, err, answers = run(gh, capsys)
    assert (code, out, err, answers) == (
        2, frame("  ✗ #7            not judged     GitHub's answer for the pull request holds no draft flag"), not_judged_pair(), [],
    )
    changed_nothing(gh)


@pytest.mark.parametrize(
    ("gh_kwargs", "sonar_kwargs", "rows"),
    [
        ({"checks": {**LEGACY_RED, "tests": "failure"}}, {}, [pull_row(), "  ! checks        red            tests (failure) beside quality / verdict"]),
        ({"checks": {**LEGACY_RED, "tests": None}}, {}, [pull_row(), "  ! checks        running        tests"]),
        ({"checks": {**LEGACY_RED, "tests": None, "lint": None}}, {}, [pull_row(), "  ! checks        running        tests, lint"]),
        (
            {"checks": {**LEGACY_RED, "tests": None}, "statuses": {"external-ci": "pending"}}, {},
            [pull_row(), "  ! checks        running        tests, external-ci"],
        ),
        (
            {"checks": {**LEGACY_RED, "tests": "failure", "lint": "failure"}}, {},
            [pull_row(), "  ! checks        red            tests (failure), lint (failure) beside quality / verdict"],
        ),
        (
            {"checks": {**LEGACY_RED, "tests": None, "lint": "failure"}}, {},
            [pull_row(), "  ! checks        running        tests"],
        ),
        ({"statuses": {"external-ci": "pending"}}, {}, [pull_row(), "  ! checks        running        external-ci"]),
        (
            {"checks": {"quality / verdict": "failure"}}, {},
            [pull_row(), "  ! checks        refused        quality / sonar did not run: the door needs it green or failed"],
        ),
        (
            {"checks": {"quality / sonar": "success", "quality / verdict": "success"}}, {},
            [pull_row(), "  ! checks        refused        no check is red: the door is not the only way, land the pull request normally"],
        ),
        (
            {"checks": {"quality / sonar": "failure"}}, {},
            [pull_row(), "  ! checks        refused        quality / verdict must have failed for the door to be the only way"],
        ),
        (
            {"checks": SONAR_GREEN}, {},
            [
                pull_row(), verdict_only_checks_row(),
                "  ! loosenings    refused        quality / verdict is red, but judged again at the merge commit it finds nothing: re-run the verdict",
            ],
        ),
        (
            {"checks": SONAR_GREEN, "base_config": TWO_JOBS, "head_config": ONE_JOB}, {},
            [pull_row(), verdict_only_checks_row(), "  ! loosenings    red", f"    ! loosened (undeclared): {B_REMOVED}", '    ! mandatory job "B" is missing from the run'],
        ),
        (
            {}, {"new": 1},
            [
                pull_row(), checks_row(), loosenings_none(), "  ! sonar         refused        the pull request adds findings:",
                "    ! sonar a.py:3 - s1: bad",
            ],
        ),
        (
            {"base_config": GATE_CONFIG, "head_config": '[gate]\nmandatory_jobs = ["checks"]\nlegacy = "report"\n' + declaring("legacy fail → report")}, {},
            [
                pull_row(), checks_row(), "  ! loosenings    1 declared",
                "    ! legacy fail → report · overnightworks/songmaker#1345 of 2026-10-20", "        reason: ruled",
                ("  ! sonar         refused        legacy = report at the pull request's current merge commit: "
                 "quality / sonar cannot be red on legacy findings"),
            ],
        ),
    ],
    ids=[
        "another red check", "a check running", "two checks running", "a check and a status running", "two other red checks",
        "a check running beside a red one", "a pending status", "sonar did not run", "nothing red",
        "the verdict not red", "nothing re-derived", "an undeclared loosening", "an added sonar finding",
        "a declared fail to report loosening with legacy findings",
    ],
)
def test_each_refusal_is_its_row_and_the_pair(gh_kwargs, sonar_kwargs, rows, capsys):
    gh = FakeGh(**gh_kwargs)
    code, out, err, answers = run(gh, capsys, transport=sonar(**sonar_kwargs))
    assert (code, out, err, answers) == (1, frame(*rows), refused_pair(), [])
    changed_nothing(gh)


@pytest.mark.parametrize(
    ("gh_kwargs", "sonar_kwargs", "rows"),
    [
        (
            {"admin": False}, {},
            [f"  ✗ #7            not judged     the gh login has no admin permission on {REPO}, which switching quality_gate needs"],
        ),
        ({"fail_on": ["pull"]}, {}, ["  ✗ #7            not judged     pull broke"]),
        (
            {"merge_sha": None}, {},
            [
                pull_row(), checks_row(),
                "  ✗ loosenings    not judged     GitHub has no merge commit for the pull request yet, so the config the jobs ran on is unknown",
            ],
        ),
        (
            {}, {"unreachable": True},
            [pull_row(), checks_row(), loosenings_none(), "  ✗ sonar         not judged     SonarCloud is down"],
        ),
        (
            {"settings": {"allow_merge_commit": False, "allow_squash_merge": False, "allow_rebase_merge": True}}, {},
            [
                pull_row(), checks_row(), loosenings_none(), legacy_row(),
                "  ✗ merge         not judged     the repository and its branch rules allow no merge method the door uses (a merge commit or a squash)",
            ],
        ),
        ({"body": ABSENT}, {}, [pull_row(), "  ✗ landing       not judged     GitHub's answer holds no 'body'"]),
        (not_json_answer("repository"), {}, [not_json_row("#7")]),
        (not_json_answer("pull"), {}, [not_json_row("#7")]),
        (not_json_answer("checks"), {}, [pull_row(), not_json_row("checks")]),
        (not_json_answer("statuses"), {}, [pull_row(), not_json_row("checks")]),
    ],
    ids=[
        "no admin right", "gh fails on the pull request", "no merge commit", "sonar unreachable", "no method the door uses",
        "a pull answer without a body", "a repository answer that is no JSON", "a pull answer that is no JSON",
        "a check-run answer that is no JSON", "a status answer that is no JSON",
    ],
)
def test_a_step_that_cannot_be_judged_is_its_cross_row_and_the_pair(gh_kwargs, sonar_kwargs, rows, capsys):
    gh = FakeGh(**gh_kwargs)
    code, out, err, answers = run(gh, capsys, transport=sonar(**sonar_kwargs))
    assert (code, out, err, answers) == (2, frame(*rows), not_judged_pair(), [])
    changed_nothing(gh)


def test_the_rows_name_the_pull_requests_base_not_the_default_branch(capsys):
    gh = FakeGh(base_ref="release/1")
    shown = run(gh, capsys)
    assert shown == (
        0,
        frame(
            "  · #7            open           head aaaaaaa · into release/1",
            checks_row(),
            loosenings_none(),
            legacy_row(),
            "  · merge         merge commit   allowed on release/1",
            "  · landing       work item      #3",
            "merged.",
        ),
        prompt(),
        ["yes\n"],
    )


def at_a_terminal(gh, window, columns, transport=None):
    """What the door shows on a terminal of `columns` before it asks, read off the screen, with its exit and stderr."""
    terminal = window(columns)
    errors = io.StringIO()
    with contextlib.redirect_stdout(terminal.stream), contextlib.redirect_stderr(errors):
        code = cli.main(
            ["emergency-merge", REPO, "7"], gh=gh, transport=transport or sonar(), read_line=lambda: "no\n", is_terminal=lambda: True,
            remote_for=no_network, environment={"TERM": "xterm"},
        )
    return code, screen(terminal.read()).splitlines(), errors.getvalue()


@pytest.mark.parametrize(
    ("gh_kwargs", "sonar_kwargs", "code", "lines", "err"),
    [
        (
            {"checks": SONAR_GREEN, "base_config": TWO_JOBS, "head_config": ONE_JOB + declaring(B_REMOVED, reason="a reason that runs on past the window")},
            {}, 1,
            [
                pull_row(), verdict_only_checks_row(), "  ! loosenings    1 declared",
                f'    ! {B_REMOVED} · overnightworks/songmaker#1345 of 2026-10-20',
                "        reason: a reason that runs on past the window",
                "  · merge         merge commit   allowed on main", "  · landing       work item      #3",
            ],
            prompt() + not_confirmed_pair(),
        ),
        (
            {"body": "Work-Item: #123456789"}, {}, 1,
            [
                pull_row(), checks_row(), loosenings_none(), legacy_row(),
                "  · merge         merge commit   allowed on main", "  · landing       work item      #123456789",
            ],
            prompt() + not_confirmed_pair(),
        ),
        (
            {"checks": SONAR_GREEN, "base_config": TWO_JOBS, "head_config": ONE_JOB},
            {}, 1,
            [
                pull_row(), verdict_only_checks_row(), "  ! loosenings    red", f"    ! loosened (undeclared): {B_REMOVED}",
                '    ! mandatory job "B" is missing from the run',
            ],
            refused_pair(),
        ),
        (
            {},
            {"added": [{"component": "k:a.py", "line": 3, "rule": "s1", "message": "a message that runs on past the window"}]}, 1,
            [
                pull_row(), checks_row(), loosenings_none(), "  ! sonar         refused        the pu…",
                "    ! sonar a.py:3 - s1: a message that runs on past the window",
            ],
            refused_pair(),
        ),
    ],
    ids=["a declaration", "a long item number", "an undeclared loosening", "an added finding"],
)
def test_what_the_operator_must_read_stands_whole_at_a_terminal(gh_kwargs, sonar_kwargs, code, lines, err, window):
    assert at_a_terminal(FakeGh(**gh_kwargs), window, 40, sonar(**sonar_kwargs)) == (code, lines, err)


@pytest.mark.parametrize(
    ("gh_kwargs", "code", "lines", "err"),
    [
        ({"checks": {"quality / verdict": "failure"}}, 1, [pull_row(), "  ! checks        refused        qualit…"], refused_pair()),
        ({"admin": False}, 2, ["  ✗ #7            not judged     the gh…"], not_judged_pair()),
        (
            {"checks": {**LEGACY_RED, "a_long_running_check": None}}, 1,
            [pull_row(), "  ! checks        running        a_long…"], refused_pair(),
        ),
        (
            {"checks": {**LEGACY_RED, "a_long_red_check": "failure"}}, 1,
            [pull_row(), "  ! checks        red            a_long…"], refused_pair(),
        ),
    ],
    ids=["a refused fact", "a cross fact", "a running check", "a red check"],
)
def test_a_refused_or_cross_fact_ends_at_the_window(gh_kwargs, code, lines, err, window):
    assert at_a_terminal(FakeGh(**gh_kwargs), window, 40) == (code, lines, err)
