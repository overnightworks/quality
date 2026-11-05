import pytest

from tests.support.check import assert_every_line_starts_with_the_door
from tests.support.merge import NO_BODY, REPO, SHA, FakeGh, changed_nothing, drive


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("Work-Item: #9\n\nCloses #9\n\nWhy.", "Closes #9\n\nWhy.\n\nWork-Item: #9"),
        ("Why.\n\nWork-Item: #9\n\nCloses #9", "Why.\n\nCloses #9\n\nWork-Item: #9"),
        ("Why.\nWork-Item: #9\nCloses #9", "Why.\nCloses #9\n\nWork-Item: #9"),
        ("Work-Item: #9", "Work-Item: #9"),
        ("Why.\nWork-Item: #9\n\nCloses #9", "Why.\n\nCloses #9\n\nWork-Item: #9"),
        ("Why.\n\nWork-Item: #9", "Why.\n\nWork-Item: #9"),
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
        ("```\nWork-Item: #1\n````\nWork-Item: #9", "```\nWork-Item: #1\n````\n\nWork-Item: #9"),
        ("   ```\nWork-Item: #1\n```\nWork-Item: #9", "```\nWork-Item: #1\n```\n\nWork-Item: #9"),
        ("```\nWork-Item: #1\n   ```\nWork-Item: #9", "```\nWork-Item: #1\n   ```\n\nWork-Item: #9"),
        ("```\nWork-Item: #1\n```  \t\nWork-Item: #9", "```\nWork-Item: #1\n```\n\nWork-Item: #9"),
        ("``\nWork-Item: #9", "``\n\nWork-Item: #9"),
        ("Why.\r\nWork-Item: #9\r\nCloses #9", "Why.\nCloses #9\n\nWork-Item: #9"),
        ("Why.\n \t\nWork-Item: #9\n\nCloses #9", "Why.\n \t\nCloses #9\n\nWork-Item: #9"),
        ("Why.\n\nWork-Item: #9\n \t\nCloses #9", "Why.\n\nCloses #9\n\nWork-Item: #9"),
        (" Why. \nWork-Item: #9", "Why.\n\nWork-Item: #9"),
        ("Work-Item: #12", "Work-Item: #12"),
        ("Work-Item: acme/widgets#123", "Work-Item: #123"),
        ("No-Item: docs", "No-Item: docs"),
        ("No-Item: deps", "No-Item: deps"),
        (
            "Why.\n\nWork-Item: #9\nCo-Authored-By: A <a@example.invalid>",
            "Why.\n\nCo-Authored-By: A <a@example.invalid>\n\nWork-Item: #9",
        ),
    ],
    ids=[
        "work item first", "own paragraph in the middle", "shared paragraph", "the line alone", "the line ends its paragraph", "the line last after a blank line", "this repository named",
        "key and kind in any case", "a fenced example beside the live line", "a shorter run inside a longer fence",
        "a tilde fence a backtick run does not close", "a closing run with an info string closes nothing",
        "a fence left open runs to the end", "a longer closing run closes", "a fence opened three spaces in", "a fence closed three spaces in",
        "a closing run followed by blanks closes", "a two character run opens no fence", "carriage returns end lines like line feeds",
        "a whitespace-only line before the line counts as blank", "a whitespace-only line after the line counts as blank", "outer spaces of the rest are dropped",
        "a multi-digit item", "a multi-digit item of this repository", "no item docs", "no item deps", "a body ending in a trailer paragraph",
    ],
)
def test_the_merge_commit_ends_with_the_bodys_one_classification_line(body, message):
    gh = FakeGh(body=body)
    assert (drive(gh)[0], gh.merge_messages) == (0, [message])
    assert gh.merge_calls[0][-2:] == ("--body-file", "-")


def test_a_squash_ends_with_the_same_line():
    gh = FakeGh(body="Work-Item: #9\n\nWhy.", settings={"allow_merge_commit": False, "allow_squash_merge": True, "allow_rebase_merge": False})
    assert (drive(gh)[0], gh.merge_messages) == (0, ["Why.\n\nWork-Item: #9"])
    assert gh.merge_calls[0][5:] == ("--squash", "--match-head-commit", SHA, "--body-file", "-")


REFUSED_NO_LINE = "emergency-merge: refused - the pull request body carries no Work-Item: or No-Item: lines outside code blocks; the merge commit ends with exactly one"
REFUSED_TWO_LINES = "emergency-merge: refused - the pull request body carries 2 Work-Item: or No-Item: lines outside code blocks; the merge commit ends with exactly one"


@pytest.mark.parametrize(
    ("body", "line"),
    [
        ("", REFUSED_NO_LINE),
        (None, REFUSED_NO_LINE),
        ("```\nWork-Item: #9\n```", REFUSED_NO_LINE),
        ("  Work-Item: #9", REFUSED_NO_LINE),
        ("Work-Item: #9\nWork-Item: #8", REFUSED_TWO_LINES),
        ("Work-Item: #9\nNo-Item: fix", REFUSED_TWO_LINES),
        ("Work-Item:\nWork-Item: #9", REFUSED_TWO_LINES),
        ("Work-Item: 9", "emergency-merge: refused - the pull request body carries Work-Item: 9; a work item reads OWNER/REPO#n or #n"),
        ("Work-Item: #9\N{NO-BREAK SPACE}", "emergency-merge: refused - the pull request body carries Work-Item: #9\N{NO-BREAK SPACE}; a work item reads OWNER/REPO#n or #n"),
        ("No-Item: chore", "emergency-merge: refused - the pull request body carries No-Item: chore; an issue-less pull request is deps, docs, fix"),
        ("Work-Item: acme/other#9", "emergency-merge: refused - the pull request body names work item acme/other#9 of another repository, not of acme/widgets"),
        ("Work-Item: #9\x1b[8m\N{RIGHT-TO-LEFT OVERRIDE}", "emergency-merge: refused - the pull request body carries Work-Item: #9\\x1b[8m\\u202e; a work item reads OWNER/REPO#n or #n"),
    ],
    ids=[
        "empty", "null", "only fenced", "indented", "two work items", "a work item and a no item", "an empty value counts as a line", "malformed work item",
        "a no-break space is no trimmed whitespace", "unknown kind", "another repository", "a control character in the value",
    ],
)
def test_a_body_without_one_valid_line_keeps_the_door_shut(body, line, capsys):
    gh = FakeGh(body=body)
    code, answers = drive(gh)
    output = capsys.readouterr()
    assert (code, output.out, output.err, answers) == (1, f"{line}\n", "", [])
    assert_every_line_starts_with_the_door(output.out.splitlines())
    changed_nothing(gh)


@pytest.mark.parametrize(
    ("body", "line"),
    [
        (5, "GitHub's answer for the pull request holds no body text"),
        (False, "GitHub's answer for the pull request holds no body text"),
        (0, "GitHub's answer for the pull request holds no body text"),
        ([], "GitHub's answer for the pull request holds no body text"),
        ({}, "GitHub's answer for the pull request holds no body text"),
        (NO_BODY, "'body'"),
    ],
    ids=["a number", "false", "zero", "an empty list", "an empty object", "no body field"],
)
def test_a_body_that_is_no_text_cannot_be_judged(body, line, capsys):
    gh = FakeGh(body=body)
    assert drive(gh)[0] == 2
    output = capsys.readouterr()
    assert (output.out, output.err) == (f"emergency-merge: cannot judge - {line}; nothing was changed\n", "")
    changed_nothing(gh)


@pytest.mark.parametrize(
    ("body", "line"), [("Work-Item: #9\n\nWhy.", "Work-Item: #9"), ("Why.\n\nNo-Item: fix", "No-Item: fix")], ids=["a work item", "no item"]
)
def test_the_operator_reads_the_merge_commits_last_line_before_ja(body, line, capsys):
    shown = []
    drive(FakeGh(body=body), when_asked=lambda: shown.append(capsys.readouterr()))
    assert (shown[0].out, shown[0].err) == (
        f"{REPO} #7 head {SHA}: 4 legacy finding(s)\nmerges with a merge commit\nthe merge commit ends with {line}\n",
        "",
    )


def test_the_merge_commit_ends_with_the_judged_body_not_a_later_one():
    gh = FakeGh(body="Work-Item: #9", later_body="Work-Item: #8")
    drive(gh)
    assert gh.merge_messages == ["Work-Item: #9"]
