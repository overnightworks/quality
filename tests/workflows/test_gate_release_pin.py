import re
from pathlib import Path

import yaml
from verify_release import RELEASE_TAG, gate_tool_refs

GATE = Path(__file__).parents[2] / ".github" / "workflows" / "gate.yml"


def jobs_with_a_tool_checkout() -> int:
    jobs = yaml.safe_load(GATE.read_text(encoding="utf-8"))["jobs"].values()
    return sum(any(step.get("with", {}).get("repository") == "overnightworks/quality" for step in job["steps"]) for job in jobs)


def test_gate_bakes_one_release_tag_on_every_commit():
    """CALLER-6: every tool checkout in gate.yml pins the same release tag."""
    text = GATE.read_text(encoding="utf-8")
    refs = gate_tool_refs(text)
    assert len(refs) == text.count("repository: overnightworks/quality") == jobs_with_a_tool_checkout()
    assert len(set(refs)) == 1, f"gate.yml tool refs must be one release tag: {refs}"
    assert RELEASE_TAG.fullmatch(refs[0]), f"gate.yml tool refs must be a release tag: {refs}"


def test_gate_pins_the_tool_for_every_caller_but_quality_which_runs_its_own_tree():
    """CALLER-7: quality's own CI runs the tool from the commit under test; every other caller gets the pinned checkout."""
    text = GATE.read_text(encoding="utf-8")
    own_repository = "github.repository != 'overnightworks/quality'"
    checkouts = re.findall(r"- name: Check out the gate's own tool\n\s+if: (.+)\n", text)
    assert checkouts == [own_repository] * jobs_with_a_tool_checkout()
    tool_dirs = re.findall(r"TOOL_DIR: (.+)\n", text)
    assert tool_dirs == ["${{ github.repository == 'overnightworks/quality' && '.' || '.gate-tool' }}"] * jobs_with_a_tool_checkout()
