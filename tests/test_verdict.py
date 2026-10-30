import datetime
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from quality import __version__, cli
from quality.config import CONFIG_PATH, LOOSENED_EXAMPLE, LegacyMode
from quality.ratchet import BASELINE_PATH, NO_RENAMES
from quality.report import CannotJudge, printable
from quality.verdict import NO_BASE_SETTINGS, GateSettings, Side, judge, read_base, read_jobs
from tests.support.advisories import allowance_config
from tests.support.check import assert_every_line_starts_with_the_door
from tests.support.git_repositories import GIT_IDENTITY, head_commit, put
from tests.support.secrets import UNKNOWN_KEY_CONFIGS
from tests.support.sonar import reply
from tests.support.verdict import (
    CHECKS_GREEN,
    DECLARED,
    GREEN_GATE,
    ONLY_DECLARED,
    declaration,
    fetch_jobs,
    gate_config,
    job,
    verdict_of,
)

FIXTURES = Path(__file__).parent / "fixtures"


def problems(jobs, mandatory=("checks",), base=(), gate=GREEN_GATE, attempt=1, legacy=LegacyMode.REPORT, base_legacy=LegacyMode.REPORT):
    head, base_side = Side(GateSettings(mandatory, legacy), None), Side(GateSettings(base, base_legacy), None)
    return judge(head, base_side, jobs, gate, attempt, NO_RENAMES).blocking


@pytest.mark.parametrize(
    ("jobs", "attempt", "expected"),
    [
        ([job()], 1, ()),
        ([], 1, ('mandatory job "checks" is missing from the run',)),
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
    assert 'loosened (undeclared): mandatory job "tests" removed' in found
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
    assert read_base(repo, head_commit(repo)).settings == GateSettings(("tests",), LegacyMode.REPORT)


def test_the_legacy_mode_is_read_from_the_base_commit(build_repo):
    """#33 sentence 4: the base config's legacy mode is read from the base commit."""
    repo = build_repo({"quality/config.toml": '[gate]\nmandatory_jobs = ["tests"]\nlegacy = "fail"\n'})
    assert read_base(repo, head_commit(repo)).settings == GateSettings(("tests",), LegacyMode.FAIL)


@pytest.mark.parametrize(
    ("base_legacy", "legacy", "expected"),
    [
        (LegacyMode.FAIL, LegacyMode.REPORT, ("loosened (undeclared): legacy fail → report",)),
        (LegacyMode.FAIL, LegacyMode.FAIL, ()),
        (LegacyMode.REPORT, LegacyMode.FAIL, ()),
        (LegacyMode.REPORT, LegacyMode.REPORT, ()),
    ],
    ids=["fail to report", "fail kept", "report to fail", "report kept"],
)
def test_switching_legacy_back_from_fail_is_a_loosening(base_legacy, legacy, expected):
    """#33 sentence 4: a head config that is not "fail" under a base config that is shows as loosened and is red"""
    head, base = Side(GateSettings(("checks",), legacy), None), Side(GateSettings(("checks",), base_legacy), None)
    result = judge(head, base, [job()], GREEN_GATE, 1, NO_RENAMES)
    assert (result.blocking, result.green) == (expected, not expected)


def test_a_base_without_the_config_contributes_nothing(build_repo):
    """#6 rule 3: a base without the config adds no mandatory jobs."""
    repo = build_repo({"README.md": "x"})
    assert read_base(repo, head_commit(repo)) == Side(NO_BASE_SETTINGS, None)


@pytest.mark.parametrize(("conclusion", "code"), [("success", 0), ("failure", 1)])
def test_verdict_exit_code_and_summary_follow_the_run(capsys, run_env, conclusion, code):
    """#6 rule 4: exit code and step summary follow the run."""
    repo, summary = run_env
    page = json.dumps(
        {
            "total_count": 1,
            "jobs": [{"id": 1, "name": "checks", "run_attempt": 1, "status": "completed", "conclusion": conclusion}],
        }
    )
    assert cli.main(["verdict", str(repo)], fetcher_for=lambda token: lambda url: (page, None)) == code
    assert capsys.readouterr().out.strip().endswith("green" if code == 0 else "red")
    assert summary.read_text().strip().endswith("green" if code == 0 else "red")


@pytest.mark.parametrize(
    ("job_name_in_toml", "printed"),
    [("a\\n::error::b", "a\\x0a::error::b"), ("\\u202eneerg :tcidrev\\u202c", "\\u202eneerg :tcidrev\\u202c")],
)
def test_a_job_name_with_a_line_feed_or_format_character_prints_as_one_escaped_problem_line_in_log_and_summary(
    capsys, run_env, job_name_in_toml, printed
):
    """#97 rule 2, cell 15 (E6) and #228 rule 7: the problem line is escaped, the same in the step summary, and `verdict: red` stays a line."""
    repo, summary = run_env
    (repo / "quality/config.toml").write_text(f'[gate]\nmandatory_jobs = ["checks", "{job_name_in_toml}"]\n')
    page = json.dumps(
        {"total_count": 1, "jobs": [{"id": 1, "name": "checks", "run_attempt": 1, "status": "completed", "conclusion": "success"}]}
    )
    assert cli.main(["verdict", str(repo)], fetcher_for=lambda token: lambda url: (page, None)) == 1
    lines = capsys.readouterr().out.split("\n")
    assert [line for line in lines if printed in line] != []
    assert lines[-2:] == ["verdict: red", ""]
    assert_every_line_starts_with_the_door(lines)
    assert summary.read_text().split("\n") == lines


@UNKNOWN_KEY_CONFIGS
def test_verdict_refuses_a_config_with_unknown_keys(capsys, run_env, config):
    """#40 rule 2: the verdict keeps refusing unknown keys of quality/config.toml."""
    repo, _ = run_env
    (repo / "quality/config.toml").write_text(config)
    assert cli.main(["verdict", str(repo)], fetcher_for=lambda token: lambda url: ({"jobs": []}, None)) == 2
    assert "unknown key" in capsys.readouterr().err


def test_a_head_without_a_config_is_missing_at_the_head(capsys, run_env):
    """#6 rule 1 and #52 rules 3 and 4, cell 10: an absent head config is still "missing", now named at the head."""
    repo, _ = run_env
    (repo / CONFIG_PATH).unlink()
    assert cli.main(["verdict", str(repo)], fetcher_for=lambda token: lambda url: ({"jobs": []}, None)) == 2
    assert f"verdict: cannot judge - {CONFIG_PATH} at the head: {CONFIG_PATH} is missing" in capsys.readouterr().err


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
        read_base(repo, base)


def test_a_malformed_base_config_names_the_base_commit(build_repo):
    """#48 rule 4: the verdict names the side of a malformed config.toml, as the door does."""
    repo = build_repo({CONFIG_PATH: "[gate"})
    base = head_commit(repo)
    with pytest.raises(CannotJudge, match=f"^{CONFIG_PATH} at the base commit {base}: {CONFIG_PATH} is not valid TOML"):
        read_base(repo, base)


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
    with pytest.raises(CannotJudge, match="a ref name or a SHA"):
        read_base(repo, ref)


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
    subprocess.run(["git", "-C", str(repo), "update-ref", full_ref, head_commit(repo)], check=True)
    assert read_base(repo, ref).settings == GateSettings(("tests",), LegacyMode.REPORT)


def test_a_pull_request_that_removes_legacy_fail_is_red_in_the_verdict(monkeypatch, capsys, run_env):
    """#33 sentence 4: at the real entry, a head config without legacy under a base that fails is red, naming the loosening."""
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
    assert cli.main(["verdict", str(repo)], fetcher_for=lambda token: lambda url: (page, None)) == 1
    assert "loosened (undeclared): legacy fail → report" in capsys.readouterr().out


LEGACY_FAIL_CONFIG = '[gate]\nmandatory_jobs = ["checks"]\nlegacy = "fail"\n'
NEW_CCN_KEY = '[ccn."src/a.py"]\n"f" = 16\n'


def main_lines(count: int) -> str:
    return f'[function_lines."src/quality/cli.py"]\n"main" = {count}\n'


@pytest.mark.parametrize(
    ("base", "head", "code", "expected"),
    [
        (main_lines(82), main_lines(90), 1, "loosened (undeclared): size baseline raises function_lines src/quality/cli.py main 82 -> 90"),
        (main_lines(82), main_lines(70), 0, "verdict: green"),
        (main_lines(82), main_lines(82), 0, "verdict: green"),
        (None, main_lines(82), 0, "verdict: green"),
        (main_lines(82), None, 1, "loosened (undeclared): quality/baseline.toml removed"),
        (main_lines(82), main_lines(82) + NEW_CCN_KEY, 1, "loosened (undeclared): size baseline adds ccn src/a.py f 16"),
        (main_lines(82) + NEW_CCN_KEY, main_lines(82), 0, "verdict: green"),
        (None, None, 0, "verdict: green"),
        ("[ccn", main_lines(82), 2, f"{BASELINE_PATH} at the base commit {{base}}: {BASELINE_PATH} is not valid TOML"),
        (main_lines(82), "[ccn", 2, f"cannot judge - {BASELINE_PATH} at the head: {BASELINE_PATH} is not valid TOML"),
        (main_lines(82), f"[ccn]\na = {'9' * 5000}\n", 2, f"cannot judge - {BASELINE_PATH} at the head: {BASELINE_PATH} is not valid TOML"),
        (main_lines(82), f"a = {'[' * 5000}{']' * 5000}\n", 2, f"cannot judge - {BASELINE_PATH} at the head: {BASELINE_PATH} is not valid TOML"),
        (b"\xff", main_lines(82), 2, f"cannot read {BASELINE_PATH} at the base commit {{base}}: 'utf-8' codec"),
        (main_lines(82), b"\xff", 2, f"cannot judge - {BASELINE_PATH} at the head: {BASELINE_PATH} is not valid UTF-8 ("),
    ],
    ids=[
        "cell 1 growth", "cell 2 lowering", "cell 3 equal", "cell 4 adoption", "cell 5 base file deleted",
        "cell 6 new key", "cell 7 key dropped", "cell 8 both absent", "cell 9 base malformed", "cell 10 head malformed",
        "head oversized integer", "head nested too deep", "cell 9 base not UTF-8", "#76 cell 18 head not UTF-8",
    ],
)
def test_the_size_baseline_only_falls_against_the_base(capsys, pull_request, base, head, code, expected):
    """#48 rules 2 and 3, cells 1-10: growth, a new key and a deleted base file are red in the ruled literal."""
    repo, commit_base = pull_request
    base_sha = commit_base(base)
    put(repo / BASELINE_PATH, head)
    actual_code, output = verdict_of(repo, capsys)
    assert (actual_code, expected.format(base=base_sha) in output, "Traceback" in output) == (code, True, False)


@pytest.mark.parametrize(
    ("base", "head", "hints"),
    [("[ccn", main_lines(82), 0), (main_lines(82), "[ccn", 1), (b"\xff", main_lines(82), 0), (main_lines(82), b"\xff", 1)],
    ids=["cell 16 base malformed", "cell 16 head malformed", "#76 cell 18 base not UTF-8", "#76 cell 18 head not UTF-8"],
)
def test_only_the_head_baseline_is_told_to_rewrite_itself(capsys, pull_request, base, head, hints):
    """#52 rule 7, cell 16: a pull request cannot rewrite its base, so only a malformed head baseline gets the hint."""
    repo, commit_base = pull_request
    commit_base(base)
    put(repo / BASELINE_PATH, head)
    code, output = verdict_of(repo, capsys)
    assert (code, output.count("rewrite it with `quality baseline write`")) == (2, hints), output


UNKNOWN_TABLE_FACT = f"has an unknown table [future]; quality {__version__} knows ccn, file_lines, function_lines, clones"


@pytest.mark.parametrize(
    ("base", "head", "advice"),
    [(main_lines(82), "[future]\n", 1), ("[future]\n", main_lines(82), 0)],
    ids=["cell 14 head", "cell 15 base"],
)
def test_an_unknown_baseline_table_states_its_fact_and_advises_only_the_head(capsys, pull_request, base, head, advice):
    """#153 cells 14 and 15: the head is sent to the release its gate pins; the base keeps its wrapper and no advice."""
    repo, commit_base = pull_request
    commit_base(base)
    put(repo / BASELINE_PATH, head)
    code, output = verdict_of(repo, capsys)
    assert code == 2
    assert UNKNOWN_TABLE_FACT in output
    assert output.count("write the baseline with the quality release your gate pins") == advice, output
    assert "rewrite it" not in output


def make_baseline_a_directory(repo: Path) -> None:
    (repo / BASELINE_PATH).unlink()
    put(repo / BASELINE_PATH / "inner.toml", main_lines(82))


def make_baseline_a_symlink(repo: Path) -> None:
    (repo / BASELINE_PATH).rename(repo / "target.toml")
    (repo / BASELINE_PATH).symlink_to("../target.toml")


@pytest.mark.parametrize("make_non_file", [make_baseline_a_directory, make_baseline_a_symlink], ids=["a directory", "a symlink"])
def test_a_base_baseline_that_is_no_regular_file_is_named_so_and_never_parsed(capsys, monkeypatch, pull_request, make_non_file):
    """#142 rule 2 (#5 finding 80): a directory or symlink at the base commit is exit 2, `not a regular file`, no TOML."""
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
    """#142 rule 3 (#5 finding 85): git's exit code is named, its stderr and the checkout's path are not printed."""
    repo, commit_base = pull_request
    commit_base(main_lines(82))
    monkeypatch.setenv("GATE_BASE_SHA", "f" * 40)
    code, output = verdict_of(repo, capsys)
    assert (code, f"cannot read the base commit {'f' * 40}: git exited 128" in output, "fatal:" in output) == (2, True, False)


def test_a_head_baseline_that_links_out_of_the_repository_cannot_be_judged(capsys, pull_request, elsewhere):
    """#48 rule 4, cell 11: a head baseline symlinked out of the repository is exit 2, no traceback."""
    repo, commit_base = pull_request
    commit_base(main_lines(82))
    outside = elsewhere / "baseline.toml"
    outside.write_text(main_lines(82))
    (repo / BASELINE_PATH).unlink()
    (repo / BASELINE_PATH).symlink_to(outside)
    code, output = verdict_of(repo, capsys)
    expected = f"cannot judge - {BASELINE_PATH} at the head: {BASELINE_PATH} resolves outside the repository"
    assert (code, expected in output, "Traceback" in output) == (2, True, False)


def test_without_a_base_the_base_is_never_read(capsys, monkeypatch, pull_request, fake_git, elsewhere):
    """#48 rule 5, cell 12: on push GATE_BASE_SHA is empty, a raise against the previous commit stays green, git unread."""
    repo, commit_base = pull_request
    commit_base(main_lines(82))
    monkeypatch.setenv("GATE_BASE_SHA", "")
    put(repo / BASELINE_PATH, main_lines(90))
    calls = elsewhere / "git-calls"
    fake_git(f'echo "$@" >> "{calls}"')
    assert verdict_of(repo, capsys) == (0, "verdict: green\n")
    assert not calls.exists()


@pytest.mark.parametrize(
    ("base_sha", "expected"),
    [
        ("HEAD~1", "'HEAD~1' is not a ref name or a SHA"),
        ("f" * 40, f"cannot read the base commit {'f' * 40}"),
        ("-x", "'-x' is not a ref name or a SHA"),
    ],
    ids=["cell 14 a revision expression", "cell 15 a SHA not in the clone", "cell 16 an option"],
)
def test_a_base_that_is_no_ref_name_or_sha_in_the_clone_cannot_be_judged(
    capsys, monkeypatch, pull_request, fake_git, elsewhere, base_sha, expected
):
    """#48 rule 4, cells 14-16 (#5 finding 57): exit 2 naming the base; a ref git would read as an option never reaches git."""
    repo, commit_base = pull_request
    commit_base(main_lines(82))
    monkeypatch.setenv("GATE_BASE_SHA", base_sha)
    calls = elsewhere / "git-calls"
    fake_git(f'echo "$@" >> "{calls}"')
    code, output = verdict_of(repo, capsys)
    assert (code, expected in output, "Traceback" in output) == (2, True, False)
    assert calls.exists() == (base_sha == "f" * 40)


def test_a_raised_baseline_and_a_dropped_legacy_fail_are_both_listed(capsys, pull_request):
    """#48 rule 1, cell 17: the baseline loosening stands next to the legacy loosening, and the verdict is red."""
    repo, commit_base = pull_request
    commit_base(main_lines(82), config=LEGACY_FAIL_CONFIG)
    put(repo / "quality" / "config.toml", '[gate]\nmandatory_jobs = ["checks"]\n')
    put(repo / BASELINE_PATH, main_lines(90))
    code, output = verdict_of(repo, capsys)
    assert code == 1
    assert "loosened (undeclared): legacy fail → report" in output
    assert "loosened (undeclared): size baseline raises function_lines src/quality/cli.py main 82 -> 90" in output


def test_a_verdict_without_git_on_the_path_cannot_judge(capsys, monkeypatch, pull_request, elsewhere):
    """#58 cell 1 (#5 finding 92): with no git to read the base commit the verdict is exit 2, never a traceback."""
    repo, commit_base = pull_request
    commit_base(main_lines(82))
    monkeypatch.setenv("PATH", str(elsewhere))
    code, output = verdict_of(repo, capsys)
    assert (code, "verdict: cannot judge - cannot run git" in output, "Traceback" in output) == (2, True, False)


def test_the_github_fetcher_sends_the_token_and_reads_the_jobs_page(stub_server):
    """#58 cell 4 (#5 finding 95): the real fetcher sends the token and returns the jobs page unchanged."""
    origin, heard = stub_server(reply(200, CHECKS_GREEN.encode()))
    assert (fetch_jobs(origin), heard[0]["Authorization"]) == (CHECKS_GREEN, "Bearer t")


def test_a_truncated_jobs_page_cannot_be_judged(stub_server):
    """#58 cell 2 (#5 finding 92): an IncompleteRead is cannot judge, which the verdict reports as exit 2."""
    origin, _ = stub_server(reply(200, b"abcd", Content_Length="100"))
    with pytest.raises(CannotJudge):
        fetch_jobs(origin)


def test_the_github_token_never_follows_a_redirect(stub_server):
    """#58 cell 3 (#5 finding 93): a 302 to another host is refused, and that host never sees the token."""
    other_origin, other_heard = stub_server(reply(200, CHECKS_GREEN.encode()))
    origin, _ = stub_server(reply(302, b"", Location=other_origin.replace("127.0.0.1", "localhost") + "/stolen"))
    with pytest.raises(CannotJudge, match="HTTP Error 302"):
        fetch_jobs(origin)
    assert other_heard == []


def test_a_refused_jobs_page_keeps_the_verdicts_wording(stub_server):
    """#58 rule 4: a GitHub that refuses reads as before, the URL asked and urllib's words, with the actions hint."""
    origin, _ = stub_server(reply(403, b""))
    with pytest.raises(CannotJudge) as refused:
        fetch_jobs(origin)
    assert str(refused.value) == (
        f"cannot read {origin}/jobs: HTTP Error 403: Forbidden (does the caller grant actions: read?)"
    )


def test_git_output_is_decoded_as_utf_8_whatever_the_locale(build_repo):
    """#58 cell 8 (#5 finding 94): under LANG=C a commit subject in UTF-8 still reads, it does not fail to decode."""
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
        code = cli.main(["verdict", str(repo)], fetcher_for=lambda token: lambda url: (page, None))
        captured = capsys.readouterr()
        return code, (captured.out + captured.err).splitlines()

    return run


A_RAN = {"A": "success"}
UNDECLARED_B = ['loosened (undeclared): mandatory job "B" removed', 'mandatory job "B" is missing from the run']
LINE_FEED_JOB = 'mandatory job "a\n::error::b" removed'
RLO_JOB = 'mandatory job "x\u202ey" removed'
LITERAL_JOB = 'mandatory job "x\\u202ey" removed'
UNDECLARED_LITERAL = [
    "loosened (undeclared): " + LITERAL_JOB, "declared loosening matches nothing: " + LITERAL_JOB,
    'mandatory job "x\\u202ey" is missing from the run', "verdict: red",
]
MAIN_RAISED = "size baseline raises function_lines src/quality/cli.py main 82 -> 90"


@pytest.mark.parametrize(
    ("base", "head", "jobs", "baselines", "expected"),
    [
        (gate_config("A", "B"), gate_config("A"), A_RAN, (None, None), [*UNDECLARED_B, "verdict: red"]),
        (gate_config("A", "B"), gate_config("A", declared=(declaration(B_REMOVED),)), A_RAN, (None, None),
         [DECLARED + B_REMOVED, ONLY_DECLARED]),
        (gate_config("A", "B"), gate_config("A", declared=(declaration(B_REMOVED),)), {**A_RAN, "B": "failure"},
         (None, None), [DECLARED + B_REMOVED, ONLY_DECLARED]),
        (gate_config("A", "B", legacy="fail"), gate_config("A", declared=(declaration(B_REMOVED),)), A_RAN, (None, None),
         [DECLARED + B_REMOVED, "loosened (undeclared): legacy fail → report", "verdict: red"]),
        (gate_config("A", "B"), gate_config("A", declared=(declaration(B_REMOVED),)), {"A": "failure"}, (None, None),
         [DECLARED + B_REMOVED, 'mandatory job "A" (attempt 1) concluded failure', "verdict: red"]),
        (gate_config("A", "B"), gate_config("A", declared=(declaration('mandatory job "b" removed'),)), A_RAN,
         (None, None),
         [UNDECLARED_B[0], 'declared loosening matches nothing: mandatory job "b" removed', UNDECLARED_B[1], "verdict: red"]),
        (gate_config("A", declared=(declaration(B_REMOVED),)), gate_config("A", declared=(declaration(B_REMOVED),)),
         A_RAN, (None, None), ["verdict: green"]),
        (gate_config("A", declared=(declaration(B_REMOVED),)), gate_config("A"), A_RAN, (None, None), ["verdict: green"]),
        (gate_config("A", "B", declared=(declaration(B_REMOVED),)), gate_config("A", declared=(declaration(B_REMOVED),)),
         A_RAN, (None, None), [*UNDECLARED_B, "verdict: red"]),
        (gate_config("A", "B", declared=(declaration(B_REMOVED),)),
         gate_config("A", declared=(declaration(B_REMOVED, item="#103", date="2026-10-21"),)), A_RAN, (None, None),
         ["loosened (declared, #103 of 2026-10-21): " + B_REMOVED, ONLY_DECLARED]),
        (gate_config("A"), gate_config("A", declared=(declaration(MAIN_RAISED),)), A_RAN, (main_lines(82), main_lines(90)),
         [DECLARED + MAIN_RAISED, ONLY_DECLARED]),
        (gate_config("A"), gate_config("A", declared=(declaration("quality/baseline.toml removed"),)), A_RAN,
         (main_lines(82), None), [DECLARED + "quality/baseline.toml removed", ONLY_DECLARED]),
        (gate_config("A", legacy="fail"), gate_config("A", declared=(declaration("legacy fail → report"),)), A_RAN,
         (None, None), [DECLARED + "legacy fail → report", ONLY_DECLARED]),
        (gate_config("A", "a\n::error::b"), gate_config("A", declared=(declaration(LINE_FEED_JOB),)), A_RAN,
         (None, None), [DECLARED + 'mandatory job "a\\x0a::error::b" removed', ONLY_DECLARED]),
        (gate_config("A", "a\n::error::b"), gate_config("A", declared=(declaration(printable(LINE_FEED_JOB)),)), A_RAN,
         (None, None), ['loosened (undeclared): mandatory job "a\\x0a::error::b" removed',
                        'declared loosening matches nothing: mandatory job "a\\x0a::error::b" removed',
                        'mandatory job "a\\x0a::error::b" is missing from the run', "verdict: red"]),
        (gate_config("A", "x\u202ey"), gate_config("A", declared=(declaration(RLO_JOB),)), A_RAN, (None, None),
         [DECLARED + LITERAL_JOB, ONLY_DECLARED]),
        (gate_config("A", "x\u202ey"), gate_config("A", declared=(declaration(LITERAL_JOB),)), A_RAN, (None, None),
         UNDECLARED_LITERAL),
        (gate_config("A", "x\\u202ey"), gate_config("A", declared=(declaration(LITERAL_JOB),)), A_RAN, (None, None),
         [DECLARED + LITERAL_JOB, ONLY_DECLARED]),
        (gate_config("A", "x\\u202ey"), gate_config("A", declared=(declaration(RLO_JOB),)), A_RAN, (None, None),
         UNDECLARED_LITERAL),
        (None, gate_config("A", declared=(declaration(B_REMOVED),)), A_RAN, (None, None),
         ["declared loosening matches nothing: " + B_REMOVED, "verdict: red"]),
    ],
    ids=[
        "cell 1 undeclared removal", "cell 2 declared removal", "cell 3 declared removal, B still runs and fails",
        "cell 4 declared next to undeclared", "cell 5 declared next to A failed", "cell 6 a typo in what",
        "cell 7 inherited declaration", "cell 7 inherited declaration deleted", "cell 8 inherited declaration",
        "cell 8 a new item and date", "cell 10 baseline raise", "cell 10 baseline removal", "cell 10 legacy",
        "cell 13 a line feed declared as the TOML escape", "cell 13 the printed form copied", "#228 rule 7 a format character declared as its TOML escape",
        "#228 rule 7 the printed form of a format character copied", "#228 rule 7 a name holding the escape text itself",
        "#228 rule 7 the escape text declared as a format character", "cell 26 adoption",
    ],
)
def test_a_declared_loosening_stays_red_and_says_so(judged, base, head, jobs, baselines, expected):
    """#103 rules 2 and 3 (E2-E5): each loosening line says whether it is declared, and only declarations the head
    adds, matching exactly, declare; a declared job removal is no longer judged."""
    assert judged(base, head, jobs, baselines) == (0 if expected == ["verdict: green"] else 1, expected)


def test_a_run_without_a_base_judges_no_declaration(judged):
    """#103 rule 2, cell 9: a push run judges no loosening and no declaration, so its declarations leave it green."""
    head = gate_config("A", declared=(declaration(B_REMOVED),))
    assert judged(gate_config("A", "B"), head, A_RAN, with_base=False) == (0, ["verdict: green"])


PRESENTATOR = "overnightworks/agent-presentator#371"
QWEN = "speech/providers/qwen/uv.lock"
ALLOWS = f"loosened (declared, {PRESENTATOR} of 2026-10-21): advisory allowance "
ADDS_QWEN = "advisory allowance adds GHSA-462x-pgg6-55fp in " + QWEN + " until 2027-01-31"


@pytest.mark.parametrize(
    ("base", "head", "expected"),
    [
        (gate_config("A"), gate_config("A") + allowance_config(),
         [f"{ALLOWS}adds GHSA-462x-pgg6-55fp in {QWEN} until 2027-01-31", ONLY_DECLARED]),
        (gate_config("A"), gate_config("A") + allowance_config(until=datetime.date(2099, 12, 31)),
         [f"{ALLOWS}adds GHSA-462x-pgg6-55fp in {QWEN} until 2099-12-31", ONLY_DECLARED]),
        (gate_config("A") + allowance_config(), gate_config("A") + allowance_config(until=datetime.date(2027, 7, 31)),
         [f"{ALLOWS}extends GHSA-462x-pgg6-55fp in {QWEN} to 2027-07-31", ONLY_DECLARED]),
        (gate_config("A") + allowance_config(), gate_config("A") + allowance_config(until=datetime.date(2026, 12, 31)), ["verdict: green"]),
        (gate_config("A") + allowance_config(), gate_config("A") + allowance_config(reason="reworded"), ["verdict: green"]),
        (gate_config("A") + allowance_config(), gate_config("A") + allowance_config(item="#116"), ["verdict: green"]),
        (gate_config("A") + allowance_config(), gate_config("A") + allowance_config(date=datetime.date(2026, 11, 1)), ["verdict: green"]),
        (gate_config("A") + allowance_config(), gate_config("A"), ["verdict: green"]),
        (gate_config("A") + allowance_config(), gate_config("A") + allowance_config(lock="uv.lock"),
         [f"{ALLOWS}adds GHSA-462x-pgg6-55fp in uv.lock until 2027-01-31", ONLY_DECLARED]),
        (gate_config("A"), gate_config("A") + allowance_config(lock="frontend/pnpm-lock.yaml"),
         [f"{ALLOWS}adds GHSA-462x-pgg6-55fp in frontend/pnpm-lock.yaml until 2027-01-31", ONLY_DECLARED]),
        (gate_config("A") + allowance_config(), gate_config("A") + allowance_config("CVE-2026-112395"),
         [f"{ALLOWS}adds CVE-2026-112395 in {QWEN} until 2027-01-31", ONLY_DECLARED]),
        (None, gate_config("A") + allowance_config() + allowance_config(lock="uv.lock"),
         [f"{ALLOWS}adds GHSA-462x-pgg6-55fp in {QWEN} until 2027-01-31", f"{ALLOWS}adds GHSA-462x-pgg6-55fp in uv.lock until 2027-01-31",
          ONLY_DECLARED]),
        (gate_config("A") + allowance_config(), gate_config("A") + allowance_config(), ["verdict: green"]),
        (gate_config("A"), gate_config("A", declared=(declaration(ADDS_QWEN),)) + allowance_config(),
         [f"{ALLOWS}adds GHSA-462x-pgg6-55fp in {QWEN} until 2027-01-31", f"declared loosening matches nothing: {ADDS_QWEN}",
          "verdict: red"]),
        (gate_config("A", "B"), gate_config("A") + allowance_config(),
         [f"{ALLOWS}adds GHSA-462x-pgg6-55fp in {QWEN} until 2027-01-31", *UNDECLARED_B, "verdict: red"]),
        (gate_config("A"), gate_config("A") + allowance_config("GHSA-\u001b[8m", lock="a\tb/uv.lock"),
         [f"{ALLOWS}adds GHSA-\\x1b[8m in a\\x09b/uv.lock until 2027-01-31", ONLY_DECLARED]),
        (gate_config("A", "B"), gate_config("A", declared=(declaration(B_REMOVED),)) + allowance_config(),
         [DECLARED + B_REMOVED, f"{ALLOWS}adds GHSA-462x-pgg6-55fp in {QWEN} until 2027-01-31", ONLY_DECLARED]),
    ],
    ids=[
        "cell 14 adds", "cell 14 adds with a far until", "cell 15 extends", "cell 16 until earlier", "cell 16 reason reworded", "cell 16 item changed",
        "cell 16 date changed", "cell 16 deleted", "cell 17 lock changed", "#145 R3 a pnpm lock", "cell 17 id changed", "cell 18 adoption",
        "cell 20 unchanged", "cell 21 a gate.loosened entry for an allowance", "cell 22 next to an undeclared removal",
        "cell 25 control characters in id and lock", "cell 28 next to a declared removal",
    ],
)
def test_an_advisory_allowance_declares_its_own_loosening(judged, base, head, expected):
    """#116 R2, E6, E7, E8: an allowance the base lacks, or whose until the head moves later, is a loosening declared
    with the allowance's own item and date; nothing else about an allowance loosens, and a [[gate.loosened]] entry
    naming one matches nothing."""
    assert judged(base, head, A_RAN) == (0 if expected == ["verdict: green"] else 1, expected)


def test_a_run_without_a_base_judges_no_advisory_allowance(judged):
    """#116 cell 19: a push or merge group judges no loosening, so a new allowance leaves it green."""
    assert judged(gate_config("A"), gate_config("A") + allowance_config(), A_RAN, with_base=False) == (0, ["verdict: green"])


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
    """#103 rule 1, cell 11 (E1): a malformed declaration is exit 2, one line: the fact, `, like:` and the example's
    lines joined by an escaped line feed."""
    repo, _ = run_env
    (repo / CONFIG_PATH).write_text('[gate]\nmandatory_jobs = ["checks"]\n' + entry)
    assert cli.main(["verdict", str(repo)], fetcher_for=lambda token: lambda url: (CHECKS_GREEN, None)) == 2
    lines = capsys.readouterr().err.splitlines()
    assert len(lines) == 1
    assert lines[0].endswith(f"{named}, like:\\x0a" + "\\x0a".join(LOOSENED_EXAMPLE))


@pytest.mark.parametrize(
    "job_entry",
    [{"id": 1, "name": "checks", "run_attempt": "1"}, {"id": 1, "name": None, "run_attempt": 1},
     {"id": "1", "name": "checks", "run_attempt": 1}],
    ids=["run_attempt as text", "name null", "id as text"],
)
def test_a_job_field_of_the_wrong_type_cannot_be_judged(capsys, run_env, job_entry):
    """#103 rule 6, cell 25 (#5 finding 14): a jobs page with a field of the wrong type is exit 2, one line."""
    repo, _ = run_env
    page = json.dumps({"total_count": 1, "jobs": [{"status": "completed", "conclusion": "success", **job_entry}]})
    assert cli.main(["verdict", str(repo)], fetcher_for=lambda token: lambda url: (page, None)) == 2
    captured = capsys.readouterr()
    assert captured.err.startswith("verdict: cannot judge - "), captured
    assert captured.err.count("\n") == 1, captured
