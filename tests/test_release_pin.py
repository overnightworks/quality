import re
from pathlib import Path

from fleet_quality import __version__

GATE = Path(__file__).parent.parent / ".github" / "workflows" / "gate.yml"


def test_gate_bakes_the_tag_of_the_current_version():
    baked = re.findall(r"ref: (v\d+\.\d+\.\d+)\b", GATE.read_text(encoding="utf-8"))
    assert baked == [f"v{__version__}"]
