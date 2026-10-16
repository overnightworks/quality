import pytest

from fleet_quality.config import (
    ConfigError,
    load_mandatory_jobs,
    load_secret_allowances,
    read_mandatory_jobs,
    read_secret_allowances,
)


def test_reads_the_mandatory_job_names():
    """#6 rule 1: the mandatory job names come from [gate] mandatory_jobs."""
    assert load_mandatory_jobs('[gate]\nmandatory_jobs = ["checks", "checks (3.12)"]') == ("checks", "checks (3.12)")


@pytest.mark.parametrize(
    "text",
    [
        None,
        "",
        "[gate]\n",
        "[gate]\nmandatory_jobs = []",
        '[gate]\nmandatory_job = ["checks"]',
        '[gate]\nmandatory_jobs = ["checks"]\nextra = 1',
        '[other]\nx = 1\n[gate]\nmandatory_jobs = ["checks"]',
        "[gate]\nmandatory_jobs = [1]",
        '[gate]\nmandatory_jobs = ""',
        "not toml [",
        "gate = 1",
        "gate = []",
    ],
)
def test_refuses_a_config_that_does_not_name_mandatory_jobs(text):
    """#6 rule 1: a config without a non-empty mandatory_jobs list is refused with the fix."""
    with pytest.raises(ConfigError, match="mandatory_jobs"):
        load_mandatory_jobs(text)


def test_a_repository_without_the_file_is_refused(tmp_path):
    """#6 rule 1: a repository without quality/config.toml is refused."""
    with pytest.raises(ConfigError, match="missing"):
        read_mandatory_jobs(tmp_path)


def test_a_config_file_that_cannot_be_decoded_is_refused(tmp_path):
    """defect:#6 finding 5: an unreadable config file is a ConfigError, not a traceback."""
    (tmp_path / "quality").mkdir()
    (tmp_path / "quality" / "config.toml").write_bytes(b"\xff\xfe")
    with pytest.raises(ConfigError, match="cannot be read"):
        read_mandatory_jobs(tmp_path)


def test_a_config_directory_that_cannot_be_entered_is_refused(tmp_path):
    """defect:#6 finding 5: permission denied while looking for the config is a ConfigError, not a traceback."""
    config_dir = tmp_path / "quality"
    config_dir.mkdir()
    config_dir.chmod(0)
    try:
        with pytest.raises(ConfigError, match="cannot be read"):
            read_mandatory_jobs(tmp_path)
    finally:
        config_dir.chmod(0o700)


def test_reads_every_allowed_fingerprint_with_its_reason():
    """#16: [secrets] allow maps each fingerprint to its reason, and the gate keys still load beside it."""
    text = (
        '[gate]\nmandatory_jobs = ["checks"]\n'
        '[secrets]\nallow = [{ fingerprint = "a:b:c:1", reason = "enum value, not a key" }]'
    )
    assert load_secret_allowances(text) == {"a:b:c:1": "enum value, not a key"}
    assert load_mandatory_jobs(text) == ("checks",)


@pytest.mark.parametrize("text", [None, '[gate]\nmandatory_jobs = ["checks"]'])
def test_a_config_without_allowances_allows_nothing(text):
    """#16: an absent allow list allows nothing."""
    assert load_secret_allowances(text) == {}


@pytest.mark.parametrize(
    "text",
    [
        "[secrets]\nallow = [\"a:b:c:1\"]",
        "[secrets]\nallow = [{ fingerprint = \"a:b:c:1\" }]",
        "[secrets]\nallow = [{ fingerprint = \"a:b:c:1\", reason = \"\" }]",
        "[secrets]\nallow = [{ fingerprint = \"\", reason = \"r\" }]",
        "[secrets]\nallow = [{ fingerprint = \"a\", reason = \"r\", extra = 1 }]",
        "[secrets]\nallow = [{ fingerprint = \"a\", reason = \"two\\nlines\" }]",
        "[secrets]\nallow = \"a:b:c:1\"",
        "[secrets]\nignore = []",
        "secrets = 1",
        "not toml [",
    ],
)
def test_refuses_an_allowance_that_is_not_a_fingerprint_with_a_reason(text):
    """#16: an allow entry without a fingerprint and a one-line reason is refused."""
    with pytest.raises(ConfigError, match="quality/config.toml"):
        load_secret_allowances(text)


def test_a_repository_without_the_file_allows_nothing(tmp_path):
    """#16: a missing quality/config.toml allows nothing."""
    assert read_secret_allowances(tmp_path) == {}
