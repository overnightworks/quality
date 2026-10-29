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


def test_the_invocations_include_the_gate_hygiene_step():
    """An extractor that matches nothing would pass every case above."""
    assert any(workflow == "gate.yml" and argv[:1] == ["hygiene"] for workflow, _, _, argv in quality_invocations())
