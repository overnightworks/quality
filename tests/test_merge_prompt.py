"""The question the operator's door asks after its rows, and what each way of answering it does."""

import dataclasses
import io
import os
import signal
import sys

import pytest

from quality import cli
from tests.support.merge import (
    B_REMOVED,
    ONE_JOB,
    REPO,
    SONAR_GREEN,
    TWO_JOBS,
    FakeGh,
    changed_nothing,
    declaring,
    drive,
    landing_pair,
    no_network,
    not_confirmed_pair,
    prompt,
    rows_before_the_prompt,
    sonar,
    terminal_pair,
)
from tests.support.terminal import Terminal


@pytest.mark.parametrize(
    ("typed", "pair"),
    [
        ("", "\n" + not_confirmed_pair()),
        ("yes", "\n" + not_confirmed_pair()),
        ("\n", not_confirmed_pair()),
        ("ja\n", not_confirmed_pair()),
        ("Yes\n", not_confirmed_pair()),
        ("YES\n", not_confirmed_pair()),
        ("y\n", not_confirmed_pair()),
        ("yes yes\n", not_confirmed_pair()),
    ],
    ids=["end of input", "an unended yes", "Enter alone", "the old word", "a capital", "capitals", "the first letter", "twice"],
)
def test_anything_but_yes_changes_nothing_and_says_so(typed, pair, capsys):
    gh, at_the_read = FakeGh(), []
    code, answers = drive(gh, typed=typed, when_asked=lambda: at_the_read.append(list(gh.requests)))
    streams = capsys.readouterr()
    assert (code, streams.out, streams.err, answers) == (1, rows_before_the_prompt(), prompt() + pair, [typed])
    assert gh.requests == at_the_read[0]
    assert gh.sequence == []


@pytest.mark.parametrize("repository", [REPO, "acme/gadgets"], ids=["on the default repository", "on another repository"])
@pytest.mark.parametrize(
    ("gh_kwargs", "typed", "start", "pair"),
    [
        ({}, "no\n", prompt(12), not_confirmed_pair),
        ({"body": "Work-Item: acme/other#9"}, "yes\n", "", landing_pair),
    ],
    ids=["not confirmed", "a refused landing line"],
)
def test_the_prompt_and_the_pair_name_the_pull_request_asked_for(repository, gh_kwargs, typed, start, pair, capsys):
    err = start + pair(12, repository)
    gh = FakeGh(checks=SONAR_GREEN, base_config=TWO_JOBS, head_config=ONE_JOB + declaring(B_REMOVED), **gh_kwargs)
    code, _ = drive(gh, repo=repository, pull_request="12", typed=typed)
    shown = capsys.readouterr().err
    assert (code, shown) == (1, err)
    changed_nothing(gh)


@pytest.mark.parametrize("typed", ["yes\n", " yes \n", "\tyes\t\n", "\u00a0yes\u00a0\n"], ids=["yes", "spaces around", "tabs around", "non-breaking spaces around"])
def test_yes_merges_with_or_without_whitespace_around_it(typed, capsys):
    gh = FakeGh()
    assert drive(gh, typed=typed)[0] == 0
    assert gh.sequence == ["gate false", "merge", "gate true", "comment"]
    assert capsys.readouterr().err == prompt()


def test_ctrl_c_at_the_prompt_ends_the_run_interrupted_and_changes_nothing(capsys):
    gh, sent, at_the_read = FakeGh(), [], []
    saved = signal.getsignal(signal.SIGINT)
    try:
        with pytest.raises(SystemExit) as exited:
            cli.run(
                door=lambda: drive(gh, typed=KeyboardInterrupt(), when_asked=lambda: at_the_read.append(list(gh.requests)))[0],
                environment={}, send_signal=lambda pid, number: sent.append((pid, number)),
            )
    finally:
        signal.signal(signal.SIGINT, saved)
    streams = capsys.readouterr()
    assert (exited.value.code, sent) == (130, [(os.getpid(), signal.SIGINT)])
    assert (streams.out, streams.err) == (rows_before_the_prompt(), prompt())
    assert gh.requests == at_the_read[0]
    assert gh.sequence == []


def door(gh, **ports):
    return cli.main(["emergency-merge", REPO, "7"], gh=gh, transport=sonar(), remote_for=no_network, **ports)


def test_the_door_reads_one_line_of_stdin(monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO("yes\nno\n"))
    code = door(FakeGh(), terminal=dataclasses.replace(cli.Terminal.standard_input(), is_terminal=lambda: True))
    left_unread = sys.stdin.read()
    assert (code, left_unread) == (0, "no\n")


@pytest.mark.parametrize(
    ("stdin_stream", "stdout_stream", "code"),
    [(Terminal, io.StringIO, 0), (io.StringIO, Terminal, 1)],
    ids=["terminal stdin alone", "terminal stdout alone"],
)
def test_only_stdin_decides_whether_the_door_has_a_terminal(stdin_stream, stdout_stream, code, monkeypatch):
    monkeypatch.setattr("sys.stdin", stdin_stream("yes\n"))
    monkeypatch.setattr("sys.stdout", stdout_stream())
    assert door(FakeGh()) == code


@pytest.mark.parametrize("stdin", [None, io.StringIO("yes\n")], ids=["closed", "no terminal"])
def test_a_stdin_that_is_no_terminal_is_the_pair_alone(stdin, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", stdin)
    gh = FakeGh()
    code = door(gh)
    streams = capsys.readouterr()
    assert (code, streams.out, streams.err, gh.calls) == (1, "", terminal_pair(), [])


def test_a_closed_stderr_takes_the_prompt_and_the_pair(capsys, monkeypatch):
    monkeypatch.setattr("sys.stderr", None)
    code, _ = drive(FakeGh(), typed="no\n")
    shown = capsys.readouterr().out
    assert (code, shown) == (1, rows_before_the_prompt())
