from pathlib import Path

import pytest
import yaml

from quality import deadline, scanners, sides, sonar, verdict, web

GATE = Path(__file__).parents[2] / ".github" / "workflows" / "gate.yml"
INSTALL_STEP = "Install the pinned scanners"
SCAN_STEP = "Scan"


@pytest.fixture(scope="module")
def jobs() -> dict:
    return yaml.safe_load(GATE.read_text(encoding="utf-8"))["jobs"]


def limit_seconds(jobs: dict, job: str) -> int:
    return jobs[job]["timeout-minutes"] * 60


def step_limit_seconds(jobs: dict, job: str, name: str) -> int:
    step = next(step for step in jobs[job]["steps"] if step.get("name") == name)
    return step["timeout-minutes"] * 60


def test_the_check_job_outlasts_its_setup_install_run_deadline_and_margin(jobs):
    """ADVISORY-34: GitHub cancelling the job would show nothing; the run's own deadline expires first"""
    setup_allowance = 60
    untimed_calls_allowance = 60
    margin = 4 * scanners.VET_TIMEOUT_SECONDS + untimed_calls_allowance
    parts = setup_allowance + step_limit_seconds(jobs, "check", INSTALL_STEP) + deadline.RUN_DEADLINE_SECONDS + margin
    assert limit_seconds(jobs, "check") >= parts


def test_the_sonar_job_outlasts_its_scan_task_wait_findings_wait_and_one_request(jobs):
    """SONAR-85: the job's limit covers the scan, the task wait and the findings wait"""
    setup_allowance = 90
    margin = 60
    task_wait = sonar.TASK_POLL_ATTEMPTS * sonar.TASK_POLL_INTERVAL_SECONDS
    findings_wait = (sonar.FINDINGS_READ_ATTEMPTS - 1) * sonar.TASK_POLL_INTERVAL_SECONDS
    parts = (
        setup_allowance
        + step_limit_seconds(jobs, "sonar", SCAN_STEP)
        + task_wait
        + findings_wait
        + web.REQUEST_TIMEOUT_SECONDS
        + margin
    )
    assert limit_seconds(jobs, "sonar") >= parts


def test_the_verdict_job_outlasts_its_fetches_rereads_and_one_request(jobs):
    """CALLER-13: the job's limit covers its git fetches, its re-reads of the jobs and one request"""
    setup_allowance = 60
    margin = 120
    fetches = 3 * sides.FETCH_TIMEOUT_SECONDS
    rereads = verdict.MAX_REREADS * verdict.REREAD_INTERVAL_SECONDS
    parts = setup_allowance + fetches + rereads + web.REQUEST_TIMEOUT_SECONDS + margin
    assert limit_seconds(jobs, "verdict") >= parts


@pytest.mark.parametrize(("job", "name"), [("check", INSTALL_STEP), ("sonar", SCAN_STEP)])
def test_the_install_and_scan_steps_declare_a_limit_of_their_own(jobs, job, name):
    """CALLER-12: a slow download or scan ends before the job does"""
    step = next(step for step in jobs[job]["steps"] if step.get("name") == name)
    assert isinstance(step["timeout-minutes"], int)
