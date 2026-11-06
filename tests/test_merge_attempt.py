"""What the operator's door prints after `yes`: a row per step, the closing line, or the pair and exit 2."""

import os
import shlex
import sys

import pytest

from tests.support.merge import (
    B_REMOVED,
    ONE_JOB,
    REPO,
    SHA,
    SONAR_GREEN,
    TWO_JOBS,
    FakeGh,
    attempt_rows,
    closing_line,
    comment_line,
    declaring,
    drive,
    log_body,
    prompt,
    restore_line,
    rows_before_the_prompt,
)


def off():
    return "  ✓ quality_gate  off            acme/widgets\n"


def on():
    return "  ✓ quality_gate  on\n"


def merged():
    return "  ✓ #7            merged         merge commit\n"


def posted():
    return "  ✓ log comment   posted         on #7\n"


def off_failed_row(detail):
    return f"  ✗ quality_gate  off failed     {detail}\n"


def restore_failed_row(detail="restore broke"):
    return f"  ✗ quality_gate  not restored   {detail}\n"


def merge_failed_row():
    return "  ✗ #7            not merged     merge broke\n"


def unknown_row(detail):
    return f"  ✗ #7            state unknown  {detail}\n"


def comment_failed_row(detail="comment broke"):
    return f"  ✗ log comment   not posted     {detail}\n"


def not_restored():
    return "NOT restored, it may still be off"


def merge_failed():
    return "merge failed"


def merge_state_unknown():
    return "merge state unknown"


def not_merged():
    return "is not merged"


def merge_broke():
    return "merge broke"


def unrestored_log_body(outcome="merge"):
    return log_body(outcome=outcome, restored=not_restored())


def all_four():
    return ["gate false", "merge", "gate true", "comment"]


def all_four_but(*left_out):
    return [step for step in all_four() if step not in left_out]


def rerun_pair(fact):
    return f"error: quality_gate on again · #7 {fact}\nnext: quality emergency-merge acme/widgets 7\n"


def unknown_pair():
    return "error: quality_gate on again · #7 may be merged\nnext: gh pr view 7 --repo acme/widgets\n"


def unrestored_unknown_pair():
    return (
        "error: quality_gate MAY STILL BE OFF on acme/widgets · #7 may be merged\nnext:\n"
        + restore_line()
        + "    gh pr view 7 --repo acme/widgets\n"
    )


def merged_restore_pair():
    return "error: quality_gate MAY STILL BE OFF on acme/widgets · #7 is merged\nnext:\n" + restore_line()


def merged_comment_pair():
    return "error: quality_gate on again · #7 is merged · no log comment on #7\nnext:\n" + comment_line()


def after_the_question(gh, capsys, **arguments):
    """The exit code, stdout and stderr once `when_asked` read the question."""
    code, _ = drive(gh, when_asked=capsys.readouterr, **arguments)
    streams = capsys.readouterr()
    return code, streams.out, streams.err


def gate_body(value):
    return f'{{"properties":[{{"property_name":"quality_gate","value":"{value}"}}]}}'


def gate_request(repository, value):
    return (("api", "-X", "PATCH", f"repos/{repository}/properties/values", "--input", "-"), gate_body(value))


def requests_of_the_attempt(gh, repository=REPO):
    return gh.requests[gh.requests.index(gate_request(repository, "false")) :]


@pytest.mark.parametrize(
    ("settings", "flag", "method"),
    [
        ({}, "--merge", "merge commit"),
        ({"allow_merge_commit": False, "allow_squash_merge": True}, "--squash", "squash"),
    ],
    ids=["a merge commit", "a squash"],
)
def test_a_merge_that_goes_through_is_four_rows_and_the_closing_line(settings, flag, method, capsys):
    gh = FakeGh(settings=settings or None)
    streams = after_the_question(gh, capsys)
    assert (*streams, requests_of_the_attempt(gh)) == (
        0,
        attempt_rows(method) + closing_line(),
        "",
        [
            gate_request(REPO, "false"),
            (("pr", "merge", "7", "--repo", REPO, flag, "--match-head-commit", SHA, "--body-file", "-"), "Closes #3\n\nWork-Item: #3"),
            gate_request(REPO, "true"),
            (("api", "-X", "POST", "repos/acme/widgets/issues/7/comments", "-f", log_body(method=method)), None),
        ],
    )


def gadgets_requests(outcome, restored, *between_restore_and_comment):
    findings = 'declared loosening (overnightworks/songmaker#1345 of 2026-10-20): mandatory job "B" removed - reason: ruled'
    return [
        gate_request("acme/gadgets", "false"),
        (("pr", "merge", "12", "--repo", "acme/gadgets", "--merge", "--match-head-commit", SHA, "--body-file", "-"), "Closes #3\n\nWork-Item: #3"),
        gate_request("acme/gadgets", "true"),
        *between_restore_and_comment,
        (
            ("api", "-X", "POST", "repos/acme/gadgets/issues/12/comments", "-f", log_body(outcome=outcome, restored=restored, findings=findings)),
            None,
        ),
    ]


@pytest.mark.parametrize(
    ("gh_kwargs", "code", "out", "err", "requests"),
    [
        (
            {},
            0,
            (
                "  ✓ quality_gate  off            acme/gadgets\n  ✓ #12           merged         merge commit\n  ✓ quality_gate  on\n"
                "  ✓ log comment   posted         on #12\n✓ merged #12 · quality_gate on again · <1s\n"
            ),
            "",
            gadgets_requests("merge", "on"),
        ),
        (
            {"fail_on": ["restore", "merge", "comment"]},
            2,
            (
                "  ✓ quality_gate  off            acme/gadgets\n  ✗ quality_gate  not restored   restore broke\n"
                "  ✗ #12           not merged     merge broke\n  ✗ log comment   not posted     comment broke\n"
            ),
            (
                "error: quality_gate MAY STILL BE OFF on acme/gadgets · #12 is not merged · no log comment on #12\nnext:\n"
                f"    echo '{gate_body('true')}' | gh api -X PATCH repos/acme/gadgets/properties/values --input -\n"
                "    quality emergency-merge acme/gadgets 12\n"
                "    gh api -X POST repos/acme/gadgets/issues/12/comments -f 'body=Emergency door used by @felix at 2026-11-06T12:00:00+00:00: "
                'declared loosening (overnightworks/songmaker#1345 of 2026-10-20): mandatory job "B" removed - reason: ruled; '
                "merges with a merge commit; quality_gate off → merge failed → NOT restored, it may still be off.'\n"
            ),
            gadgets_requests(merge_failed(), not_restored(), (("api", "repos/acme/gadgets/pulls/12"), None)),
        ),
    ],
    ids=["merged", "the restore, the merge and the comment fail"],
)
def test_the_attempt_names_the_pull_request_asked_for(gh_kwargs, code, out, err, requests, capsys):
    gh = FakeGh(checks=SONAR_GREEN, base_config=TWO_JOBS, head_config=ONE_JOB + declaring(B_REMOVED), **gh_kwargs)
    actual = after_the_question(gh, capsys, repo="acme/gadgets", pull_request="12")
    assert actual == (code, out, err)
    assert requests_of_the_attempt(gh, "acme/gadgets") == requests


def test_the_closing_line_counts_the_seconds_from_the_answer(capsys):
    gh = FakeGh()
    seconds = [0.0]

    def merging(arguments, stdin=None):
        if list(arguments)[:2] == ["pr", "merge"]:
            seconds[0] = 114.0
        return gh(arguments, stdin)

    def asking():
        seconds[0] = 100.0
        capsys.readouterr()

    drive(merging, when_asked=asking, clock=lambda: seconds[0])
    actual = capsys.readouterr().out
    assert actual == (
        "  ✓ quality_gate  off            acme/widgets\n"
        "  ✓ #7            merged         merge commit\n"
        "  ✓ quality_gate  on\n"
        "  ✓ log comment   posted         on #7\n"
        "✓ merged #7 · quality_gate on again · 14s\n"
    )


@pytest.mark.parametrize(
    ("gh_kwargs", "code", "out", "err", "sequence", "comment"),
    [
        (
            {"fail_on": ["gate"]}, 2,
            off_failed_row("gate broke") + on() + posted(),
            rerun_pair(not_merged()), all_four_but("gate false", "merge"), log_body("not switched off", "no merge"),
        ),
        (
            {"interrupt_on": ["gate"]}, 2,
            "\n" + off_failed_row("interrupted") + on() + posted(),
            rerun_pair(not_merged()), all_four_but("gate false", "merge"), log_body("not switched off", "no merge"),
        ),
        (
            {"fail_on": ["merge"]}, 2,
            off() + on() + merge_failed_row() + posted(),
            rerun_pair(not_merged()), all_four_but("merge"), log_body(outcome=merge_failed()),
        ),
        (
            {"fail_on": ["merge"], "merge_lands": True}, 0,
            off() + on() + merged() + posted() + closing_line(),
            "", all_four_but("merge"), log_body(),
        ),
        (
            {"interrupt_on": ["merge"]}, 2,
            off() + "\n" + on() + unknown_row("interrupted") + posted(),
            unknown_pair(), all_four_but("merge"), log_body(outcome=merge_state_unknown()),
        ),
        (
            {"interrupt_on": ["merge"], "merge_lands": True}, 0,
            off() + "\n" + on() + merged() + posted() + closing_line(),
            "", all_four_but("merge"), log_body(),
        ),
        (
            {"fail_on": ["merge", "read back"]}, 2,
            off() + on() + unknown_row(merge_broke()) + posted(),
            unknown_pair(), all_four_but("merge"), log_body(outcome=merge_state_unknown()),
        ),
        (
            {"fail_on": ["merge"], "interrupt_on": ["read back"]}, 2,
            off() + on() + "\n" + unknown_row(merge_broke()) + posted(),
            unknown_pair(), all_four_but("merge"), log_body(outcome=merge_state_unknown()),
        ),
        (
            {"fail_on": ["merge", "restore"], "raw_answers": {"read back": "[]"}}, 2,
            off() + restore_failed_row() + unknown_row(merge_broke()) + posted(),
            unrestored_unknown_pair(), all_four_but("merge", "gate true"),
            unrestored_log_body(merge_state_unknown()),
        ),
        (
            {"fail_on": ["restore"]}, 2,
            off() + merged() + restore_failed_row() + posted(),
            merged_restore_pair(),
            all_four_but("gate true"), unrestored_log_body(),
        ),
        (
            {"interrupt_on": ["restore"]}, 2,
            off() + merged() + "\n" + restore_failed_row("interrupted while switching it on") + posted(),
            merged_restore_pair(),
            all_four_but("gate true"), unrestored_log_body(),
        ),
        (
            {"fail_on": ["restore", "merge"]}, 2,
            off() + restore_failed_row() + merge_failed_row() + posted(),
            ("error: quality_gate MAY STILL BE OFF on acme/widgets · #7 is not merged\nnext:\n" + restore_line() + "    quality emergency-merge acme/widgets 7\n"),
            all_four_but("merge", "gate true"), unrestored_log_body(merge_failed()),
        ),
    ],
    ids=[
        "the switch off fails", "interrupted while switching off", "the merge fails", "the merge fails but landed", "the merge is interrupted",
        "interrupted, but landed", "the read back fails", "the read back is interrupted", "the read back is no object", "the restore fails",
        "the restore is interrupted", "the restore and the merge fail",
    ],
)
def test_each_way_the_attempt_ends_is_its_rows_and_the_pair(gh_kwargs, code, out, err, sequence, comment, capsys):
    gh = FakeGh(**gh_kwargs)
    assert after_the_question(gh, capsys) == (code, out, err)
    assert (gh.sequence, gh.comments) == (sequence, [comment])


@pytest.mark.parametrize(
    ("gh_kwargs", "out", "err"),
    [
        (
            {"fail_on": ["comment"]},
            off() + merged() + on() + comment_failed_row(),
            merged_comment_pair(),
        ),
        (
            {"interrupt_on": ["comment"]},
            off() + merged() + on() + "\n" + comment_failed_row("interrupted"),
            merged_comment_pair(),
        ),
        (
            {"fail_on": ["restore", "comment"]},
            off() + merged() + restore_failed_row() + comment_failed_row(),
            (
                "error: quality_gate MAY STILL BE OFF on acme/widgets · #7 is merged · no log comment on #7\nnext:\n"
                + restore_line()
                + comment_line(restored=not_restored())
            ),
        ),
    ],
    ids=["it fails", "it is interrupted", "and the restore fails"],
)
def test_a_log_comment_not_posted_is_its_cross_row_and_its_command(gh_kwargs, out, err, capsys):
    gh = FakeGh(**gh_kwargs)
    assert after_the_question(gh, capsys) == (2, out, err)
    (comment_argv,) = [argv for argv, _ in gh.requests if argv[-2:-1] == ("-f",)]
    assert shlex.split(err.splitlines()[-1]) == ["gh", *comment_argv]


def test_the_restore_command_repeats_the_request_that_failed(capsys):
    gh = FakeGh(fail_on=["restore"])
    _, _, err = after_the_question(gh, capsys)
    argv, stdin = next(request for request in gh.requests if request[1] is not None and '"true"' in request[1])
    assert shlex.split(err.splitlines()[2]) == ["echo", stdin, "|", "gh", *argv]


@pytest.mark.parametrize(
    "gh_kwargs", [{"fail_on": ["restore"]}, {"fail_on": ["restore", "merge"]}, {"fail_on": ["restore", "merge", "read back"]}],
    ids=["merged", "read back", "read back fails"],
)
def test_the_gate_row_stands_before_any_further_request(gh_kwargs, capsys):
    gh = FakeGh(**gh_kwargs)
    shown, requests = "", []

    def watching(arguments, stdin=None):
        nonlocal shown
        shown += capsys.readouterr().out
        requests.append((gh.moment_of(arguments, stdin), shown))
        return gh(arguments, stdin)

    drive(watching)
    not_restored = restore_failed_row()
    later = requests[[moment for moment, _ in requests].index("restore") + 1 :]
    assert later[0][1].endswith(not_restored)
    assert all(not_restored in text for _, text in later)


@pytest.mark.parametrize("draft", [False, True], ids=["at the first row", "at a stopped row"])
def test_a_stdout_that_refuses_a_row_stops_the_door_before_the_question(draft, capsys, monkeypatch):
    gh = FakeGh(draft=draft)
    with open("/dev/full", "w", buffering=1) as full:
        monkeypatch.setattr(sys, "stdout", full)
        code, answers = drive(gh)
    assert (code, capsys.readouterr().err, answers, gh.calls) == (
        2,
        (
            "error: stdout refused a row · nothing changed\n"
            "next: send stdout where it can be written, then run quality emergency-merge acme/widgets 7 again\n"
        ),
        [],
        [("api", "repos/acme/widgets"), ("api", "repos/acme/widgets/pulls/7")],
    )


def test_a_closed_stdout_takes_the_rows_and_the_door_still_asks(capsys, monkeypatch):
    gh = FakeGh()
    monkeypatch.setattr(sys, "stdout", None)
    code, _ = drive(gh)
    assert (code, capsys.readouterr().err, gh.sequence) == (0, prompt(), all_four())


class RefusingGh(FakeGh):
    """A `FakeGh` that points the descriptor of `stream` at a full device at the first request of `moment`."""

    def __init__(self, moment, stream, **arguments):
        super().__init__(**arguments)
        self.moment, self.stream, self.refusing = moment, stream, False

    def __call__(self, arguments, stdin=None):
        if not self.refusing and self.moment_of(arguments, stdin) == self.moment:
            self.refusing = True
            with open("/dev/full", "wb") as full:
                os.dup2(full.fileno(), self.stream.fileno())
        return super().__call__(arguments, stdin)


@pytest.mark.parametrize(
    ("moment", "gh_kwargs", "code", "written", "err", "sequence"),
    [
        ("gate", {}, 0, "", "", all_four()),
        ("gate", {"fail_on": ["gate"]}, 2, "", rerun_pair(not_merged()), all_four_but("gate false", "merge")),
        ("merge", {}, 0, off(), "", all_four()),
        ("merge", {"interrupt_on": ["merge"]}, 2, off(), unknown_pair(), all_four_but("merge")),
        ("restore", {}, 0, off() + merged(), "", all_four()),
        (
            "restore", {"fail_on": ["restore"]}, 2, off() + merged(),
            merged_restore_pair(), all_four_but("gate true"),
        ),
        ("read back", {"fail_on": ["merge"], "merge_lands": True}, 0, off() + on(), "", all_four_but("merge")),
        ("comment", {}, 0, off() + merged() + on(), "", all_four()),
        (
            "comment", {"fail_on": ["comment"]}, 2, off() + merged() + on(),
            merged_comment_pair(), all_four_but("comment"),
        ),
    ],
    ids=[
        "at the first row", "off failed", "merged", "at an interrupted line", "on", "at the not-restored row", "the read back", "posted",
        "not posted",
    ],
)
def test_a_stdout_that_refuses_a_row_after_the_answer_leaves_the_attempt_whole(moment, gh_kwargs, code, written, err, sequence, capsys, monkeypatch, tmp_path):
    shown = tmp_path / "stdout"
    with shown.open("w", buffering=1) as stream:
        monkeypatch.setattr(sys, "stdout", stream)
        gh = RefusingGh(moment, stream, **gh_kwargs)
        result, _ = drive(gh)
    actual = (result, shown.read_text(), capsys.readouterr().err, gh.sequence)
    assert actual == (code, rows_before_the_prompt() + written, prompt() + err, sequence)
