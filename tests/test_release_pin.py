import re
from pathlib import Path

from fleet_quality import __version__

GATE = Path(__file__).parent.parent / ".github" / "workflows" / "gate.yml"


def test_gate_bakes_the_tag_of_the_current_version():
    """#6 rule 5: every tool checkout in gate.yml pins the tag of this version."""
    text = GATE.read_text(encoding="utf-8")
    tool_checkouts = re.findall(r"repository: overnightworks/quality\n(?:\s+.*\n)*?\s+ref: (\S+)", text)
    assert len(tool_checkouts) == text.count("repository: overnightworks/quality") == 2
    assert set(tool_checkouts) == {f"v{__version__}"}


def test_gate_pins_the_tool_for_every_caller_but_quality_which_runs_its_own_tree():
    """#6 rule 5: quality's own CI runs the tool from the commit under test; every other caller gets the pinned checkout."""
    text = GATE.read_text(encoding="utf-8")
    own_repository = "github.repository != 'overnightworks/quality'"
    checkouts = re.findall(r"- name: Check out the gate's own tool\n\s+if: (.+)\n", text)
    assert checkouts == [own_repository] * 2
    tool_dirs = re.findall(r"TOOL_DIR: (.+)\n", text)
    assert tool_dirs == ["${{ github.repository == 'overnightworks/quality' && '.' || '.gate-tool' }}"] * 2
