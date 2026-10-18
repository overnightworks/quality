from pathlib import Path

import yaml

CODEQL = Path(__file__).parent.parent / ".github" / "workflows" / "codeql.yml"


def load_workflow() -> dict:
    return yaml.safe_load(CODEQL.read_text(encoding="utf-8"))


def test_callers_pass_exactly_one_required_string_input_named_languages():
    inputs = load_workflow()[True]["workflow_call"]["inputs"]
    assert list(inputs) == ["languages"]
    assert inputs["languages"]["required"] is True
    assert inputs["languages"]["type"] == "string"


def test_alerts_upload_under_the_category_the_default_setup_used():
    steps = load_workflow()["jobs"]["analyze"]["steps"]
    categories = [step["with"]["category"] for step in steps if "category" in step.get("with", {})]
    assert categories == ["/language:${{ matrix.language }}"]
