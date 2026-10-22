import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest
from conftest import GIT_IDENTITY
from verify_release import ReleaseFacts, gate_tool_refs, judge_release

SCRIPT = Path(__file__).parent.parent / "scripts" / "verify_release.py"
RELEASE = ReleaseFacts(
    tag="v1.2.3",
    work_tree_is_clean=True,
    tag_commit="a" * 40,
    head_commit="a" * 40,
    head_is_on_origin_main=True,
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
        (replace(RELEASE, tag_commit="b" * 40), "tag at HEAD"),
        (replace(RELEASE, tag_commit=None), "tag at HEAD"),
        (replace(RELEASE, head_is_on_origin_main=False), "on origin/main"),
        (replace(RELEASE, installed_version="1.2.4.dev1+gabc"), "installed version"),
        (replace(RELEASE, gate_refs=("v1.2.3", "v1.2.2", "v1.2.3")), "gate refs"),
        (replace(RELEASE, gate_refs=()), "gate refs"),
        (replace(RELEASE, gate_refs=("v1.2.3", None, "v1.2.3")), "gate refs"),
    ],
    ids=["dirty", "tag elsewhere", "tag missing", "off main", "other metadata", "other ref", "no refs", "missing ref"],
)
def test_a_release_is_refused_naming_the_check_that_fails(facts, check):
    """#153 cell 13: exactly one check fails, and it is named."""
    failed = [verdict.check for verdict in judge_release(facts) if not verdict.holds]
    assert failed == [check]


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


def test_the_script_refuses_a_tag_the_checkout_does_not_carry(release_checkout):
    """The entry point reads real git facts and exits 1 with one line per check."""
    done = run_script(release_checkout, "v0.0.1")
    assert done.returncode == 1
    assert done.stdout.count("\n") == 5
    assert "FAIL tag at HEAD" in done.stdout
    assert "ok   on origin/main" in done.stdout
    assert "ok   clean" in done.stdout


def test_the_script_refuses_a_checkout_with_a_staged_edit(release_checkout):
    (release_checkout / "edit.txt").write_text("edit\n", encoding="utf-8")
    git(release_checkout, "add", "edit.txt")
    done = run_script(release_checkout, "v0.0.1")
    assert done.returncode == 1
    assert "FAIL clean" in done.stdout


def test_the_script_refuses_a_head_that_origin_main_does_not_hold(release_checkout):
    git(release_checkout, "commit", "-q", "--allow-empty", "-m", "unpushed")
    done = run_script(release_checkout, "v0.0.1")
    assert done.returncode == 1
    assert "FAIL on origin/main" in done.stdout


def test_the_script_refuses_when_origin_cannot_be_fetched_even_if_the_cached_main_holds_head(
    release_checkout, tmp_path
):
    git(release_checkout, "fetch", "-q", "origin", "main")
    git(release_checkout, "remote", "set-url", "origin", str(tmp_path / "missing.git"))
    done = run_script(release_checkout, "v0.0.1")
    assert done.returncode == 1
    assert "FAIL on origin/main" in done.stdout


@pytest.mark.parametrize("arguments", [[], ["v1.2.3", "v1.2.4"]], ids=["no tag", "two tags"])
def test_the_script_prints_a_usage_line_and_exits_2_for_other_than_one_tag(tmp_path, arguments):
    done = run_script(tmp_path, *arguments)
    assert done.returncode == 2
    assert done.stderr.startswith("usage:")
