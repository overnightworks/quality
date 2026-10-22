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
    installed_version="1.2.3",
    gate_refs=("v1.2.3", "v1.2.3", "v1.2.3"),
)


def test_a_clean_checkout_at_the_tag_with_its_version_and_refs_is_a_release():
    """#153 cell 12."""
    verdicts = judge_release(RELEASE)
    assert all(verdict.holds for verdict in verdicts)
    assert len(verdicts) == 4


@pytest.mark.parametrize(
    ("facts", "check"),
    [
        (replace(RELEASE, work_tree_is_clean=False), "clean"),
        (replace(RELEASE, tag_commit="b" * 40), "tag at HEAD"),
        (replace(RELEASE, tag_commit=None), "tag at HEAD"),
        (replace(RELEASE, installed_version="1.2.4.dev1+gabc"), "installed version"),
        (replace(RELEASE, gate_refs=("v1.2.3", "v1.2.2", "v1.2.3")), "gate refs"),
        (replace(RELEASE, gate_refs=()), "gate refs"),
    ],
    ids=["dirty", "tag elsewhere", "tag missing", "other metadata", "other ref", "no refs"],
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


def test_the_script_refuses_a_tag_the_checkout_does_not_carry(tmp_path):
    """The entry point reads real git facts and exits 1 with one line per check."""
    git(tmp_path, "init", "-q")
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / ".github" / "workflows" / "gate.yml").write_text("jobs: {}\n", encoding="utf-8")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "first")
    done = subprocess.run([sys.executable, "-I", str(SCRIPT), "v0.0.1"], cwd=tmp_path, capture_output=True, text=True, check=False)
    assert done.returncode == 1
    assert done.stdout.count("\n") == 4
    assert "FAIL tag at HEAD" in done.stdout
