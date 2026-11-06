"""The question the operator's door asks after its rows, and what each way of answering it does."""

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


@pytest.mark.parametrize(
    ("typed", "pair"),
    [
        ("", "\n" + not_confirmed_pair()),
        ("ja", "\n" + not_confirmed_pair()),
        ("\n", not_confirmed_pair()),
        ("yes\n", not_confirmed_pair()),
        ("JA\n", not_confirmed_pair()),
        ("ja ja\n", not_confirmed_pair()),
    ],
    ids=["end of input", "an unended ja", "Enter alone", "yes", "JA", "ja ja"],
)
def test_anything_but_ja_changes_nothing_and_says_so(typed, pair, capsys):
    gh, at_the_read = FakeGh(), []
    code, answers = drive(gh, typed=typed, when_asked=lambda: at_the_read.append(list(gh.requests)))
    streams = capsys.readouterr()
    assert (code, streams.out, streams.err, answers) == (1, rows_before_the_prompt(), prompt() + pair, [typed])
    assert gh.requests == at_the_read[0]
    assert gh.sequence == []


@pytest.mark.parametrize("repository", [REPO, "acme/gadgets"], ids=["on the default repository", "on another repository"])
@pytest.mark.parametrize(
    ("gh_kwargs", "typed", "pair"),
    [
        ({}, "no\n", not_confirmed_pair),
        ({"body": "Work-Item: acme/other#9"}, "ja\n", landing_pair),
    ],
    ids=["not confirmed", "a refused landing line"],
)
def test_the_prompt_and_the_pair_name_the_pull_request_asked_for(repository, gh_kwargs, typed, pair, capsys):
    err = (prompt(12) if typed == "no\n" else "") + pair(12, repository)
    gh = FakeGh(checks=SONAR_GREEN, base_config=TWO_JOBS, head_config=ONE_JOB + declaring(B_REMOVED), **gh_kwargs)
    code, _ = drive(gh, repo=repository, pull_request="12", typed=typed)
    assert (code, capsys.readouterr().err) == (1, err)
    changed_nothing(gh)


@pytest.mark.parametrize("typed", ["ja\n", " ja \n", "\tja\t\n"], ids=["ja", "spaces around", "tabs around"])
def test_ja_merges_with_or_without_whitespace_around_it(typed, capsys):
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


def test_the_door_reads_one_line_of_stdin(monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO(" ja \nno\n"))
    code = cli.main(["emergency-merge", REPO, "7"], gh=FakeGh(), transport=sonar(), is_terminal=lambda: True, remote_for=no_network)
    assert (code, sys.stdin.read()) == (0, "no\n")


class Stream(io.StringIO):
    def __init__(self, text: str, *, terminal: bool) -> None:
        super().__init__(text)
        self.terminal = terminal

    def isatty(self) -> bool:
        return self.terminal


@pytest.mark.parametrize(
    ("stdin_terminal", "stdout_terminal", "code"),
    [(True, False, 0), (False, True, 1)],
    ids=["terminal stdin alone", "terminal stdout alone"],
)
def test_only_stdin_decides_whether_the_door_has_a_terminal(stdin_terminal, stdout_terminal, code, monkeypatch):
    monkeypatch.setattr("sys.stdin", Stream("ja\n", terminal=stdin_terminal))
    monkeypatch.setattr("sys.stdout", Stream("", terminal=stdout_terminal))
    result = cli.main(["emergency-merge", REPO, "7"], gh=FakeGh(), transport=sonar(), remote_for=no_network)
    assert result == code


@pytest.mark.parametrize("stdin", [None, io.StringIO("ja\n")], ids=["closed", "no terminal"])
def test_a_stdin_that_is_no_terminal_is_the_pair_alone(stdin, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", stdin)
    gh = FakeGh()
    code = cli.main(["emergency-merge", REPO, "7"], gh=gh, transport=sonar(), remote_for=no_network)
    streams = capsys.readouterr()
    assert (code, streams.out, streams.err, gh.calls) == (1, "", terminal_pair(), [])


def test_a_closed_stderr_takes_the_prompt_and_the_pair(capsys, monkeypatch):
    monkeypatch.setattr("sys.stderr", None)
    code, _ = drive(FakeGh(), typed="no\n")
    assert (code, capsys.readouterr().out) == (1, rows_before_the_prompt())
