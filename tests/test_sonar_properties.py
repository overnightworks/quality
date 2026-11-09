from pathlib import Path

import pytest

from quality import cli
from tests.support.sonar import (
    CLEAN_PROPERTIES,
    PULL_REQUEST_ENVIRONMENT,
    TOKEN,
    environment_without_token,
    run_properties,
    set_environment,
    sonar_off_files,
    work_in_scratch_directory,
)


@pytest.fixture(autouse=True)
def scratch_working_directory(monkeypatch, tmp_path):
    """The config is read relative to the working directory, so every test works in its own."""
    work_in_scratch_directory(monkeypatch, tmp_path)


def test_properties_passes_the_callers_own_clean_properties(monkeypatch, capsys, build_repo):
    """SONAR-56, SONAR-57, SONAR-59: the caller's own organization, key and allowlisted settings pass"""
    assert run_properties(monkeypatch, capsys, build_repo)[0] == 0


PYTHON_REPORT_KEY = "sonar.python.coverage.reportPaths"
LCOV_REPORT_KEY = "sonar.javascript.lcov.reportPaths"


@pytest.mark.parametrize(
    ("key", "value", "files", "code", "named"),
    [
        (PYTHON_REPORT_KEY, "coverage.xml,covreage.xml", ["coverage.xml"], 2, "covreage.xml"),
        (PYTHON_REPORT_KEY, "reports/*.xml", ["reports/a.xml"], 0, None),
        (PYTHON_REPORT_KEY, "reports/*.xml", ["reports/a.txt"], 2, "reports/*.xml"),
        (PYTHON_REPORT_KEY, "reports/**", ["reports/deep/coverage.xml"], 0, None),
        (PYTHON_REPORT_KEY, "coverage.xml, other.xml", ["coverage.xml", "other.xml"], 0, None),
        (PYTHON_REPORT_KEY, "reports", ["reports/coverage.xml"], 2, "reports"),
        (PYTHON_REPORT_KEY, "", [], 0, None),
        (LCOV_REPORT_KEY, "coverage/lcov.info", [], 2, "coverage/lcov.info"),
        (LCOV_REPORT_KEY, "coverage/lcov.info", ["coverage/lcov.info"], 0, None),
        (PYTHON_REPORT_KEY, "/etc/hostname", [], 2, "/etc/hostname"),
        (PYTHON_REPORT_KEY, "../coverage.xml", [], 2, "../coverage.xml"),
        (PYTHON_REPORT_KEY, "sub/../coverage.xml", ["coverage.xml", "sub/other.xml"], 2, "sub/../coverage.xml"),
        (PYTHON_REPORT_KEY, "reports/**.xml", ["reports/a.xml"], 2, "reports/**.xml"),
    ],
    ids=[
        "typo beside a valid sibling", "glob that matches", "glob that matches nothing", "trailing dir/** over a file below",
        "space after the comma", "directory instead of a file", "empty value", "missing lcov report", "present lcov report",
        "absolute entry", "entry with a parent segment", "parent segment over an existing report", "** inside a path segment",
    ],
)
def test_properties_requires_every_named_coverage_report_to_exist(monkeypatch, capsys, build_repo, key, value, files, code, named):
    """SONAR-50, SONAR-51, SONAR-52, SONAR-53, SONAR-54, SONAR-55: each entry of a coverage report key is judged on its own; a missing report is exit 2 naming key and entry"""
    properties = CLEAN_PROPERTIES + f"{key}={value}\n"
    result = run_properties(monkeypatch, capsys, build_repo, properties, extra_files={name: "<coverage/>" for name in files})
    assert result[0] == code
    if named:
        assert key in result[2]
        assert named in result[2]


@pytest.mark.parametrize(
    ("properties", "named"),
    [
        ("sonar.scanner.sonarcloudUrl=https://evil.example\nsonar.scanner.apiBaseUrl=https://evil.example", "sonar.scanner.sonarcloudUrl"),
        ("sonar.host.url=https://evil.example", "sonar.host.url"),
        ("sonar.host.url : https://evil.example", "sonar.host.url"),
        ("sonar.host.url https://evil.example", "sonar.host.url"),
        ("sonar\\u002ehost.url=https://evil.example", "sonar.host.url"),
        ("sonar.host\\.url=https://evil.example", "sonar.host.url"),
        ("sonar.host.\\\nurl=https://evil.example", "sonar.host.url"),
        ("sonar.region=us", "sonar.region"),
        ("sonar.scanner.proxyHost=evil.example", "sonar.scanner.proxyHost"),
        ("sonar.modules=a", "sonar.modules"),
        ("sonar.projectBaseDir=/", "sonar.projectBaseDir"),
        ("sonar.working.directory=/tmp", "sonar.working.directory"),
        ("sonar.issue.ignore.multicriteria=e1", "sonar.issue.ignore.multicriteria"),
        ("sonar.qualitygate.wait=true", "sonar.qualitygate.wait"),
        ("sonar.projectName=${env.SONAR_TOKEN}", "sonar.projectName"),
        ("sonar.projectName=${sonar.token}", "sonar.projectName"),
        ("sonar.projectName=\\u0024{env.SONAR_TOKEN}", "sonar.projectName"),
        ("sonar.projectName=\\$\\{env.SONAR_TOKEN}", "sonar.projectName"),
        ("sonar.projectName=$\\\n{env.SONAR_TOKEN}", "sonar.projectName"),
        ("sonar.sources=${env.HOME}", "sonar.sources"),
        ("sonar.organization=someone-else", "sonar.organization"),
        ("sonar.projectKey=other_project", "sonar.projectKey"),
        ("sonar.projectName=bad\\uZZZZ", "malformed unicode escape"),
    ],
    ids=[
        "endpoint pair", "host url", "colon separator", "whitespace separator", "unicode escaped key", "backslash escaped key",
        "continuation split key", "region", "proxy", "modules", "base dir", "working directory", "ignore rules", "gate wait",
        "env substitution", "token substitution", "unicode escaped substitution", "backslash escaped substitution",
        "continuation split substitution", "substitution in a path key", "foreign organization", "foreign key", "malformed escape",
    ],
)
def test_properties_refuses_what_could_steer_the_token_naming_it(monkeypatch, capsys, build_repo, properties, named):
    """SONAR-57, SONAR-58, SONAR-59, SONAR-62: a foreign key, endpoint family or ${...} substitution in any decoded value is exit 2, naming the key"""
    code, _, err = run_properties(monkeypatch, capsys, build_repo, CLEAN_PROPERTIES + properties + "\n")
    assert code == 2
    assert named in err


def test_properties_drops_a_final_unpaired_backslash_like_java_properties(monkeypatch, capsys, build_repo):
    """SONAR-62: a last line ending in a backslash without a newline is read as java.util.Properties reads it"""
    properties = "sonar.projectKey=overnightworks_quality\nsonar.organization=overnightworks\\"
    assert run_properties(monkeypatch, capsys, build_repo, properties)[0] == 0


def test_properties_refuses_a_properties_file_without_the_callers_key(monkeypatch, capsys, build_repo):
    """SONAR-59: a file that does not name the caller's project key is exit 2"""
    code, _, err = run_properties(monkeypatch, capsys, build_repo, "sonar.organization=overnightworks\n")
    assert (code, "sonar.projectKey" in err) == (2, True)


def test_properties_lets_a_lone_dollar_a_brace_and_comments_pass(monkeypatch, capsys, build_repo):
    """SONAR-58, SONAR-62: a lone dollar or a brace without a dollar is not a substitution; comments are skipped"""
    properties = CLEAN_PROPERTIES + "# sonar.host.url=https://evil.example\n! sonar.region=us\nsonar.projectName=cost $ {x}\n"
    assert run_properties(monkeypatch, capsys, build_repo, properties)[0] == 0


@pytest.mark.parametrize(
    "environment",
    [{**PULL_REQUEST_ENVIRONMENT, "SONAR_TOKEN": ""}, {k: v for k, v in PULL_REQUEST_ENVIRONMENT.items() if k != "SONAR_TOKEN"}],
    ids=["empty token", "unset token"],
)
def test_properties_refuses_a_missing_token_and_never_skips(monkeypatch, capsys, build_repo, environment):
    """SONAR-61, SONAR-68: an empty or unset token is exit 2 naming SONAR_TOKEN"""
    code, _, err = run_properties(monkeypatch, capsys, build_repo, environment=environment)
    assert (code, "SONAR_TOKEN is empty" in err) == (2, True)


def test_properties_refuses_a_tracked_scanner_work_directory(monkeypatch, capsys, build_repo):
    """SONAR-60: a committed .scannerwork/report-task.txt is exit 2"""
    files = {".scannerwork/report-task.txt": "ceTaskId=x\n"}
    code, _, err = run_properties(monkeypatch, capsys, build_repo, extra_files=files)
    assert (code, ".scannerwork" in err) == (2, True)


def test_properties_refuses_a_repository_without_properties(monkeypatch, capsys, build_repo):
    """SONAR-47: no sonar-project.properties is exit 2"""
    code, _, err = run_properties(monkeypatch, capsys, build_repo, properties=None)
    assert (code, "sonar-project.properties" in err) == (2, True)


def properties_link_outside(repo: Path, elsewhere: Path) -> None:
    outside = elsewhere / "outside.properties"
    outside.write_text("sonar.host.url=https://evil.example\n")
    (repo / "sonar-project.properties").symlink_to(outside)


def properties_directory(repo: Path, elsewhere: Path) -> None:
    (repo / "sonar-project.properties").mkdir()


def properties_not_utf8(repo: Path, elsewhere: Path) -> None:
    (repo / "sonar-project.properties").write_bytes(b"sonar.sources=\xff\xfe\n")


def properties_missing(repo: Path, elsewhere: Path) -> None:
    pass


def properties_link_inside(repo: Path, elsewhere: Path) -> None:
    (repo / "real.properties").write_text(CLEAN_PROPERTIES)
    (repo / "sonar-project.properties").symlink_to("real.properties")


@pytest.mark.parametrize(
    ("arrange", "code", "said"),
    [
        (properties_link_outside, 2, "sonar-project.properties resolves outside the repository"),
        (properties_directory, 2, "sonar-project.properties is not a file"),
        (properties_not_utf8, 2, "cannot read sonar-project.properties:"),
        (properties_missing, 2, "sonar-project.properties is missing"),
        (properties_link_inside, 0, ""),
    ],
    ids=["link outside", "directory", "not utf-8", "missing", "link inside"],
)
def test_a_properties_file_quality_cannot_use_is_exit_2_naming_it_repository_relative(
    monkeypatch, capsys, build_repo, elsewhere, arrange, code, said
):
    """SONAR-47, SONAR-63, SONAR-64: sonar-project.properties is read inside the checkout and named repository-relative"""
    set_environment(monkeypatch, PULL_REQUEST_ENVIRONMENT)
    repo = build_repo({"README.md": "x\n"})
    arrange(repo, elsewhere)
    monkeypatch.chdir(repo)
    result = cli.main(["sonar", "properties"])
    captured = capsys.readouterr()
    output = captured.out + captured.err
    assert (result, said in output, str(repo) in output, TOKEN in output) == (code, True, False, False)


def test_properties_with_sonar_off_reads_no_token_and_writes_off(monkeypatch, capsys, build_repo, tmp_path):
    """SONAR-66: With [gate] sonar = false the door needs no properties file and no token, hands the job sonar=off and exits 0."""
    step_output = tmp_path / "github_output"
    code, out, err = run_properties(
        monkeypatch, capsys, build_repo, properties=None, environment=environment_without_token(), extra_files=sonar_off_files(),
        step_output=step_output,
    )
    off_line = "  · sonar         off            quality/config.toml sets [gate] sonar = false\n✓ off · nothing reaches SonarCloud · <1s\n"
    assert (code, out, err, step_output.read_text()) == (0, off_line, "", "sonar=off\n")


@pytest.mark.parametrize(
    ("properties", "token_set", "fact"),
    [
        (CLEAN_PROPERTIES, False, "sonar-project.properties exists while quality/config.toml sets [gate] sonar = false"),
        (None, True, "SONAR_TOKEN is set while quality/config.toml sets [gate] sonar = false"),
    ],
    ids=["a properties file", "a token"],
)
def test_properties_with_sonar_off_refuses_the_wiring_of_a_scan(monkeypatch, capsys, build_repo, tmp_path, properties, token_set, fact):
    """SONAR-65: While SonarCloud is off, a properties file or a passed token is exit 2 and nothing is handed to the job."""
    step_output = tmp_path / "github_output"
    code, out, err = run_properties(
        monkeypatch, capsys, build_repo, properties=properties,
        environment=PULL_REQUEST_ENVIRONMENT if token_set else environment_without_token(), extra_files=sonar_off_files(),
        step_output=step_output,
    )
    assert (code, out, err, step_output.exists()) == (2, "", f"error: {fact}\nnext: push the fix; the gate judges the new commit\n", False)


def test_properties_without_the_key_still_refuses_an_empty_token(monkeypatch, capsys, build_repo, tmp_path):
    """SONAR-13, SONAR-61, SONAR-68: A config without the key runs SonarCloud, so an empty token stays exit 2 and nothing is written."""
    step_output = tmp_path / "github_output"
    files = {"quality/config.toml": '[gate]\nmandatory_jobs = ["check"]\n'}
    code, _, err = run_properties(
        monkeypatch, capsys, build_repo, environment={**PULL_REQUEST_ENVIRONMENT, "SONAR_TOKEN": ""}, extra_files=files,
        step_output=step_output,
    )
    assert (code, "SONAR_TOKEN is empty" in err, step_output.exists()) == (2, True, False)


def test_properties_without_a_config_still_refuses_an_empty_token(monkeypatch, capsys, build_repo, tmp_path):
    """SONAR-13, SONAR-61, SONAR-68: A repository without quality/config.toml runs SonarCloud, so a missing token is exit 2 and nothing is written."""
    step_output = tmp_path / "github_output"
    (tmp_path / "quality" / "config.toml").unlink()
    code, _, err = run_properties(
        monkeypatch, capsys, build_repo, properties=None, environment=environment_without_token(), step_output=step_output,
    )
    assert (code, "SONAR_TOKEN is empty" in err, step_output.exists()) == (2, True, False)


def test_properties_with_sonar_off_and_an_unwritable_output_cannot_judge(monkeypatch, capsys, build_repo, tmp_path):
    """SONAR-103: With [gate] sonar = false an output file the job cannot be handed is exit 2 naming GITHUB_OUTPUT"""
    code, _, err = run_properties(
        monkeypatch, capsys, build_repo, properties=None, environment=environment_without_token(), extra_files=sonar_off_files(),
        step_output=tmp_path,
    )
    assert (code, "GITHUB_OUTPUT is not writable" in err) == (2, True)


def test_properties_refuses_a_malformed_sonar_key(monkeypatch, capsys, build_repo):
    """VERDICT-17: A sonar value that is not a boolean is exit 2 naming the key, never a traceback."""
    files = {"quality/config.toml": '[gate]\nmandatory_jobs = ["check"]\nsonar = "no"\n'}
    code, _, err = run_properties(monkeypatch, capsys, build_repo, extra_files=files)
    assert (code, err) == (
        2, "error: quality/config.toml [gate] sonar must be true or false\nnext:\n    write it in quality/config.toml like this:\n        [gate]\n        sonar = false\n    push the fix; the gate judges the new commit\n",
    )
