"""The rows the operator's door prints before its prompt and the pair it prints when a step stops it."""

import contextlib
import io

import pytest

from quality import cli
from quality.report import printable
from tests.support.config_refusals import all_refusals
from tests.support.door import RELEASE_ROW
from tests.support.merge import (
    ABSENT,
    B_REMOVED,
    GATE_CONFIG,
    LEGACY_RED,
    MERGE_SHA,
    ONE_JOB,
    REPO,
    SHA,
    SONAR_GREEN,
    TWO_JOBS,
    FakeGh,
    attempt_rows,
    changed_nothing,
    closing_line,
    comment_line,
    declaring,
    door_ports,
    frozen_now,
    no_network,
    not_confirmed_pair,
    prompt,
    restore_line,
    run,
    sonar,
)
from tests.support.merge import pull_row as pull_row_with_line_end
from tests.support.terminal import screen


def frame(*lines):
    return "".join(f"{line}\n" for line in lines)


def release_row():
    return RELEASE_ROW.removesuffix("\n")


def pull_row():
    return pull_row_with_line_end().removesuffix("\n")


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


def merge_and_landing_rows(work_item="#3"):
    return ["  · merge         merge commit   allowed on main", f"  · landing       work item      {work_item}"]


def rows_before_the_attempt():
    return [release_row(), pull_row(), checks_row(), loosenings_none(), legacy_row(), *merge_and_landing_rows()]


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
        "next: fix what the ✗ row names, then run quality merge acme/widgets#7 again",
    )


@pytest.mark.parametrize(
    ("terminal", "repository", "code", "pair"),
    [
        (False, REPO, 1, frame(
            "error: not merged · the operator's merge needs a terminal on stdin · nothing changed",
            "next: the operator runs it in his own terminal",
        )),
        (True, "not-a-repo", 2, frame(
            "error: no pull request not-a-repo#7 · a pull request reads OWNER/REPO#N",
            "next: quality merge --help",
        )),
        (False, "not-a-repo", 2, frame(
            "error: no pull request not-a-repo#7 · a pull request reads OWNER/REPO#N",
            "next: quality merge --help",
        )),
    ],
    ids=["no terminal", "a malformed name", "a malformed name and no terminal"],
)
def test_a_refusal_before_any_read_is_the_pair_alone(terminal, repository, code, pair, capsys):
    """MERGE-21, HELP-12, HELP-18: no terminal, or a name of another form, is the pair alone and nothing is read."""
    gh = FakeGh()
    refused, out, err, _ = run(gh, capsys, terminal=terminal, repo=repository)
    assert (refused, out, err, gh.calls) == (code, "", pair, [])


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
    """MERGE-14: a draft or a closed pull request stops the door at its `!` row and the pair."""
    gh = FakeGh(**gh_kwargs)
    code, out, err, answers = run(gh, capsys)
    assert (code, out, err, answers) == (1, frame(row), pair, [])
    assert gh.requests == [(("api", f"repos/{REPO}"), None), (("api", f"repos/{REPO}/pulls/7"), None)]
    changed_nothing(gh)


def test_the_door_asks_for_the_head_commits_checks_and_statuses_and_the_login_by_these_requests(capsys):
    gh = FakeGh()
    assert run(gh, capsys)[0] == 0
    assert gh.requests[:6] == [
        (("api", f"repos/{REPO}"), None),
        (("api", f"repos/{REPO}/pulls/7"), None),
        (("api", f"repos/{REPO}/contents/.github/workflows?ref={MERGE_SHA}"), None),
        (("api", "-H", "Accept: application/vnd.github.raw", f"repos/{REPO}/contents/.github/workflows/ci.yml?ref={MERGE_SHA}"), None),
        (("api", "--paginate", f"repos/{REPO}/commits/{SHA}/check-runs?per_page=100", "--jq", ".check_runs[]"), None),
        (("api", "--paginate", f"repos/{REPO}/commits/{SHA}/status?per_page=100", "--jq", ".statuses[]"), None),
    ]
    assert [request for request in gh.requests if request[0][:2] == ("api", "user")] == [(("api", "user", "--jq", ".login"), None)]


@pytest.mark.parametrize("draft", [ABSENT, None, "false"], ids=["missing", "null", "a string"])
def test_a_draft_flag_that_is_no_boolean_cannot_be_judged(draft, capsys):
    """MERGE-16: a draft flag that is no boolean is its `✗` row and the pair."""
    gh = FakeGh(draft=draft)
    code, out, err, answers = run(gh, capsys)
    assert (code, out, err, answers) == (
        2, frame("  ✗ #7            not judged     GitHub's answer for the pull request holds no draft flag"), not_judged_pair(), [],
    )
    changed_nothing(gh)


@pytest.mark.parametrize(
    ("gh_kwargs", "sonar_kwargs", "rows"),
    [
        ({"checks": {**LEGACY_RED, "tests": "failure"}}, {}, [release_row(), pull_row(), "  ! checks        red            tests (failure) beside quality / verdict"]),
        ({"checks": {**LEGACY_RED, "tests": None}}, {}, [release_row(), pull_row(), "  ! checks        running        tests"]),
        ({"checks": {**LEGACY_RED, "tests": None, "lint": None}}, {}, [release_row(), pull_row(), "  ! checks        running        tests, lint"]),
        (
            {"checks": {**LEGACY_RED, "tests": None}, "statuses": {"external-ci": "pending"}}, {},
            [release_row(), pull_row(), "  ! checks        running        tests, external-ci"],
        ),
        (
            {"checks": {**LEGACY_RED, "tests": "failure", "lint": "failure"}}, {},
            [release_row(), pull_row(), "  ! checks        red            tests (failure), lint (failure) beside quality / verdict"],
        ),
        (
            {"checks": {**LEGACY_RED, "tests": None, "lint": "failure"}}, {},
            [release_row(), pull_row(), "  ! checks        running        tests"],
        ),
        ({"statuses": {"external-ci": "pending"}}, {}, [release_row(), pull_row(), "  ! checks        running        external-ci"]),
        (
            {"checks": {"quality / verdict": "failure"}}, {},
            [release_row(), pull_row(), "  ! checks        refused        quality / sonar did not run: the door needs it green or failed"],
        ),
        (
            {"checks": {"quality / sonar": "success", "quality / verdict": "success"}}, {},
            [release_row(), pull_row(), "  ! checks        refused        no check is red: the door is not the only way, land the pull request normally"],
        ),
        (
            {"checks": {"quality / sonar": "failure"}}, {},
            [release_row(), pull_row(), "  ! checks        refused        quality / verdict must have failed for the door to be the only way"],
        ),
        (
            {"checks": SONAR_GREEN}, {},
            [
                release_row(), pull_row(), verdict_only_checks_row(),
                "  ! loosenings    refused        quality / verdict is red, but judged again at the merge commit it finds nothing: re-run the verdict",
            ],
        ),
        (
            {"checks": SONAR_GREEN, "base_config": TWO_JOBS, "head_config": ONE_JOB}, {},
            [release_row(), pull_row(), verdict_only_checks_row(), "  ! loosenings    red", f"    ! {B_REMOVED}", '    ! B missing · not in the run'],
        ),
        (
            {}, {"new": 1},
            [
                release_row(), pull_row(), checks_row(), loosenings_none(), "  ! sonar         refused        the pull request adds findings:",
                "    ! a.py:3 · s1: bad",
            ],
        ),
        (
            {"base_config": GATE_CONFIG, "head_config": '[gate]\nmandatory_jobs = ["checks"]\nlegacy = "report"\n' + declaring("legacy fail → report")}, {},
            [
                release_row(), pull_row(), checks_row(), "  ! loosenings    1 declared",
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
    """MERGE-14, MERGE-15: each refusal is its `!` row and the pair, and the verdict's blocking lines stand below the
    `loosenings` row."""
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
            {}, {"unreachable": True},
            [release_row(), pull_row(), checks_row(), loosenings_none(), "  ✗ sonar         not judged     SonarCloud is down"],
        ),
        (
            {"settings": {"allow_merge_commit": False, "allow_squash_merge": False, "allow_rebase_merge": True}}, {},
            [
                release_row(), pull_row(), checks_row(), loosenings_none(), legacy_row(),
                "  ✗ merge         not judged     the repository and its branch rules allow no merge method the door uses (a merge commit or a squash)",
            ],
        ),
        ({"body": ABSENT}, {}, [release_row(), pull_row(), "  ✗ landing       not judged     GitHub's answer holds no 'body'"]),
        (not_json_answer("repository"), {}, [not_json_row("#7")]),
        (not_json_answer("pull"), {}, [not_json_row("#7")]),
        (not_json_answer("checks"), {}, [release_row(), pull_row(), not_json_row("checks")]),
        (not_json_answer("statuses"), {}, [release_row(), pull_row(), not_json_row("checks")]),
    ],
    ids=[
        "no admin right", "gh fails on the pull request", "sonar unreachable", "no method the door uses",
        "a pull answer without a body", "a repository answer that is no JSON", "a pull answer that is no JSON",
        "a check-run answer that is no JSON", "a status answer that is no JSON",
    ],
)
def test_a_step_that_cannot_be_judged_is_its_cross_row_and_the_pair(gh_kwargs, sonar_kwargs, rows, capsys):
    """MERGE-16: a step that cannot be judged is its `✗` row and the pair."""
    gh = FakeGh(**gh_kwargs)
    code, out, err, answers = run(gh, capsys, transport=sonar(**sonar_kwargs))
    assert (code, out, err, answers) == (2, frame(*rows), not_judged_pair(), [])
    changed_nothing(gh)


def sonar_string_config():
    return '[gate]\nmandatory_jobs = ["checks"]\nsonar = "false"\n'


def unknown_key_config():
    return "[secrets]\nignore = []\n"


def push_step():
    return "push the fix to the pull request's branch, then run quality merge acme/widgets#7 again"


@pytest.mark.parametrize(
    "refusal", [refusal for refusal in all_refusals() if "gate" in refusal.loaders and refusal.text is not None], ids=lambda refusal: refusal.id
)
def test_every_config_refused_at_the_merge_commit_is_its_fact_on_the_row_and_its_example_under_next(refusal, capsys):
    """MERGE-17, MERGE-20, O10: a config refused at the merge commit is its fact on the `✗ loosenings` row, its
    example under `next:` and the push step."""
    gh = FakeGh(merge_config=refusal.text)
    code, out, err, answers = run(gh, capsys)
    lines = err.splitlines()
    row = f"  ✗ loosenings    not judged     {printable(refusal.fact)}"
    assert (code, out, lines[0], answers) == (2, frame(release_row(), pull_row(), checks_row(), row), "error: could not judge · nothing changed", [])
    if refusal.example is None:
        assert lines[1:] == [f"next: {push_step()}"]
    else:
        steps = [line.strip() for line in lines[2:]]
        assert (lines[1], steps) == ("next:", ["write it in quality/config.toml like this:", *map(printable, refusal.example), push_step()])
    changed_nothing(gh)


@pytest.mark.parametrize(
    ("gh_kwargs", "into", "fact", "pair_end"),
    [
        (
            {"merge_config": sonar_string_config()}, "main", "quality/config.toml [gate] sonar must be true or false",
            ["next:", "    write it in quality/config.toml like this:", "        [gate]", "        sonar = false", f"    {push_step()}"],
        ),
        (
            {"merge_config": unknown_key_config()}, "main",
            "quality/config.toml has unknown key ignore in [secrets] - only allow exists", [f"next: {push_step()}"],
        ),
        (
            {"base_config": sonar_string_config(), "base_ref": "release/v0.9"}, "release/v0.9",
            "quality/config.toml [gate] sonar must be true or false",
            ["next:", "    write it in quality/config.toml like this:", "        [gate]", "        sonar = false", "    fix it on release/v0.9 first"],
        ),
        (
            {"base_config": unknown_key_config(), "base_ref": "release/v0.9"}, "release/v0.9",
            "quality/config.toml has unknown key ignore in [secrets] - only allow exists", ["next: fix it on release/v0.9 first"],
        ),
        (
            {"base_config": unknown_key_config(), "base_ref": "release/\u202eevil"}, "release/\\u202eevil",
            "quality/config.toml has unknown key ignore in [secrets] - only allow exists", ["next: fix it on release/\\u202eevil first"],
        ),
    ],
    ids=[
        "the merge commit, with an example", "the merge commit, without an example", "the base, with an example",
        "the base, without an example", "a base branch with a bidi override",
    ],
)
def test_a_config_refusal_at_the_door_reads_as_drawn(gh_kwargs, into, fact, pair_end, capsys):
    """MERGE-17, MERGE-18, MERGE-20, MERGE-22, O10: a config refused at the base or at the merge commit reads as
    drawn, with its step and its text escaped."""
    gh = FakeGh(**gh_kwargs)
    assert run(gh, capsys) == (
        2,
        frame(
            release_row(), f"  · #7            open           head aaaaaaa · into {into}",
            checks_row(),
            f"  ✗ loosenings    not judged     {fact}",
        ),
        frame("error: could not judge · nothing changed", *pair_end),
        [],
    )
    changed_nothing(gh)


def test_a_config_refused_at_both_commits_is_fixed_on_the_base_first(capsys):
    """MERGE-18, MERGE-19: a config refused at both commits is fixed on the base first."""
    gh = FakeGh(base_config=sonar_string_config(), merge_config=unknown_key_config(), base_ref="release/v0.9")
    code, out, err, _ = run(gh, capsys)
    assert (code, out.splitlines()[-1], err.splitlines()[1:]) == (
        2,
        "  ✗ loosenings    not judged     quality/config.toml [gate] sonar must be true or false",
        ["next:", "    write it in quality/config.toml like this:", "        [gate]", "        sonar = false", "    fix it on release/v0.9 first"],
    )
    assert [call for call in gh.calls if any(f"?ref={MERGE_SHA}" in argument and "/.github/workflows" not in argument for argument in call)] == []


def test_the_rows_name_the_pull_requests_base_not_the_default_branch(capsys):
    """MERGE-1, MERGE-4, MERGE-35, MERGE-36: the rows name the pull request's base, not the default branch, before
    and after yes."""
    gh = FakeGh(base_ref="release/1")
    shown = run(gh, capsys)
    assert shown == (
        0,
        frame(
            release_row(), "  · #7            open           head aaaaaaa · into release/1",
            checks_row(),
            loosenings_none(),
            legacy_row(),
            "  · merge         merge commit   allowed on release/1",
            "  · landing       work item      #3",
            *attempt_rows().splitlines(),
            closing_line().rstrip("\n"),
        ),
        prompt(),
        ["yes\n"],
    )


def at_a_terminal(gh, window, columns, transport=None, typed="no\n"):
    """What the door shows on a terminal of `columns` before it asks, read off the screen, with its exit and stderr."""
    terminal = window(columns)
    errors = io.StringIO()
    with contextlib.redirect_stdout(terminal.stream), contextlib.redirect_stderr(errors):
        code = cli.main(
            ["merge", f"{REPO}#7"], gh=gh, transport=transport or sonar(), terminal=cli.Terminal(lambda: typed, lambda: True),
            remote_for=no_network, environment={"TERM": "xterm"}, now=frozen_now, ports=door_ports(),
        )
    return code, screen(terminal.read()).splitlines(), errors.getvalue()


@pytest.mark.parametrize(
    ("gh_kwargs", "sonar_kwargs", "code", "lines", "err"),
    [
        (
            {"checks": SONAR_GREEN, "base_config": TWO_JOBS, "head_config": ONE_JOB + declaring(B_REMOVED, reason="a reason that runs on past the window")},
            {}, 1,
            [
                release_row(), pull_row(), verdict_only_checks_row(), "  ! loosenings    1 declared",
                f'    ! {B_REMOVED} · overnightworks/songmaker#1345 of 2026-10-20',
                "        reason: a reason that runs on past the window",
                *merge_and_landing_rows(),
            ],
            prompt() + not_confirmed_pair(),
        ),
        (
            {"body": "Work-Item: #123456789"}, {}, 1,
            [
                release_row(), pull_row(), checks_row(), loosenings_none(), legacy_row(), *merge_and_landing_rows("#123456789"),
            ],
            prompt() + not_confirmed_pair(),
        ),
        (
            {"checks": SONAR_GREEN, "base_config": TWO_JOBS, "head_config": ONE_JOB},
            {}, 1,
            [
                release_row(), pull_row(), verdict_only_checks_row(), "  ! loosenings    red", f"    ! {B_REMOVED}",
                '    ! B missing · not in the run',
            ],
            refused_pair(),
        ),
        (
            {},
            {"added": [{"component": "k:a.py", "line": 3, "rule": "s1", "message": "a message that runs on past the window"}]}, 1,
            [
                release_row(), pull_row(), checks_row(), loosenings_none(), "  ! sonar         refused        the pu…",
                "    ! a.py:3 · s1: a message that runs on past the window",
            ],
            refused_pair(),
        ),
    ],
    ids=["a declaration", "a long item number", "an undeclared loosening", "an added finding"],
)
def test_what_the_operator_must_read_stands_whole_at_a_terminal(gh_kwargs, sonar_kwargs, code, lines, err, window):
    """LENGTH-7, MERGE-2, MERGE-3, MERGE-5: a declaration and the landing line stand whole at a terminal,
    and no legacy row shows where Sonar is green."""
    assert at_a_terminal(FakeGh(**gh_kwargs), window, 40, sonar(**sonar_kwargs)) == (code, lines, err)


@pytest.mark.parametrize(
    ("gh_kwargs", "code", "lines", "err"),
    [
        ({"checks": {"quality / verdict": "failure"}}, 1, [release_row(), pull_row(), "  ! checks        refused        qualit…"], refused_pair()),
        ({"admin": False}, 2, ["  ✗ #7            not judged     the gh…"], not_judged_pair()),
        (
            {"checks": {**LEGACY_RED, "a_long_running_check": None}}, 1,
            [release_row(), pull_row(), "  ! checks        running        a_long…"], refused_pair(),
        ),
        (
            {"checks": {**LEGACY_RED, "a_long_red_check": "failure"}}, 1,
            [release_row(), pull_row(), "  ! checks        red            a_long…"], refused_pair(),
        ),
        (
            {"merge_config": sonar_string_config()}, 2, [release_row(), pull_row(), checks_row(), "  ✗ loosenings    not judged     qualit…"],
            frame(
                "error: could not judge · nothing changed", "next:", "    write it in quality/config.toml like this:",
                "        [gate]", "        sonar = false", f"    {push_step()}",
            ),
        ),
    ],
    ids=["a refused fact", "a cross fact", "a running check", "a red check", "a config refusal"],
)
def test_a_refused_or_cross_fact_ends_at_the_window(gh_kwargs, code, lines, err, window):
    assert at_a_terminal(FakeGh(**gh_kwargs), window, 40) == (code, lines, err)


@pytest.mark.parametrize(
    ("gh_kwargs", "lines", "pair_end"),
    [
        (
            {"fail_on": ["gate"]},
            ["  ✗ quality_gate  off failed     gate b…", "  ✓ quality_gate  on", "  ✓ log comment   posted         on #7"],
            ["error: quality_gate on again · #7 is not merged", "next: quality merge acme/widgets#7"],
        ),
        (
            {"fail_on": ["merge"]},
            [
                "  ✓ quality_gate  off            acme/widgets", "  ✓ quality_gate  on",
                "  ✗ #7            not merged     merge …", "  ✓ log comment   posted         on #7",
            ],
            ["error: quality_gate on again · #7 is not merged", "next: quality merge acme/widgets#7"],
        ),
        (
            {"fail_on": ["restore"]},
            [
                "  ✓ quality_gate  off            acme/widgets", "  ✓ #7            merged         merge commit",
                "  ✗ quality_gate  not restored   restor…", "  ✓ log comment   posted         on #7",
            ],
            [
                "error: quality_gate MAY STILL BE OFF on acme/widgets · #7 is merged", "next:",
                *restore_line().splitlines(),
            ],
        ),
        (
            {"fail_on": ["comment"]},
            [
                "  ✓ quality_gate  off            acme/widgets", "  ✓ #7            merged         merge commit",
                "  ✓ quality_gate  on", "  ✗ log comment   not posted     commen…",
            ],
            ["error: quality_gate on again · #7 is merged · no log comment on #7", "next:", *comment_line().splitlines()],
        ),
    ],
    ids=["the switch off fails", "the merge fails", "the restore fails", "the comment fails"],
)
def test_an_attempts_cross_fact_ends_at_the_window(gh_kwargs, lines, pair_end, window):
    """LENGTH-1, LENGTH-6, MERGE-37, MERGE-39: at a terminal a `✗` row of the attempt ends at the window, and the pair
    and its commands stand whole."""
    code, shown, err = at_a_terminal(FakeGh(**gh_kwargs), window, 40, typed="yes\n")
    assert (code, shown, err) == (
        2, rows_before_the_attempt() + lines, prompt() + "".join(f"{line}\n" for line in pair_end),
    )
