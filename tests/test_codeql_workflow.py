from pathlib import Path

import yaml

CODEQL = Path(__file__).parent.parent / ".github" / "workflows" / "codeql.yml"


def load_workflow() -> dict:
    return yaml.safe_load(CODEQL.read_text(encoding="utf-8"))


def test_callers_pass_exactly_one_required_string_input_named_languages():
    """#68 rule 4: callers depend on exactly one required string input, languages."""
    inputs = load_workflow()[True]["workflow_call"]["inputs"]
    assert list(inputs) == ["languages"]
    assert inputs["languages"]["required"] is True
    assert inputs["languages"]["type"] == "string"


def test_alerts_upload_under_the_category_the_default_setup_used():
    """#68 rule 4: the upload category stays /language:<x>, the Default Setup's, so alerts carry over."""
    steps = load_workflow()["jobs"]["analyze"]["steps"]
    categories = [step["with"]["category"] for step in steps if "category" in step.get("with", {})]
    assert categories == ["/language:${{ matrix.language }}"]
