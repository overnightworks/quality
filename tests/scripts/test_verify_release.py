import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest
from verify_release import ReleaseFacts, branches_holding_head, gate_tool_refs, judge_release

from tests.support.git_repositories import GIT_IDENTITY

SCRIPT = Path(__file__).parents[2] / "scripts" / "verify_release.py"
RELEASE = ReleaseFacts(
    release_tags_at_head=("v1.2.3",),
    work_tree_is_clean=True,
    branches_holding_head=frozenset({"main"}),
    installed_version="1.2.3",
    gate_refs=("v1.2.3", "v1.2.3", "v1.2.3"),
)


def test_a_clean_checkout_at_the_tag_with_its_version_and_refs_is_a_release():
    """#153 cell 12."""
    verdicts = judge_release(RELEASE)
    assert all(verdict.holds for verdict in verdicts)
    assert len(verdicts) == 5


@pytest.mark.parametrize(
    ("facts", "check"),
    [
        (replace(RELEASE, work_tree_is_clean=False), "clean"),
        (replace(RELEASE, branches_holding_head=frozenset()), "on origin/main or origin/release/v1.2"),
        (replace(RELEASE, installed_version="1.2.4.dev1+gabc"), "installed version"),
        (replace(RELEASE, gate_refs=("v1.2.3", "v1.2.2", "v1.2.3")), "gate refs"),
        (replace(RELEASE, gate_refs=()), "gate refs"),
        (replace(RELEASE, gate_refs=("v1.2.3", None, "v1.2.3")), "gate refs"),
    ],
    ids=["dirty", "off main", "other metadata", "other ref", "no refs", "missing ref"],
)
def test_a_release_is_refused_naming_the_check_that_fails(facts, check):
    """#153 cell 13: exactly one check fails, and it is named."""
    failed = [verdict.check for verdict in judge_release(facts) if not verdict.holds]
    assert failed == [check]


def test_a_patch_tag_held_only_by_its_minors_release_branch_is_a_release():
    verdicts = judge_release(replace(RELEASE, branches_holding_head=frozenset({"release/v1.2"})))
    assert all(verdict.holds for verdict in verdicts)


@pytest.mark.parametrize(
    ("tag", "holding", "check"),
    [
        ("v1.2.3", {"release/v1.3"}, "on origin/main or origin/release/v1.2"),
        ("v1.3.0", {"release/v1.3"}, "on origin/main"),
        ("v1.2.3", set(), "on origin/main or origin/release/v1.2"),
        ("v1.3.0", set(), "on origin/main"),
    ],
    ids=["other minor's branch", "minor tag on a release branch", "patch tag held by nothing", "minor tag held by nothing"],
)
def test_a_tag_held_by_no_branch_it_accepts_fails_only_the_branch_check(tag, holding, check):
    facts = replace(
        RELEASE,
        release_tags_at_head=(tag,),
        installed_version=tag.removeprefix("v"),
        gate_refs=(tag, tag, tag),
        branches_holding_head=frozenset(holding),
    )
    assert [verdict.check for verdict in judge_release(facts) if not verdict.holds] == [check]


@pytest.mark.parametrize("tags", [(), ("v1.2.3", "v1.2.4")], ids=["none", "two"])
def test_a_release_without_exactly_one_tag_at_head_fails_every_check_that_needs_the_tag(tags):
    failed = [verdict.check for verdict in judge_release(replace(RELEASE, release_tags_at_head=tags)) if not verdict.holds]
    assert failed[0].startswith("one release tag at HEAD (found: ")
    assert failed[1:] == ["installed version", "gate refs"]


def test_the_gate_tool_refs_are_the_refs_of_the_checkouts_of_this_repository_only():
    gate = (
        "jobs:\n  a:\n    steps:\n"
        "      - uses: actions/checkout@x\n        with:\n          ref: other\n"
        "      - uses: actions/checkout@x\n        with:\n          repository: overnightworks/quality\n          ref: v9.9.9\n"
    )
    assert gate_tool_refs(gate) == ["v9.9.9"]


def git(repo: Path, *arguments: str) -> None:
    subprocess.run(["git", *GIT_IDENTITY, *arguments], cwd=repo, check=True, capture_output=True)


def run_script(repo: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run([sys.executable, "-I", str(SCRIPT), *arguments], cwd=repo, capture_output=True, text=True, check=False)


@pytest.fixture
def release_checkout(tmp_path):
    """A clone of a local bare origin whose main holds one commit with a gate.yml."""
    origin, checkout = tmp_path / "origin.git", tmp_path / "checkout"
    git(tmp_path, "init", "-q", "--bare", "--initial-branch=main", str(origin))
    git(tmp_path, "clone", "-q", str(origin), str(checkout))
    (checkout / ".github" / "workflows").mkdir(parents=True)
    (checkout / ".github" / "workflows" / "gate.yml").write_text("jobs: {}\n", encoding="utf-8")
    git(checkout, "add", ".")
    git(checkout, "commit", "-qm", "first")
    git(checkout, "push", "-q", "origin", "HEAD:main")
    return checkout


def tag_head(repo: Path, *tags: str) -> None:
    for tag in tags:
        git(repo, "tag", tag)


def test_the_script_names_the_release_tag_it_judges(release_checkout):
    tag_head(release_checkout, "v0.0.1")
    done = run_script(release_checkout)
    assert done.stdout.count("\n") == 5
    assert "ok   one release tag at HEAD (found: v0.0.1)" in done.stdout
    assert "ok   on origin/main" in done.stdout
    assert "ok   clean" in done.stdout


def test_the_script_ignores_a_tag_that_is_not_of_the_release_form(release_checkout):
    tag_head(release_checkout, "v0.0.1", "v0.0.2-rc1", "latest")
    done = run_script(release_checkout)
    assert "ok   one release tag at HEAD (found: v0.0.1)" in done.stdout


@pytest.mark.parametrize(
    ("tags", "found"),
    [(("latest",), "none"), (("v0.0.1", "v0.0.2"), "v0.0.1, v0.0.2")],
    ids=["none", "two"],
)
def test_the_script_refuses_a_head_without_exactly_one_release_tag(release_checkout, tags, found):
    tag_head(release_checkout, *tags)
    done = run_script(release_checkout)
    assert done.returncode == 1
    assert f"FAIL one release tag at HEAD (found: {found})" in done.stdout


def test_the_script_counts_a_release_tag_that_the_fetch_brings_in(release_checkout):
    git(release_checkout, "config", "remote.origin.tagOpt", "--tags")
    tag_head(release_checkout, "v0.0.1", "v0.0.2")
    git(release_checkout, "push", "-q", "origin", "v0.0.2")
    git(release_checkout, "tag", "-d", "v0.0.2")
    done = run_script(release_checkout)
    assert done.returncode == 1
    assert "FAIL one release tag at HEAD (found: v0.0.1, v0.0.2)" in done.stdout


def test_the_script_refuses_a_checkout_with_a_staged_edit(release_checkout):
    tag_head(release_checkout, "v0.0.1")
    (release_checkout / "edit.txt").write_text("edit\n", encoding="utf-8")
    git(release_checkout, "add", "edit.txt")
    done = run_script(release_checkout)
    assert done.returncode == 1
    assert "FAIL clean" in done.stdout


def test_the_script_refuses_a_head_that_origin_main_does_not_hold(release_checkout):
    git(release_checkout, "commit", "-q", "--allow-empty", "-m", "unpushed")
    done = run_script(release_checkout)
    assert done.returncode == 1
    assert "FAIL on origin/main" in done.stdout


def test_the_script_refuses_when_origin_cannot_be_fetched_even_if_the_cached_main_holds_head(
    release_checkout, tmp_path
):
    git(release_checkout, "fetch", "-q", "origin", "main")
    git(release_checkout, "remote", "set-url", "origin", str(tmp_path / "missing.git"))
    done = run_script(release_checkout)
    assert done.returncode == 1
    assert "FAIL on origin/main" in done.stdout


@pytest.fixture
def release_branch_only(release_checkout):
    """HEAD is one commit beyond origin's main, held by origin's release/v1.2 alone, whose remote-tracking ref is gone."""
    git(release_checkout, "commit", "-q", "--allow-empty", "-m", "patch")
    git(release_checkout, "push", "-q", "origin", "HEAD:refs/heads/release/v1.2")
    git(release_checkout, "update-ref", "-d", "refs/remotes/origin/release/v1.2")
    return release_checkout


def test_the_script_accepts_a_patch_tag_that_only_its_minors_release_branch_holds(release_branch_only):
    tag_head(release_branch_only, "v1.2.3")
    done = run_script(release_branch_only)
    assert "ok   on origin/release/v1.2" in done.stdout


def test_the_script_refuses_a_minor_tag_that_only_a_release_branch_holds(release_branch_only):
    tag_head(release_branch_only, "v1.2.0")
    done = run_script(release_branch_only)
    assert "FAIL on origin/main" in done.stdout


def test_the_script_refuses_a_patch_tag_that_only_another_minors_release_branch_holds(release_checkout):
    git(release_checkout, "commit", "-q", "--allow-empty", "-m", "patch")
    git(release_checkout, "push", "-q", "origin", "HEAD:refs/heads/release/v1.3")
    tag_head(release_checkout, "v1.2.3")
    done = run_script(release_checkout)
    assert "FAIL on origin/main or origin/release/v1.2" in done.stdout


def test_the_script_refuses_a_release_branch_that_origin_deleted_even_if_its_cached_ref_holds_head(release_branch_only, tmp_path):
    git(release_branch_only, "fetch", "-q", "origin", "release/v1.2:refs/remotes/origin/release/v1.2")
    git(tmp_path / "origin.git", "branch", "-D", "release/v1.2")
    tag_head(release_branch_only, "v1.2.3")
    done = run_script(release_branch_only)
    assert "FAIL on origin/main or origin/release/v1.2" in done.stdout


def test_the_script_accepts_a_patch_tag_that_main_holds_when_origin_has_no_release_branch(release_checkout):
    tag_head(release_checkout, "v1.2.3")
    done = run_script(release_checkout)
    assert "ok   on origin/main" in done.stdout


@pytest.mark.integration
def test_a_failed_listing_of_the_branches_holding_head_holds_none_even_if_it_printed_a_branch(tmp_path, monkeypatch):
    fake_git = tmp_path / "bin" / "git"
    fake_git.parent.mkdir()
    fake_git.write_text("#!/bin/sh\necho main\nexit 1\n", encoding="utf-8")
    fake_git.chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake_git.parent}:/usr/bin:/bin")
    assert branches_holding_head(fetched=True) == frozenset()


def test_the_script_exits_2_for_an_unexpected_command_line_argument(release_checkout):
    done = run_script(release_checkout, "v0.0.1")
    assert done.returncode == 2
    assert done.stdout == ""
