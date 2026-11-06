import os
from dataclasses import dataclass
from pathlib import Path

import pytest

from tests.support.workflow_steps import (
    Answer,
    FakeGh,
    StepRun,
    run_step,
    step,
    step_environment,
    step_names,
    step_script,
    workflow,
)

pytestmark = pytest.mark.integration

FETCH_STEP = "Fetch the files the build made"
CHECK_STEP = "Refuse files the release cannot carry exactly"
TAG_STEP = "Create the tag at the gated commit"
PUBLISH_STEP = "Publish the release on the tag with every file the build made"


@dataclass(frozen=True)
class TagCall:
    repository: str
    commit: str
    version: str

    @property
    def matching_refs(self) -> tuple[str, ...]:
        return ("api", f"repos/{self.repository}/git/matching-refs/tags/v{self.version}")

    @property
    def create_ref(self) -> tuple[str, ...]:
        return (
            "api",
            "-X",
            "POST",
            f"repos/{self.repository}/git/refs",
            "-f",
            f"ref=refs/tags/v{self.version}",
            "-f",
            f"sha={self.commit}",
        )


@pytest.fixture(scope="module")
def release_tag() -> dict:
    return workflow("release-tag.yml")


@pytest.fixture
def tag_call() -> TagCall:
    return TagCall(repository="acme/widgets", commit="c3" * 20, version="1.2.3")


def ref_at(version: str, commit: str) -> list[dict]:
    return [{"ref": f"refs/tags/v{version}", "object": {"sha": commit}}]


def files_directory(release_tag: dict) -> str:
    return step(release_tag, "tag", FETCH_STEP)["with"]["path"]


def planted(directory: Path, *names: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for name in names:
        (directory / name).write_text("file\n")


def run_tag_step(
    release_tag: dict, tag_call: TagCall, name: str, directory: Path, answers: dict, **scenario: str
) -> StepRun:
    tools = directory / "tools"
    FakeGh(tools, answers)
    work = directory / "work"
    work.mkdir(exist_ok=True)
    values = {
        "GH_TOKEN": "token",
        "GH_REPO": tag_call.repository,
        "GITHUB_REPOSITORY": tag_call.repository,
        "VERSION": tag_call.version,
        "COMMIT": tag_call.commit,
        "ARTIFACT": "wheels",
        "NOTES": "",
        **scenario,
    }
    environment = step_environment(release_tag, "tag", name, **values)
    return run_step(step_script(release_tag, "tag", name), work, environment, tools)


def release_call(tag_call: TagCall, files: list[str], notes: str = "") -> tuple[str, ...]:
    return (
        "release",
        "create",
        f"v{tag_call.version}",
        *files,
        "--verify-tag",
        "--title",
        f"v{tag_call.version}",
        "--generate-notes",
        "--notes",
        notes,
    )


def test_the_tag_takes_version_commit_artifact_and_notes(release_tag):
    inputs = release_tag[True]["workflow_call"]["inputs"]
    assert set(inputs) == {"version", "commit", "artifact", "notes"}
    assert {name: {key: value for key, value in spec.items() if key != "description"} for name, spec in inputs.items()} == {
        "version": {"type": "string", "required": True},
        "commit": {"type": "string", "required": True},
        "artifact": {"type": "string", "required": False, "default": ""},
        "notes": {"type": "string", "required": False, "default": ""},
    }
    assert all(isinstance(spec["description"], str) and spec["description"] for spec in inputs.values())


def test_the_tag_job_reads_its_inputs_and_names_this_repository(release_tag):
    assert release_tag["jobs"]["tag"]["env"] == {
        "GH_TOKEN": "${{ github.token }}",
        "GH_REPO": "${{ github.repository }}",
        "VERSION": "${{ inputs.version }}",
        "COMMIT": "${{ inputs.commit }}",
        "ARTIFACT": "${{ inputs.artifact }}",
        "NOTES": "${{ inputs.notes }}",
    }


def test_the_files_are_fetched_only_for_a_named_artifact(release_tag):
    fetch = step(release_tag, "tag", FETCH_STEP)
    assert fetch["if"] == "inputs.artifact != ''"
    assert fetch["with"] == {"name": "${{ inputs.artifact }}", "path": "release-files"}


def test_the_steps_check_the_files_then_tag_then_publish(release_tag):
    assert step_names(release_tag, "tag") == [FETCH_STEP, CHECK_STEP, TAG_STEP, PUBLISH_STEP]


def test_no_tag_job_or_step_is_skippable(release_tag):
    job = release_tag["jobs"]["tag"]
    assert "if" not in job
    assert "continue-on-error" not in job
    assert all("continue-on-error" not in candidate for candidate in job["steps"])
    assert [candidate["name"] for candidate in job["steps"] if "if" in candidate] == [FETCH_STEP]


def test_only_contents_write_is_granted(release_tag):
    assert release_tag["permissions"] == {}
    assert release_tag["jobs"]["tag"]["permissions"] == {"contents": "write"}


@pytest.mark.parametrize("name", ["a#b", "#a", "a#"])
def test_a_file_name_with_a_hash_is_refused(release_tag, tag_call, tmp_path, name):
    planted(tmp_path / "work" / files_directory(release_tag), name, "b")
    result = run_tag_step(release_tag, tag_call, CHECK_STEP, tmp_path, {})
    assert result.returncode == 1
    assert result.stderr == f"Refused: the file {name} has a # in its name, which gh reads as the start of an asset label.\n"
    assert result.calls == []


@pytest.mark.parametrize("planting", ["no directory", "an empty directory"])
def test_a_named_artifact_that_brought_no_file_is_refused(release_tag, tag_call, tmp_path, planting):
    if planting == "an empty directory":
        planted(tmp_path / "work" / files_directory(release_tag))
    result = run_tag_step(release_tag, tag_call, CHECK_STEP, tmp_path, {})
    assert result.returncode == 1
    assert result.stderr == "Refused: the artifact wheels brought no file.\n"
    assert result.calls == []


def test_files_without_a_named_artifact_are_refused(release_tag, tag_call, tmp_path):
    planted(tmp_path / "work" / files_directory(release_tag), "a")
    result = run_tag_step(release_tag, tag_call, CHECK_STEP, tmp_path, {}, ARTIFACT="")
    assert result.returncode == 1
    assert result.stderr == "Refused: files were fetched, but no artifact was named.\n"
    assert result.calls == []


@pytest.mark.parametrize("entry", ["wheels", "link", "\n"], ids=["a subdirectory", "a symbolic link", "a directory named by line feeds"])
def test_an_entry_that_is_not_a_regular_top_level_file_is_refused(release_tag, tag_call, tmp_path, entry):
    directory = tmp_path / "work" / files_directory(release_tag)
    planted(directory, "b")
    if entry == "link":
        (directory / entry).symlink_to("b")
    else:
        planted(directory / entry, "x")
    result = run_tag_step(release_tag, tag_call, CHECK_STEP, tmp_path, {})
    assert result.returncode == 1
    assert result.stderr == f"Refused: the artifact wheels holds {entry}, which is not a regular top-level file.\n"
    assert result.calls == []


def test_the_files_land_where_the_steps_read_them(release_tag, tag_call, tmp_path):
    directory = files_directory(release_tag)
    planted(tmp_path / "work" / directory, "a")
    checked = run_tag_step(release_tag, tag_call, CHECK_STEP, tmp_path, {})
    published = run_tag_step(
        release_tag, tag_call, PUBLISH_STEP, tmp_path, {release_call(tag_call, [f"{directory}/a"]): Answer()}
    )
    assert (checked.returncode, published.returncode) == (0, 0)
    assert published.calls == [list(release_call(tag_call, [f"{directory}/a"]))]


def test_the_tag_is_created_at_the_gated_commit(release_tag, tag_call, tmp_path):
    answers = {tag_call.matching_refs: Answer(body=[]), tag_call.create_ref: Answer(body={})}
    result = run_tag_step(release_tag, tag_call, TAG_STEP, tmp_path, answers)
    assert result.returncode == 0
    assert result.calls == [list(tag_call.matching_refs), list(tag_call.create_ref)]


def test_a_rerun_keeps_the_tag_that_already_points_at_the_commit(release_tag, tag_call, tmp_path):
    answers = {tag_call.matching_refs: Answer(body=ref_at(tag_call.version, tag_call.commit))}
    result = run_tag_step(release_tag, tag_call, TAG_STEP, tmp_path, answers)
    assert result.returncode == 0
    assert result.calls == [list(tag_call.matching_refs)]


def test_a_tag_at_another_commit_fails_without_creating_anything(release_tag, tag_call, tmp_path):
    other = "d4" * 20
    answers = {tag_call.matching_refs: Answer(body=ref_at(tag_call.version, other))}
    result = run_tag_step(release_tag, tag_call, TAG_STEP, tmp_path, answers)
    assert result.returncode == 1
    assert result.stderr == f"Refused: the tag v1.2.3 exists at {other}, not at the gated commit {tag_call.commit}.\n"
    assert result.calls == [list(tag_call.matching_refs)]


def test_a_longer_tag_name_is_not_this_tag(release_tag, tag_call, tmp_path):
    answers = {
        tag_call.matching_refs: Answer(body=ref_at("1.2.30", "d4" * 20)),
        tag_call.create_ref: Answer(body={}),
    }
    result = run_tag_step(release_tag, tag_call, TAG_STEP, tmp_path, answers)
    assert result.returncode == 0
    assert result.calls[-1] == list(tag_call.create_ref)


def test_the_release_carries_exactly_the_files_the_build_made(release_tag, tag_call, tmp_path):
    directory = files_directory(release_tag)
    planted(tmp_path / "work" / directory, "b", "a", ".c")
    argv = release_call(tag_call, [f"{directory}/.c", f"{directory}/a", f"{directory}/b"], notes="Highlights")
    result = run_tag_step(release_tag, tag_call, PUBLISH_STEP, tmp_path, {argv: Answer()}, NOTES="Highlights")
    assert result.returncode == 0
    assert result.calls == [list(argv)]


def test_a_release_without_an_artifact_carries_no_file(release_tag, tag_call, tmp_path):
    argv = release_call(tag_call, [])
    result = run_tag_step(release_tag, tag_call, PUBLISH_STEP, tmp_path, {argv: Answer()}, ARTIFACT="")
    assert result.returncode == 0
    assert result.calls == [list(argv)]


def test_notes_reach_the_release_as_one_argument(release_tag, tag_call, tmp_path):
    notes = "$(touch planted) `touch planted`\nsecond line"
    argv = release_call(tag_call, [], notes=notes)
    result = run_tag_step(release_tag, tag_call, PUBLISH_STEP, tmp_path, {argv: Answer()}, ARTIFACT="", NOTES=notes)
    assert result.returncode == 0
    assert result.calls == [list(argv)]
    assert not os.path.exists(tmp_path / "work" / "planted")


def test_the_tag_is_created_before_the_release_and_a_failed_publication_is_retried_on_it(
    release_tag, tag_call, tmp_path
):
    directory = files_directory(release_tag)
    planted(tmp_path / "work" / directory, "a")
    argv = release_call(tag_call, [f"{directory}/a"])
    first = {tag_call.matching_refs: Answer(body=[]), tag_call.create_ref: Answer(body={}), argv: Answer(exit_code=1)}
    assert run_tag_step(release_tag, tag_call, TAG_STEP, tmp_path, first).returncode == 0
    assert run_tag_step(release_tag, tag_call, PUBLISH_STEP, tmp_path, first).returncode == 1
    second = {tag_call.matching_refs: Answer(body=ref_at(tag_call.version, tag_call.commit)), argv: Answer()}
    assert run_tag_step(release_tag, tag_call, TAG_STEP, tmp_path, second).returncode == 0
    result = run_tag_step(release_tag, tag_call, PUBLISH_STEP, tmp_path, second)
    assert result.returncode == 0
    assert result.calls == [
        list(tag_call.matching_refs),
        list(tag_call.create_ref),
        list(argv),
        list(tag_call.matching_refs),
        list(argv),
    ]
