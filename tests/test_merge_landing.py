import pytest

from tests.support.check import assert_every_line_starts_with_the_door
from tests.support.merge import FakeGh, changed_nothing, drive

REFUSED = "emergency-merge: refused - the pull request body"
NO_LINE = f"{REFUSED} carries no Work-Item: or No-Item: lines outside code blocks; the merge commit ends with exactly one"
TWO_LINES = f"{REFUSED} carries 2 Work-Item: or No-Item: lines outside code blocks; the merge commit ends with exactly one"


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("Work-Item: #9\n\nCloses #9\n\nWhy.", "Closes #9\n\nWhy.\n\nWork-Item: #9"),
        ("Why.\n\nWork-Item: #9\n\nCloses #9", "Why.\n\nCloses #9\n\nWork-Item: #9"),
        ("Why.\nWork-Item: #9\nCloses #9", "Why.\nCloses #9\n\nWork-Item: #9"),
        ("Work-Item: #9", "Work-Item: #9"),
        ("Work-Item: acme/widgets#9\n\nCloses #9", "Closes #9\n\nWork-Item: #9"),
        ("no-item:  FIX\t\n\nWhy.", "Why.\n\nNo-Item: fix"),
        ("```text\nWork-Item: #1\n```\nWork-Item: #9", "```text\nWork-Item: #1\n```\n\nWork-Item: #9"),
        ("````\n```\nWork-Item: #1\n````\nWork-Item: #9", "````\n```\nWork-Item: #1\n````\n\nWork-Item: #9"),
        ("~~~\n```\nNo-Item: docs\n~~~\nNo-Item: fix", "~~~\n```\nNo-Item: docs\n~~~\n\nNo-Item: fix"),
        (
            "```\n```text\nWork-Item: #1\n```\nWork-Item: #9",
            "```\n```text\nWork-Item: #1\n```\n\nWork-Item: #9",
        ),
        ("Work-Item: #9\n\n```\nWork-Item: #1", "```\nWork-Item: #1\n\nWork-Item: #9"),
        (
            "Why.\n\nWork-Item: #9\nCo-Authored-By: A <a@example.invalid>",
            "Why.\n\nCo-Authored-By: A <a@example.invalid>\n\nWork-Item: #9",
        ),
    ],
    ids=[
        "work item first", "own paragraph in the middle", "shared paragraph", "the line alone", "this repository named",
        "key and kind in any case", "a fenced example beside the live line", "a shorter run inside a longer fence",
        "a tilde fence a backtick run does not close", "a closing run with an info string closes nothing",
        "a fence left open runs to the end", "a body ending in a trailer paragraph",
    ],
)
def test_the_merge_commit_ends_with_the_bodys_one_classification_line(body, message):
    gh = FakeGh(body=body)
    assert (drive(gh)[0], gh.merge_messages) == (0, [message])
    assert gh.merge_calls[0][-2:] == ("--body-file", "-")


def test_a_squash_ends_with_the_same_line():
    gh = FakeGh(body="Work-Item: #9\n\nWhy.", settings={"allow_merge_commit": False, "allow_squash_merge": True, "allow_rebase_merge": False})
    assert (drive(gh)[0], gh.merge_messages) == (0, ["Why.\n\nWork-Item: #9"])
    assert gh.merge_calls[0][5:] == ("--squash", "--match-head-commit", "a" * 40, "--body-file", "-")


@pytest.mark.parametrize(
    ("body", "line"),
    [
        ("", NO_LINE),
        (None, NO_LINE),
        ("```\nWork-Item: #9\n```", NO_LINE),
        ("  Work-Item: #9", NO_LINE),
        ("Work-Item: #9\nWork-Item: #8", TWO_LINES),
        ("Work-Item: #9\nNo-Item: fix", TWO_LINES),
        ("Work-Item: 9", f"{REFUSED} carries Work-Item: 9; a work item reads OWNER/REPO#n or #n"),
        ("No-Item: chore", f"{REFUSED} carries No-Item: chore; an issue-less pull request is deps, docs, fix"),
        ("Work-Item: acme/other#9", f"{REFUSED} names work item acme/other#9 of another repository, not of acme/widgets"),
        ("Work-Item: #9\x1b[8m‮", f"{REFUSED} carries Work-Item: #9\\x1b[8m\\u202e; a work item reads OWNER/REPO#n or #n"),
    ],
    ids=[
        "empty", "null", "only fenced", "indented", "two work items", "a work item and a no item", "malformed work item",
        "unknown kind", "another repository", "a control character in the value",
    ],
)
def test_a_body_without_one_valid_line_keeps_the_door_shut(body, line, capsys):
    gh = FakeGh(body=body)
    code, answers = drive(gh)
    lines = capsys.readouterr().out.splitlines()
    assert (code, line in lines, answers) == (1, True, [])
    assert_every_line_starts_with_the_door(lines)
    changed_nothing(gh)


def test_a_body_that_is_no_text_cannot_be_judged(capsys):
    gh = FakeGh(body=5)
    assert drive(gh)[0] == 2
    assert capsys.readouterr().out.splitlines() == [
        "emergency-merge: cannot judge - GitHub's answer for the pull request holds no body text; nothing was changed"
    ]
    changed_nothing(gh)


@pytest.mark.parametrize(
    ("body", "line"), [("Work-Item: #9", "Work-Item: #9"), ("No-Item: fix", "No-Item: fix")], ids=["a work item", "no item"]
)
def test_the_operator_reads_the_merge_commits_last_line_before_ja(body, line, capsys):
    shown = []
    drive(FakeGh(body=body), when_asked=lambda: shown.append(capsys.readouterr().out))
    assert shown[0].endswith(f"merges with a merge commit\nthe merge commit ends with {line}\n")


def test_the_merge_commit_ends_with_the_judged_body_not_a_later_one():
    gh = FakeGh(body="Work-Item: #9", later_body="Work-Item: #8")
    drive(gh)
    assert gh.merge_messages == ["Work-Item: #9"]
