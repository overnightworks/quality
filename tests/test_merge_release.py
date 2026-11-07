"""The release `quality merge` judges at: the one its target's gate pins at the pull request's merge commit, named in
its first row; for quality itself the installed quality; a release it cannot take is the pair alone, nothing changed."""

import os

import pytest

from quality import checkout
from quality.gh import run_gh, workflow_texts_at
from quality.pin import RUN_AT_PIN_VARIABLE, ReleaseAtPin
from quality.report import CannotJudge, Release
from tests.support.door import RELEASE_ROW
from tests.support.merge import (
    BASE_SHA,
    MERGE_SHA,
    REPO,
    FakeGh,
    Reexecuted,
    ReexecutionPort,
    changed_nothing,
    door_ports,
    drive,
    gate_workflow,
    pull_row,
    running_at,
)

OTHER_COMMIT = "7f" * 20
OWN_GATE = "$/.github/workflows/gate.yml"
AGAIN = "next: fix what the error names, then run quality merge acme/widgets#7 again\n"
NO_GATE = (
    "error: no workflow of acme/widgets at the pull request's merge commit calls quality's gate.yml, so no release is pinned\n"
    "next: add the gate as quality's docs/gate.md describes\n"
)


def at_merge(**files):
    return {MERGE_SHA: files}


def door(capsys, gh, **arguments):
    """The door's exit, stdout and stderr, answered `no` at its question."""
    code, _ = drive(gh, typed="no\n", **arguments)
    printed = capsys.readouterr()
    return code, printed.out, printed.err


def listing_refused(what):
    return f"error: the workflow listing of acme/widgets at ddddddd {what}\n" + AGAIN


def workflow_reads(gh):
    return [call for call in gh.calls if any("/contents/.github/workflows" in argument for argument in call)]


def test_the_first_row_names_the_release_the_targets_gate_pins_at_the_merge_commit(capsys):
    code, out, _ = door(capsys, FakeGh())
    assert (code, out.startswith(RELEASE_ROW + pull_row())) == (1, True)


@pytest.mark.parametrize(
    ("gh_kwargs", "err"),
    [
        pytest.param(
            {"workflows": at_merge(**{"ci.yml": gate_workflow("v0.9.2", OTHER_COMMIT)})},
            "error: the gate pins quality v0.9.2, which has no quality merge\nnext: raise the gate pin to v0.10.0 or later\n",
            id="a pin below the floor",
        ),
        pytest.param({"workflows": {}}, NO_GATE, id="no workflow directory"),
        pytest.param({"workflows": at_merge(**{"ci.yml": "on: push\njobs:\n  build:\n    runs-on: ubuntu-latest\n"})}, NO_GATE, id="workflows that call no gate"),
        pytest.param({"workflows": at_merge(**{"ci.yml": gate_workflow(uses=OWN_GATE)})}, NO_GATE, id="an unrelated target's own gate.yml"),
        pytest.param(
            {"workflows": at_merge(**{"ci.yml": gate_workflow(uses=f"{checkout.GATE_REPOSITORY}/.github/workflows/gate.yml@main # v0.10.0")})},
            "error: the gate pin in .github/workflows/ci.yml is not @<commit sha> # v<tag>\n"
            "next: pin the gate as docs/gate.md shows: @<commit sha> # v<tag>\n",
            id="an unknown pin",
        ),
        pytest.param(
            {"listing": [{"name": "ci.yml", "type": "file"}, {"name": "gone.yml", "type": "file"}]},
            "error: cannot read .github/workflows/gone.yml, so the gate pin is unknown\nnext: fix .github/workflows/gone.yml\n",
            id="a listed workflow the read does not find",
        ),
        pytest.param(
            {"merge_sha": "main"}, "error: the merge commit gh names is not a commit sha\n" + AGAIN, id="a merge sha that is no commit",
        ),
        pytest.param(
            {"merge_sha": None},
            "error: GitHub has no merge commit for the pull request yet, so the release its gate ran at is unknown\n" + AGAIN,
            id="no merge commit",
        ),
        pytest.param({"fail_on": ("listing",)}, "error: cannot read the workflows of acme/widgets at ddddddd: listing broke\n" + AGAIN, id="a listing gh cannot read"),
        pytest.param({"fail_on": ("workflow",)}, "error: cannot read the workflows of acme/widgets at ddddddd: workflow broke\n" + AGAIN, id="a workflow gh cannot read"),
        pytest.param({"listing": {"name": "ci.yml"}}, listing_refused("is not a list of files"), id="not a list"),
        pytest.param({"listing": [{"type": "file"}]}, listing_refused("is not a list of files"), id="an entry without a name"),
        pytest.param(
            {"listing": [{"name": "ci.yml", "type": "symlink"}]}, listing_refused("holds the symlink ci.yml"), id="a workflow that is a symlink",
        ),
        *(
            pytest.param({"listing": [{"name": name, "type": "file"}]}, listing_refused("is not a list of files"), id=f"a file named {label}")
            for label, name in (
                ("with a slash", "a/b.yml"), ("with a backslash", "a\\b.yml"), ("with a percent sign", "a%2e.yml"),
                ("with a question mark", "a?ref=main.yml"), ("with a hash", "a#b.yml"), ("with a control character", "a\x07b.yml"),
                (".", "."), ("..", ".."), ("empty", ""), ("../x.yml", "../x.yml"), ("notes#1.md", "notes#1.md"),
            )
        ),
    ],
)
def test_a_release_the_door_cannot_take_is_the_pair_alone_and_nothing_changed(gh_kwargs, err, capsys):
    gh = FakeGh(**gh_kwargs)
    assert door(capsys, gh) == (2, "", err)
    changed_nothing(gh)


@pytest.fixture
def gh_failing_with(tmp_path, monkeypatch):
    def install(stderr):
        executable = tmp_path / "gh"
        executable.write_text(f"#!/bin/sh\necho '{stderr}' >&2\nexit 1\n")
        executable.chmod(0o755)
        monkeypatch.setenv("PATH", f"{tmp_path}{os.pathsep}{os.environ['PATH']}")

    return install


@pytest.mark.integration
@pytest.mark.parametrize(("repository", "sha"), [("acme/app404", "d" * 40), ("acme/widgets", "a404" + "d" * 36)], ids=["a name with 404", "a sha with 404"])
def test_a_listing_failure_that_is_no_404_is_cannot_read_even_when_its_request_names_404(repository, sha, gh_failing_with):
    gh_failing_with("HTTP 403: Forbidden")
    with pytest.raises(CannotJudge, match="cannot read the workflows of"):
        workflow_texts_at(run_gh, repository, sha)


@pytest.mark.integration
def test_a_listing_that_gh_answers_with_404_is_no_workflow_directory(gh_failing_with):
    gh_failing_with("gh: Not Found (HTTP 404)")
    assert workflow_texts_at(run_gh, "acme/widgets", "d" * 40) == {}


@pytest.mark.parametrize("merge_sha", ["main", None], ids=["a merge sha that is no commit", "no merge commit"])
def test_no_listing_is_read_without_a_merge_commit(merge_sha, capsys):
    gh = FakeGh(merge_sha=merge_sha)
    door(capsys, gh)
    assert workflow_reads(gh) == []


@pytest.mark.parametrize(
    ("listing", "skipped"),
    [
        pytest.param([{"name": "../sub", "type": "dir"}, {"name": "ci.yml", "type": "file"}], "../sub", id="a directory entry"),
        pytest.param([{"name": "README.md", "type": "file"}, {"name": "ci.yml", "type": "file"}], "README.md", id="a file that is no workflow"),
    ],
)
def test_an_entry_that_is_no_workflow_file_is_skipped_and_never_read(listing, skipped, capsys):
    gh = FakeGh(listing=listing)
    code, out, _ = door(capsys, gh)
    assert (code, out.startswith(RELEASE_ROW)) == (1, True)
    assert [call for call in workflow_reads(gh) if skipped in call[-1]] == []


def test_a_workflow_name_is_read_percent_encoded_at_the_merge_commit(capsys):
    gh = FakeGh(workflows=at_merge(**{"ci gate.yml": gate_workflow()}))
    _, out, _ = door(capsys, gh)
    assert out.startswith("  · release       v0.10.0        pinned in .github/workflows/ci gate.yml\n")
    assert workflow_reads(gh)[1][-1] == f"repos/{REPO}/contents/.github/workflows/ci%20gate.yml?ref={MERGE_SHA}"


@pytest.mark.parametrize(
    ("gh_kwargs", "repo"),
    [
        pytest.param({"workflows": at_merge(**{"ci.yml": gate_workflow("v0.10.1", OTHER_COMMIT)})}, REPO, id="another release"),
        pytest.param(
            {"workflows": {BASE_SHA: {"ci.yml": gate_workflow("v0.9.2", OTHER_COMMIT)}, **at_merge(**{"ci.yml": gate_workflow("v0.10.1", OTHER_COMMIT)})}},
            REPO, id="a pin-bump pull request",
        ),
        pytest.param(
            {"workflows": at_merge(**{"ci.yml": gate_workflow("v0.10.1", OTHER_COMMIT)})}, checkout.GATE_REPOSITORY, id="quality pinned to a release",
        ),
    ],
)
def test_a_pin_at_another_release_re_executes_into_it_before_any_row(gh_kwargs, repo, capsys):
    port = ReexecutionPort()
    gh = FakeGh(**gh_kwargs)
    ports = door_ports(reexecute=port)
    with pytest.raises(Reexecuted):
        drive(gh, repo=repo, ports=ports)
    assert (port.asked, capsys.readouterr().out) == (
        [(ReleaseAtPin(Release("v0.10.1"), OTHER_COMMIT), f"quality merge {repo}#7", ["merge", f"{repo}#7"])], "",
    )


def test_the_child_at_its_pin_names_the_release_first(capsys):
    gh = FakeGh(workflows=at_merge(**{"ci.yml": gate_workflow("v0.10.1", OTHER_COMMIT)}))
    _, out, _ = door(capsys, gh, ports=door_ports(running_at("0.10.1", OTHER_COMMIT)), environment={RUN_AT_PIN_VARIABLE: "cached"})
    assert out.startswith("  · release       v0.10.1        pinned in .github/workflows/ci.yml\n" + pull_row())


def test_the_child_at_another_commit_is_the_pair_alone(capsys):
    gh = FakeGh(workflows=at_merge(**{"ci.yml": gate_workflow("v0.10.1", OTHER_COMMIT)}))
    assert door(capsys, gh, environment={RUN_AT_PIN_VARIABLE: "cached"}) == (
        2,
        "",
        (
            "error: the gate pins 7f7f7f7 as v0.10.1; uv prepared quality 0.10.0 at 5e5e5e5\n"
            "next:\n    uv cache clean overnightworks-quality\n    then run quality merge acme/widgets#7 again\n"
        ),
    )
    changed_nothing(gh)


def test_quality_itself_is_judged_by_the_installed_quality(capsys):
    gh = FakeGh(workflows=at_merge(**{"ci.yml": gate_workflow(uses=OWN_GATE)}))
    code, out, _ = door(capsys, gh, repo=checkout.GATE_REPOSITORY)
    assert (code, out.startswith("  · release       installed      0.10.0 · quality's gate pins no release\n" + pull_row())) == (1, True)


def test_quality_itself_in_a_child_is_the_pair_alone(capsys):
    gh = FakeGh(workflows=at_merge(**{"ci.yml": gate_workflow(uses=OWN_GATE)}))
    assert door(capsys, gh, repo=checkout.GATE_REPOSITORY, environment={RUN_AT_PIN_VARIABLE: "fetched"}) == (
        2,
        "",
        (
            "error: the gate of overnightworks/quality pins no release, but this is quality 0.10.0 prepared by uv\n"
            "next: run quality merge overnightworks/quality#7 again\n"
        ),
    )
    changed_nothing(gh)
