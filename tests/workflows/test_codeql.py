from pathlib import Path

import yaml

CODEQL = Path(__file__).parents[2] / ".github" / "workflows" / "codeql.yml"


def load_workflow() -> dict:
    return yaml.safe_load(CODEQL.read_text(encoding="utf-8"))


def test_callers_pass_one_required_languages_input_and_an_optional_config_file():
    """CODEQL-1: languages is the one required string input. CODEQL-3: config-file is optional, a string, empty by default."""
    inputs = load_workflow()[True]["workflow_call"]["inputs"]
    assert list(inputs) == ["languages", "config-file"]
    assert inputs["languages"]["required"] is True
    assert inputs["languages"]["type"] == "string"
    assert inputs["config-file"]["required"] is False
    assert inputs["config-file"]["type"] == "string"
    assert inputs["config-file"]["default"] == ""


def test_init_hands_the_config_file_input_to_codeql():
    """CODEQL-3: init receives the caller's config-file."""
    steps = load_workflow()["jobs"]["analyze"]["steps"]
    init = next(step for step in steps if step["uses"].startswith("github/codeql-action/init@"))
    assert init["with"]["config-file"] == "${{ inputs.config-file }}"


def test_alerts_upload_under_the_category_the_default_setup_used():
    """CODEQL-2: the upload category stays /language:<x>, the Default Setup's, so alerts carry over."""
    steps = load_workflow()["jobs"]["analyze"]["steps"]
    categories = [step["with"]["category"] for step in steps if "category" in step.get("with", {})]
    assert categories == ["/language:${{ matrix.language }}"]
