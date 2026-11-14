import pytest

from tests.support.check import assert_every_line_starts_with_the_door
from tests.support.door import RELEASE_ROW
from tests.support.merge import (
    ABSENT,
    SHA,
    FakeGh,
    base_row,
    changed_nothing,
    drive,
    landing_pair,
    not_judged_pair,
    prompt,
    pull_row,
)


def landing_sentence(count="no"):
    return f"the pull request body carries {count} Work-Item: or No-Item: lines outside code blocks; the merge commit ends with exactly one"


def no_body_text():
    return "GitHub's answer for the pull request holds no body text"


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
        ("~~~\nWork-Item: #1\n~~~~\nWork-Item: #9", "~~~\nWork-Item: #1\n~~~~\n\nWork-Item: #9"),
        ("``\nWork-Item: #9", "``\n\nWork-Item: #9"),
        ("~~\nWork-Item: #9", "~~\n\nWork-Item: #9"),
        ("    ```\nWork-Item: #9", "```\n\nWork-Item: #9"),
        ("Why.\r\nWork-Item: #9\r\nCloses #9", "Why.\nCloses #9\n\nWork-Item: #9"),
        ("Why.\n \t\nWork-Item: #9\n\nCloses #9", "Why.\n \t\nCloses #9\n\nWork-Item: #9"),
        ("Why.\n\nWork-Item: #9\n \t\nCloses #9", "Why.\n\nCloses #9\n\nWork-Item: #9"),
        (" Why. \nWork-Item: #9", "Why.\n\nWork-Item: #9"),
        ("Work-Item: #12", "Work-Item: #12"),
        ("Work-Item: acme/widgets#123", "Work-Item: #123"),
        ("No-Item: docs", "No-Item: docs"),
        ("No-Item: deps", "No-Item: deps"),
        ("@octocat please look.\nWork-Item: #9", "@octocat please look.\n\nWork-Item: #9"),
        (
            "Why.\n\nWork-Item: #9\nCo-Authored-By: A <a@example.invalid>",
            "Why.\n\nCo-Authored-By: A <a@example.invalid>\n\nWork-Item: #9",
        ),
    ],
    ids=[
        "work item first", "own paragraph in the middle", "shared paragraph", "the line alone", "the line ends its paragraph", "the line last after a blank line", "this repository named",
        "key and kind in any case", "a fenced example beside the live line", "a shorter run inside a longer fence",
        "a tilde fence a backtick run does not close", "a closing run with an info string closes nothing",
        "a fence never closed runs to the end", "a longer closing run closes", "a fence opened three spaces in",
        "a fence closed three spaces in",
        "a closing run followed by blanks closes", "a longer tilde run closes", "a two character run opens no fence", "a two character tilde run opens no fence", "a four space run opens no fence", "carriage returns end lines like line feeds",
        "a whitespace-only line before the line counts as blank", "a whitespace-only line after the line counts as blank", "outer spaces of the rest are dropped",
        "a multi-digit item", "a multi-digit item of this repository", "no item docs", "no item deps", "a message starting with an at sign", "a body ending in a trailer paragraph",
    ],
)
def test_the_merge_commit_ends_with_the_bodys_one_classification_line(body, message):
    """MERGE-30, MERGE-64, MERGE-65, MERGE-66, MERGE-69: the merge commit ends with the body's one line outside the fences, in its
    trailer form, after a blank line."""
    gh = FakeGh(body=body)
    assert (drive(gh)[0], gh.merge_messages) == (0, [message])
    assert "-F" not in gh.merge_calls[0]


def test_a_squash_ends_with_the_same_line():
    """MERGE-69: a squash ends with the same line."""
    gh = FakeGh(body="Work-Item: #9\n\nWhy.", settings={"allow_merge_commit": False, "allow_squash_merge": True, "allow_rebase_merge": False})
    assert (drive(gh)[0], gh.merge_messages) == (0, ["Why.\n\nWork-Item: #9"])
    assert [(body["merge_method"], body["sha"]) for body in gh.merge_bodies] == [("squash", SHA)]


@pytest.mark.parametrize(
    ("body", "line"),
    [
        ("", landing_sentence()),
        (None, landing_sentence()),
        ("```\nWork-Item: #9\n```", landing_sentence()),
        ("  Work-Item: #9", landing_sentence()),
        ("```\nWork-Item: #1\n    ```\nWork-Item: #9", landing_sentence()),
        ("```\nWork-Item: #1\n```\N{NO-BREAK SPACE}\nWork-Item: #9", landing_sentence()),
        ("~~~~\nWork-Item: #1\n~~~\nWork-Item: #9", landing_sentence()),
        ("Work-Item: #9\nWork-Item: #8", landing_sentence("2")),
        ("Work-Item: #9\nNo-Item: fix", landing_sentence("2")),
        ("Work-Item:\nWork-Item: #9", landing_sentence("2")),
        ("Work-Item: 9", "the pull request body carries Work-Item: 9; a work item reads OWNER/REPO#n or #n"),
        ("Work-Item: #9\N{NO-BREAK SPACE}", "the pull request body carries Work-Item: #9\N{NO-BREAK SPACE}; a work item reads OWNER/REPO#n or #n"),
        ("No-Item: chore", "the pull request body carries No-Item: chore; an issue-less pull request is deps, docs, fix"),
        ("Work-Item: acme/other#9", "the pull request body names work item acme/other#9 of another repository, not of acme/widgets"),
        ("Work-Item: #9\x1b[8m\N{RIGHT-TO-LEFT OVERRIDE}", "the pull request body carries Work-Item: #9\\x1b[8m\\u202e; a work item reads OWNER/REPO#n or #n"),
    ],
    ids=[
        "empty", "null", "only fenced", "indented", "a four space run closes nothing", "a closing run followed by a no-break space closes nothing", "a shorter tilde run closes nothing", "two work items", "a work item and a no item", "an empty value counts as a line", "malformed work item",
        "a no-break space is no trimmed whitespace", "unknown kind", "another repository", "a control character in the value",
    ],
)
def test_a_body_without_one_valid_line_keeps_the_door_shut(body, line, capsys):
    """MERGE-14, MERGE-22, MERGE-63, MERGE-64, MERGE-65, MERGE-66, MERGE-67, MERGE-68: a body without one valid line
    stops the door at its `! landing  refused` row under the pull request's row, with the reason and the pair, and
    changes nothing; the line's text cannot forge a line."""
    gh = FakeGh(body=body)
    code, answers = drive(gh)
    output = capsys.readouterr()
    assert (code, output.out, output.err, answers) == (1, f"{RELEASE_ROW}{pull_row()}  ! landing       refused        {line}\n", landing_pair(), [])
    assert_every_line_starts_with_the_door(output.out.splitlines())
    changed_nothing(gh)


@pytest.mark.parametrize(
    "body",
    [5, False, 0, [], {}],
    ids=["a number", "false", "zero", "an empty list", "an empty object"],
)
def test_a_body_that_is_no_text_cannot_be_judged(body, capsys):
    """MERGE-16: a body that is no text is not judged: its `✗ landing` row and the pair, nothing changed."""
    gh = FakeGh(body=body)
    code, answers = drive(gh)
    output = capsys.readouterr()
    assert (code, output.out, output.err, answers) == (2, f"{RELEASE_ROW}{pull_row()}  ✗ landing       not judged     {no_body_text()}\n", not_judged_pair(), [])
    changed_nothing(gh)


def test_a_pull_answer_without_a_body_field_cannot_be_judged(capsys):
    """MERGE-16: a pull answer without a body field is not judged: its `✗ landing` row and the pair."""
    gh = FakeGh(body=ABSENT)
    code, answers = drive(gh)
    output = capsys.readouterr()
    assert (code, output.out, output.err, answers) == (
        2, f"{RELEASE_ROW}{pull_row()}  ✗ landing       not judged     GitHub's answer holds no 'body'\n", not_judged_pair(), []
    )
    changed_nothing(gh)


@pytest.mark.parametrize(
    ("body", "row"),
    [
        ("Work-Item: #9\n\nWhy.", "work item      #9"),
        ("Why.\n\nNo-Item: fix", "no item        fix"),
        ("Work-Item: acme/widgets#12", "work item      #12"),
        ("No-Item: DOCS", "no item        docs"),
    ],
    ids=["a work item", "no item", "this repository named whole", "a kind in capitals"],
)
def test_the_last_row_before_the_question_is_what_the_merge_commit_ends_with(body, row, capsys):
    """MERGE-5: the last row before the question is what the merge commit ends with."""
    shown = []
    drive(FakeGh(body=body), when_asked=lambda: shown.append(capsys.readouterr()))
    assert (shown[0].out, shown[0].err) == (
        (f"{RELEASE_ROW}{pull_row()}{base_row()}  ✓ checks        ok             only quality / sonar and quality / verdict are red\n"
        f"  ✓ loosenings    none\n  ! sonar         4 legacy findings on main\n  · merge         merge commit   allowed on main\n  · landing       {row}\n"),
        prompt(),
    )


def test_the_merge_commit_ends_with_the_judged_body_not_a_later_one():
    """MERGE-70: the merge commit ends with the body read with the head, not a later one."""
    gh = FakeGh(body="Work-Item: #9", later_body="Work-Item: #8")
    drive(gh)
    assert gh.merge_messages == ["Work-Item: #9"]
