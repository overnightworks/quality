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


def test_the_check_job_installs_the_scanners_through_one_pinned_mise_step():
    """CALLER-1, CALLER-2: one mise-action pinned by commit, a release and its binary's checksum, installing from the lock."""
    gate_step = the_mise_step("gate.yml", "check")
    given = gate_step["with"]
    assert MISE_ACTION.fullmatch(gate_step["uses"].split(" ")[0]), gate_step["uses"]
    assert re.fullmatch(r"[0-9a-f]{64}", given["sha256"])
    assert given["version"]
    assert given["working_directory"].endswith("src/quality/mise")
    assert given["install_args"] == "--locked"
    assert [given[name] for name in GIVEN_FALSE] == [False] * len(GIVEN_FALSE)


OPTIONAL_SUBDIRECTORY = re.compile(r"env\.TOOL_DIR != '(?P<root>[^']*)' && format\('(?P<template>[^']*)', env\.TOOL_DIR\) \|\| '(?P<otherwise>[^']*)'")


def rendered(expression: str, tool_dir: str) -> str:
    """The workflow `expression` with `github.workspace` as `/work` and `env.TOOL_DIR` as `tool_dir`; only the forms the workflows use are understood."""

    def evaluate(match: re.Match) -> str:
        text = match[1]
        if text == "github.workspace":
            return "/work"
        optional = OPTIONAL_SUBDIRECTORY.fullmatch(text)
        assert optional, f"the test does not understand the expression {text!r}"
        # GitHub compares strings without regard to case.
        if tool_dir.casefold() == optional["root"].casefold():
            return optional["otherwise"]
        return optional["template"].replace("{0}", tool_dir)

    return re.sub(r"\$\{\{ (.*?) \}\}", evaluate, expression)


@pytest.mark.parametrize(("workflow", "job", "tool_dir"), [("gate.yml", "check", "."), ("gate.yml", "check", ".gate-tool"), ("ci.yml", "tests", ".")])
def test_the_ceiling_of_mise_is_the_normalised_parent_of_the_manifest_directory(workflow, job, tool_dir):
    """CALLER-3, CALLER-4: trust protects nothing in CI, so mise must never read above the manifest's parent, whatever spelling of the path it compares."""
    step = the_mise_step(workflow, job)
    manifest_directory = step["with"]["working_directory"].replace("${{ env.TOOL_DIR }}", tool_dir)
    expected = posixpath.normpath("/work/" + manifest_directory.rsplit("/", 1)[0])
    assert rendered(step["env"]["MISE_CEILING_PATHS"], tool_dir) == expected


def test_the_tests_job_installs_the_same_scanners_as_the_gate_and_runs_no_check():
    """CALLER-4: the pin stands twice and is held equal; the gate's check job is quality's own check."""
    gate_step, tests_step = the_mise_step("gate.yml", "check"), the_mise_step("ci.yml", "tests")
    assert {key: value for key, value in tests_step["with"].items() if key != "working_directory"} == {
        key: value for key, value in gate_step["with"].items() if key != "working_directory"
    }
    assert tests_step["uses"] == gate_step["uses"]
    assert [step for step in load("ci.yml")["jobs"]["tests"]["steps"] if "quality check" in step.get("run", "")] == []



def test_both_install_steps_let_mise_retry_a_download_the_same_whole_number_of_times():
    """CALLER-11: the gate's and quality's own install step carry one `MISE_HTTP_RETRIES`, a count of at least 1."""
    retries = [the_mise_step(workflow, job)["env"]["MISE_HTTP_RETRIES"] for workflow, job in (("gate.yml", "check"), ("ci.yml", "tests"))]
    assert retries[0] == retries[1]
    assert int(retries[0]) >= 1
