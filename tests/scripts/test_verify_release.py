import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest
from verify_release import ReleaseFacts, gate_tool_refs, judge_release, rules_without_release

from quality.rules import RuleRelease, Switch, SwitchChange
from tests.support.git_repositories import GIT_IDENTITY

SCRIPT = Path(__file__).parents[2] / "scripts" / "verify_release.py"
RELEASE = ReleaseFacts(
    release_tags_at_head=("v1.2.3",),
    work_tree_is_clean=True,
    head_is_on_origin_main=True,
    installed_version="1.2.3",
    gate_refs=("v1.2.3", "v1.2.3", "v1.2.3"),
    rules_without_release=(),
)


def test_a_clean_checkout_at_the_tag_with_its_version_and_refs_is_a_release():
    """#153 cell 12."""
    verdicts = judge_release(RELEASE)
    assert all(verdict.holds for verdict in verdicts)
    assert len(verdicts) == 6


@pytest.mark.parametrize(
    ("facts", "check"),
    [
        (replace(RELEASE, work_tree_is_clean=False), "clean"),
        (replace(RELEASE, head_is_on_origin_main=False), "on origin/main"),
        (replace(RELEASE, installed_version="1.2.4.dev1+gabc"), "installed version"),
        (replace(RELEASE, gate_refs=("v1.2.3", "v1.2.2", "v1.2.3")), "gate refs"),
        (replace(RELEASE, gate_refs=()), "gate refs"),
        (replace(RELEASE, gate_refs=("v1.2.3", None, "v1.2.3")), "gate refs"),
        (replace(RELEASE, rules_without_release=("python-print",)), "rule release facts (missing: python-print)"),
    ],
    ids=["dirty", "off main", "other metadata", "other ref", "no refs", "missing ref", "a rule without a release"],
)
def test_a_release_is_refused_naming_the_check_that_fails(facts, check):
    """#153 cell 13: exactly one check fails, and it is named."""
    failed = [verdict.check for verdict in judge_release(facts) if not verdict.holds]
    assert failed == [check]


@pytest.mark.parametrize(
    ("release", "missing"),
    [
        (RuleRelease("alpha", None, Switch.ALWAYS), ("alpha",)),
        (RuleRelease("alpha", "v1.0.0", Switch.ALWAYS, SwitchChange(None, Switch.REPORT_ONLY)), ("alpha",)),
        (RuleRelease("alpha", "v1.0.0", Switch.ALWAYS, SwitchChange("v1.1.0", Switch.REPORT_ONLY)), ()),
        (RuleRelease("alpha", "v1.0.0", Switch.ALWAYS), ()),
    ],
    ids=["first release missing", "switch release missing", "switch released", "no switch"],
)
def test_the_rules_without_a_released_first_release_or_switch_are_named(release, missing):
    assert rules_without_release([release]) == missing


@pytest.mark.parametrize("tags", [(), ("v1.2.3", "v1.2.4")], ids=["none", "two"])
def test_a_release_without_exactly_one_tag_at_head_fails_every_check_that_needs_the_tag(tags):
    failed = [
        verdict.check for verdict in judge_release(replace(RELEASE, release_tags_at_head=tags)) if not verdict.holds
    ]
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
    return subprocess.run(
        [sys.executable, "-I", str(SCRIPT), *arguments], cwd=repo, capture_output=True, text=True, check=False
    )


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
    assert done.stdout.count("\n") == 6
    assert "rule release facts (missing: " in done.stdout
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


def test_the_script_exits_2_for_an_unexpected_command_line_argument(release_checkout):
    done = run_script(release_checkout, "v0.0.1")
    assert done.returncode == 2
    assert done.stdout == ""
