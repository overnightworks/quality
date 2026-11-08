import base64
import http.server
import threading
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from quality import cli, sonar
from quality.output import SEPARATOR
from quality.report import CannotJudge
from tests.support.sonar import (
    ANALYSED_MAIN,
    CLEAN_PROPERTIES,
    FEATURE_ENVIRONMENT,
    FINISHED_FOR_FEATURE,
    FINISHED_FOR_MAIN,
    FINISHED_FOR_PR_7,
    KEY,
    LEGACY_COUNT,
    PULL_REQUEST_ENVIRONMENT,
    PUSH_ENVIRONMENT,
    TASK_FIXTURE,
    TOKEN,
    FakeSonar,
    Responder,
    environment_without_token,
    issue,
    issues_page,
    legacy_config,
    legacy_config_absent,
    reply,
    run_cli,
    run_properties,
    set_environment,
    sonar_answering,
    sonar_off_files,
)


def pending(status: str) -> dict:
    return {"status": status}


@pytest.fixture(autouse=True)
def scratch_working_directory(monkeypatch, tmp_path):
    """The task file and the config are read relative to the working directory, so every test works in its own."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "quality").mkdir()
    (tmp_path / "quality" / "config.toml").write_text(legacy_config_absent)


def test_known_pass_fixture_is_clean_and_prints_the_proof(monkeypatch, capsys):
    """SONAR-5, SONAR-36: known-pass fixture is clean and prints the proof line"""
    code, out, _, _ = run_cli(monkeypatch, capsys, sonar_answering())
    assert (code, out) == (0, "  · analysis      pull request   #7 · overnightworks_quality\n  ✓ new           none\n  · legacy        0 open         on main\n  ✓ quality gate  passed\n✓ clean · new 0 · legacy 0 · <1s\n")


def test_known_fail_fixture_lists_every_open_finding_and_exits_one(monkeypatch, capsys):
    """SONAR-35, SONAR-36, SONAR-69: known-fail fixture lists every open finding and exits 1"""
    code, out, err, _ = run_cli(monkeypatch, capsys, sonar_answering(issues=issues_page(2, 1, 2)))
    assert code == 1
    assert "    ! src/a1.py:1  open  python:S1: m\n    ! src/a2.py:2  open  python:S1: m\n" in out
    assert (err, "      fix: fix it in the code; an accepted or false-positive mark in SonarCloud still counts\n" in out) == ("", True)
    assert out.endswith("\n! red · new 2 · legacy 0 · <1s\n")


@pytest.mark.parametrize(
    ("pages", "expected_count", "expected_requests"),
    [
        ([issues_page(3, 1, 2), issues_page(3, 3)], 3, 2),
        ([issues_page(5, 1, 2), issues_page(5)], 5, 2),
        ([issues_page(0, 1)], 1, 1),
        ([issues_page(10_000_000, 1)], 10_000_000, sonar.MAXIMUM_FINDINGS_PAGES),
    ],
    ids=["every page read", "empty page ends paging", "listed beats a zero total", "page limit bounds paging"],
)
def test_counts_findings_across_pages(monkeypatch, capsys, pages, expected_count, expected_requests):
    """SONAR-69: findings are counted across pages, bounded by the search limit and never by the claimed total"""
    fake = sonar_answering()
    fake.answers["issues/search"] = pages
    code, out, _, _ = run_cli(monkeypatch, capsys, fake)
    assert (code, len(fake.urls_to("issues/search"))) == (1, expected_requests)
    assert f"new {expected_count} " in out


def test_asks_for_statuses_a_person_may_have_accepted_and_encodes_the_scope(monkeypatch, capsys):
    """SONAR-69, SONAR-72: asks for issueStatuses a person may have accepted and url-encodes the scope"""
    fake = sonar_answering(
        task={**FINISHED_FOR_PR_7, "pullRequest": None, "branch": "feat/a&b#c%d"}, issues=issues_page(0)
    )
    environment = {**PUSH_ENVIRONMENT, "GITHUB_REF_NAME": "feat/a&b#c%d"}
    code, *_ = run_cli(monkeypatch, capsys, fake, environment)
    query = parse_qs(urlparse(fake.urls_to("issues/search")[0]).query)
    assert code == 0
    assert query["issueStatuses"] == ["OPEN,CONFIRMED,ACCEPTED,FALSE_POSITIVE"]
    assert query["branch"] == ["feat/a&b#c%d"]
    assert "statuses" not in query
    assert query["componentKeys"] == [KEY]


def test_sends_the_token_in_the_header_only(monkeypatch, capsys):
    """SONAR-78: the token travels as Basic '<token>:' in the header, never in a URL"""
    fake = sonar_answering()
    run_cli(monkeypatch, capsys, fake)
    sent = {authorization for _, authorization in fake.requests}
    assert len(sent) == 1
    scheme, _, encoded = sent.pop().partition(" ")
    assert (scheme, base64.b64decode(encoded).decode()) == ("Basic", f"{TOKEN}:")
    assert all(TOKEN not in url and url.startswith("https://sonarcloud.io/api/") for url, _ in fake.requests)


@pytest.mark.parametrize(
    "fake",
    [
        FakeSonar({"ce/task": [{"task": FINISHED_FOR_PR_7}], "measures/component": [sonar.SonarHttpError(404, "u")]}),
        FakeSonar({"ce/task": [{"task": FINISHED_FOR_PR_7}], "measures/component": [CannotJudge("down")]}),
        sonar_answering(issues={"issues": []}),
        sonar_answering(issues={"total": 1, "issues": [issue(1, "no-file-path")]}),
        sonar_answering(issues=["not", "an", "object"]),
        sonar_answering(issues={"total": "many", "issues": []}),
        sonar_answering(issues={"total": 0, "issues": {}}),
        sonar_answering(issues={"total": 1, "issues": [{**issue(1), "component": None}]}),
    ],
    ids=[
        "scope unresolved", "measures unreachable", "no total", "component without file", "wrong shape", "bad total",
        "issues not a list", "component not a string",
    ],
)
def test_an_unreadable_answer_is_exit_two_and_never_clean(monkeypatch, capsys, fake):
    """SONAR-29, SONAR-80, SONAR-82: anything unreadable is exit 2 with no findings printed, never a clean zero"""
    code, out, err, _ = run_cli(monkeypatch, capsys, fake)
    assert (code, out) == (2, "")
    assert err.startswith("error: ")


@pytest.mark.parametrize(
    ("issues", "reason"),
    [
        ({"total": 0, "issues": {}}, "issues that are not a list"),
        ({"total": 1, "issues": [{**issue(1), "component": None}]}, "is not a string"),
    ],
    ids=["issues not a list", "component not a string"],
)
def test_a_malformed_issues_answer_names_what_is_wrong(monkeypatch, capsys, issues, reason):
    """SONAR-29: the issues check and finding_from_issue refuse with CannotJudge, naming the shape"""
    code, _, err, _ = run_cli(monkeypatch, capsys, sonar_answering(issues=issues))
    assert code == 2
    assert reason in err


def test_does_not_read_findings_when_the_scope_does_not_resolve(monkeypatch, capsys):
    """SONAR-80: an unresolved scope is exit 2 before findings are read"""
    fake = sonar_answering()
    fake.answers["measures/component"] = [sonar.SonarHttpError(404, "u")]
    code, *_ = run_cli(monkeypatch, capsys, fake)
    assert (code, fake.urls_to("issues/search")) == (2, [])


@pytest.mark.parametrize(
    ("edit", "reason"),
    [
        (("serverUrl=https://sonarcloud.io", "serverUrl=https://evil.example"), "evil.example"),
        (("projectKey=overnightworks_quality", "projectKey=other_project"), "other_project"),
        (("organization=overnightworks", "organization=someone-else"), "someone-else"),
        (("projectKey=overnightworks_quality\n", ""), "None"),
        (("ceTaskId=AXtask-1\n", ""), "ceTaskId"),
    ],
    ids=["foreign server", "foreign project", "foreign organization", "missing project", "missing task id"],
)
def test_a_task_file_naming_something_foreign_is_refused_before_any_request(
    monkeypatch, capsys, edit, reason
):
    """SONAR-72, SONAR-73, SONAR-75: a task file naming a foreign server, project, organization or none is exit 2 before any request"""
    fake = sonar_answering()
    text = TASK_FIXTURE.read_text().replace(*edit)
    code, _, err, _ = run_cli(monkeypatch, capsys, fake, task_text=text)
    assert (code, fake.requests) == (2, [])
    assert reason in err


def test_findings_reads_only_the_literal_scannerwork_report_task_file(monkeypatch, capsys):
    """SONAR-70: findings reads only .scannerwork/report-task.txt"""
    set_environment(monkeypatch, PULL_REQUEST_ENVIRONMENT)
    Path(".scannerwork").mkdir()
    Path(".scannerwork/report-task.txt").write_text(TASK_FIXTURE.read_text())
    code = cli.main(["sonar", "findings"], transport=sonar_answering(), sleep=lambda _: None)
    assert code == 0
    assert "legacy 0" in capsys.readouterr().out


@pytest.mark.parametrize("breakage", ["absent", "symlink loop"])
def test_a_task_file_that_cannot_be_read_is_refused_before_any_request(monkeypatch, capsys, breakage):
    """SONAR-71, SONAR-75: an unreadable task file is exit 2 before any request"""
    fake = sonar_answering()
    set_environment(monkeypatch, PULL_REQUEST_ENVIRONMENT)
    if breakage == "symlink loop":
        sonar.TASK_FILE.parent.mkdir()
        sonar.TASK_FILE.symlink_to(Path.cwd() / sonar.TASK_FILE)
    code = cli.main(["sonar", "findings"], transport=fake)
    assert (code, fake.requests) == (2, [])
    assert "report-task.txt" in capsys.readouterr().err


def test_never_requests_the_urls_the_task_file_names(monkeypatch, capsys):
    """SONAR-74: only sonarcloud.io is requested, whatever URLs the task file names"""
    fake = sonar_answering()
    text = TASK_FIXTURE.read_text().replace("https://sonarcloud.io/api/ce", "https://evil.example/api/ce")
    run_cli(monkeypatch, capsys, fake, task_text=text)
    assert all(url.startswith("https://sonarcloud.io/api/") for url, _ in fake.requests)


def test_a_task_id_with_an_ampersand_stays_one_query_value(monkeypatch, capsys):
    """SONAR-76: a task id with an ampersand stays one query value"""
    fake = sonar_answering()
    text = TASK_FIXTURE.read_text().replace("ceTaskId=AXtask-1", "ceTaskId=a&b=c")
    run_cli(monkeypatch, capsys, fake, task_text=text)
    assert parse_qs(urlparse(fake.urls_to("ce/task")[0]).query) == {"id": ["a&b=c"]}


@pytest.mark.parametrize(
    "environment",
    [
        {**PULL_REQUEST_ENVIRONMENT, "SONAR_TOKEN": ""},
        {key: value for key, value in PULL_REQUEST_ENVIRONMENT.items() if key != "SONAR_TOKEN"},
        {**PULL_REQUEST_ENVIRONMENT, "GITHUB_REPOSITORY": "no-slash"},
        {key: value for key, value in PULL_REQUEST_ENVIRONMENT.items() if key != "PULL_REQUEST_NUMBER"},
    ],
    ids=["empty token", "unset token", "bad repository", "no pull request number"],
)
def test_an_unusable_environment_is_refused_before_any_request(monkeypatch, capsys, environment):
    """SONAR-68, SONAR-75: a missing or empty token (the gate never goes anonymous), bad repository or missing pull request number is exit 2 before any request"""
    fake = sonar_answering()
    code, *_ = run_cli(monkeypatch, capsys, fake, environment)
    assert (code, fake.requests) == (2, [])


@pytest.mark.parametrize(
    ("statuses", "code", "polls", "sleeps"),
    [
        (["PENDING", "IN_PROGRESS", "SUCCESS"], 0, 3, 2),
        (["PENDING", "FAILED"], 2, 2, 1),
        (["CANCELED"], 2, 1, 0),
        (["PENDING"], 2, sonar.TASK_POLL_ATTEMPTS, sonar.TASK_POLL_ATTEMPTS - 1),
    ],
    ids=["waits until success", "failed", "canceled", "pending past the limit"],
)
def test_waits_for_its_own_task_before_reading_findings(monkeypatch, capsys, statuses, code, polls, sleeps):
    """SONAR-76, SONAR-79: waits for the analysis task, bounded polling, no findings read unless it succeeded"""
    fake = sonar_answering()
    fake.answers["ce/task"] = [{"task": FINISHED_FOR_PR_7 | {"status": status}} for status in statuses]
    result = run_cli(monkeypatch, capsys, fake)
    assert (result[0], len(fake.urls_to("ce/task")), len(result[3])) == (code, polls, sleeps)
    assert set(result[3]) <= {sonar.TASK_POLL_INTERVAL_SECONDS}
    assert bool(fake.urls_to("issues/search")) == (code == 0)


@pytest.mark.parametrize(
    ("environment", "task"),
    [
        (PULL_REQUEST_ENVIRONMENT, FINISHED_FOR_PR_7 | {"pullRequest": "6"}),
        (PULL_REQUEST_ENVIRONMENT, FINISHED_FOR_PR_7 | {"pullRequest": None}),
        (PULL_REQUEST_ENVIRONMENT, FINISHED_FOR_PR_7 | {"componentKey": "other_project"}),
        (PULL_REQUEST_ENVIRONMENT, FINISHED_FOR_PR_7 | {"organization": "someone-else"}),
        (PULL_REQUEST_ENVIRONMENT, {key: value for key, value in FINISHED_FOR_PR_7.items() if key != "organization"}),
        (PULL_REQUEST_ENVIRONMENT, FINISHED_FOR_PR_7 | {"pullRequest": None, "branch": "7"}),
        (FEATURE_ENVIRONMENT, FINISHED_FOR_FEATURE | {"branch": "other"}),
    ],
    ids=["other pull request", "no pull request", "other component", "other organization", "no organization", "branch only", "other branch"],
)
def test_a_finished_task_that_is_not_this_runs_is_refused(monkeypatch, capsys, environment, task):
    """SONAR-72, SONAR-77, SONAR-79: a finished task of another pull request, component or organization is exit 2"""
    fake = sonar_answering(task=task)
    code, _, err, _ = run_cli(monkeypatch, capsys, fake, environment)
    assert (code, fake.urls_to("issues/search")) == (2, [])
    assert "another analysis" in err


@pytest.mark.parametrize(
    ("environment", "task", "findings", "code", "line", "asks_legacy"),
    [
        (PULL_REQUEST_ENVIRONMENT, FINISHED_FOR_PR_7, 1, 1, "\n! red · new 1 · legacy 0 · <1s\n", True),
        (PUSH_ENVIRONMENT, FINISHED_FOR_MAIN, 25, 1, "\n! red · 25 open on main · <1s\n", False),
        (PUSH_ENVIRONMENT, FINISHED_FOR_MAIN, 0, 0, "\n✓ clean · 0 open on main · <1s\n", False),
        ({**PUSH_ENVIRONMENT, "PULL_REQUEST_NUMBER": "7"}, FINISHED_FOR_MAIN, 1, 1, "\n! red · 1 open on main · <1s\n", False),
        (FEATURE_ENVIRONMENT, FINISHED_FOR_FEATURE, 3, 0, "\n✓ report only · 3 open on feat/a · <1s\n", False),
    ],
    ids=["pull request with a finding", "default branch with findings", "clean default branch", "push ignores a pull request number", "other branch with findings"],
)
def test_findings_fail_a_pull_request_and_the_default_branch_but_only_report_on_another_branch(monkeypatch, capsys, environment, task, findings, code, line, asks_legacy):
    """SONAR-34, SONAR-35, SONAR-36, SONAR-37, SONAR-38: any open finding fails a pull request and the default branch, lists it, and counts legacy only on a pull request; another branch only reports"""
    fake = sonar_answering(task=task)
    fake.answers["issues/search"] = [issues_page(findings, *range(1, min(findings, 100) + 1)), issues_page(findings)]
    result = run_cli(monkeypatch, capsys, fake, environment)
    assert result[0] == code
    assert line in result[1]
    assert bool(fake.urls_to(LEGACY_COUNT)) == asks_legacy
    assert ("src/a1.py:1" in result[1]) == bool(findings)


@pytest.mark.parametrize(
    ("config_text", "legacy", "code", "line"),
    [
        (legacy_config_absent, 17, 0, "✓ clean · new 0 · legacy 17 · <1s"),
        (legacy_config("report"), 17, 0, "✓ clean · new 0 · legacy 17 · <1s"),
        (legacy_config("fail"), 1, 1, "! red · new 0 · legacy 1 · <1s"),
        (legacy_config("fail"), 0, 0, "✓ clean · new 0 · legacy 0 · <1s"),
    ],
    ids=["absent key reports", "report", "fail with legacy findings", "fail without legacy findings"],
)
def test_legacy_findings_are_counted_and_fail_a_pull_request_only_when_switched_on(
    monkeypatch, capsys, config_text, legacy, code, line
):
    """SONAR-36, SONAR-40, SONAR-41: both counts print on a pull request; legacy = "fail" turns legacy findings red"""
    fake = sonar_answering(legacy=legacy)
    result = run_cli(monkeypatch, capsys, fake, config_text=config_text)
    assert result[0] == code
    assert line in result[1] + result[2]


def test_legacy_findings_are_counted_with_the_default_branch_and_never_listed(monkeypatch, capsys):
    """SONAR-33: legacy is one count-only search of the default branch, with the same open statuses"""
    fake = sonar_answering(legacy=3)
    run_cli(monkeypatch, capsys, fake)
    counted = parse_qs(urlparse(fake.urls_to(LEGACY_COUNT)[0]).query)
    assert (counted["branch"], counted["ps"], counted["issueStatuses"]) == (
        ["main"], ["1"], [sonar.OPEN_ISSUE_STATUSES],
    )
    assert len(fake.urls_to("issues/search")) == 1


@pytest.mark.parametrize("value", ["warn", "FAIL", ""])
def test_a_legacy_value_other_than_report_or_fail_is_exit_two_before_any_request(monkeypatch, capsys, value):
    """SONAR-42: any other legacy value is a config error, read before the first SonarCloud request"""
    fake = sonar_answering()
    code, _, err, _ = run_cli(monkeypatch, capsys, fake, config_text=legacy_config(value))
    assert (code, fake.requests) == (2, [])
    assert "legacy" in err


def test_another_branch_never_reads_the_branch_list(monkeypatch, capsys):
    """SONAR-44: only pull requests and default-branch runs read project_branches/list"""
    fake = sonar_answering(task=FINISHED_FOR_FEATURE)
    run_cli(monkeypatch, capsys, fake, FEATURE_ENVIRONMENT)
    assert fake.urls_to("project_branches/list") == []


@pytest.mark.parametrize("environment", [PULL_REQUEST_ENVIRONMENT, PUSH_ENVIRONMENT], ids=["pull request", "default branch"])
def test_pull_requests_and_default_branch_runs_read_the_branch_list_once_before_any_count(
    monkeypatch, capsys, environment
):
    """SONAR-43: one branch read, ahead of every count, on a pull request and on the default branch"""
    task = FINISHED_FOR_MAIN if environment is PUSH_ENVIRONMENT else FINISHED_FOR_PR_7
    fake = sonar_answering(task=task)
    run_cli(monkeypatch, capsys, fake, environment)
    endpoints = [fake.endpoint_of(url) for url, _ in fake.requests]
    assert endpoints.count("project_branches/list") == 1
    assert endpoints.index("project_branches/list") < endpoints.index("issues/search")


@pytest.mark.parametrize(
    "branches",
    [(), ({"name": "main", "isMain": True},), ({"name": "other", "isMain": True, "analysisDate": "d"},)],
    ids=["no entry", "entry without analysis date", "only another branch"],
)
@pytest.mark.parametrize(
    ("config_text", "code"), [(legacy_config_absent, 0), (legacy_config("fail"), 2)], ids=["report", "fail"]
)
@pytest.mark.parametrize("environment", [PULL_REQUEST_ENVIRONMENT, PUSH_ENVIRONMENT], ids=["pull request", "default branch"])
def test_a_default_branch_never_analysed_leaves_legacy_unknown(monkeypatch, capsys, environment, branches, config_text, code):
    """SONAR-43, SONAR-45: report prints legacy unknown and exits 0; fail cannot judge and exits 2, on pull requests and the default branch"""
    task = FINISHED_FOR_MAIN if environment is PUSH_ENVIRONMENT else FINISHED_FOR_PR_7
    fake = sonar_answering(task=task, branches=branches)
    result = run_cli(monkeypatch, capsys, fake, environment, config_text=config_text)
    assert result[0] == code
    assert ("  · legacy        unknown        main not analysed yet\n" in result[1]) == (code == 0)
    assert fake.urls_to(LEGACY_COUNT) == []
    if code == 2:
        assert result[2].startswith("error: legacy unknown (main not analysed yet)\n")
        assert fake.urls_to("issues/search") == []


@pytest.mark.parametrize("environment", [PULL_REQUEST_ENVIRONMENT, PUSH_ENVIRONMENT], ids=["pull request", "default branch"])
@pytest.mark.parametrize("config_text", [legacy_config_absent, legacy_config("fail")], ids=["report", "fail"])
@pytest.mark.parametrize(
    "unmarked",
    [{**ANALYSED_MAIN, "isMain": False}, {"name": "main"}],
    ids=["not main", "no isMain and no analysis date"],
)
def test_a_default_branch_that_is_not_sonarclouds_main_is_exit_two(monkeypatch, capsys, environment, config_text, unmarked):
    """SONAR-46: a default branch without isMain counts only changed code, so it is refused with the fix"""
    task = FINISHED_FOR_MAIN if environment is PUSH_ENVIRONMENT else FINISHED_FOR_PR_7
    fake = sonar_answering(task=task, branches=(unmarked,))
    code, out, err, _ = run_cli(monkeypatch, capsys, fake, environment, config_text=config_text)
    assert (code, out, fake.urls_to("issues/search")) == (2, "", [])
    assert "set SonarCloud's main branch to main" in err


@pytest.mark.parametrize("answer", [{}, {"branches": "main"}, {"branches": [{}]}, ["main"]], ids=["no key", "not a list", "no name", "wrong shape"])
def test_an_unreadable_branch_list_is_exit_two(monkeypatch, capsys, answer):
    """SONAR-29: a branch list that cannot be read is never taken for an unanalysed default branch"""
    fake = sonar_answering()
    fake.answers["project_branches/list"] = [answer]
    code, _, err, _ = run_cli(monkeypatch, capsys, fake)
    assert (code, fake.urls_to("issues/search")) == (2, [])
    assert err.startswith("error: ")


@pytest.mark.parametrize("default_branch", [None, ""], ids=["unset", "empty"])
def test_a_missing_default_branch_name_is_exit_two_before_any_request(monkeypatch, capsys, default_branch):
    """SONAR-31, SONAR-32: the default branch is never assumed to be main; an absent or empty name is exit 2"""
    environment = {key: value for key, value in PULL_REQUEST_ENVIRONMENT.items() if key != "DEFAULT_BRANCH"}
    if default_branch is not None:
        environment["DEFAULT_BRANCH"] = default_branch
    fake = sonar_answering()
    code, _, err, _ = run_cli(monkeypatch, capsys, fake, environment)
    assert (code, fake.requests) == (2, [])
    assert "DEFAULT_BRANCH" in err


def test_the_default_branch_comes_from_the_environment_not_from_the_name_main(monkeypatch, capsys):
    """SONAR-31, SONAR-33: a repository whose default branch is trunk gets its legacy count from trunk"""
    trunk = {**ANALYSED_MAIN, "name": "trunk"}
    fake = sonar_answering(legacy=4, branches=(trunk,))
    code, out, _, _ = run_cli(monkeypatch, capsys, fake, {**PULL_REQUEST_ENVIRONMENT, "DEFAULT_BRANCH": "trunk"})
    assert (code, "  · legacy        4 open         on trunk\n" in out) == (0, True)
    assert parse_qs(urlparse(fake.urls_to(LEGACY_COUNT)[0]).query)["branch"] == ["trunk"]


@pytest.fixture
def loopback():
    """A loopback server whose handler is set per test; returns (set_responder, url)."""
    release = threading.Event()
    responder: list[Responder] = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            responder[0](self)

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield responder, release, f"http://127.0.0.1:{server.server_address[1]}/x"
    release.set()
    server.shutdown()
    server.server_close()


def test_the_real_transport_returns_json(loopback):
    """The real transport returns the JSON body."""
    responder, _, url = loopback
    responder.append(reply(200, b'{"a": 1}'))
    assert sonar.fetch_json(url, "Basic x") == {"a": 1}


def test_the_real_transport_sends_no_authorization_without_a_credential(loopback):
    """#73 rule 1: an anonymous request carries no Authorization header at all, not even an empty Basic one"""
    responder, _, url = loopback
    received = []

    def answer(handler):
        received.append(handler.headers.get("Authorization"))
        reply(200, b"{}")(handler)

    responder.append(answer)
    sonar.fetch_json(url, None)
    sonar.fetch_json(url, "Basic x")
    assert received == [None, "Basic x"]


@pytest.mark.parametrize(
    ("responder", "message"),
    [
        (reply(200, b"<html>"), "not JSON"),
        (reply(302, b"", Location="http://127.0.0.1:1/stolen"), "HTTP 302"),
        (reply(404, b"{}"), "HTTP 404"),
        (reply(200, b'{"a":"\xff\xfe\xfd"}'), "not JSON"),
        (reply(200, b"abcd", Content_Length="100"), "not JSON"),
    ],
    ids=["non-JSON", "redirect refused", "http error", "non-UTF-8 body", "truncated body"],
)
def test_the_real_transport_refuses_what_it_cannot_trust(loopback, responder, message):
    """SONAR-29, SONAR-81, SONAR-82: the real transport maps non-JSON, redirect (a credentialed request is not forwarded), HTTP error, non-UTF-8 and truncated bodies to cannot judge"""
    set_responder, _, url = loopback
    set_responder.append(responder)
    with pytest.raises(CannotJudge, match=message):
        sonar.fetch_json(url, "Basic x")


def test_the_real_transport_gives_up_on_a_server_that_never_answers(loopback):
    """SONAR-82: a server that never answers is cannot judge after the timeout"""
    responder, release, url = loopback
    responder.append(lambda handler: release.wait(30))
    with pytest.raises(CannotJudge, match="did not answer"):
        sonar.fetch_json(url, "Basic x", timeout=0.25)


@pytest.mark.integration
def test_the_real_transport_reports_an_unreachable_server():
    """SONAR-82: an unreachable server is cannot judge"""
    with pytest.raises(CannotJudge, match="could not be reached"):
        sonar.fetch_json("http://127.0.0.1:1/x", "Basic x")


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


def test_a_word_naming_no_sonar_command_is_refused_as_a_usage_error(capsys):
    """HELP-15: a word that names no sonar command is refused as a usage error, its help named."""
    word = "pre" + "flight"
    commands = ", ".join(cli.build_parser().commands["sonar"].commands)
    code = cli.main(["sonar", word])
    printed = capsys.readouterr()
    assert (code, printed.out, printed.err) == (
        2,
        "",
        f"error: no command {word}{SEPARATOR}commands: {commands}\nnext: quality sonar --help\n",
    )


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


def test_quality_with_its_own_config_fails_a_pull_request_on_legacy_findings(monkeypatch, capsys):
    """SONAR-41: quality's shipped config turns one legacy finding into exit 1 for a pull request with none"""
    shipped = (Path(__file__).parent.parent / "quality" / "config.toml").read_text(encoding="utf-8")
    code, out, err, _ = run_cli(monkeypatch, capsys, sonar_answering(legacy=1), config_text=shipped)
    assert code == 1
    assert (out.splitlines()[-1], err) == ("! red · new 0 · legacy 1 · <1s", "")


def test_a_repository_without_a_config_is_refused_before_any_request(monkeypatch, capsys):
    """SONAR-39: the config is read and validated before the first SonarCloud request"""
    fake = sonar_answering()
    code, _, err, _ = run_cli(monkeypatch, capsys, fake, config_text=None)
    assert (code, fake.requests) == (2, [])
    assert "quality/config.toml is missing" in err


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
