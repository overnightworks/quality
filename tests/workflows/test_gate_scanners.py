import posixpath
import re
from pathlib import Path

import pytest
import yaml

WORKFLOWS = Path(__file__).parents[2] / ".github" / "workflows"
MISE_ACTION = re.compile(r"jdx/mise-action@[0-9a-f]{40}")
GIVEN_FALSE = ("cache", "env", "export_path", "add_shims_to_path")


def load(name: str) -> dict:
    return yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))


def the_mise_step(workflow: str, job: str) -> dict:
    steps = [step for step in load(workflow)["jobs"][job]["steps"] if str(step.get("uses", "")).startswith("jdx/mise-action@")]
    assert len(steps) == 1, f"{workflow} {job} must install through exactly one mise-action step, it has {len(steps)}"
    return steps[0]


def test_the_hygiene_job_installs_the_scanners_through_one_pinned_mise_step():
    """#228 rule 4: one mise-action pinned by commit, a release and its binary's checksum, installing from the lock."""
    gate_step = the_mise_step("gate.yml", "hygiene")
    given = gate_step["with"]
    assert MISE_ACTION.fullmatch(gate_step["uses"].split(" ")[0]), gate_step["uses"]
    assert re.fullmatch(r"[0-9a-f]{64}", given["sha256"])
    assert given["version"]
    assert given["working_directory"].endswith("src/quality/mise")
    assert given["install_args"] == "--locked"
    assert [given[name] for name in GIVEN_FALSE] == [False] * len(GIVEN_FALSE)


def rendered(expression: str, tool_dir: str) -> str:
    """The workflow `expression` with `github.workspace` as `/work` and `env.TOOL_DIR` as `tool_dir`, evaluated as Python."""

    def evaluate(match: re.Match) -> str:
        python = match[1].replace("&&", " and ").replace("||", " or ").replace("env.TOOL_DIR", "TOOL_DIR")
        python = python.replace("github.workspace", "'/work'")
        return str(eval(python, {"TOOL_DIR": tool_dir, "format": str.format}))  # noqa: S307 - the expression is this repository's own workflow text

    return re.sub(r"\$\{\{ (.*?) \}\}", evaluate, expression)


@pytest.mark.parametrize(("workflow", "job", "tool_dir"), [("gate.yml", "hygiene", "."), ("gate.yml", "hygiene", ".gate-tool"), ("ci.yml", "tests", ".")])
def test_the_ceiling_of_mise_is_the_normalised_parent_of_the_manifest_directory(workflow, job, tool_dir):
    """#265 B1: trust protects nothing in CI, so mise must never read above the manifest's parent, whatever spelling of the path it compares."""
    step = the_mise_step(workflow, job)
    manifest_directory = step["with"]["working_directory"].replace("${{ env.TOOL_DIR }}", tool_dir)
    expected = posixpath.normpath("/work/" + manifest_directory.rsplit("/", 1)[0])
    assert rendered(step["env"]["MISE_CEILING_PATHS"], tool_dir) == expected


def test_the_tests_job_installs_the_same_scanners_as_the_gate_and_runs_no_hygiene():
    """#265 D8: the pin stands twice and is held equal; the gate's hygiene job is quality's own check."""
    gate_step, tests_step = the_mise_step("gate.yml", "hygiene"), the_mise_step("ci.yml", "tests")
    assert {key: value for key, value in tests_step["with"].items() if key != "working_directory"} == {
        key: value for key, value in gate_step["with"].items() if key != "working_directory"
    }
    assert tests_step["uses"] == gate_step["uses"]
    assert [step for step in load("ci.yml")["jobs"]["tests"]["steps"] if "quality hygiene" in step.get("run", "")] == []


@pytest.mark.parametrize("workflow", sorted(path.name for path in WORKFLOWS.glob("*.yml")))
def test_no_step_of_any_workflow_names_engines(workflow):
    """#265 test 8: the engines directory is gone, so no step's name, command, inputs or environment mentions it."""
    for job in load(workflow).get("jobs", {}).values():
        for step in job.get("steps", []):
            assert "engines" not in yaml.safe_dump(step), step
