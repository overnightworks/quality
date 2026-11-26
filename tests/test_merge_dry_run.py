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
    consenting,
    declaring,
    door_ports,
    drive,
    gate_workflow,
    loosened,
    run,
)


def declared_loosening(**consent):
    return {
        "checks": SONAR_GREEN, "base_config": TWO_JOBS, "head_config": ONE_JOB + declaring(B_REMOVED),
        "reviews": consenting(declared=(loosened(B_REMOVED),), legacy_branch=None, **consent),
    }


def writes(gh):
    return [argv for argv in gh.calls if "-X" in argv]


@pytest.mark.parametrize("gh_kwargs", [{}, declared_loosening()], ids=["legacy findings", "a declared loosening"])
def test_a_dry_run_shows_the_rows_and_its_line_and_changes_nothing(gh_kwargs, capsys):
    """MERGE-23, MERGE-25, MERGE-27: a dry run shows the rows with the consent and its line, and sends no request that
    changes anything."""
    gh = FakeGh(**gh_kwargs)
    code, out, err = run(gh, capsys, words=["--dry-run"])
    assert (code, out.endswith("  ✓ consent       given          review 4101 at aaaaaaa\n~ dry run · would switch quality_gate off, merge #7, switch it on · nothing changed\n"), err, gh.sequence) == (0, True, "", [])
    assert writes(gh) == []


def test_a_dry_run_shows_the_release_row_first(capsys):
    """PIN-42: a dry run shows the release row first."""
    _, out, _ = run(FakeGh(), capsys, words=["--dry-run"])
    assert out.startswith(RELEASE_ROW)


def test_a_dry_run_re_executes_with_its_dry_run_word(capsys):
    """PIN-41: a dry run re-executes into the pinned release with its own words."""
    port = ReexecutionPort()
    gh = FakeGh(workflows={MERGE_SHA: {"ci.yml": gate_workflow("v0.10.1", "7f" * 20)}})
    ports = door_ports(reexecute=port)
    with pytest.raises(Reexecuted):
        drive(gh, words=["--dry-run"], ports=ports)
    assert [words for _, _, words in port.asked] == [["merge", "acme/widgets#7", "--dry-run"]]


def test_a_dry_run_reads_the_pull_request_asked_for(capsys):
    """MERGE-25, MERGE-27: a dry run names the pull request asked for and sends no request that changes anything."""
    gh = FakeGh(**declared_loosening(repository="acme/gadgets", pull_request=12))
    code, out, _ = run(gh, capsys, repo="acme/gadgets", pull_request="12", words=["--dry-run"])
    login = ("api", "user", "--jq", ".login")
    named = [[word for word in argv if word.startswith(("repos/", "orgs/"))] for argv in gh.calls if argv != login]
    assert (code, out.splitlines()[-1]) == (0, "~ dry run · would switch quality_gate off, merge #12, switch it on · nothing changed")
    assert gh.calls.count(("api", "repos/acme/gadgets/pulls/12")) == 1
    assert [word for argv in gh.calls for word in argv if "/pulls/" in word and not word.startswith("repos/acme/gadgets/pulls/12")] == []
    assert [len(words) for words in named] == [1] * len(named)
    assert [word for (word,) in named if word != "repos/acme/gadgets" and not word.startswith(("repos/acme/gadgets/", "orgs/acme/"))] == []
    assert gh.calls.count(login) == 1
    assert writes(gh) == []


@pytest.mark.parametrize(
    ("gh_kwargs", "code"),
    [
        ({"checks": {**LEGACY_RED, "test": "failure"}}, 1),
        ({"draft": True}, 1),
        ({"fail_on": ["pull"]}, 2),
        ({"body": "a body without the line"}, 1),
        ({"behind_by": 3}, 1),
    ],
    ids=["another check red", "a draft", "gh down", "a refused landing line", "a head behind the base tip"],
)
def test_a_dry_run_refuses_where_the_door_refuses(gh_kwargs, code, capsys):
    """MERGE-24: a dry run refuses where the door refuses."""
    door = run(FakeGh(**gh_kwargs), capsys)
    dry_gh = FakeGh(**gh_kwargs)
    dry_run = run(dry_gh, capsys, words=["--dry-run"])
    assert dry_run == door
    assert (dry_run[0], writes(dry_gh)) == (code, [])


@pytest.mark.parametrize("words", [[], ["--dry-run"]], ids=["a merge", "a dry run"])
def test_v_adds_nothing_to_the_door(words, capsys):
    """MERGE-28: -v adds nothing to a merge or a dry run."""
    plain_gh, verbose_gh = FakeGh(), FakeGh()
    plain = run(plain_gh, capsys, words=words)
    verbose = run(verbose_gh, capsys, words=[*words, "-v"])
    assert verbose + (verbose_gh.requests,) == plain + (plain_gh.requests,)


@pytest.mark.parametrize(
    ("gh_kwargs", "pattern"),
    [
        ({"reviews": []}, r"next: the operator leaves the text above as his review at \w+, then (.+) runs again"),
        ({"fail_on": ["merge"]}, r"next: (.+)"),
        ({"later_compare": ("e" * 40, 0)}, r"next: (.+)"),
        ({"fail_on": ["pull"]}, r"next: fix what the ✗ row names, then run (.+) again"),
        (
            {"body": "a body without the line"},
            r"next: give the pull request body exactly one Work-Item: or No-Item: line outside code blocks, then run (.+) again",
        ),
        ({"merge_config": "[secrets]\nignore = []\n"}, r"next: push the fix to the pull request's branch, then run (.+) again"),
    ],
    ids=["no consent", "not merged", "a base that moved", "not judged", "a refused landing line", "a config refused at the merge commit"],
)
def test_a_next_step_that_names_the_door_again_names_the_pull_request_asked_for(gh_kwargs, pattern, capsys):
    """MERGE-20, MERGE-68, MERGE-121: a next step that names the door again names the pull request asked for."""
    gh = FakeGh(**{**declared_loosening(repository="acme/gadgets", pull_request=12), **gh_kwargs})
    _, _, err = run(gh, capsys, repo="acme/gadgets", pull_request="12")
    (step,) = [line for line in err.splitlines() if line.startswith("next: ")]
    command = shlex.split(re.fullmatch(pattern, step)[1])
    arguments = cli.read_invocation(command[1:])
    assert (command[0], arguments.command, arguments.pull_request, arguments.dry_run) == (
        "quality", "merge", cli.PullRequestName("acme/gadgets", 12), False,
    )
