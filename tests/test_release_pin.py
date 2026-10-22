import re
from pathlib import Path

import pytest
import yaml
from verify_release import gate_tool_refs

GATE = Path(__file__).parent.parent / ".github" / "workflows" / "gate.yml"


def jobs_with_a_tool_checkout() -> int:
    jobs = yaml.safe_load(GATE.read_text(encoding="utf-8"))["jobs"].values()
    return sum(any(step.get("with", {}).get("repository") == "overnightworks/quality" for step in job["steps"]) for job in jobs)


RELEASE_TAG = re.compile(r"v\d+\.\d+\.\d+")


def the_one_release_tag(refs: list[str]) -> str:
    """The tag every tool checkout names; red when they differ or one is no release tag."""
    assert len(set(refs)) == 1, f"gate.yml tool refs must be one release tag: {refs}"
    assert RELEASE_TAG.fullmatch(refs[0]), f"gate.yml tool refs must be a release tag: {refs}"
    return refs[0]


@pytest.mark.parametrize("refs", [["v0.4.0", "v0.4.1", "v0.4.0"], ["main"], ["v0.4.0", "0.4.0"], []], ids=["differ", "branch", "no v", "none"])
def test_tool_refs_that_are_not_one_release_tag_are_red(refs):
    """#153 cell 11."""
    with pytest.raises(AssertionError):
        the_one_release_tag(refs)


def test_gate_bakes_one_release_tag_on_every_commit():
    """#6 rule 5: every tool checkout in gate.yml pins the same release tag."""
    text = GATE.read_text(encoding="utf-8")
    refs = gate_tool_refs(text)
    assert len(refs) == text.count("repository: overnightworks/quality") == jobs_with_a_tool_checkout()
    the_one_release_tag(refs)


def test_gate_pins_the_tool_for_every_caller_but_quality_which_runs_its_own_tree():
    """#6 rule 5: quality's own CI runs the tool from the commit under test; every other caller gets the pinned checkout."""
    text = GATE.read_text(encoding="utf-8")
    own_repository = "github.repository != 'overnightworks/quality'"
    checkouts = re.findall(r"- name: Check out the gate's own tool\n\s+if: (.+)\n", text)
    assert checkouts == [own_repository] * jobs_with_a_tool_checkout()
    tool_dirs = re.findall(r"TOOL_DIR: (.+)\n", text)
    assert tool_dirs == ["${{ github.repository == 'overnightworks/quality' && '.' || '.gate-tool' }}"] * jobs_with_a_tool_checkout()
