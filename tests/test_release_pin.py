import re
from pathlib import Path

from fleet_quality import __version__

GATE = Path(__file__).parent.parent / ".github" / "workflows" / "gate.yml"


def test_gate_bakes_the_tag_of_the_current_version():
    """#6 rule 5: every tool checkout in gate.yml, unconditional, pins the tag of this version."""
    text = GATE.read_text(encoding="utf-8")
    tool_checkouts = re.findall(r"repository: overnightworks/quality\n(?:\s+.*\n)*?\s+ref: (\S+)", text)
    assert len(tool_checkouts) == text.count("repository: overnightworks/quality") == 2
    assert set(tool_checkouts) == {f"v{__version__}"}


def test_gate_runs_the_baked_tool_for_every_caller():
    """defect:#6 finding 4: no caller, quality itself included, runs the tool from its own working tree."""
    text = GATE.read_text(encoding="utf-8")
    assert "github.repository" not in text
    assert text.count("TOOL_DIR: .gate-tool") == 2
