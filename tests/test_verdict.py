import json
import subprocess
from pathlib import Path

import pytest
from conftest import UNKNOWN_KEY_CONFIGS

from quality import cli
from quality.config import LegacyMode
from quality.hygiene import CannotJudge
from quality.verdict import NO_BASE_SETTINGS, GateSettings, Job, judge, read_base_settings, read_jobs

FIXTURES = Path(__file__).parent / "fixtures"
GREEN_GATE = {"hygiene": "success"}


def job(name="checks", attempt=1, status="completed", conclusion="success"):
    return Job(name, attempt, status, conclusion)


def problems(jobs, mandatory=("checks",), base=(), gate=GREEN_GATE, attempt=1, legacy=LegacyMode.REPORT, base_legacy=LegacyMode.REPORT):
    return judge(GateSettings(mandatory, legacy), GateSettings(base, base_legacy), jobs, gate, attempt).problems


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
    ids=[
        "success",
        "missing",
        "in_progress",
        "queued",
        "failure",
        "skipped",
        "cancelled",
        "newer-failure-beats-older-success",
        "newer-success-beats-older-failure",
        "attempt-below-current-counts",
        "attempt-above-current-ignored",
        "duplicate-entries-one-failing",
        "duplicate-entries-all-success",
        "other-job-failing-ignored",
        "name-must-match-exactly",
    ],
)
def test_judges_the_effective_result_of_each_mandatory_job(jobs, attempt, expected):
    """#6 rule 2: the newest attempt up to the current one decides each mandatory job."""
    assert problems(jobs, attempt=attempt) == expected


def test_a_gate_job_that_did_not_succeed_is_red():
    """#6 rule 4: a gate job that did not succeed makes the verdict red."""
    assert problems([job()], gate={"hygiene": "failure"}) == ('gate job "hygiene" result failure',)


def test_a_job_dropped_against_the_base_is_loosened_and_red():
    """#6 rule 3: a mandatory job dropped against the base is loosened and red."""
    found = problems([job("tests-lite")], mandatory=("tests-lite",), base=("tests",))
    assert 'loosened: mandatory job "tests" removed' in found
    assert 'mandatory job "tests" is missing from the run' in found


def test_a_base_job_kept_by_the_head_is_green():
    """#6 rule 3: a base job the head keeps is not loosened."""
    assert problems([job()], mandatory=("checks",), base=("checks",)) == ()


def fetch_from_fixtures(*names):
    pages = [(FIXTURES / name).read_text(encoding="utf-8") for name in names]
    links = [f'<https://api.example/next{i}>; rel="next"' for i in range(len(pages) - 1)] + [None]
    served = iter(zip(pages, links, strict=True))
    return lambda url: next(served)


def test_reads_the_mandatory_job_that_only_sits_on_page_two():
    """#6 rule 2: jobs on later pages are read from recorded responses."""
    jobs = read_jobs("o/r", "1", fetch_from_fixtures("jobs-page1.json", "jobs-page2.json"))
    assert problems(jobs, mandatory=("quality / verdict",)) == ()
    assert problems(jobs, mandatory=("checks",)) == ('mandatory job "checks" is missing from the run',)


def test_a_missing_second_page_cannot_be_judged():
    """#6 rule 2: jobs read disagreeing with total_count cannot be judged."""
    fetch = fetch_from_fixtures("jobs-page1.json")
    with pytest.raises(CannotJudge, match="read 2 jobs but the run has 3"):
        read_jobs("o/r", "1", fetch)


def test_more_pages_than_the_cap_cannot_be_judged():
    """#6 rule 2: past the page cap the run cannot be judged."""
    page = (FIXTURES / "jobs-page1.json").read_text(encoding="utf-8")
    with pytest.raises(CannotJudge, match="more than 10 pages"):
        read_jobs("o/r", "1", lambda url: (page, '<https://api.example/next>; rel="next"'))


def test_base_config_is_read_from_the_base_commit(build_repo):
    """#6 rule 3: the base config is read from the base commit."""
    repo = build_repo({"quality/config.toml": '[gate]\nmandatory_jobs = ["tests"]\n'})
    assert read_base_settings(repo, _head(repo)) == GateSettings(("tests",), LegacyMode.REPORT)


def test_the_legacy_mode_is_read_from_the_base_commit(build_repo):
    """#33 sentence 4: the base config's legacy mode is read from the base commit."""
    repo = build_repo({"quality/config.toml": '[gate]\nmandatory_jobs = ["tests"]\nlegacy = "fail"\n'})
    assert read_base_settings(repo, _head(repo)) == GateSettings(("tests",), LegacyMode.FAIL)


@pytest.mark.parametrize(
    ("base_legacy", "legacy", "expected"),
    [
        (LegacyMode.FAIL, LegacyMode.REPORT, ("loosened: legacy fail → report",)),
        (LegacyMode.FAIL, LegacyMode.FAIL, ()),
        (LegacyMode.REPORT, LegacyMode.FAIL, ()),
        (LegacyMode.REPORT, LegacyMode.REPORT, ()),
    ],
    ids=["fail to report", "fail kept", "report to fail", "report kept"],
)
def test_switching_legacy_back_from_fail_is_a_loosening(base_legacy, legacy, expected):
    """#33 sentence 4: a head config that is not "fail" under a base config that is shows as loosened and is red"""
    result = judge(
        GateSettings(("checks",), legacy), GateSettings(("checks",), base_legacy), [job()], GREEN_GATE, 1
    )
    assert (result.problems, result.green) == (expected, not expected)


def test_a_base_without_the_config_contributes_nothing(build_repo):
    """#6 rule 3: a base without the config adds no mandatory jobs."""
    repo = build_repo({"README.md": "x"})
    assert read_base_settings(repo, _head(repo)) == NO_BASE_SETTINGS


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
def test_verdict_exit_code_and_summary_follow_the_run(capsys, run_env, conclusion, code):
    """#6 rule 4: exit code and step summary follow the run."""
    repo, summary = run_env
    page = json.dumps(
        {
            "total_count": 1,
            "jobs": [{"name": "checks", "run_attempt": 1, "status": "completed", "conclusion": conclusion}],
        }
    )
    assert cli.main(["verdict", str(repo)], fetcher_for=lambda token: lambda url: (page, None)) == code
    assert capsys.readouterr().out.strip().endswith("green" if code == 0 else "red")
    assert summary.read_text().strip().endswith("green" if code == 0 else "red")


@UNKNOWN_KEY_CONFIGS
def test_verdict_refuses_a_config_with_unknown_keys(capsys, run_env, config):
    """#40 rule 2: the verdict keeps refusing unknown keys of quality/config.toml."""
    repo, _ = run_env
    (repo / "quality/config.toml").write_text(config)
    assert cli.main(["verdict", str(repo)], fetcher_for=lambda token: lambda url: ({"jobs": []}, None)) == 2
    assert "unknown key" in capsys.readouterr().err


def test_verdict_that_cannot_read_the_run_exits_two(capsys, run_env):
    """#6 rule 2: a page error exits 2 with the reason."""
    repo, _ = run_env

    def refuse(url):
        raise CannotJudge("403 (does the caller grant actions: read?)")

    assert cli.main(["verdict", str(repo)], fetcher_for=lambda token: refuse) == 2
    assert "actions: read" in capsys.readouterr().err


@pytest.mark.parametrize(
    "body",
    ["not json", "[]", '{"total_count": 1, "jobs": [1]}', '{"jobs": []}'],
    ids=["not-json", "not-an-object", "job-not-an-object", "no-total-count"],
)
def test_a_page_of_the_wrong_shape_cannot_be_judged(body):
    """defect:#6 finding 3: decoding and shape errors of a page are cannot-judge, not a traceback."""
    with pytest.raises(CannotJudge, match="expected jobs page"):
        read_jobs("o/r", "1", lambda url: (body, None))


def test_a_base_config_that_git_cannot_show_cannot_be_judged(build_repo):
    """defect:#6 finding 3: the base config is listed but git cannot show it - cannot-judge, not a traceback."""
    repo = build_repo({"quality/config.toml": '[gate]\nmandatory_jobs = ["tests"]\n'})
    git = ["git", "-C", str(repo)]
    blob = subprocess.run([*git, "rev-parse", "HEAD:quality/config.toml"], capture_output=True, text=True, check=True)
    (repo / ".git" / "objects" / blob.stdout[:2] / blob.stdout.strip()[2:]).unlink()
    base = subprocess.run([*git, "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    with pytest.raises(CannotJudge, match="at the base commit"):
        read_base_settings(repo, base)


def test_a_non_numeric_run_attempt_exits_two(monkeypatch, capsys, run_env):
    """defect:#6 finding 3: a non-numeric GITHUB_RUN_ATTEMPT exits 2 and reaches the step summary."""
    repo, summary = run_env
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "first")
    page = json.dumps({"total_count": 0, "jobs": []})
    assert cli.main(["verdict", str(repo)], fetcher_for=lambda token: lambda url: (page, None)) == 2
    assert "cannot judge" in summary.read_text()


@pytest.mark.parametrize(
    "ref",
    ["--upload-pack=x", "-n", "a..b", "main.lock", "a b", "x:y", "a//b", "a/.hidden", "a.lock/b", "a/", "@", "a@{1}", "a~1"],
)
def test_a_base_ref_git_would_reject_is_refused(build_repo, ref):
    """#31 rule 1 (S8705): a base ref that is no valid git ref name, or reads as an option, is refused."""
    repo = build_repo({})
    with pytest.raises(CannotJudge, match="not a ref name git accepts"):
        read_base_settings(repo, ref)


@pytest.mark.parametrize(
    ("ref", "full_ref"),
    [
        ("main", "refs/heads/main"),
        ("release+hotfix", "refs/heads/release+hotfix"),
        ("origin/main", "refs/remotes/origin/main"),
    ],
)
def test_a_valid_ref_name_is_still_a_readable_base(build_repo, ref, full_ref):
    """#31 rule 1 (S8705): valid ref names, with a slash or a plus, still work."""
    repo = build_repo({"quality/config.toml": '[gate]\nmandatory_jobs = ["tests"]\n'})
    subprocess.run(["git", "-C", str(repo), "update-ref", full_ref, _head(repo)], check=True)
    assert read_base_settings(repo, ref) == GateSettings(("tests",), LegacyMode.REPORT)


def test_a_pull_request_that_removes_legacy_fail_is_red_in_the_verdict(monkeypatch, capsys, run_env):
    """#33 sentence 4: at the real entry, a head config without legacy under a base that fails is red, naming the loosening."""
    repo, _ = run_env
    config = repo / "quality" / "config.toml"
    config.write_text('[gate]\nmandatory_jobs = ["checks"]\nlegacy = "fail"\n')
    git = ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.invalid"]
    subprocess.run([*git, "commit", "-qam", "base switches legacy on"], check=True)
    monkeypatch.setenv("GATE_BASE_SHA", _head(repo))
    config.write_text('[gate]\nmandatory_jobs = ["checks"]\n')
    page = json.dumps(
        {"total_count": 1, "jobs": [{"name": "checks", "run_attempt": 1, "status": "completed", "conclusion": "success"}]}
    )
    assert cli.main(["verdict", str(repo)], fetcher_for=lambda token: lambda url: (page, None)) == 1
    assert "loosened: legacy fail → report" in capsys.readouterr().out
