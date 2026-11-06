import tomllib
from pathlib import Path

import pytest
from verify_release import RELEASE_TAG

from tests.support.git_repositories import git, head_commit, make_repo
from tests.support.workflow_steps import (
    WORKFLOWS,
    FakeGh,
    recorded_calls,
    recording_command,
    run_step,
    step,
    step_environment,
    step_script,
    workflow,
)

pytestmark = pytest.mark.integration

FORMAT_STEP = "Refuse a malformed version"
VERIFY_STEP = "Refuse unless the commit, tagged here, is the release its version names"
CHECKOUT_STEP = "Check out the gated commit with its history and tags"


@pytest.fixture(scope="module")
def release() -> dict:
    return workflow("release.yml")


def run_verify_step(release: dict, directory: Path, version: str, failing: str = "") -> tuple[object, Path, str]:
    checkout = make_repo(directory / "checkout", {"README": "the gated commit\n"})
    commit = head_commit(checkout)
    tools = directory / "tools"
    record = recording_command(tools, "uv", failing=failing)
    environment = step_environment(release, "build", VERIFY_STEP, VERSION=version, COMMIT=commit)
    return run_step(step_script(release, "build", VERIFY_STEP), checkout, environment, tools), record, commit


def test_the_dispatch_takes_only_the_version(release):
    dispatch = release[True]
    assert set(dispatch) == {"workflow_dispatch"}
    assert set(dispatch["workflow_dispatch"]["inputs"]) == {"version"}
    version = dispatch["workflow_dispatch"]["inputs"]["version"]
    assert set(version) == {"description", "required", "type"}
    assert (version["required"], version["type"]) == (True, "string")
    assert version["description"] == workflow("release-gate.yml")[True]["workflow_call"]["inputs"]["version"]["description"]


def test_the_build_runs_only_after_the_gate(release):
    assert release["jobs"]["build"]["needs"] == "gate"


def test_no_job_may_write(release):
    assert release["permissions"] == {}
    assert all("write" not in job.get("permissions", {}).values() for job in release["jobs"].values())


def test_a_release_never_cancels_a_running_release(release):
    group = release["concurrency"]["group"]
    assert release["concurrency"]["cancel-in-progress"] is False
    assert isinstance(group, str) and group and "${{" not in group


def test_the_gate_call_is_this_repository_s_own_and_gets_the_version(release):
    call = release["jobs"]["gate"]
    called = call["uses"].removeprefix("$/")
    assert called != call["uses"]
    assert (WORKFLOWS.parents[1] / called).is_file()
    assert call["with"] == {"version": "${{ inputs.version }}"}
    assert call["permissions"] == {"actions": "read", "checks": "read", "contents": "read", "pull-requests": "read"}


def test_the_build_checks_out_and_verifies_the_gated_commit(release):
    assert release["jobs"]["build"]["env"] == {"VERSION": "${{ inputs.version }}", "COMMIT": "${{ needs.gate.outputs.commit }}"}
    assert step(release, "build", CHECKOUT_STEP)["with"] == {
        "ref": "${{ needs.gate.outputs.commit }}",
        "fetch-depth": 0,
        "persist-credentials": False,
    }


def test_the_build_tags_the_gated_commit_in_its_own_checkout_then_verifies_it(release, tmp_path):
    result, record, commit = run_verify_step(release, tmp_path, "0.21.0")
    distribution = tomllib.loads((WORKFLOWS.parents[1] / "pyproject.toml").read_text(encoding="utf-8"))["project"]["name"]
    assert result.returncode == 0
    assert git(tmp_path / "checkout", "tag", "--points-at", commit).stdout == "v0.21.0\n"
    assert recorded_calls(record) == [["sync", "--locked", "--reinstall-package", distribution], ["run", "--locked", "python", "scripts/verify_release.py"]]


def test_a_failed_verification_fails_the_build(release, tmp_path):
    result, _, _ = run_verify_step(release, tmp_path, "0.21.0", failing="run")
    assert result.returncode == 1


@pytest.mark.parametrize("version", ["0.0.0", "0.21.0", "10.200.3000"])
def test_a_version_the_gate_accepts_is_a_tag_verify_release_accepts(tmp_path, version):
    gate = workflow("release-gate.yml")
    environment = step_environment(gate, "gate", FORMAT_STEP, VERSION=version, GH_TOKEN="token", COMMIT="a1" * 20)
    accepted = run_step(step_script(gate, "gate", FORMAT_STEP), tmp_path, environment, FakeGh(tmp_path / "tools", {}).directory)
    assert accepted.returncode == 0
    assert RELEASE_TAG.fullmatch(f"v{version}")


@pytest.mark.parametrize("name", ["release.yml", "release-gate.yml"])
def test_no_run_value_of_a_release_workflow_interpolates_an_expression(name):
    scripts = [candidate["run"] for job in workflow(name)["jobs"].values() for candidate in job.get("steps", []) if "run" in candidate]
    assert scripts
    assert all("${{" not in script for script in scripts)
