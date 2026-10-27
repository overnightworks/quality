import base64
from urllib.parse import parse_qs, urlparse

import pytest
from test_sonar import (
    FEATURE_ENVIRONMENT,
    FINISHED_FOR_FEATURE,
    FINISHED_FOR_MAIN,
    FINISHED_FOR_PR_7,
    GATE_OK,
    KEY,
    PUSH_ENVIRONMENT,
    TOKEN,
    failed_condition,
    gate_error,
    issues_page,
    legacy_config,
    run_cli,
    sonar_answering,
)

from quality import sonar
from quality.hygiene import CannotJudge


@pytest.fixture(autouse=True)
def checkout(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "quality").mkdir()


COVERAGE_LINE = "sonar: quality gate ERROR - new_coverage is 70.0, the gate needs at least 80"


def test_a_failed_gate_with_no_findings_exits_one_and_names_each_failed_condition(monkeypatch, capsys):
    """#193 sentence 1, cells 1, 13: ERROR prints one line per failed condition with its demand; a passed condition is not named"""
    gate = gate_error(
        failed_condition(),
        {"status": "OK", "metricKey": "new_bugs", "comparator": "GT", "actualValue": "0", "errorThreshold": "0"},
        failed_condition("new_duplicated_lines_density", "GT", "5.5", "3"),
    )
    code, out, err, _ = run_cli(monkeypatch, capsys, sonar_answering(gate=gate))
    assert code == 1
    assert err.splitlines()[:2] == [COVERAGE_LINE, "sonar: quality gate ERROR - new_duplicated_lines_density is 5.5, the gate needs at most 3"]
    assert f"sonar: new 0 · legacy 0 ({KEY}, main)" in err
    assert "new_bugs" not in out + err


def test_a_passed_gate_leaves_the_summary_and_exit_unchanged(monkeypatch, capsys):
    """#193 sentence 1, cell 2: OK prints no gate line"""
    code, out, err, _ = run_cli(monkeypatch, capsys, sonar_answering(gate=GATE_OK))
    assert (code, out, err) == (0, f"sonar: new 0 · legacy 0 ({KEY}, main)\n", "")


def test_a_failed_gate_and_new_findings_print_both(monkeypatch, capsys):
    """#193 cell 3"""
    code, out, err, _ = run_cli(monkeypatch, capsys, sonar_answering(issues=issues_page(1, 1), gate=gate_error(failed_condition())))
    assert code == 1
    assert "sonar src/a1.py:1" in out
    assert COVERAGE_LINE in err


@pytest.mark.parametrize(
    "gate",
    [
        {"projectStatus": {"status": "NONE", "conditions": []}},
        {"projectStatus": {"status": "WARN", "conditions": []}},
        {"projectStatus": {"status": "SURPRISE", "conditions": []}},
        {},
        {"projectStatus": "OK"},
        ["not", "an", "object"],
        {"projectStatus": {"status": "ERROR", "conditions": "none"}},
        {"projectStatus": {"status": "ERROR", "conditions": []}},
        gate_error(failed_condition(comparator="EQ")),
        gate_error({"status": "ERROR", "metricKey": "new_coverage"}),
    ],
    ids=["none", "warn", "unknown word", "no projectStatus", "projectStatus not an object", "wrong shape",
         "conditions not a list", "error without a failed condition", "unknown comparator", "condition without a value"],
)
def test_a_gate_answer_that_is_not_ok_or_a_readable_error_is_exit_two(monkeypatch, capsys, gate):
    """#193 sentence 1, cells 4, 5, 6: anything but OK and a readable ERROR is cannot-judge"""
    code, _, err, _ = run_cli(monkeypatch, capsys, sonar_answering(gate=gate))
    assert code == 2
    assert "sonar: cannot judge" in err


def test_the_gate_is_read_by_the_tasks_analysis_id_and_not_by_project_and_pull_request(monkeypatch, capsys):
    """#193 cell 7"""
    fake = sonar_answering()
    run_cli(monkeypatch, capsys, fake)
    (url,) = fake.urls_to("qualitygates/project_status")
    assert parse_qs(urlparse(url).query) == {"analysisId": ["analysis-7"]}


@pytest.mark.parametrize("analysis_id", [None, "", 7], ids=["absent", "empty", "not text"])
def test_a_finished_task_without_an_analysis_id_is_exit_two_before_the_gate_read(monkeypatch, capsys, analysis_id):
    """#193 cell 8"""
    fake = sonar_answering(task=FINISHED_FOR_PR_7 | {"analysisId": analysis_id})
    code, _, err, _ = run_cli(monkeypatch, capsys, fake)
    assert (code, fake.urls_to("qualitygates/project_status")) == (2, [])
    assert "analysisId" in err


@pytest.mark.parametrize("failure", [sonar.SonarHttpError(status, "u") for status in (401, 403, 404, 500)] + [CannotJudge("SonarCloud did not answer")],
                         ids=["401", "403", "404", "500", "timeout"])
def test_a_gate_read_that_fails_is_exit_two_naming_sonarcloud(monkeypatch, capsys, failure):
    """#193 cell 9"""
    fake = sonar_answering()
    fake.answers["qualitygates/project_status"] = [failure]
    code, _, err, _ = run_cli(monkeypatch, capsys, fake)
    assert code == 2
    assert "SonarCloud" in err


def test_the_gate_read_carries_the_token_in_the_header_only(monkeypatch, capsys):
    """#193 sentence 5, cell 10"""
    fake = sonar_answering()
    run_cli(monkeypatch, capsys, fake)
    (url,) = fake.urls_to("qualitygates/project_status")
    assert TOKEN not in url
    assert dict(fake.requests)[url] == "Basic " + base64.b64encode(f"{TOKEN}:".encode()).decode()


@pytest.mark.parametrize(
    ("environment", "task"), [(PUSH_ENVIRONMENT, FINISHED_FOR_MAIN), (FEATURE_ENVIRONMENT, FINISHED_FOR_FEATURE)],
    ids=["default branch", "other branch"],
)
def test_a_branch_run_makes_no_gate_request_and_ignores_a_failing_gate(monkeypatch, capsys, environment, task):
    """#193 sentence 4, cell 11"""
    fake = sonar_answering(task=task, gate=gate_error(failed_condition()))
    code, *_ = run_cli(monkeypatch, capsys, fake, environment)
    assert (code, fake.urls_to("qualitygates/project_status")) == (0, [])


@pytest.mark.parametrize(
    ("config_text", "legacy", "gate", "code"),
    [
        (legacy_config("fail"), 1, GATE_OK, 1),
        (legacy_config("fail"), 0, gate_error(failed_condition()), 1),
        (legacy_config("report"), 5, GATE_OK, 0),
    ],
    ids=["legacy fail with OK", "ERROR with no legacy", "legacy report with OK"],
)
def test_legacy_and_the_gate_each_fail_a_pull_request_on_their_own(monkeypatch, capsys, config_text, legacy, gate, code):
    """#193 sentence 6, cell 12"""
    result = run_cli(monkeypatch, capsys, sonar_answering(legacy=legacy, gate=gate), config_text=config_text)
    assert result[0] == code


@pytest.mark.parametrize(
    ("gate", "code", "in_stdout"),
    [
        ({"projectStatus": {"status": "OK", "conditions": [], "ignoredConditions": True}}, 0, True),
        (gate_error(failed_condition(), ignoredConditions=True), 1, True),
        ({"projectStatus": {"status": "OK", "conditions": [], "ignoredConditions": False}}, 0, False),
        (gate_error(failed_condition()), 1, False),
    ],
    ids=["OK ignored", "ERROR ignored", "OK not ignored", "ERROR not ignored"],
)
def test_ignored_conditions_are_disclosed_on_stdout_without_changing_the_exit(monkeypatch, capsys, gate, code, in_stdout):
    """#193 sentence 3, cells 14, 15"""
    result = run_cli(monkeypatch, capsys, sonar_answering(gate=gate))
    assert result[0] == code
    assert (sonar.IGNORED_CONDITIONS_LINE + "\n" in result[1]) == in_stdout
    assert sonar.IGNORED_CONDITIONS_LINE not in result[2]
    assert "fewer than 20 new lines" in sonar.IGNORED_CONDITIONS_LINE


def test_an_adoption_pull_request_with_an_unanalysed_default_branch_is_judged_by_the_gate(monkeypatch, capsys):
    """#193 sentence 4, cell 16"""
    fake = sonar_answering(branches=(), gate=gate_error(failed_condition()))
    code, _, err, _ = run_cli(monkeypatch, capsys, fake)
    assert code == 1
    assert COVERAGE_LINE in err
    assert "legacy unknown" in err
