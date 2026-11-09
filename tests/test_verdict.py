import datetime
import json
import os
import shutil
import subprocess
import sys
import urllib.error
from pathlib import Path

import pytest

from quality import __version__, cli, verdict
from quality.config import CONFIG_PATH, LOOSENED_EXAMPLE, LegacyMode
from quality.ratchet import BASELINE_PATH, NO_RENAMES
from quality.report import CannotJudge, Instruction, printable
from quality.sides import NO_BASE_SETTINGS, Side, read_base
from quality.verdict import GateSettings, JobEvent, JobKind, JobOutcome, judge, read_jobs
from tests.support.advisories import allowance_config
from tests.support.check import assert_every_line_starts_with_the_door
from tests.support.git_repositories import GIT_IDENTITY, head_commit, put
from tests.support.secrets import UNKNOWN_KEY_CONFIGS
from tests.support.sonar import reply
from tests.support.verdict import (
    CHECKS_GREEN,
    GREEN_GATE,
    ONLY_DECLARED,
    declaration,
    declared,
    fetch_jobs,
    gate_config,
    job,
    verdict_of,
    verdict_view,
    viewed,
)

FIXTURES = Path(__file__).parent / "fixtures"


def problems(jobs, mandatory=("checks",), base=(), gate=GREEN_GATE, attempt=1, legacy=LegacyMode.REPORT, base_legacy=LegacyMode.REPORT):
    head, base_side = Side(GateSettings(mandatory, legacy), None), Side(GateSettings(base, base_legacy), None)
    result = judge(head, base_side, jobs, gate, attempt, NO_RENAMES)
    return (*result.undeclared, *(outcome for outcome in result.jobs if not outcome.green))


def failed(event, fact="", name="checks", kind=JobKind.MANDATORY):
    return JobOutcome(name, kind, event, fact)


@pytest.mark.parametrize(
    ("jobs", "attempt", "expected"),
    [
        ([job()], 1, ()),
        ([], 1, (failed(JobEvent.MISSING, "not in the run"),)),
        ([job(conclusion="failure")], 1, (failed(JobEvent.FAILURE, "attempt 1"),)),
        ([job(conclusion="skipped")], 1, (failed(JobEvent.SKIPPED, "attempt 1"),)),
        ([job(conclusion="cancelled")], 1, (failed(JobEvent.CANCELLED, "attempt 1"),)),
        ([job(), job(attempt=2, conclusion="failure")], 2, (failed(JobEvent.FAILURE, "attempt 2"),)),
        ([job(conclusion="failure"), job(attempt=2)], 2, ()),
        ([job()], 2, ()),
        ([job(), job(attempt=3, conclusion="failure")], 2, ()),
        ([job(), job(conclusion="failure")], 1, (failed(JobEvent.SEVERAL_ENTRIES, "2 entries in attempt 1, not all success"),)),
        ([job(), job()], 1, ()),
        ([job(name="other", conclusion="failure"), job()], 1, ()),
        ([job(name="checks (3.12)")], 1, (failed(JobEvent.MISSING, "not in the run"),)),
    ],
    ids=[
        "success",
        "missing",
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
    """VERDICT-2, VERDICT-3, VERDICT-5, VERDICT-6, VERDICT-7, VERDICT-8: the newest attempt up to the current one
    decides each mandatory job; several entries, a missing job and a failed, skipped or cancelled one are red; other
    jobs and other names do not count."""
    assert problems(jobs, attempt=attempt) == expected


def test_a_gate_job_that_did_not_succeed_is_red():
    """VERDICT-14: a gate job that did not succeed makes the verdict red, with no mandatory entry."""
    assert problems([job()], gate={"check": "failure"}) == (failed(JobEvent.FAILURE, name="check", kind=JobKind.GATE),)


def test_a_job_dropped_against_the_base_is_loosened_and_red():
    """VERDICT-13: a mandatory job dropped against the base is loosened and red."""
    found = problems([job("tests-lite")], mandatory=("tests-lite",), base=("tests",))
    assert 'mandatory job "tests" removed' in found
    assert failed(JobEvent.MISSING, "not in the run", "tests") in found


def test_a_base_job_kept_by_the_head_is_green():
    """VERDICT-13: a base job the head keeps is not loosened."""
    assert problems([job()], mandatory=("checks",), base=("checks",)) == ()


def fetch_from_fixtures(*names):
    pages = [(FIXTURES / name).read_text(encoding="utf-8") for name in names]
    links = [f'<https://api.example/next{i}>; rel="next"' for i in range(len(pages) - 1)] + [None]
    served = iter(zip(pages, links, strict=True))
    return lambda url: next(served)


def test_reads_the_mandatory_job_that_only_sits_on_page_two():
    """VERDICT-11: jobs on later pages are read from recorded responses."""
    jobs = read_jobs("o/r", "1", fetch_from_fixtures("jobs-page1.json", "jobs-page2.json"))
    assert problems(jobs, mandatory=("quality / verdict",)) == ()
    assert problems(jobs, mandatory=("checks",)) == (failed(JobEvent.MISSING, "not in the run"),)


def test_a_missing_second_page_cannot_be_judged():
    """VERDICT-12: jobs read disagreeing with total_count cannot be judged."""
    fetch = fetch_from_fixtures("jobs-page1.json")
    with pytest.raises(CannotJudge, match="read 2 jobs but the run has 3"):
        read_jobs("o/r", "1", fetch)


def test_more_pages_than_the_cap_cannot_be_judged():
    """VERDICT-12: past the page cap the run cannot be judged."""
    page = (FIXTURES / "jobs-page1.json").read_text(encoding="utf-8")
    with pytest.raises(CannotJudge, match="more than 10 pages"):
        read_jobs("o/r", "1", lambda url: (page, '<https://api.example/next>; rel="next"'))


def test_base_config_is_read_from_the_base_commit(build_repo):
    """VERDICT-13: the base config is read from the base commit."""
    repo = build_repo({"quality/config.toml": '[gate]\nmandatory_jobs = ["tests"]\n'})
    assert read_base(repo, head_commit(repo)).settings == GateSettings(("tests",), LegacyMode.REPORT)


def test_the_legacy_mode_is_read_from_the_base_commit(build_repo):
    """VERDICT-51: the base config's legacy mode is read from the base commit."""
    repo = build_repo({"quality/config.toml": '[gate]\nmandatory_jobs = ["tests"]\nlegacy = "fail"\n'})
    assert read_base(repo, head_commit(repo)).settings == GateSettings(("tests",), LegacyMode.FAIL)


@pytest.mark.parametrize(
    ("base_legacy", "legacy", "expected"),
    [
        (LegacyMode.FAIL, LegacyMode.REPORT, ("legacy fail → report",)),
        (LegacyMode.FAIL, LegacyMode.FAIL, ()),
        (LegacyMode.REPORT, LegacyMode.FAIL, ()),
        (LegacyMode.REPORT, LegacyMode.REPORT, ()),
    ],
    ids=["fail to report", "fail kept", "report to fail", "report kept"],
)
def test_switching_legacy_back_from_fail_is_a_loosening(base_legacy, legacy, expected):
    """VERDICT-51: a head config that is not "fail" under a base config that is shows as loosened and is red"""
    head, base = Side(GateSettings(("checks",), legacy), None), Side(GateSettings(("checks",), base_legacy), None)
    result = judge(head, base, [job()], GREEN_GATE, 1, NO_RENAMES)
    assert (result.undeclared, result.green) == (expected, not expected)


@pytest.mark.parametrize(
    ("base_sonar", "sonar", "expected"),
    [
        (True, False, ("sonar true → false",)),
        (True, True, ()),
        (False, True, ()),
        (False, False, ()),
    ],
    ids=["on to off", "on kept", "off to on", "off kept"],
)
def test_turning_sonar_off_is_a_loosening(base_sonar, sonar, expected):
    """VERDICT-18: A head config that turns SonarCloud off under a base config that runs it shows as loosened and is
    red."""
    head = Side(GateSettings(("checks",), LegacyMode.REPORT, sonar=sonar), None)
    base = Side(GateSettings(("checks",), LegacyMode.REPORT, sonar=base_sonar), None)
    result = judge(head, base, [job()], GREEN_GATE, 1, NO_RENAMES)
    assert (result.undeclared, result.green) == (expected, not expected)


def test_an_adoption_may_start_with_sonar_off(build_repo):
    """VERDICT-19: A base commit without the config holds no gate, so a head that adopts the gate with sonar off is
    green."""
    repo = build_repo({"README.md": "x"})
    base = read_base(repo, head_commit(repo))
    head = Side(GateSettings(("checks",), LegacyMode.REPORT, sonar=False), None)
    result = judge(head, base, [job()], GREEN_GATE, 1, NO_RENAMES)
    assert (result.undeclared, result.green) == ((), True)


def test_a_base_without_the_config_contributes_nothing(build_repo):
    """VERDICT-19: a base without the config adds no mandatory jobs."""
    repo = build_repo({"README.md": "x"})
    assert read_base(repo, head_commit(repo)) == Side(NO_BASE_SETTINGS, None)


@pytest.mark.parametrize(("conclusion", "code"), [("success", 0), ("failure", 1)])
def test_verdict_exit_code_and_summary_follow_the_run(capsys, run_env, conclusion, code):
    """VERDICT-32, VERDICT-35: exit code and step summary follow the run."""
    _, summary = run_env
    page = json.dumps(
        {
            "total_count": 1,
            "jobs": [{"id": 1, "name": "checks", "run_attempt": 1, "status": "completed", "conclusion": conclusion}],
        }
    )
    assert cli.main(["verdict"], fetcher_for=lambda token: lambda url: (page, None)) == code
    printed = capsys.readouterr().out
    assert (printed.splitlines()[-1].startswith("✓ green · " if code == 0 else "! red · "), summary.read_text()) == (True, f"```\n{printed}```\n")


@pytest.mark.parametrize(
    ("job_name_in_toml", "printed"),
    [("a\\n::error::b", "a\\x0a::error::b"), ("\\u202eneerg :tcidrev\\u202c", "\\u202eneerg :tcidrev\\u202c")],
)
def test_a_job_name_with_a_line_feed_or_format_character_prints_as_one_escaped_problem_line_in_log_and_summary(
    capsys, run_env, job_name_in_toml, printed
):
    """VERDICT-54: the job's row is escaped, the same in the step summary, and the closing line stays a line."""
    repo, summary = run_env
    (repo / "quality/config.toml").write_text(f'[gate]\nmandatory_jobs = ["checks", "{job_name_in_toml}"]\n')
    page = json.dumps(
        {"total_count": 1, "jobs": [{"id": 1, "name": "checks", "run_attempt": 1, "status": "completed", "conclusion": "success"}]}
    )
    assert cli.main(["verdict"], fetcher_for=lambda token: lambda url: (page, None)) == 1
    lines = capsys.readouterr().out.split("\n")
    assert [line for line in lines if printed in line] != []
    assert (lines[-2].startswith("! red · "), lines[-1]) == (True, "")
    assert_every_line_starts_with_the_door(lines)
    assert summary.read_text().split("\n") == ["```", *lines[:-1], "```", ""]


@UNKNOWN_KEY_CONFIGS
def test_verdict_refuses_a_config_with_unknown_keys(capsys, run_env, config):
    """VERDICT-39: the verdict keeps refusing unknown keys of quality/config.toml."""
    repo, _ = run_env
    (repo / "quality/config.toml").write_text(config)
    assert cli.main(["verdict"], fetcher_for=lambda token: lambda url: ({"jobs": []}, None)) == 2
    assert "unknown key" in capsys.readouterr().err


def test_a_head_without_a_config_is_missing_at_the_head(capsys, run_env):
    """VERDICT-4: an absent head config is exit 2, its fact `is missing`."""
    repo, _ = run_env
    (repo / CONFIG_PATH).unlink()
    assert cli.main(["verdict"], fetcher_for=lambda token: lambda url: ({"jobs": []}, None)) == 2
    assert f"error: {CONFIG_PATH} is missing" in capsys.readouterr().err


def test_verdict_that_cannot_read_the_run_exits_two(capsys, run_env):
    """READ-6, VERDICT-38: a page error exits 2 with the reason and its step."""

    def refuse(url):
        raise verdict.unreadable_jobs(url, urllib.error.HTTPError(url, 403, "Forbidden", {}, None))

    assert cli.main(["verdict"], fetcher_for=lambda token: refuse) == 2
    assert "next:\n    grant actions: read in the caller job's permissions\n" in capsys.readouterr().err


@pytest.mark.parametrize(
    "body",
    ["not json", "[]", '{"total_count": 1, "jobs": [1]}', '{"jobs": []}'],
    ids=["not-json", "not-an-object", "job-not-an-object", "no-total-count"],
)
def test_a_page_of_the_wrong_shape_cannot_be_judged(body):
    """VERDICT-46: decoding and shape errors of a page are cannot judge, not a traceback."""
    with pytest.raises(CannotJudge, match="expected jobs page"):
        read_jobs("o/r", "1", lambda url: (body, None))


def test_a_base_config_that_git_cannot_show_cannot_be_judged(build_repo):
    """BASELINE-23: a base file git lists but cannot show is cannot judge, not a traceback."""
    repo = build_repo({"quality/config.toml": '[gate]\nmandatory_jobs = ["tests"]\n'})
    git = ["git", "-C", str(repo)]
    blob = subprocess.run([*git, "rev-parse", "HEAD:quality/config.toml"], capture_output=True, text=True, check=True)
    (repo / ".git" / "objects" / blob.stdout[:2] / blob.stdout.strip()[2:]).unlink()
    base = subprocess.run([*git, "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    with pytest.raises(CannotJudge, match=rf"^cannot read {CONFIG_PATH} at the base commit {base}: git exited [1-9]"):
        read_base(repo, base)


def test_a_non_numeric_run_attempt_exits_two(monkeypatch, capsys, run_env):
    """VERDICT-44: exit 2, and the refusal reaches the step summary."""
    _, summary = run_env
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "first")
    page = json.dumps({"total_count": 0, "jobs": []})
    assert cli.main(["verdict"], fetcher_for=lambda token: lambda url: (page, None)) == 2
    assert "error: GITHUB_RUN_ATTEMPT is not a run attempt" in summary.read_text()


def test_a_pull_request_that_turns_sonar_off_is_red_in_the_verdict(monkeypatch, capsys, run_env):
    """VERDICT-18: At the real entry, a head config with sonar = false under a base that scans is red, naming the
    loosening."""
    repo, _ = run_env
    monkeypatch.setenv("GATE_BASE_SHA", head_commit(repo))
    (repo / "quality" / "config.toml").write_text('[gate]\nmandatory_jobs = ["checks"]\nsonar = false\n')
    page = json.dumps(
        {"total_count": 1, "jobs": [{"id": 1, "name": "checks", "run_attempt": 1, "status": "completed", "conclusion": "success"}]}
    )
    assert cli.main(["verdict"], fetcher_for=lambda token: lambda url: (page, None)) == 1
    printed = capsys.readouterr().out
    assert ("    ! sonar true → false" in printed, "! red" in printed) == (True, True)


def test_a_pull_request_that_removes_legacy_fail_is_red_in_the_verdict(monkeypatch, capsys, run_env):
    """VERDICT-51: at the real entry, a head config without legacy under a base that fails is red, naming the loosening."""
    repo, _ = run_env
    config = repo / "quality" / "config.toml"
    config.write_text('[gate]\nmandatory_jobs = ["checks"]\nlegacy = "fail"\n')
    git = ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.invalid"]
    subprocess.run([*git, "commit", "-qam", "base switches legacy on"], check=True)
    monkeypatch.setenv("GATE_BASE_SHA", head_commit(repo))
    config.write_text('[gate]\nmandatory_jobs = ["checks"]\n')
    page = json.dumps(
        {"total_count": 1, "jobs": [{"id": 1, "name": "checks", "run_attempt": 1, "status": "completed", "conclusion": "success"}]}
    )
    assert cli.main(["verdict"], fetcher_for=lambda token: lambda url: (page, None)) == 1
    assert "    ! legacy fail → report" in capsys.readouterr().out


LEGACY_FAIL_CONFIG = '[gate]\nmandatory_jobs = ["checks"]\nlegacy = "fail"\n'
NEW_CCN_KEY = '[ccn."src/a.py"]\n"f" = 16\n'


def main_lines(count: int) -> str:
    return f'[function_lines."src/quality/cli.py"]\n"main" = {count}\n'


@pytest.mark.parametrize(
    ("base", "head", "code", "expected"),
    [
        (main_lines(82), main_lines(90), 1, "    ! size baseline raises function_lines src/quality/cli.py main 82 -> 90"),
        (main_lines(82), main_lines(70), 0, "✓ green"),
        (main_lines(82), main_lines(82), 0, "✓ green"),
        (None, main_lines(82), 0, "✓ green"),
        (main_lines(82), None, 1, "    ! quality/baseline.toml removed"),
        (main_lines(82), main_lines(82) + NEW_CCN_KEY, 1, "    ! size baseline adds ccn src/a.py f 16"),
        (main_lines(82) + NEW_CCN_KEY, main_lines(82), 0, "✓ green"),
        (None, None, 0, "✓ green"),
        ("[ccn", main_lines(82), 2, f"{BASELINE_PATH} at the base commit {{base}}: {BASELINE_PATH} is not valid TOML"),
        (main_lines(82), "[ccn", 2, f"error: {BASELINE_PATH} at the head: {BASELINE_PATH} is not valid TOML"),
        (main_lines(82), f"[ccn]\na = {'9' * 5000}\n", 2, f"error: {BASELINE_PATH} at the head: {BASELINE_PATH} is not valid TOML"),
        (main_lines(82), f"a = {'[' * 5000}{']' * 5000}\n", 2, f"error: {BASELINE_PATH} at the head: {BASELINE_PATH} is not valid TOML"),
        (b"\xff", main_lines(82), 2, f"cannot read {BASELINE_PATH} at the base commit {{base}}: 'utf-8' codec"),
        (main_lines(82), b"\xff", 2, f"error: {BASELINE_PATH} at the head: {BASELINE_PATH} is not valid UTF-8 ("),
    ],
    ids=[
        "growth", "lowering", "equal", "adoption", "base file deleted", "new key", "key dropped", "both absent",
        "base malformed", "head malformed", "head oversized integer", "head nested too deep", "base not UTF-8",
        "head not UTF-8",
    ],
)
def test_the_size_baseline_only_falls_against_the_base(capsys, pull_request, base, head, code, expected):
    """BASELINE-1, BASELINE-2, BASELINE-3, BASELINE-6, BASELINE-7, BASELINE-8, BASELINE-10, BASELINE-20: the
    baseline only falls, and a malformed one on either side cannot be judged."""
    repo, commit_base = pull_request
    base_sha = commit_base(base)
    put(repo / BASELINE_PATH, head)
    actual_code, output = verdict_of(repo, capsys)
    assert (actual_code, expected.format(base=base_sha) in output, "Traceback" in output) == (code, True, False)


@pytest.mark.parametrize(
    ("base", "head", "hints"),
    [("[ccn", main_lines(82), 0), (main_lines(82), "[ccn", 1), (b"\xff", main_lines(82), 0), (main_lines(82), b"\xff", 1)],
    ids=["base malformed", "head malformed", "base not UTF-8", "head not UTF-8"],
)
def test_only_the_head_baseline_is_told_to_rewrite_itself(capsys, pull_request, base, head, hints):
    """BASELINE-25: a pull request cannot rewrite its base, so only a malformed head baseline gets the restore hint."""
    repo, commit_base = pull_request
    commit_base(base)
    put(repo / BASELINE_PATH, head)
    code, output = verdict_of(repo, capsys)
    assert (code, output.count("restore quality/baseline.toml from git, or repair it by hand")) == (2, hints), output


UNKNOWN_TABLE_FACT = f"has an unknown table [future]; quality {__version__} knows ccn, file_lines, function_lines, clones"


@pytest.mark.parametrize(
    ("base", "head", "advice"),
    [(main_lines(82), "[future]\n", 1), ("[future]\n", main_lines(82), 0)],
    ids=["head", "base"],
)
def test_an_unknown_baseline_table_states_its_fact_and_advises_only_the_head(capsys, pull_request, base, head, advice):
    """BASELINE-25, CEILING-3: the head is sent to the release its gate pins; the base gets no advice."""
    repo, commit_base = pull_request
    commit_base(base)
    put(repo / BASELINE_PATH, head)
    code, output = verdict_of(repo, capsys)
    assert code == 2
    assert UNKNOWN_TABLE_FACT in output
    assert output.count("run the quality release your gate pins") == advice, output
    assert f"restore {BASELINE_PATH}" not in output


def make_baseline_a_directory(repo: Path) -> None:
    (repo / BASELINE_PATH).unlink()
    put(repo / BASELINE_PATH / "inner.toml", main_lines(82))


def make_baseline_a_symlink(repo: Path) -> None:
    (repo / BASELINE_PATH).rename(repo / "target.toml")
    (repo / BASELINE_PATH).symlink_to("../target.toml")


@pytest.mark.parametrize("make_non_file", [make_baseline_a_directory, make_baseline_a_symlink], ids=["a directory", "a symlink"])
def test_a_base_baseline_that_is_no_regular_file_is_named_so_and_never_parsed(capsys, monkeypatch, pull_request, make_non_file):
    """BASELINE-22: a directory or symlink at the base commit is exit 2, `not a regular file`, and is never parsed."""
    repo, commit_base = pull_request
    commit_base(main_lines(82))
    make_non_file(repo)
    git = ["git", "-C", str(repo), *GIT_IDENTITY]
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run([*git, "commit", "-q", "-m", "non-file"], check=True)
    base_sha = head_commit(repo)
    monkeypatch.setenv("GATE_BASE_SHA", base_sha)
    subprocess.run([*git, "rm", "-rq", "--cached", BASELINE_PATH], check=True)
    if (repo / BASELINE_PATH).is_symlink():
        (repo / BASELINE_PATH).unlink()
    else:
        shutil.rmtree(repo / BASELINE_PATH)
    put(repo / BASELINE_PATH, main_lines(82))
    code, output = verdict_of(repo, capsys)
    expected = f"cannot read {BASELINE_PATH} at the base commit {base_sha}: not a regular file"
    assert (code, expected in output, "TOML" in output) == (2, True, False)


def test_a_base_git_refuses_to_read_is_worded_without_git_stderr_or_the_checkout_path(capsys, monkeypatch, pull_request):
    """BASELINE-23, BASELINE-24: git's exit code is named and git's own words are not printed."""
    repo, commit_base = pull_request
    commit_base(main_lines(82))
    monkeypatch.setenv("GATE_BASE_SHA", "f" * 40)
    code, output = verdict_of(repo, capsys)
    assert (code, f"cannot read the base commit {'f' * 40}: git exited 128" in output, "fatal:" in output) == (2, True, False)


def test_a_head_baseline_that_links_out_of_the_repository_cannot_be_judged(capsys, pull_request, elsewhere):
    """BASELINE-21: a head baseline symlinked out of the repository is exit 2, no traceback."""
    repo, commit_base = pull_request
    commit_base(main_lines(82))
    outside = elsewhere / "baseline.toml"
    outside.write_text(main_lines(82))
    (repo / BASELINE_PATH).unlink()
    (repo / BASELINE_PATH).symlink_to(outside)
    code, output = verdict_of(repo, capsys)
    expected = f"error: {BASELINE_PATH} at the head: {BASELINE_PATH} resolves outside the repository"
    assert (code, expected in output, "Traceback" in output) == (2, True, False)


def test_without_a_base_the_base_is_never_read(capsys, monkeypatch, pull_request, fake_git, elsewhere):
    """BASELINE-28: on a push GATE_BASE_SHA is empty, so a raise against the previous commit stays green, git unread."""
    repo, commit_base = pull_request
    commit_base(main_lines(82))
    monkeypatch.setenv("GATE_BASE_SHA", "")
    put(repo / BASELINE_PATH, main_lines(90))
    calls = elsewhere / "git-calls"
    fake_git(f'echo "$@" >> "{calls}"')
    assert viewed(repo, capsys) == (0, ["✓ green"])
    assert not calls.exists()


@pytest.mark.parametrize(
    ("base_sha", "expected"),
    [
        ("HEAD~1", "error: GATE_BASE_SHA is not a commit sha"),
        ("f" * 40, f"cannot read the base commit {'f' * 40}"),
        ("-x", "error: GATE_BASE_SHA is not a commit sha"),
    ],
    ids=["a revision expression", "a sha missing from the clone", "an option"],
)
def test_a_base_that_is_no_commit_sha_or_is_missing_from_the_clone_cannot_be_judged(
    capsys, monkeypatch, pull_request, fake_git, elsewhere, base_sha, expected
):
    """BASELINE-23, BASELINE-26, VERDICT-44: exit 2 naming the base; a base that is no commit sha never reaches git."""
    repo, commit_base = pull_request
    commit_base(main_lines(82))
    monkeypatch.setenv("GATE_BASE_SHA", base_sha)
    calls = elsewhere / "git-calls"
    fake_git(f'echo "$@" >> "{calls}"')
    code, output = verdict_of(repo, capsys)
    assert (code, expected in output, "Traceback" in output) == (2, True, False)
    assert calls.exists() == (base_sha == "f" * 40)


def test_a_raised_baseline_and_a_dropped_legacy_fail_are_both_listed(capsys, pull_request):
    """BASELINE-1, BASELINE-2, VERDICT-30, VERDICT-51: a raised entry stands beside the loosened legacy mode, red."""
    repo, commit_base = pull_request
    commit_base(main_lines(82), config=LEGACY_FAIL_CONFIG)
    put(repo / "quality" / "config.toml", '[gate]\nmandatory_jobs = ["checks"]\n')
    put(repo / BASELINE_PATH, main_lines(90))
    code, output = verdict_of(repo, capsys)
    assert code == 1
    assert "    ! legacy fail → report" in output
    assert "    ! size baseline raises function_lines src/quality/cli.py main 82 -> 90" in output


def test_a_verdict_without_git_on_the_path_cannot_judge(capsys, monkeypatch, pull_request, elsewhere):
    """With no git to read the base commit the verdict is exit 2, never a traceback."""
    repo, commit_base = pull_request
    commit_base(main_lines(82))
    monkeypatch.setenv("PATH", str(elsewhere))
    code, output = verdict_of(repo, capsys)
    assert (code, "error: cannot run git" in output, "Traceback" in output) == (2, True, False)


def test_the_github_fetcher_sends_the_token_and_reads_the_jobs_page(stub_server):
    """VERDICT-36: the real fetcher sends the token and returns the jobs page unchanged."""
    origin, heard = stub_server(reply(200, CHECKS_GREEN.encode()))
    assert (fetch_jobs(origin), heard[0]["Authorization"]) == (CHECKS_GREEN, "Bearer t")


def test_a_truncated_jobs_page_cannot_be_judged(stub_server):
    """VERDICT-20: a body cut short is cannot judge, which the verdict reports as exit 2."""
    origin, _ = stub_server(reply(200, b"abcd", Content_Length="100"))
    with pytest.raises(CannotJudge):
        fetch_jobs(origin)


def test_the_github_token_never_follows_a_redirect(stub_server):
    """VERDICT-37: a 302 to another host is refused, and that host never sees the token."""
    other_origin, other_heard = stub_server(reply(200, CHECKS_GREEN.encode()))
    origin, _ = stub_server(reply(302, b"", Location=other_origin.replace("127.0.0.1", "localhost") + "/stolen"))
    with pytest.raises(CannotJudge, match="HTTP Error 302"):
        fetch_jobs(origin)
    assert other_heard == []


def test_a_refused_jobs_page_keeps_the_verdicts_wording(stub_server):
    """READ-6, VERDICT-47: a GitHub that refuses reads as the URL asked and urllib's words, with both steps."""
    origin, _ = stub_server(reply(403, b""))
    with pytest.raises(CannotJudge) as refused:
        fetch_jobs(origin)
    assert (refused.value.reason.fact, refused.value.reason.next_steps) == (
        f"cannot read {origin}/jobs: HTTP Error 403: Forbidden", (Instruction("grant actions: read in the caller job's permissions"), Instruction("re-run this run's failed jobs"))
    )


def test_git_output_is_decoded_as_utf_8_whatever_the_locale(build_repo):
    """under LANG=C a commit subject in UTF-8 still reads, it does not fail to decode."""
    repo = build_repo({})
    subprocess.run(["git", "-C", str(repo), *GIT_IDENTITY, "commit", "-q", "--allow-empty", "-m", "Grüße"], check=True)
    program = (
        "import sys; from quality import process; "
        "print(ascii(process.run(['git', '-C', sys.argv[1], 'log', '-1', '--format=%s']).stdout.strip()))"
    )
    ascii_locale = {**os.environ, "LC_ALL": "C", "LANG": "C", "PYTHONUTF8": "0", "PYTHONCOERCECLOCALE": "0"}
    read = subprocess.run(
        [sys.executable, "-c", program, str(repo)], capture_output=True, text=True, env=ascii_locale, check=False
    )
    assert (read.returncode, read.stdout.strip()) == (0, ascii("Grüße")), read.stderr


B_REMOVED = 'mandatory job "B" removed'


@pytest.fixture
def judged(capsys, monkeypatch, pull_request):
    """Commits the base side, writes the head side, and returns the verdict's exit code and printed lines."""
    repo, commit_base = pull_request

    def run(base_config, head_config, jobs, baselines=(None, None), with_base=True):
        put(repo / CONFIG_PATH, base_config)
        commit_base(baselines[0])
        if not with_base:
            monkeypatch.setenv("GATE_BASE_SHA", "")
        put(repo / CONFIG_PATH, head_config)
        put(repo / BASELINE_PATH, baselines[1])
        listed = [
            {"id": number, "name": name, "run_attempt": 1, "status": "completed", "conclusion": state}
            for number, (name, state) in enumerate(jobs.items())
        ]
        page = json.dumps({"total_count": len(listed), "jobs": listed})
        code = cli.main(["verdict"], fetcher_for=lambda token: lambda url: (page, None), clock=lambda: 0.0)
        return code, verdict_view(capsys.readouterr().out)

    return run


A_RAN = {"A": "success"}
UNDECLARED_B = ['    ! mandatory job "B" removed']
LINE_FEED_JOB = 'mandatory job "a\n::error::b" removed'
RLO_JOB = 'mandatory job "x\u202ey" removed'
LITERAL_JOB = 'mandatory job "x\\u202ey" removed'
UNDECLARED_LITERAL = [
    "    ! " + LITERAL_JOB, declared(LITERAL_JOB), "! red",
]
MAIN_RAISED = "size baseline raises function_lines src/quality/cli.py main 82 -> 90"


@pytest.mark.parametrize(
    ("base", "head", "jobs", "baselines", "expected"),
    [
        (gate_config("A", "B"), gate_config("A"), A_RAN, (None, None), [*UNDECLARED_B, "! red"]),
        (gate_config("A", "B"), gate_config("A", declared=(declaration(B_REMOVED),)), A_RAN, (None, None),
         [declared(B_REMOVED), ONLY_DECLARED]),
        (gate_config("A", "B"), gate_config("A", declared=(declaration(B_REMOVED),)), {**A_RAN, "B": "failure"},
         (None, None), [declared(B_REMOVED), ONLY_DECLARED]),
        (gate_config("A", "B", legacy="fail"), gate_config("A", declared=(declaration(B_REMOVED),)), A_RAN, (None, None),
         [declared(B_REMOVED), "    ! legacy fail → report", "! red"]),
        (gate_config("A", "B"), gate_config("A", declared=(declaration(B_REMOVED),)), {"A": "failure"}, (None, None),
         [declared(B_REMOVED), "! red"]),
        (gate_config("A", "B"), gate_config("A", declared=(declaration('mandatory job "b" removed'),)), A_RAN,
         (None, None),
         [UNDECLARED_B[0], declared('mandatory job "b" removed'), "! red"]),
        (gate_config("A", declared=(declaration(B_REMOVED),)), gate_config("A", declared=(declaration(B_REMOVED),)),
         A_RAN, (None, None), ["✓ green"]),
        (gate_config("A", declared=(declaration(B_REMOVED),)), gate_config("A"), A_RAN, (None, None), ["✓ green"]),
        (gate_config("A", "B", declared=(declaration(B_REMOVED),)), gate_config("A", declared=(declaration(B_REMOVED),)),
         A_RAN, (None, None), [*UNDECLARED_B, "! red"]),
        (gate_config("A", "B", declared=(declaration(B_REMOVED),)),
         gate_config("A", declared=(declaration(B_REMOVED, item="#103", date="2026-10-21"),)), A_RAN, (None, None),
         [declared(B_REMOVED, "#103 of 2026-10-21"), ONLY_DECLARED]),
        (gate_config("A"), gate_config("A", declared=(declaration(MAIN_RAISED),)), A_RAN, (main_lines(82), main_lines(90)),
         [declared(MAIN_RAISED), ONLY_DECLARED]),
        (gate_config("A"), gate_config("A", declared=(declaration("quality/baseline.toml removed"),)), A_RAN,
         (main_lines(82), None), [declared("quality/baseline.toml removed"), ONLY_DECLARED]),
        (gate_config("A", legacy="fail"), gate_config("A", declared=(declaration("legacy fail → report"),)), A_RAN,
         (None, None), [declared("legacy fail → report"), ONLY_DECLARED]),
        (gate_config("A"), gate_config("A") + "sonar = false\n" + declaration("sonar true → false"), A_RAN,
         (None, None), [declared("sonar true → false"), ONLY_DECLARED]),
        (gate_config("A", "a\n::error::b"), gate_config("A", declared=(declaration(LINE_FEED_JOB),)), A_RAN,
         (None, None), [declared('mandatory job "a\\x0a::error::b" removed'), ONLY_DECLARED]),
        (gate_config("A", "a\n::error::b"), gate_config("A", declared=(declaration(printable(LINE_FEED_JOB)),)), A_RAN,
         (None, None), ['    ! mandatory job "a\\x0a::error::b" removed',
                        declared('mandatory job "a\\x0a::error::b" removed'), "! red"]),
        (gate_config("A", "x\u202ey"), gate_config("A", declared=(declaration(RLO_JOB),)), A_RAN, (None, None),
         [declared(LITERAL_JOB), ONLY_DECLARED]),
        (gate_config("A", "x\u202ey"), gate_config("A", declared=(declaration(LITERAL_JOB),)), A_RAN, (None, None),
         UNDECLARED_LITERAL),
        (gate_config("A", "x\\u202ey"), gate_config("A", declared=(declaration(LITERAL_JOB),)), A_RAN, (None, None),
         [declared(LITERAL_JOB), ONLY_DECLARED]),
        (gate_config("A", "x\\u202ey"), gate_config("A", declared=(declaration(RLO_JOB),)), A_RAN, (None, None),
         UNDECLARED_LITERAL),
        (None, gate_config("A", declared=(declaration(B_REMOVED),)), A_RAN, (None, None),
         [declared(B_REMOVED), "! red"]),
    ],
    ids=[
        "cell 1 undeclared removal", "cell 2 declared removal", "cell 3 declared removal, B still runs and fails",
        "cell 4 declared next to undeclared", "cell 5 declared next to A failed", "cell 6 a typo in what",
        "cell 7 inherited declaration", "cell 7 inherited declaration deleted", "cell 8 inherited declaration",
        "cell 8 a new item and date", "cell 10 baseline raise", "cell 10 baseline removal", "cell 10 legacy", "sonar off declared",
        "cell 13 a line feed declared as the TOML escape", "cell 13 the printed form copied", "a format character declared as its TOML escape",
        "the printed form of a format character copied", "a name holding the escape text itself",
        "the escape text declared as a format character", "cell 26 adoption",
    ],
)
def test_a_declared_loosening_stays_red_and_says_so(judged, base, head, jobs, baselines, expected):
    """VERDICT-13, VERDICT-52, VERDICT-53, VERDICT-54, VERDICT-55, VERDICT-56, VERDICT-59, VERDICT-60, VERDICT-62:
    each loosening line says whether it is declared, and only declarations the head adds, matching exactly, declare;
    a declared job removal is no longer judged."""
    assert judged(base, head, jobs, baselines) == (0 if expected == ["✓ green"] else 1, expected)


def test_a_run_without_a_base_judges_no_declaration(judged):
    """VERDICT-61: a push run judges no loosening and no declaration, so its declarations leave it green."""
    head = gate_config("A", declared=(declaration(B_REMOVED),))
    assert judged(gate_config("A", "B"), head, A_RAN, with_base=False) == (0, ["✓ green"])


PRESENTATOR = "overnightworks/agent-presentator#371"
QWEN = "speech/providers/qwen/uv.lock"
def allows(change):
    return declared(f"advisory allowance {change}", f"{PRESENTATOR} of 2026-10-21")


ADDS_QWEN = "advisory allowance adds GHSA-462x-pgg6-55fp in " + QWEN + " until 2027-01-31"


@pytest.mark.parametrize(
    ("base", "head", "expected"),
    [
        (gate_config("A"), gate_config("A") + allowance_config(),
         [allows(f"adds GHSA-462x-pgg6-55fp in {QWEN} until 2027-01-31"), ONLY_DECLARED]),
        (gate_config("A"), gate_config("A") + allowance_config(until=datetime.date(2099, 12, 31)),
         [allows(f"adds GHSA-462x-pgg6-55fp in {QWEN} until 2099-12-31"), ONLY_DECLARED]),
        (gate_config("A") + allowance_config(), gate_config("A") + allowance_config(until=datetime.date(2027, 7, 31)),
         [allows(f"extends GHSA-462x-pgg6-55fp in {QWEN} to 2027-07-31"), ONLY_DECLARED]),
        (gate_config("A") + allowance_config(), gate_config("A") + allowance_config(until=datetime.date(2026, 12, 31)), ["✓ green"]),
        (gate_config("A") + allowance_config(), gate_config("A") + allowance_config(reason="reworded"), ["✓ green"]),
        (gate_config("A") + allowance_config(), gate_config("A") + allowance_config(item="#116"), ["✓ green"]),
        (gate_config("A") + allowance_config(), gate_config("A") + allowance_config(date=datetime.date(2026, 11, 1)), ["✓ green"]),
        (gate_config("A") + allowance_config(), gate_config("A"), ["✓ green"]),
        (gate_config("A") + allowance_config(), gate_config("A") + allowance_config(lock="uv.lock"),
         [allows("adds GHSA-462x-pgg6-55fp in uv.lock until 2027-01-31"), ONLY_DECLARED]),
        (gate_config("A"), gate_config("A") + allowance_config(lock="frontend/pnpm-lock.yaml"),
         [allows("adds GHSA-462x-pgg6-55fp in frontend/pnpm-lock.yaml until 2027-01-31"), ONLY_DECLARED]),
        (gate_config("A") + allowance_config(), gate_config("A") + allowance_config("CVE-2026-112395"),
         [allows(f"adds CVE-2026-112395 in {QWEN} until 2027-01-31"), ONLY_DECLARED]),
        (None, gate_config("A") + allowance_config() + allowance_config(lock="uv.lock"),
         [allows(f"adds GHSA-462x-pgg6-55fp in {QWEN} until 2027-01-31"), allows("adds GHSA-462x-pgg6-55fp in uv.lock until 2027-01-31"),
          ONLY_DECLARED]),
        (gate_config("A") + allowance_config(), gate_config("A") + allowance_config(), ["✓ green"]),
        (gate_config("A"), gate_config("A", declared=(declaration(ADDS_QWEN),)) + allowance_config(),
         [allows(f"adds GHSA-462x-pgg6-55fp in {QWEN} until 2027-01-31"), declared(ADDS_QWEN),
          "! red"]),
        (gate_config("A", "B"), gate_config("A") + allowance_config(),
         [*UNDECLARED_B, allows(f"adds GHSA-462x-pgg6-55fp in {QWEN} until 2027-01-31"), "! red"]),
        (gate_config("A"), gate_config("A") + allowance_config("GHSA-\u001b[8m", lock="a\tb/uv.lock"),
         [allows("adds GHSA-\\x1b[8m in a\\x09b/uv.lock until 2027-01-31"), ONLY_DECLARED]),
        (gate_config("A", "B"), gate_config("A", declared=(declaration(B_REMOVED),)) + allowance_config(),
         [declared(B_REMOVED), allows(f"adds GHSA-462x-pgg6-55fp in {QWEN} until 2027-01-31"), ONLY_DECLARED]),
    ],
    ids=[
        "cell 14 adds", "cell 14 adds with a far until", "cell 15 extends", "cell 16 until earlier", "cell 16 reason reworded", "cell 16 item changed",
        "cell 16 date changed", "cell 16 deleted", "cell 17 lock changed", "#145 R3 a pnpm lock", "cell 17 id changed", "cell 18 adoption",
        "cell 20 unchanged", "cell 21 a gate.loosened entry for an allowance", "cell 22 next to an undeclared removal",
        "cell 25 control characters in id and lock", "cell 28 next to a declared removal",
    ],
)
def test_an_advisory_allowance_declares_its_own_loosening(judged, base, head, expected):
    """VERDICT-64, VERDICT-65, VERDICT-66, VERDICT-67, #116 R2, #116 E8: an allowance the base lacks, or whose until the
    head moves later, is a loosening declared with the allowance's own item and date; nothing else about an
    allowance loosens, and a [[gate.loosened]] entry naming one matches nothing."""
    assert judged(base, head, A_RAN) == (0 if expected == ["✓ green"] else 1, expected)


def test_a_run_without_a_base_judges_no_advisory_allowance(judged):
    """VERDICT-61: a push or merge group judges no loosening, so a new allowance leaves it green."""
    assert judged(gate_config("A"), gate_config("A") + allowance_config(), A_RAN, with_base=False) == (0, ["✓ green"])


LOOSENED = f"{CONFIG_PATH} [[gate.loosened]]"
LOOSENED_KEYS = f"{LOOSENED} entry 1 needs exactly a what, an item, a date and a reason"
LOOSENED_DATE = f"{LOOSENED} entry 1 needs date as a TOML date without a time"
LOOSENED_ITEM = f"{LOOSENED} entry 1 needs item as #<number> or <owner>/<repo>#<number>"
LOOSENED_REASON = f"{LOOSENED} entry 1 needs a non-empty one-line reason"


@pytest.mark.parametrize(
    ("entry", "named"),
    [
        (declaration(B_REMOVED).replace('reason = "ruled"\n', ""), LOOSENED_KEYS),
        (declaration(B_REMOVED) + "extra = 1\n", LOOSENED_KEYS),
        (declaration(B_REMOVED, date='"20.10.2026"'), LOOSENED_DATE),
        (declaration(B_REMOVED, date="2026-10-20T10:00:00"), LOOSENED_DATE),
        (declaration(B_REMOVED, item="siehe Chat"), LOOSENED_ITEM),
        (declaration(B_REMOVED).replace('"ruled"', '"two\\nlines"'), LOOSENED_REASON),
        (declaration(""), f"{LOOSENED} entry 1 needs a non-empty what"),
        (declaration(B_REMOVED).replace('"ruled"', '""'), LOOSENED_REASON),
        (
            declaration(B_REMOVED) + declaration(B_REMOVED, item="#1"),
            f'{LOOSENED} declares what = "mandatory job \\"B\\" removed" twice; one entry per loosening',
        ),
        (f"[gate.loosened]\nwhat = {json.dumps(B_REMOVED)}\n", f"{CONFIG_PATH} needs [[gate.loosened]] as an array of tables"),
        (declaration(B_REMOVED, item="overnightworks/s\u043engmaker#1345"), LOOSENED_ITEM),
    ],
    ids=[
        "missing key", "extra key", "text date", "datetime", "item siehe Chat", "two-line reason", "empty what",
        "empty reason", "duplicate what", "loosened as a table", "Cyrillic item",
    ],
)
def test_a_malformed_declaration_cannot_be_judged(capsys, run_env, entry, named):
    """O10, VERDICT-39, VERDICT-40, VERDICT-57: a malformed declaration is exit 2, the pair: the fact, then the
    example's lines whole below `next:` and the head's step."""
    repo, _ = run_env
    (repo / CONFIG_PATH).write_text('[gate]\nmandatory_jobs = ["checks"]\n' + entry)
    assert cli.main(["verdict"], fetcher_for=lambda token: lambda url: (CHECKS_GREEN, None)) == 2
    example = [f"        {line}" for line in LOOSENED_EXAMPLE]
    assert capsys.readouterr().err.splitlines() == [f"error: {named}", "next:", f"    write it in {CONFIG_PATH} like this:", *example, "    push the fix; the gate judges the new commit"]


@pytest.mark.parametrize(
    "job_entry",
    [{"id": 1, "name": "checks", "run_attempt": "1"}, {"id": 1, "name": None, "run_attempt": 1},
     {"id": "1", "name": "checks", "run_attempt": 1}],
    ids=["run_attempt as text", "name null", "id as text"],
)
def test_a_job_field_of_the_wrong_type_cannot_be_judged(capsys, run_env, job_entry):
    """VERDICT-46: a jobs page with a field of the wrong type is exit 2, the pair."""
    page = json.dumps({"total_count": 1, "jobs": [{"status": "completed", "conclusion": "success", **job_entry}]})
    assert cli.main(["verdict"], fetcher_for=lambda token: lambda url: (page, None)) == 2
    captured = capsys.readouterr()
    assert captured.err.startswith("error: "), captured
    assert captured.err.count("\n") == 2, captured
