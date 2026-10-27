import datetime
import re

import pytest

from quality import cli
from quality.config import (
    CONFIG_PATH,
    AdvisoryAllowance,
    Allowance,
    ConfigError,
    Declaration,
    LegacyMode,
    load_advisory_allowances,
    load_gate_settings,
    load_secret_allowances,
    load_staged_secret_allowances,
    read_advisory_allowances,
    read_gate_settings,
    read_secret_allowances,
)
from tests.support.advisories import allowance_config


def test_reads_the_mandatory_job_names():
    """#6 rule 1: the mandatory job names come from [gate] mandatory_jobs."""
    settings = load_gate_settings('[gate]\nmandatory_jobs = ["checks", "checks (3.12)"]')
    assert settings.mandatory_jobs == ("checks", "checks (3.12)")


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
        load_gate_settings(text)


def test_a_repository_without_the_file_is_refused(tmp_path):
    """#6 rule 1: a repository without quality/config.toml is refused."""
    with pytest.raises(ConfigError, match="missing"):
        read_gate_settings(tmp_path)


def test_a_config_file_that_cannot_be_decoded_is_refused(tmp_path):
    """defect:#6 finding 5: an unreadable config file is a ConfigError, not a traceback."""
    (tmp_path / "quality").mkdir()
    (tmp_path / "quality" / "config.toml").write_bytes(b"\xff\xfe")
    with pytest.raises(ConfigError, match="^cannot read quality/config.toml: 'utf-8' codec"):
        read_gate_settings(tmp_path)


def test_a_config_nested_too_deeply_cannot_be_judged_by_any_rule_that_reads_it(build_repo, capsys):
    """sentence 4: a config the TOML reader gives up on is cannot-judge, not a traceback."""
    repo = build_repo({CONFIG_PATH: f"a = {'[' * 2000}{']' * 2000}\n"})
    assert cli.main(["hygiene", str(repo)]) == 2
    named = {line.split(" - ")[0] for line in capsys.readouterr().err.splitlines() if "is not valid TOML" in line}
    assert {"hygiene: python-floor cannot judge", "hygiene: root-layout cannot judge"} <= named


def test_a_config_directory_that_cannot_be_entered_is_refused(tmp_path):
    """defect:#6 finding 5: permission denied while looking for the config is a ConfigError, not a traceback."""
    config_dir = tmp_path / "quality"
    config_dir.mkdir()
    config_dir.chmod(0)
    try:
        with pytest.raises(ConfigError, match=re.escape("cannot read quality/config.toml: [Errno 13] Permission denied")):
            read_gate_settings(tmp_path)
    finally:
        config_dir.chmod(0o700)


def test_a_config_symlink_leaving_the_repository_is_refused(tmp_path):
    """#33 S8707: a config file that resolves outside the repository is a ConfigError, never read."""
    repo = tmp_path / "repo"
    (repo / "quality").mkdir(parents=True)
    outside = tmp_path / "outside.toml"
    outside.write_text('[gate]\nmandatory_jobs = ["checks"]\nlegacy = "fail"\n')
    (repo / "quality" / "config.toml").symlink_to(outside)
    with pytest.raises(ConfigError, match="outside the repository"):
        read_gate_settings(repo)


def test_a_config_symlink_loop_is_refused(tmp_path):
    """defect:#33 review: a self-referencing config symlink is a ConfigError (exit 2), not a RuntimeError traceback."""
    (tmp_path / "quality").mkdir()
    (tmp_path / "quality" / "config.toml").symlink_to("config.toml")
    with pytest.raises(ConfigError, match="^cannot read quality/config.toml: \\[Errno 40\\]"):
        read_gate_settings(tmp_path)


HASH = "a" * 64
ENTRY = f'{{ rule = "aws-access-token", file = "tests/fixture.py", secret_sha256 = "{HASH}", reason = "fixture value" }}'


def allow_entry(**changes: str) -> str:
    fields = {"rule": "aws-access-token", "file": "tests/fixture.py", "secret_sha256": HASH, "reason": "fixture value"}
    return "{ " + ", ".join(f'{key} = "{value}"' for key, value in (fields | changes).items()) + " }"


def test_reads_every_allowed_value_with_its_reason():
    """#24 rule 1: [secrets] allow maps each rule, file and value hash to its reason, beside the gate keys."""
    text = f'[gate]\nmandatory_jobs = ["checks"]\n[secrets]\nallow = [{ENTRY}]'
    assert load_secret_allowances(text) == {Allowance("aws-access-token", "tests/fixture.py", HASH): "fixture value"}
    assert load_gate_settings(text).mandatory_jobs == ("checks",)


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


@pytest.mark.parametrize(
    "extra",
    ['[gate]\nlegacy = "fail"', "[gate]\nwhatever = 1", "[future]\nkey = 1", 'extra = "top-level"'],
    ids=["gate-key", "other-gate-key", "unknown-table", "top-level-key"],
)
def test_the_staged_allow_list_ignores_everything_outside_secrets(extra):
    """#40 rule 1: a newer gate schema cannot stop the staged scan of an older quality."""
    assert load_staged_secret_allowances(f"{extra}\n[secrets]\nallow = [{ENTRY}]") == load_secret_allowances(
        f"[secrets]\nallow = [{ENTRY}]"
    )


def test_a_repository_without_the_file_allows_nothing(tmp_path):
    """#24 rule 3: a missing quality/config.toml allows nothing."""
    assert read_secret_allowances(tmp_path) == {}


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('[gate]\nmandatory_jobs = ["checks"]', LegacyMode.REPORT),
        ('[gate]\nmandatory_jobs = ["checks"]\nlegacy = "report"', LegacyMode.REPORT),
        ('[gate]\nmandatory_jobs = ["checks"]\nlegacy = "fail"', LegacyMode.FAIL),
    ],
    ids=["key absent", "report", "fail"],
)
def test_legacy_findings_only_fail_when_the_repository_switched_them_on(text, expected):
    """#33 sentence 3: [gate] legacy is "report" unless the config says "fail"."""
    assert load_gate_settings(text).legacy is expected


@pytest.mark.parametrize("value", ['"warn"', '"FAIL"', '""', "true", "1"])
def test_any_other_legacy_value_is_a_config_error(value):
    """#33 sentence 3: any value but "report" or "fail" is refused, naming the fix."""
    with pytest.raises(ConfigError, match='legacy.*"report" or "fail"'):
        load_gate_settings(f'[gate]\nmandatory_jobs = ["checks"]\nlegacy = {value}')


def test_one_read_of_the_config_gives_the_mandatory_jobs_and_the_legacy_mode(tmp_path):
    """#33 sentence 3: the gate settings of a repository come from its quality/config.toml in one read."""
    (tmp_path / "quality").mkdir()
    (tmp_path / "quality" / "config.toml").write_text('[gate]\nmandatory_jobs = ["checks"]\nlegacy = "fail"\n')
    settings = read_gate_settings(tmp_path)
    assert (settings.mandatory_jobs, settings.legacy) == (("checks",), LegacyMode.FAIL)


QWEN_ALLOWANCE = AdvisoryAllowance(
    "GHSA-462x-pgg6-55fp", "speech/providers/qwen/uv.lock", datetime.date(2027, 1, 31), datetime.date(2026, 10, 21),
    "overnightworks/agent-presentator#371", "gradio caps pillow<13; no image input",
)


def test_an_advisory_allowance_is_read_by_every_loader_that_reads_the_whole_config(tmp_path):
    """#116 E1, E3: each [[advisories.allow]] entry is one allowance with its id, lock, until, date, item and reason,
    read alike by the gate and by hygiene."""
    text = f'[gate]\nmandatory_jobs = ["checks"]\n{allowance_config()}'
    (tmp_path / "quality").mkdir()
    (tmp_path / CONFIG_PATH).write_text(text)
    assert load_gate_settings(text).advisory_allowances == (QWEN_ALLOWANCE,)
    assert read_advisory_allowances(tmp_path) == load_advisory_allowances(text) == (QWEN_ALLOWANCE,)
    assert load_secret_allowances(text) == {}


@pytest.mark.parametrize("text", [None, '[gate]\nmandatory_jobs = ["checks"]'], ids=["no config", "no allowance"])
def test_a_config_without_advisory_allowances_allows_no_advisory(text):
    """#116 E1: no config or no [[advisories.allow]] entry allows nothing."""
    assert load_advisory_allowances(text) == ()


SONGMAKER_DECLARATION = (
    '[gate]\nmandatory_jobs = ["checks"]\n\n[[gate.loosened]]\n'
    "what = 'mandatory job \"Acceptance evidence (integration)\" removed'\n"
    'item = "overnightworks/songmaker#1345"\ndate = 2026-10-20\n'
    'reason = "the operator ruled the requirement shelf machinery out"\n'
)


def test_a_declared_loosening_is_read_with_its_ruling(tmp_path):
    """#103 rule 1 (E1): each [[gate.loosened]] entry is one declaration with its what, item, date and reason."""
    (tmp_path / "quality").mkdir()
    (tmp_path / CONFIG_PATH).write_text(SONGMAKER_DECLARATION)
    assert read_gate_settings(tmp_path).loosened == (
        Declaration(
            'mandatory job "Acceptance evidence (integration)" removed',
            "overnightworks/songmaker#1345",
            datetime.date(2026, 10, 20),
            "the operator ruled the requirement shelf machinery out",
        ),
    )


def test_a_config_without_declarations_declares_nothing():
    """#103 rule 1: a config with no [[gate.loosened]] entry declares no loosening."""
    assert load_gate_settings('[gate]\nmandatory_jobs = ["checks"]').loosened == ()


HYGIENE = ["hygiene", "--engines", "{engines}", "{repo}"]
SCAN_STAGED = ["scan-staged", "{repo}", "--engines", "{engines}"]
TEXT_DATE_DECLARATION = SONGMAKER_DECLARATION.replace("2026-10-20", '"20.10.2026"')


@pytest.mark.parametrize(
    ("command", "config", "code", "line"),
    [
        (HYGIENE, SONGMAKER_DECLARATION, 0, "hygiene: clean ("),
        (SCAN_STAGED, SONGMAKER_DECLARATION, 0, "scan-staged: clean"),
        (HYGIENE, TEXT_DATE_DECLARATION, 2, "hygiene: secret cannot judge - quality/config.toml [[gate.loosened]] entry"),
        (SCAN_STAGED, TEXT_DATE_DECLARATION, 0, "scan-staged: clean"),
        (SCAN_STAGED, allowance_config(), 0, "scan-staged: clean"),
        (SCAN_STAGED, "[advisories]\nextra = 1\n" + allowance_config(until="2027-01-31"), 0, "scan-staged: clean"),
    ],
    ids=[
        "hygiene --engines", "scan-staged", "hygiene --engines, text date", "scan-staged, text date",
        "#116 cell 13 scan-staged, an allowance", "#116 cell 13 scan-staged, a malformed allowance",
    ],
)
def test_hygiene_judges_a_declaration_as_the_verdict_does_and_the_commit_hook_reads_only_secrets(
    build_repo, clean_engines, capsys, command, config, code, line
):
    """#103 rule 1, cells 11 and 12: a [[gate.loosened]] entry is no unknown key for hygiene or the commit hook; a
    malformed one is exit 2 under hygiene, as in the verdict, and the commit hook never reads it."""
    repo = build_repo({CONFIG_PATH: config})
    assert cli.main([part.format(repo=repo, engines=clean_engines) for part in command]) == code
    captured = capsys.readouterr()
    output = captured.out + captured.err
    assert "unknown key" not in output
    assert line in output, output


ALLOWANCE_EXAMPLE_LINE = 'lock = "speech/providers/qwen/uv.lock"'
MALFORMED_SECRET = f"[secrets]\nallow = [{allow_entry(reason='')}]\n"


@pytest.mark.parametrize(
    ("entry", "named"),
    [
        (allowance_config(reason=None), "exactly an id, a lock, an until, a date, an item and a reason"),
        (allowance_config() + "extra = 1\n", "'extra': 1"),
        (allowance_config(identifier=""), "'id': ''"),
        (allowance_config(identifier="  "), "'id': '  '"),
        (allowance_config(identifier="GHSA-a\nb"), "'id': 'GHSA-a\\nb'"),
        (allowance_config(lock=""), "'lock': ''"),
        (allowance_config(lock="uv.lock\rx"), "'lock': 'uv.lock\\rx'"),
        (allowance_config(reason=""), "'reason': ''"),
        (allowance_config(reason="two\nlines"), "'reason': 'two\\nlines'"),
        (allowance_config(item="siehe Chat"), "'item': 'siehe Chat'"),
        (allowance_config(until="2027-01-31"), "'until': '2027-01-31'"),
        (allowance_config(until=datetime.datetime(2027, 1, 31)), "'until': datetime.datetime(2027, 1, 31, 0, 0)"),
        (allowance_config(date="2026-10-21"), "'date': '2026-10-21'"),
        (allowance_config(date=datetime.datetime(2026, 10, 21, 10, 0)), "'date': datetime.datetime(2026, 10, 21, 10, 0)"),
        (allowance_config() + allowance_config(reason="again"), "twice"),
        ("[advisories]\nignore = []\n", "unknown key(s) ['ignore']"),
        ('[advisories]\nallow = "x"\n', "[[advisories.allow]] as an array of tables"),
        ("advisories = 1\n", "[advisories] as a value"),
    ],
    ids=[
        "missing key", "extra key", "empty id", "blank id", "multi-line id", "empty lock", "multi-line lock",
        "empty reason", "multi-line reason", "item siehe Chat", "text until", "datetime until", "text date",
        "datetime date", "two entries for one id and lock", "unknown key in advisories", "allow not a list",
        "advisories not a table",
    ],
)
def test_a_malformed_advisory_allowance_cannot_be_judged_where_the_whole_config_is_read(
    capsys, run_env, clean_engines, entry, named
):
    """#116 cell 12: a malformed [advisories] entry is exit 2 in the verdict and in the secret and advisory rules of
    hygiene --engines, one line each naming the entry."""
    repo, _ = run_env
    (repo / CONFIG_PATH).write_text(entry + '[gate]\nmandatory_jobs = ["checks"]\n')
    assert cli.main(["verdict", str(repo)], fetcher_for=lambda token: lambda url: ("{}", None)) == 2
    verdict = capsys.readouterr().err.splitlines()
    assert (len(verdict), named in verdict[0]) == (1, True), verdict
    assert cli.main(["hygiene", "--engines", str(clean_engines), str(repo)]) == 2
    refusals = [line for line in capsys.readouterr().err.splitlines() if named in line]
    rules = [line.split(" - ")[0] for line in refusals]
    assert rules == ["hygiene: compose-port cannot judge", "hygiene: root-layout cannot judge", "hygiene: file-suffix cannot judge", "hygiene: script-name cannot judge", "hygiene: script-header cannot judge", "hygiene: test-layout cannot judge", "hygiene: test-import cannot judge", "hygiene: python-floor cannot judge", "hygiene: secret cannot judge", "hygiene: advisory cannot judge"], refusals


@pytest.mark.parametrize("entry", [allowance_config(reason=None), allowance_config(until="2027-01-31")])
def test_a_malformed_advisory_allowance_shows_the_example(entry):
    """#116 cell 12: the refusal of an [[advisories.allow]] entry shows how to write one."""
    with pytest.raises(ConfigError, match=re.escape(ALLOWANCE_EXAMPLE_LINE)):
        load_advisory_allowances(entry)


def test_the_verdict_refuses_a_malformed_secret_allowance_as_hygiene_does(capsys, run_env):
    """#116 cell 12 (the plan review's consequence): one validator reads the whole config, so the verdict refuses a
    malformed [secrets] entry too."""
    repo, _ = run_env
    (repo / CONFIG_PATH).write_text('[gate]\nmandatory_jobs = ["checks"]\n' + MALFORMED_SECRET)
    assert cli.main(["verdict", str(repo)], fetcher_for=lambda token: lambda url: ("{}", None)) == 2
    assert "[secrets] allow entry" in capsys.readouterr().err
