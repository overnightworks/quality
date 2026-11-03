import shlex
from pathlib import Path

import pytest
import yaml

from quality import cli

WORKFLOWS = Path(__file__).parents[1] / ".github" / "workflows"


def quality_invocations() -> list[tuple[str, str, str, list[str]]]:
    """Every `quality ...` line of every workflow step as (workflow, step, line, argv after `quality`)."""
    invocations = []
    for workflow in sorted(WORKFLOWS.glob("*.yml")):
        for job in yaml.safe_load(workflow.read_text(encoding="utf-8")).get("jobs", {}).values():
            for step in job.get("steps", []):
                for line in step.get("run", "").splitlines():
                    tokens = shlex.split(line)
                    if "quality" in tokens:
                        invocations.append((workflow.name, step.get("name", ""), line.strip(), tokens[tokens.index("quality") + 1 :]))
    return invocations


@pytest.mark.parametrize(
    ("workflow", "step", "line", "argv"),
    [pytest.param(*invocation, id=f"{invocation[0]}:{invocation[1]}") for invocation in quality_invocations()],
)
def test_every_quality_invocation_in_a_workflow_parses_with_the_current_parser(workflow, step, line, argv):
    """#228 invariant 4: a workflow never calls quality with arguments the parser refuses."""
    cli.build_parser().parse_args(argv)


def test_the_gate_runs_the_whole_check():
    """An extractor that matches nothing would pass every case above, and a gate that names an area is no whole check."""
    assert [argv for workflow, _, _, argv in quality_invocations() if workflow == "gate.yml" and argv[:1] == ["check"]] == [["check"]]


def test_no_workflow_runs_the_fix():
    """The gate judges and never rewrites the ceiling; a workflow that ran `--fix` would lower or record it unseen."""
    assert [line for _, _, line, argv in quality_invocations() if "--fix" in argv] == []
