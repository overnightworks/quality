from pathlib import Path

import pytest
import yaml

from quality import sonar

ROOT = Path(__file__).parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"
VERIFIED_SCANNER_VERSION = "8.1.0.6389"
TOKEN_REFERENCE = "${{ secrets.SONAR_TOKEN }}"


def load(name: str) -> dict:
    return yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def gate() -> dict:
    return load("gate.yml")


@pytest.fixture(scope="module")
def sonar_steps(gate) -> list[dict]:
    return gate["jobs"]["sonar"]["steps"]


def step_named(steps: list[dict], fragment: str) -> dict:
    return next(step for step in steps if fragment in step["name"])


def test_the_sonar_job_runs_its_steps_in_the_required_order(sonar_steps):
    """#26 rule 1: checkout, tool checkout, uv, preflight, scan, findings in this order (the coverage download is outside rule 1)"""
    kinds = [
        step["uses"].split("@")[0] if "uses" in step else step["run"].partition(" quality ")[2].split(" --")[0]
        for step in sonar_steps
        if not step.get("uses", "").startswith("actions/download-artifact")
    ]
    assert kinds == [
        "actions/checkout",
        "actions/checkout",
        "astral-sh/setup-uv",
        "sonar preflight .",
        "SonarSource/sonarqube-scan-action",
        "sonar findings",
    ]


def test_the_sonar_job_checks_out_the_caller_with_full_history_without_credentials(gate):
    """#26 rule 1: the caller checkout has fetch-depth 0 and persist-credentials false; the job is timed and read-only"""
    job = gate["jobs"]["sonar"]
    assert job["timeout-minutes"] == 20
    assert job["permissions"] == {"contents": "read"}
    assert job["steps"][0]["with"] == {"fetch-depth": 0, "persist-credentials": False}


def test_the_scan_pins_the_verified_scanner_and_the_sonarcloud_origin(sonar_steps):
    """#26 rule 4: removing scannerVersion or changing the host argument fails this test"""
    options = step_named(sonar_steps, "Scan")["with"]
    assert options["scannerVersion"] == VERIFIED_SCANNER_VERSION
    assert options["args"] == f"-Dsonar.host.url={sonar.SONAR_ORIGIN} -Dsonar.qualitygate.wait=false"


def test_the_token_reaches_only_preflight_scan_and_findings(gate):
    """#26 rule 1: no workflow-level env, no job env and no other step sees secrets.SONAR_TOKEN"""
    assert "env" not in gate
    assert not any(TOKEN_REFERENCE in str(job.get("env", {})) for job in gate["jobs"].values())
    holders = [
        (job_name, step["name"])
        for job_name, job in gate["jobs"].items()
        for step in job["steps"]
        if TOKEN_REFERENCE in str(step.get("env", {}))
    ]
    assert [name for _, name in holders] == [step["name"] for step in gate["jobs"]["sonar"]["steps"][4:]]
    assert {job for job, _ in holders} == {"sonar"}


def test_the_secret_is_required_and_the_verdict_needs_the_sonar_job(gate):
    """#26 rule 1: SONAR_TOKEN is a required workflow_call secret and verdict.needs contains sonar"""
    assert gate[True]["workflow_call"]["secrets"] == {"SONAR_TOKEN": {"required": True}}
    assert "sonar" in gate["jobs"]["verdict"]["needs"]


def test_only_the_findings_step_binds_the_pull_request_number(gate):
    """#26 rule 1: PULL_REQUEST_NUMBER is the pull request's number, set on the findings step alone"""
    binders = [
        (job_name, step["name"])
        for job_name, job in gate["jobs"].items()
        for step in job["steps"]
        if "PULL_REQUEST_NUMBER" in step.get("env", {})
    ]
    findings = step_named(gate["jobs"]["sonar"]["steps"], "Findings")
    assert binders == [("sonar", findings["name"])]
    assert findings["env"]["PULL_REQUEST_NUMBER"] == "${{ github.event.pull_request.number }}"
    assert "PULL_REQUEST_NUMBER" not in gate["jobs"]["sonar"].get("env", {})


def test_nothing_skips_the_scan_for_any_actor(gate):
    """#26 rule 2: no if: on the sonar job or its token steps, so a Dependabot or fork run is red, never skipped"""
    job = gate["jobs"]["sonar"]
    assert "if" not in job
    assert not any("if" in step for step in job["steps"] if "env" in step)


def test_quality_wires_the_secret_by_name_into_the_gate():
    """#26 rule 5: the caller job passes SONAR_TOKEN and stays the only mandatory job"""
    caller = load("ci.yml")["jobs"]["quality"]
    assert caller["secrets"] == {"SONAR_TOKEN": TOKEN_REFERENCE}
    assert caller["uses"].endswith("/.github/workflows/gate.yml")


def test_quality_binds_its_own_scan_to_its_own_project():
    """#26 rule 5: quality's own sonar-project.properties passes its own preflight"""
    sonar.preflight(ROOT, {"GITHUB_REPOSITORY": "overnightworks/quality", sonar.TOKEN_VARIABLE: "t"})


def test_the_coverage_the_tests_upload_is_the_report_the_scan_reads():
    """#26 done-when (SonarCloud coverage gate), defect:#26 finding 1: the report ci.yml uploads is the one gate.yml downloads and the scan reads"""
    uploads = [s for s in load("ci.yml")["jobs"]["tests"]["steps"] if s.get("uses", "").startswith("actions/upload-artifact")]
    downloads = [s for s in load("gate.yml")["jobs"]["sonar"]["steps"] if s.get("uses", "").startswith("actions/download-artifact")]
    assert [d["if"] for d in downloads] == ["github.repository == 'overnightworks/quality'"]
    assert [u["with"]["name"] for u in uploads] == [d["with"]["name"] for d in downloads] == ["coverage"]
    assert f"sonar.python.coverage.reportPaths={uploads[0]['with']['path']}" in (ROOT / "sonar-project.properties").read_text().splitlines()
