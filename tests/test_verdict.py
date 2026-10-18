import http.server
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest
from conftest import GIT_IDENTITY, UNKNOWN_KEY_CONFIGS
from test_sonar import Responder, reply

from quality import cli, verdict
from quality.config import CONFIG_PATH, LegacyMode
from quality.hygiene import CannotJudge
from quality.ratchet import BASELINE_PATH
from quality.verdict import NO_BASE, GateSettings, Job, Side, judge, read_base, read_jobs

FIXTURES = Path(__file__).parent / "fixtures"
GREEN_GATE = {"hygiene": "success"}


def job(name="checks", attempt=1, status="completed", conclusion="success"):
    return Job(name, attempt, status, conclusion)


def problems(jobs, mandatory=("checks",), base=(), gate=GREEN_GATE, attempt=1, legacy=LegacyMode.REPORT, base_legacy=LegacyMode.REPORT):
    head, base_side = Side(GateSettings(mandatory, legacy), None), Side(GateSettings(base, base_legacy), None)
    return judge(head, base_side, jobs, gate, attempt).problems


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
    assert read_base(repo, _head(repo)).settings == GateSettings(("tests",), LegacyMode.REPORT)


def test_the_legacy_mode_is_read_from_the_base_commit(build_repo):
    """#33 sentence 4: the base config's legacy mode is read from the base commit."""
    repo = build_repo({"quality/config.toml": '[gate]\nmandatory_jobs = ["tests"]\nlegacy = "fail"\n'})
    assert read_base(repo, _head(repo)).settings == GateSettings(("tests",), LegacyMode.FAIL)


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
    head, base = Side(GateSettings(("checks",), legacy), None), Side(GateSettings(("checks",), base_legacy), None)
    result = judge(head, base, [job()], GREEN_GATE, 1)
    assert (result.problems, result.green) == (expected, not expected)


def test_a_base_without_the_config_contributes_nothing(build_repo):
    """#6 rule 3: a base without the config adds no mandatory jobs."""
    repo = build_repo({"README.md": "x"})
    assert read_base(repo, _head(repo)) == NO_BASE


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
    base = _head(repo)
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
    subprocess.run(["git", "-C", str(repo), "update-ref", full_ref, _head(repo)], check=True)
    assert read_base(repo, ref).settings == GateSettings(("tests",), LegacyMode.REPORT)


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


CHECKS_GREEN = json.dumps(
    {"total_count": 1, "jobs": [{"name": "checks", "run_attempt": 1, "status": "completed", "conclusion": "success"}]}
)
LEGACY_FAIL_CONFIG = '[gate]\nmandatory_jobs = ["checks"]\nlegacy = "fail"\n'
NEW_CCN_KEY = '[ccn."src/a.py"]\n"f" = 16\n'


def main_lines(count: int) -> str:
    return f'[function_lines."src/quality/cli.py"]\n"main" = {count}\n'


def put(path: Path, content: str | bytes | None) -> None:
    """Writes `content` to `path`, or removes the file when `content` is None."""
    if content is None:
        path.unlink(missing_ok=True)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content if isinstance(content, bytes) else content.encode())


@pytest.fixture
def pull_request(monkeypatch, run_env):
    """Commits `base` as the pull request's base baseline and points GATE_BASE_SHA at that commit."""
    repo, _ = run_env

    def commit_base(base: str | bytes | None, config: str | None = None) -> str:
        put(repo / BASELINE_PATH, base)
        if config is not None:
            put(repo / "quality" / "config.toml", config)
        git = ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.invalid"]
        subprocess.run([*git, "add", "-A"], check=True)
        subprocess.run([*git, "commit", "-q", "--allow-empty", "-m", "base"], check=True)
        sha = _head(repo)
        monkeypatch.setenv("GATE_BASE_SHA", sha)
        return sha

    return repo, commit_base


def verdict_of(repo: Path, capsys) -> tuple[int, str]:
    code = cli.main(["verdict", str(repo)], fetcher_for=lambda token: lambda url: (CHECKS_GREEN, None))
    captured = capsys.readouterr()
    return code, captured.out + captured.err


@pytest.mark.parametrize(
    ("base", "head", "code", "expected"),
    [
        (main_lines(82), main_lines(90), 1, "loosened: size baseline raises function_lines src/quality/cli.py main 82 -> 90"),
        (main_lines(82), main_lines(70), 0, "verdict: green"),
        (main_lines(82), main_lines(82), 0, "verdict: green"),
        (None, main_lines(82), 0, "verdict: green"),
        (main_lines(82), None, 1, "loosened: quality/baseline.toml removed"),
        (main_lines(82), main_lines(82) + NEW_CCN_KEY, 1, "loosened: size baseline adds ccn src/a.py f 16"),
        (main_lines(82) + NEW_CCN_KEY, main_lines(82), 0, "verdict: green"),
        (None, None, 0, "verdict: green"),
        ("[ccn", main_lines(82), 2, f"{BASELINE_PATH} at the base commit {{base}}: {BASELINE_PATH} is not valid TOML"),
        (main_lines(82), "[ccn", 2, f"cannot judge - {BASELINE_PATH} at the head: {BASELINE_PATH} is not valid TOML"),
        (b"\xff", main_lines(82), 2, f"cannot read {BASELINE_PATH} at the base commit {{base}}: 'utf-8' codec"),
    ],
    ids=[
        "cell 1 growth", "cell 2 lowering", "cell 3 equal", "cell 4 adoption", "cell 5 base file deleted",
        "cell 6 new key", "cell 7 key dropped", "cell 8 both absent", "cell 9 base malformed", "cell 10 head malformed",
        "cell 9 base not UTF-8",
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
    [("[ccn", main_lines(82), 0), (main_lines(82), "[ccn", 1)],
    ids=["cell 16 base malformed", "cell 16 head malformed"],
)
def test_only_the_head_baseline_is_told_to_rewrite_itself(capsys, pull_request, base, head, hints):
    """#52 rule 7, cell 16: a pull request cannot rewrite its base, so only a malformed head baseline gets the hint."""
    repo, commit_base = pull_request
    commit_base(base)
    put(repo / BASELINE_PATH, head)
    code, output = verdict_of(repo, capsys)
    assert (code, output.count("rewrite it with `quality baseline write`")) == (2, hints), output


@pytest.fixture
def elsewhere(tmp_path_factory) -> Path:
    """A scratch directory outside the pull request's repository."""
    return tmp_path_factory.mktemp("elsewhere")


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
    assert "loosened: legacy fail → report" in output
    assert "loosened: size baseline raises function_lines src/quality/cli.py main 82 -> 90" in output


def test_a_verdict_without_git_on_the_path_cannot_judge(capsys, monkeypatch, pull_request, elsewhere):
    """#58 cell 1 (#5 finding 92): with no git to read the base commit the verdict is exit 2, never a traceback."""
    repo, commit_base = pull_request
    commit_base(main_lines(82))
    monkeypatch.setenv("PATH", str(elsewhere))
    code, output = verdict_of(repo, capsys)
    assert (code, "verdict: cannot judge - cannot run git" in output, "Traceback" in output) == (2, True, False)


@pytest.fixture
def stub_server():
    """`serve(responder)` starts a loopback server and returns its origin and the headers of each request it got."""
    servers = []

    def serve(responder: Responder) -> tuple[str, list[dict[str, str]]]:
        heard: list[dict[str, str]] = []

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                heard.append(dict(self.headers))
                responder(self)

            def log_message(self, *args):
                pass

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        return f"http://127.0.0.1:{server.server_address[1]}", heard

    yield serve
    for server in servers:
        server.shutdown()
        server.server_close()


def fetch_jobs(origin: str) -> str:
    body, _ = verdict.github_fetcher("t")(origin + "/jobs")
    return body


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
    with pytest.raises(CannotJudge, match="HTTP 302"):
        fetch_jobs(origin)
    assert other_heard == []


def test_git_output_is_decoded_as_utf_8_whatever_the_locale(build_repo):
    """#58 cell 8 (#5 finding 94): under LANG=C a commit subject in UTF-8 still reads, it does not fail to decode."""
    repo = build_repo({})
    subprocess.run(["git", "-C", str(repo), *GIT_IDENTITY, "commit", "-q", "--allow-empty", "-m", "Grüße"], check=True)
    program = (
        "import sys; from quality import engines; "
        "print(ascii(engines.run(['git', '-C', sys.argv[1], 'log', '-1', '--format=%s']).stdout.strip()))"
    )
    ascii_locale = {**os.environ, "LC_ALL": "C", "LANG": "C", "PYTHONUTF8": "0", "PYTHONCOERCECLOCALE": "0"}
    read = subprocess.run(
        [sys.executable, "-c", program, str(repo)], capture_output=True, text=True, env=ascii_locale, check=False
    )
    assert (read.returncode, read.stdout.strip()) == (0, ascii("Grüße")), read.stderr
