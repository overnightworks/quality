import subprocess
import tomllib
from pathlib import Path

import pytest
import yaml
from packaging.specifiers import SpecifierSet
from packaging.version import Version
from verify_release import RELEASE_TAG

from tests.support.git_repositories import git, head_commit, make_repo
from tests.support.workflow_steps import (
    FORMAT_STEP,
    WORKFLOWS,
    FakeGh,
    run_step,
    step,
    step_environment,
    step_names,
    step_script,
    workflow,
    write_executable,
)

pytestmark = pytest.mark.integration

VERIFY_STEP = "Refuse unless the commit, tagged here, is the release its version names"
CHECKOUT_STEP = "Check out the dispatched commit with its history and tags"
REFUSE_STEP = "Refuse unless the checkout is the commit the gate judged"


@pytest.fixture(scope="module")
def release() -> dict:
    return workflow("release.yml")


SEEN_BY_UV = """\
#!/bin/sh
echo "$* | $(git tag --list)" >> "$(dirname "$0")/uv.seen"
[ "$1" = "{failing}" ] && exit 1
exit 0
"""


def run_verify_step(release: dict, directory: Path, version: str, failing: str = "") -> tuple[object, list[str], str]:
    (directory / "checkout").mkdir()
    checkout = make_repo(directory / "checkout", {"README": "the gated commit\n"})
    commit = head_commit(checkout)
    tools = directory / "tools"
    tools.mkdir()
    write_executable(tools / "uv", SEEN_BY_UV.format(failing=failing))
    environment = step_environment(release, "build", VERIFY_STEP, VERSION=version, COMMIT=commit)
    result = run_step(step_script(release, "build", VERIFY_STEP), checkout, environment, tools)
    seen = tools / "uv.seen"
    return result, seen.read_text().splitlines() if seen.exists() else [], commit


def test_the_dispatch_takes_only_the_version(release):
    """RELEASE-3, RELEASE-10."""
    dispatch = release[True]
    assert set(dispatch) == {"workflow_dispatch"}
    assert set(dispatch["workflow_dispatch"]["inputs"]) == {"version"}
    version = dispatch["workflow_dispatch"]["inputs"]["version"]
    assert set(version) == {"description", "required", "type"}
    assert (version["required"], version["type"]) == (True, "string")


def test_the_build_runs_only_after_the_gate(release):
    """RELEASE-5."""
    assert release["jobs"]["build"]["needs"] == "gate"


def test_no_job_or_step_is_skippable(release):
    """RELEASE-17."""
    assert all("if" not in job and "continue-on-error" not in job for job in release["jobs"].values())
    assert all(
        "if" not in candidate and "continue-on-error" not in candidate
        for candidate in release["jobs"]["build"]["steps"]
    )


def test_the_build_s_python_satisfies_the_project_s_requires_python(release):
    """RELEASE-19."""
    requires = tomllib.loads((WORKFLOWS.parents[1] / "pyproject.toml").read_text(encoding="utf-8"))["project"][
        "requires-python"
    ]
    python = step(release, "build", "Set up uv")["with"]["python-version"]
    assert Version(python) in SpecifierSet(requires)


def test_the_tag_job_runs_only_after_the_gate_and_the_build(release):
    """RELEASE-8."""
    assert release["jobs"]["tag"]["needs"] == ["gate", "build"]


def test_only_the_tag_job_may_write(release):
    """RELEASE-1, RELEASE-9."""
    assert release["permissions"] == {}
    assert [name for name, job in release["jobs"].items() if "write" in job.get("permissions", {}).values()] == ["tag"]
    assert release["jobs"]["tag"]["permissions"] == {"contents": "write"}


def test_a_release_never_cancels_a_running_release(release):
    """RELEASE-20."""
    group = release["concurrency"]["group"]
    assert release["concurrency"]["cancel-in-progress"] is False
    assert isinstance(group, str)
    assert group
    assert "${{" not in group


def test_the_gate_call_is_this_repository_s_own_and_gets_the_version(release):
    """RELEASE-2, RELEASE-7, RELEASE-11, GATE-2."""
    call = release["jobs"]["gate"]
    called = call["uses"].removeprefix("$/")
    assert called != call["uses"]
    gate = yaml.safe_load((WORKFLOWS.parents[1] / called).read_text(encoding="utf-8"))
    inputs = gate[True]["workflow_call"]["inputs"]
    assert {name for name, declared in inputs.items() if declared["required"]} <= set(call["with"]) <= set(inputs)
    assert "outputs.commit" in release["jobs"]["build"]["env"]["COMMIT"]
    assert "commit" in gate[True]["workflow_call"]["outputs"]
    assert call["with"] == {"version": "${{ inputs.version }}"}
    assert call["permissions"] == {"actions": "read", "checks": "read", "contents": "read", "pull-requests": "read"}


def test_the_tag_call_is_this_repository_s_own_and_gets_the_gated_commit(release):
    """RELEASE-11, RELEASE-21, GATE-2."""
    call = release["jobs"]["tag"]
    called = call["uses"].removeprefix("$/")
    assert called != call["uses"]
    tag = yaml.safe_load((WORKFLOWS.parents[1] / called).read_text(encoding="utf-8"))
    inputs = tag[True]["workflow_call"]["inputs"]
    assert {name for name, spec in inputs.items() if spec["required"]} <= set(call["with"]) <= set(inputs)
    assert call["with"] == {"version": "${{ inputs.version }}", "commit": "${{ needs.gate.outputs.commit }}"}
    assert "commit" in workflow("release-gate.yml")[True]["workflow_call"]["outputs"]


def test_the_build_checks_out_the_dispatched_commit_without_a_ref(release):
    """RELEASE-4, RELEASE-6, RELEASE-7, RELEASE-18."""
    assert release["jobs"]["build"]["env"] == {
        "VERSION": "${{ inputs.version }}",
        "COMMIT": "${{ needs.gate.outputs.commit }}",
    }
    checkout = step(release, "build", CHECKOUT_STEP)
    assert checkout["uses"].startswith("actions/checkout@")
    assert "ref" not in checkout["with"]
    assert "repository" not in checkout["with"]
    assert checkout["with"]["persist-credentials"] is False


def test_nothing_runs_between_the_checkout_and_the_refusal(release):
    """RELEASE-7."""
    names = step_names(release, "build")
    assert names[:2] == [CHECKOUT_STEP, REFUSE_STEP]


def run_refusal_step(release: dict, directory: Path, checkout: Path, gated: str) -> subprocess.CompletedProcess[str]:
    environment = step_environment(release, "build", REFUSE_STEP, VERSION="0.21.0", COMMIT=gated)
    return run_step(step_script(release, "build", REFUSE_STEP), checkout, environment, directory / "tools")


def test_the_checkout_of_the_gated_commit_passes_silently(release, tmp_path):
    """RELEASE-7."""
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    make_repo(checkout, {"README": "the checkout\n"})
    result = run_refusal_step(release, tmp_path, checkout, head_commit(checkout))
    assert (result.returncode, result.stdout, result.stderr) == (0, "", "")


def test_a_checkout_of_another_commit_is_refused_naming_both_commits(release, tmp_path):
    """RELEASE-7."""
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    make_repo(checkout, {"README": "the checkout\n"})
    head = head_commit(checkout)
    gated = "b2" * 20
    result = run_refusal_step(release, tmp_path, checkout, gated)
    assert result.returncode == 1
    assert result.stderr.startswith("Refused:")
    assert head in result.stderr
    assert gated in result.stderr


def test_without_a_checkout_the_refusal_step_fails(release, tmp_path):
    """RELEASE-7."""
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    result = run_refusal_step(release, tmp_path, checkout, "b2" * 20)
    assert result.returncode != 0


def test_the_build_tags_the_gated_commit_in_its_own_checkout_then_verifies_it(release, tmp_path):
    """RELEASE-12."""
    result, seen, commit = run_verify_step(release, tmp_path, "0.21.0")
    distribution = tomllib.loads((WORKFLOWS.parents[1] / "pyproject.toml").read_text(encoding="utf-8"))["project"][
        "name"
    ]
    assert result.returncode == 0
    assert git(tmp_path / "checkout", "tag", "--points-at", commit).stdout == "v0.21.0\n"
    assert seen == [
        f"sync --locked --reinstall-package {distribution} | v0.21.0",
        "run --locked python scripts/verify_release.py | v0.21.0",
    ]


def test_a_failed_verification_fails_the_build(release, tmp_path):
    """RELEASE-12."""
    result, _, _ = run_verify_step(release, tmp_path, "0.21.0", failing="run")
    assert result.returncode == 1


@pytest.mark.parametrize("version", ["0.0.0", "0.21.0", "10.200.3000"])
def test_a_version_the_gate_accepts_is_a_tag_verify_release_accepts(tmp_path, version):
    """RELEASE-13, GATE-7."""
    gate = workflow("release-gate.yml")
    form = gate[True]["workflow_call"]["inputs"]["form"]["default"]
    environment = step_environment(
        gate, "gate", FORMAT_STEP, VERSION=version, FORM=form, GH_TOKEN="token", COMMIT="a1" * 20
    )
    accepted = run_step(
        step_script(gate, "gate", FORMAT_STEP), tmp_path, environment, FakeGh(tmp_path / "tools", {}).directory
    )
    assert accepted.returncode == 0
    assert RELEASE_TAG.fullmatch(f"v{version}")


@pytest.mark.parametrize("name", ["release.yml", "release-gate.yml", "release-tag.yml"])
def test_no_run_value_of_a_release_workflow_interpolates_an_expression(name):
    """RELEASE-4, GATE-4, TAG-16."""
    scripts = [
        candidate["run"]
        for job in workflow(name)["jobs"].values()
        for candidate in job.get("steps", [])
        if "run" in candidate
    ]
    assert scripts
    assert all("${{" not in script for script in scripts)
