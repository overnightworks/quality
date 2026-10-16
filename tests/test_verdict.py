import json
import subprocess
from pathlib import Path

import pytest

from fleet_quality import cli
from fleet_quality.hygiene import CannotJudge
from fleet_quality.verdict import Job, judge, read_base_mandatory_jobs, read_jobs

FIXTURES = Path(__file__).parent / "fixtures"
GREEN_GATE = {"hygiene": "success"}


def job(name="checks", attempt=1, status="completed", conclusion="success"):
    return Job(name, attempt, status, conclusion)


def problems(jobs, mandatory=("checks",), base=(), gate=GREEN_GATE, attempt=1):
    return judge(mandatory, base, jobs, gate, attempt).problems


@pytest.mark.parametrize(
    ("jobs", "attempt", "expected"),
    [
        ([job()], 1, ()),
        ([], 1, ('mandatory job "checks" is missing from the run',)),
        (
            [job(status="in_progress", conclusion=None)],
            1,
            ('mandatory job "checks" (attempt 1) concluded in_progress',),
        ),
        ([job(status="queued", conclusion=None)], 1, ('mandatory job "checks" (attempt 1) concluded queued',)),
        ([job(conclusion="failure")], 1, ('mandatory job "checks" (attempt 1) concluded failure',)),
        ([job(conclusion="skipped")], 1, ('mandatory job "checks" (attempt 1) concluded skipped',)),
        ([job(conclusion="cancelled")], 1, ('mandatory job "checks" (attempt 1) concluded cancelled',)),
        ([job(), job(attempt=2, conclusion="failure")], 2, ('mandatory job "checks" (attempt 2) concluded failure',)),
        ([job(conclusion="failure"), job(attempt=2)], 2, ()),
        ([job()], 2, ()),
        ([job(), job(attempt=3, conclusion="failure")], 2, ()),
        ([job(), job(conclusion="failure")], 1, ('mandatory job "checks" (attempt 1) has 2 entries, not all success',)),
        ([job(), job()], 1, ()),
        ([job(name="other", conclusion="failure"), job()], 1, ()),
        ([job(name="checks (3.12)")], 1, ('mandatory job "checks" is missing from the run',)),
    ],
    ids=lambda value: value if isinstance(value, int) else "",
)
def test_judges_the_effective_result_of_each_mandatory_job(jobs, attempt, expected):
    assert problems(jobs, attempt=attempt) == expected


def test_a_gate_job_that_did_not_succeed_is_red():
    assert problems([job()], gate={"hygiene": "failure"}) == ('gate job "hygiene" result failure',)


def test_a_job_dropped_against_the_base_is_loosened_and_red():
    found = problems([job("tests-lite")], mandatory=("tests-lite",), base=("tests",))
    assert 'loosened: mandatory job "tests" removed' in found
    assert 'mandatory job "tests" is missing from the run' in found


def test_a_base_job_kept_by_the_head_is_green():
    assert problems([job()], mandatory=("checks",), base=("checks",)) == ()


def fetch_from_fixtures(*names):
    pages = [(FIXTURES / name).read_text(encoding="utf-8") for name in names]
    links = [f'<https://api.example/next{i}>; rel="next"' for i in range(len(pages) - 1)] + [None]
    served = iter(zip(pages, links, strict=True))
    return lambda url: next(served)


def test_reads_the_mandatory_job_that_only_sits_on_page_two():
    jobs = read_jobs("o/r", "1", fetch_from_fixtures("jobs-page1.json", "jobs-page2.json"))
    assert problems(jobs) == ('mandatory job "checks" (attempt 1) concluded failure',)


def test_a_missing_second_page_cannot_be_judged():
    with pytest.raises(CannotJudge, match="read 2 jobs but the run has 3"):
        read_jobs("o/r", "1", fetch_from_fixtures("jobs-page1.json"))


def test_more_pages_than_the_cap_cannot_be_judged():
    page = (FIXTURES / "jobs-page1.json").read_text(encoding="utf-8")
    with pytest.raises(CannotJudge, match="more than 10 pages"):
        read_jobs("o/r", "1", lambda url: (page, '<https://api.example/next>; rel="next"'))


def test_base_config_is_read_from_the_base_commit(build_repo):
    repo = build_repo({"quality/config.toml": '[gate]\nmandatory_jobs = ["tests"]\n'})
    assert read_base_mandatory_jobs(repo, _head(repo)) == ("tests",)


def test_a_base_without_the_config_contributes_nothing(build_repo):
    repo = build_repo({"README.md": "x"})
    assert read_base_mandatory_jobs(repo, _head(repo)) == ()


def _head(repo):
    return subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()


@pytest.fixture
def run_env(monkeypatch, build_repo, tmp_path):
    repo = build_repo({"quality/config.toml": '[gate]\nmandatory_jobs = ["checks"]\n'})
    summary = tmp_path / "summary.md"
    for key, value in {
        "GITHUB_REPOSITORY": "o/r",
        "GITHUB_RUN_ID": "1",
        "GITHUB_RUN_ATTEMPT": "1",
        "GITHUB_TOKEN": "t",
        "GATE_NEEDS": json.dumps({"hygiene": {"result": "success"}}),
        "GITHUB_STEP_SUMMARY": str(summary),
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("GATE_BASE_SHA", raising=False)
    return repo, summary


@pytest.mark.parametrize(("conclusion", "code"), [("success", 0), ("failure", 1)])
def test_verdict_exit_code_and_summary_follow_the_run(monkeypatch, capsys, run_env, conclusion, code):
    repo, summary = run_env
    page = json.dumps(
        {
            "total_count": 1,
            "jobs": [{"name": "checks", "run_attempt": 1, "status": "completed", "conclusion": conclusion}],
        }
    )
    monkeypatch.setattr(cli, "github_fetcher", lambda token: lambda url: (page, None))
    assert cli.main(["verdict", str(repo)]) == code
    assert capsys.readouterr().out.strip().endswith("green" if code == 0 else "red")
    assert summary.read_text().strip().endswith("green" if code == 0 else "red")


def test_verdict_that_cannot_read_the_run_exits_two(monkeypatch, capsys, run_env):
    repo, _ = run_env

    def refuse(url):
        raise CannotJudge("403 (does the caller grant actions: read?)")

    monkeypatch.setattr(cli, "github_fetcher", lambda token: refuse)
    assert cli.main(["verdict", str(repo)]) == 2
    assert "actions: read" in capsys.readouterr().err
