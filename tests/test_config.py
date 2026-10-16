import pytest

from quality.config import (
    Allowance,
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


HASH = "a" * 64
ENTRY = f'{{ rule = "aws-access-token", file = "tests/fixture.py", secret_sha256 = "{HASH}", reason = "fixture value" }}'


def allow_entry(**changes: str) -> str:
    fields = {"rule": "aws-access-token", "file": "tests/fixture.py", "secret_sha256": HASH, "reason": "fixture value"}
    return "{ " + ", ".join(f'{key} = "{value}"' for key, value in (fields | changes).items()) + " }"


def test_reads_every_allowed_value_with_its_reason():
    """#24 rule 1: [secrets] allow maps each rule, file and value hash to its reason, beside the gate keys."""
    text = f'[gate]\nmandatory_jobs = ["checks"]\n[secrets]\nallow = [{ENTRY}]'
    assert load_secret_allowances(text) == {Allowance("aws-access-token", "tests/fixture.py", HASH): "fixture value"}
    assert load_mandatory_jobs(text) == ("checks",)


@pytest.mark.parametrize("text", [None, '[gate]\nmandatory_jobs = ["checks"]'])
def test_a_config_without_allowances_allows_nothing(text):
    """#24 rule 3: an absent allow list allows nothing."""
    assert load_secret_allowances(text) == {}


@pytest.mark.parametrize(
    "text",
    [
        f"[secrets]\nallow = [\"{HASH}\"]",
        '[secrets]\nallow = [{ fingerprint = "a:b:c:1", reason = "old form" }]',
        f"[secrets]\nallow = [{allow_entry(reason='')}]",
        f"[secrets]\nallow = [{allow_entry(reason='  ')}]",
        f"[secrets]\nallow = [{allow_entry(reason='two\\nlines')}]",
        f"[secrets]\nallow = [{allow_entry(rule='')}]",
        f"[secrets]\nallow = [{allow_entry(file='')}]",
        f"[secrets]\nallow = [{allow_entry(secret_sha256='a' * 63)}]",
        f"[secrets]\nallow = [{allow_entry(secret_sha256='A' * 64)}]",
        f"[secrets]\nallow = [{allow_entry(secret_sha256='g' * 64)}]",
        f"[secrets]\nallow = [{ENTRY[:-2]}, extra = 1 }}]",
        f"[secrets]\nallow = [{{ rule = \"r\", file = \"f\", secret_sha256 = \"{HASH}\" }}]",
        f"[secrets]\nallow = [{ENTRY}, {allow_entry(reason='again')}]",
        '[secrets]\nallow = "x"',
        "[secrets]\nignore = []",
        "secrets = 1",
        "not toml [",
    ],
    ids=[
        "bare-string", "fingerprint-form", "empty-reason", "blank-reason", "two-line-reason", "empty-rule",
        "empty-file", "short-hash", "uppercase-hash", "non-hex-hash", "extra-key", "no-reason", "duplicate",
        "allow-not-a-list", "unknown-key", "secrets-not-a-table", "not-toml",
    ],
)
def test_refuses_an_allowance_that_is_not_a_rule_file_hash_and_reason(text):
    """#24 rule 2: an invalid allow entry cannot be judged."""
    with pytest.raises(ConfigError, match="quality/config.toml"):
        load_secret_allowances(text)


def test_the_old_fingerprint_form_is_refused_with_the_new_form_shown():
    """#24 rule 2: the message of a fingerprint entry shows the content-bound form."""
    with pytest.raises(ConfigError, match="secret_sha256"):
        load_secret_allowances('[secrets]\nallow = [{ fingerprint = "a:b:c:1", reason = "old form" }]')


def test_a_repository_without_the_file_allows_nothing(tmp_path):
    """#24 rule 3: a missing quality/config.toml allows nothing."""
    assert read_secret_allowances(tmp_path) == {}
