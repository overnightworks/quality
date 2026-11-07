import re
import shlex

import pytest

from quality import cli
from tests.support.door import RELEASE_ROW
from tests.support.merge import (
    B_REMOVED,
    LEGACY_RED,
    MERGE_SHA,
    ONE_JOB,
    SONAR_GREEN,
    TWO_JOBS,
    FakeGh,
    Reexecuted,
    ReexecutionPort,
    declaring,
    door_ports,
    drive,
    gate_workflow,
    run,
    terminal_pair,
)


def declared_loosening():
    return {"checks": SONAR_GREEN, "base_config": TWO_JOBS, "head_config": ONE_JOB + declaring(B_REMOVED)}


def asked_after(capsys, gh_kwargs, **arguments):
    """What a door run on a fresh fake showed on stdout and requested before its question."""
    gh, shown, sent = FakeGh(**gh_kwargs), [], []

    def at_the_question():
        shown.append(capsys.readouterr().out)
        sent.append(list(gh.requests))

    drive(gh, typed="no\n", when_asked=at_the_question, **arguments)
    capsys.readouterr()
    return shown[0], sent[0]


def writes(gh):
    return [argv for argv in gh.calls if "-X" in argv or argv[:2] == ("pr", "merge")]


@pytest.mark.parametrize("gh_kwargs", [{}, declared_loosening()], ids=["legacy findings", "a declared loosening"])
def test_a_dry_run_shows_the_rows_and_its_line_and_changes_nothing(gh_kwargs, capsys):
    rows, requests_before_the_question = asked_after(capsys, gh_kwargs)
    gh = FakeGh(**gh_kwargs)
    assert run(gh, capsys, words=["--dry-run"]) + (gh.sequence,) == (
        0, rows + "~ dry run · would switch quality_gate off, merge #7, switch it on · nothing changed\n", "", [], [],
    )
    assert (writes(gh), gh.requests) == ([], requests_before_the_question)


def test_a_dry_run_shows_the_release_row_first(capsys):
    _, out, _, _ = run(FakeGh(), capsys, words=["--dry-run"])
    assert out.startswith(RELEASE_ROW)


def test_a_dry_run_re_executes_with_its_dry_run_word(capsys):
    port = ReexecutionPort()
    gh = FakeGh(workflows={MERGE_SHA: {"ci.yml": gate_workflow("v0.10.1", "7f" * 20)}})
    ports = door_ports(reexecute=port)
    with pytest.raises(Reexecuted):
        drive(gh, words=["--dry-run"], ports=ports)
    assert [words for _, _, words in port.asked] == [["merge", "acme/widgets#7", "--dry-run"]]


def test_a_dry_run_reads_the_pull_request_asked_for(capsys):
    gh = FakeGh(**declared_loosening())
    code, out, _, _ = run(gh, capsys, repo="acme/gadgets", pull_request="12", words=["--dry-run"])
    login = ("api", "user", "--jq", ".login")
    named = [[word for word in argv if word.startswith("repos/")] for argv in gh.calls if argv != login]
    assert (code, out.splitlines()[-1]) == (0, "~ dry run · would switch quality_gate off, merge #12, switch it on · nothing changed")
    assert [argv for argv in gh.calls if any("/pulls/" in word for word in argv)] == [("api", "repos/acme/gadgets/pulls/12")]
    assert [len(words) for words in named] == [1] * len(named)
    assert [word for (word,) in named if word != "repos/acme/gadgets" and not word.startswith("repos/acme/gadgets/")] == []
    assert gh.calls.count(login) == 1
    assert writes(gh) == []


@pytest.mark.parametrize(
    ("gh_kwargs", "code"),
    [
        ({"checks": {**LEGACY_RED, "test": "failure"}}, 1),
        ({"draft": True}, 1),
        ({"fail_on": ["pull"]}, 2),
        ({"body": "a body without the line"}, 1),
    ],
    ids=["another check red", "a draft", "gh down", "a refused landing line"],
)
def test_a_dry_run_refuses_where_the_door_refuses(gh_kwargs, code, capsys):
    door = run(FakeGh(**gh_kwargs), capsys)
    dry_gh = FakeGh(**gh_kwargs)
    dry_run = run(dry_gh, capsys, words=["--dry-run"])
    assert dry_run == door
    assert (dry_run[0], dry_run[3], writes(dry_gh)) == (code, [], [])


def test_a_dry_run_needs_a_terminal_like_the_door(capsys):
    gh = FakeGh()
    assert run(gh, capsys, terminal=False, words=["--dry-run"]) + (gh.calls,) == (1, "", terminal_pair(), [], [])


@pytest.mark.parametrize(
    ("words", "typed"), [([], "yes\n"), (["--dry-run"], "")], ids=["a merge", "a dry run"],
)
def test_v_adds_nothing_to_the_door(words, typed, capsys):
    plain_gh, verbose_gh = FakeGh(), FakeGh()
    plain = run(plain_gh, capsys, typed=typed, words=words)
    verbose = run(verbose_gh, capsys, typed=typed, words=[*words, "-v"])
    assert verbose[:3] + (verbose_gh.requests,) == plain[:3] + (plain_gh.requests,)


@pytest.mark.parametrize(
    ("gh_kwargs", "typed", "pattern"),
    [
        ({}, "no\n", r"next: (.+)"),
        ({"fail_on": ["merge"]}, "yes\n", r"next: (.+)"),
        ({"fail_on": ["pull"]}, "yes\n", r"next: fix what the ✗ row names, then run (.+) again"),
        (
            {"body": "a body without the line"}, "yes\n",
            r"next: give the pull request body exactly one Work-Item: or No-Item: line outside code blocks, then run (.+) again",
        ),
        (
            {"merge_config": "[secrets]\nignore = []\n"}, "yes\n",
            r"next: push the fix to the pull request's branch, then run (.+) again",
        ),
    ],
    ids=["not confirmed", "not merged", "not judged", "a refused landing line", "a config refused at the merge commit"],
)
def test_a_next_step_that_names_the_door_again_names_the_pull_request_asked_for(gh_kwargs, typed, pattern, capsys):
    gh = FakeGh(**{**declared_loosening(), **gh_kwargs})
    _, _, err, _ = run(gh, capsys, repo="acme/gadgets", pull_request="12", typed=typed)
    (step,) = [line for line in err.splitlines() if line.startswith("next: ")]
    command = shlex.split(re.fullmatch(pattern, step)[1])
    arguments = cli.read_invocation(command[1:])
    assert (command[0], arguments.command, arguments.pull_request, arguments.dry_run) == (
        "quality", "merge", cli.PullRequestName("acme/gadgets", 12), False,
    )
