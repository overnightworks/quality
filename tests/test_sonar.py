import base64
import http.server
import threading
import urllib.error
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from quality import cli, sonar
from quality.hygiene import CannotJudge

TASK_FIXTURE = Path(__file__).parent / "fixtures" / "sonar" / "report-task.txt"
TOKEN = "stub-token-4711"
KEY = "overnightworks_quality"
PULL_REQUEST_ENVIRONMENT = {
    "GITHUB_REPOSITORY": "overnightworks/quality",
    "GITHUB_EVENT_NAME": "pull_request",
    "PULL_REQUEST_NUMBER": "7",
    "GITHUB_REF_NAME": "7/merge",
    "SONAR_TOKEN": TOKEN,
}
PUSH_ENVIRONMENT = {**PULL_REQUEST_ENVIRONMENT, "GITHUB_EVENT_NAME": "push", "GITHUB_REF_NAME": "main"}
FINISHED_FOR_PR_7 = {
    "status": "SUCCESS",
    "componentKey": KEY,
    "organization": "overnightworks",
    "pullRequest": "7",
}
FINISHED_FOR_MAIN = {**FINISHED_FOR_PR_7, "pullRequest": None, "branch": "main"}


def pending(status: str) -> dict:
    return {"status": status}


def issue(number: int, component: str | None = None) -> dict:
    return {"rule": "python:S1", "component": component or f"{KEY}:src/a{number}.py", "line": number, "message": "m"}


def issues_page(total: int, *numbers: int) -> dict:
    return {"total": total, "issues": [issue(number) for number in numbers]}


@dataclass
class FakeSonar:
    """Answers per endpoint in order, the last one repeating; records every request."""

    answers: dict[str, list[object]]
    requests: list[tuple[str, str]] = field(default_factory=list)

    def __call__(self, url: str, authorization: str) -> object:
        self.requests.append((url, authorization))
        queue = self.answers[urlparse(url).path.removeprefix("/api/")]
        answer = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(answer, Exception):
            raise answer
        return answer

    def urls_to(self, endpoint: str) -> list[str]:
        return [url for url, _ in self.requests if urlparse(url).path.endswith(endpoint)]


def sonar_answering(task=FINISHED_FOR_PR_7, issues=None, measures=None) -> FakeSonar:
    return FakeSonar(
        {
            "ce/task": [{"task": task}],
            "measures/component": [measures or {"component": {}}],
            "issues/search": [issues or issues_page(0)],
        }
    )


def set_environment(monkeypatch, environment) -> None:
    for name in PULL_REQUEST_ENVIRONMENT:
        monkeypatch.delenv(name, raising=False)
    for name, value in environment.items():
        monkeypatch.setenv(name, value)


@pytest.fixture(autouse=True)
def scratch_working_directory(monkeypatch, tmp_path):
    """The task file is read relative to the working directory, so every test works in its own."""
    monkeypatch.chdir(tmp_path)


def run_cli(
    monkeypatch, capsys, fake: FakeSonar, environment=PULL_REQUEST_ENVIRONMENT, task_text: str | None = None
):
    set_environment(monkeypatch, environment)
    sonar.TASK_FILE.parent.mkdir()
    sonar.TASK_FILE.write_text(TASK_FIXTURE.read_text() if task_text is None else task_text)
    sleeps: list[float] = []
    code = cli.main(["sonar", "findings"], transport=fake, sleep=sleeps.append)
    captured = capsys.readouterr()
    assert TOKEN not in captured.out + captured.err
    return code, captured.out, captured.err, sleeps


def test_known_pass_fixture_is_clean_and_prints_the_proof(monkeypatch, capsys):
    """#22 kept row: known-pass fixture is clean and prints the proof line"""
    code, out, _, _ = run_cli(monkeypatch, capsys, sonar_answering())
    assert (code, out) == (0, f"sonar: clean for '{KEY}' (pullRequest=7): 0 open finding(s)\n")


def test_known_fail_fixture_lists_every_open_finding_and_exits_one(monkeypatch, capsys):
    """#22 kept row: known-fail fixture lists every open finding and exits 1"""
    code, out, err, _ = run_cli(monkeypatch, capsys, sonar_answering(issues=issues_page(2, 1, 2)))
    assert code == 1
    assert "sonar src/a1.py:1 - python:S1: m" in out
    assert "sonar src/a2.py:2 - python:S1: m" in out
    assert "2 open finding(s) in pullRequest=7" in err


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
    """#22 kept row: findings are counted across pages, bounded by the search limit and never by the claimed total"""
    fake = sonar_answering()
    fake.answers["issues/search"] = pages
    code, _, err, _ = run_cli(monkeypatch, capsys, fake)
    assert (code, len(fake.urls_to("issues/search"))) == (1, expected_requests)
    assert f"{expected_count} open finding(s)" in err


def test_asks_for_statuses_a_person_may_have_accepted_and_encodes_the_scope(monkeypatch, capsys):
    """#22 kept row: asks for issueStatuses a person may have accepted and url-encodes the scope"""
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
    """#22 kept row: the token travels as Basic '<token>:' in the header, never in a URL"""
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
    """#22: anything unreadable is exit 2 with no findings printed, never a clean zero"""
    code, out, err, _ = run_cli(monkeypatch, capsys, fake)
    assert (code, out) == (2, "")
    assert err.startswith("sonar: cannot judge - ")


@pytest.mark.parametrize(
    ("issues", "reason"),
    [
        ({"total": 0, "issues": {}}, "issues that are not a list"),
        ({"total": 1, "issues": [{**issue(1), "component": None}]}, "is not a string"),
    ],
    ids=["issues not a list", "component not a string"],
)
def test_a_malformed_issues_answer_names_what_is_wrong(monkeypatch, capsys, issues, reason):
    """defect:#5 finding 40: the issues check and finding_from_issue refuse with CannotJudge, naming the shape"""
    code, _, err, _ = run_cli(monkeypatch, capsys, sonar_answering(issues=issues))
    assert code == 2
    assert reason in err


def test_does_not_read_findings_when_the_scope_does_not_resolve(monkeypatch, capsys):
    """#22 kept row: an unresolved scope is exit 2 before findings are read"""
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
    """#22: a task file naming a foreign server, project, organization or none is exit 2 before any request"""
    fake = sonar_answering()
    text = TASK_FIXTURE.read_text().replace(*edit)
    code, _, err, _ = run_cli(monkeypatch, capsys, fake, task_text=text)
    assert (code, fake.requests) == (2, [])
    assert reason in err


def test_findings_reads_only_the_literal_scannerwork_report_task_file(monkeypatch, capsys):
    """#26 rule: findings reads only .scannerwork/report-task.txt"""
    set_environment(monkeypatch, PULL_REQUEST_ENVIRONMENT)
    Path(".scannerwork").mkdir()
    Path(".scannerwork/report-task.txt").write_text(TASK_FIXTURE.read_text())
    code = cli.main(["sonar", "findings"], transport=sonar_answering(), sleep=lambda _: None)
    assert code == 0
    assert "clean" in capsys.readouterr().out


@pytest.mark.parametrize("breakage", ["absent", "symlink loop"])
def test_a_task_file_that_cannot_be_read_is_refused_before_any_request(monkeypatch, capsys, breakage):
    """#22 kept row: an unreadable task file is exit 2 before any request"""
    fake = sonar_answering()
    set_environment(monkeypatch, PULL_REQUEST_ENVIRONMENT)
    if breakage == "symlink loop":
        sonar.TASK_FILE.parent.mkdir()
        sonar.TASK_FILE.symlink_to(Path.cwd() / sonar.TASK_FILE)
    code = cli.main(["sonar", "findings"], transport=fake)
    assert (code, fake.requests) == (2, [])
    assert "report-task.txt" in capsys.readouterr().err


def test_never_requests_the_urls_the_task_file_names(monkeypatch, capsys):
    """#22 kept row: only sonarcloud.io is requested, whatever URLs the task file names"""
    fake = sonar_answering()
    text = TASK_FIXTURE.read_text().replace("https://sonarcloud.io/api/ce", "https://evil.example/api/ce")
    run_cli(monkeypatch, capsys, fake, task_text=text)
    assert all(url.startswith("https://sonarcloud.io/api/") for url, _ in fake.requests)


def test_a_task_id_with_an_ampersand_stays_one_query_value(monkeypatch, capsys):
    """#22 kept row: a task id with an ampersand stays one query value"""
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
    """#22 kept row: a missing token, bad repository or missing pull request number is exit 2 before any request"""
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
    """#22 kept row: waits for the analysis task, bounded polling, no findings read unless it succeeded"""
    fake = sonar_answering()
    fake.answers["ce/task"] = [{"task": FINISHED_FOR_PR_7 | {"status": status}} for status in statuses]
    result = run_cli(monkeypatch, capsys, fake)
    assert (result[0], len(fake.urls_to("ce/task")), len(result[3])) == (code, polls, sleeps)
    assert set(result[3]) <= {sonar.TASK_POLL_INTERVAL_SECONDS}
    assert bool(fake.urls_to("issues/search")) == (code == 0)


@pytest.mark.parametrize(
    "task",
    [
        FINISHED_FOR_PR_7 | {"pullRequest": "6"},
        FINISHED_FOR_PR_7 | {"pullRequest": None},
        FINISHED_FOR_PR_7 | {"componentKey": "other_project"},
        FINISHED_FOR_PR_7 | {"organization": "someone-else"},
        {key: value for key, value in FINISHED_FOR_PR_7.items() if key != "organization"},
        FINISHED_FOR_PR_7 | {"pullRequest": None, "branch": "7"},
    ],
    ids=["other pull request", "no pull request", "other component", "other organization", "no organization", "branch only"],
)
def test_a_finished_task_that_is_not_this_runs_is_refused(monkeypatch, capsys, task):
    """#22 kept row: a finished task of another pull request, component or organization is exit 2"""
    fake = sonar_answering(task=task)
    code, _, err, _ = run_cli(monkeypatch, capsys, fake)
    assert (code, fake.urls_to("issues/search")) == (2, [])
    assert "another analysis" in err


@pytest.mark.parametrize(
    ("environment", "task", "findings", "code", "line"),
    [
        (PULL_REQUEST_ENVIRONMENT, FINISHED_FOR_PR_7, 1, 1, "sonar: 1 open finding(s) in pullRequest=7"),
        (PUSH_ENVIRONMENT, FINISHED_FOR_MAIN, 25, 0, "sonar: 25 open on main (report only until baseline)"),
        (PUSH_ENVIRONMENT, FINISHED_FOR_MAIN, 0, 0, f"sonar: clean for '{KEY}' (branch=main): 0 open finding(s)"),
        ({**PUSH_ENVIRONMENT, "PULL_REQUEST_NUMBER": "7"}, FINISHED_FOR_MAIN, 1, 0, "report only"),
    ],
    ids=["pull request with a finding", "branch with findings", "clean branch", "push ignores a pull request number"],
)
def test_findings_fail_a_pull_request_and_only_report_on_a_branch(monkeypatch, capsys, environment, task, findings, code, line):
    """#22 kept row: findings fail a pull request and only report on a branch"""
    fake = sonar_answering(task=task)
    fake.answers["issues/search"] = [issues_page(findings, *range(1, min(findings, 100) + 1)), issues_page(findings)]
    result = run_cli(monkeypatch, capsys, fake, environment)
    assert result[0] == code
    assert line in result[1] + result[2]


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (TimeoutError(), True),
        (urllib.error.URLError(TimeoutError()), True),
        (urllib.error.URLError(ConnectionRefusedError()), False),
        (OSError(), False),
    ],
)
def test_recognises_which_transport_failures_ran_out_of_time(error, expected):
    """#22 kept row: a transport failure is told apart as a timeout"""
    assert sonar.timed_out(error) is expected


Responder = Callable[[http.server.BaseHTTPRequestHandler], None]


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


def reply(status: int, body: bytes, **headers: str) -> Responder:
    def respond(handler):
        handler.send_response(status)
        sent = {"Content-Length": str(len(body))} | {name.replace("_", "-"): value for name, value in headers.items()}
        for name, value in sent.items():
            handler.send_header(name, value)
        handler.end_headers()
        handler.wfile.write(body)

    return respond


def test_the_real_transport_returns_json(loopback):
    """#22 kept row: the real transport returns the JSON body"""
    responder, _, url = loopback
    responder.append(reply(200, b'{"a": 1}'))
    assert sonar.fetch_json(url, "Basic x") == {"a": 1}


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
    """#22: the real transport maps non-JSON, redirect, HTTP error, non-UTF-8 and truncated bodies to cannot judge"""
    set_responder, _, url = loopback
    set_responder.append(responder)
    with pytest.raises(CannotJudge, match=message):
        sonar.fetch_json(url, "Basic x")


def test_the_real_transport_gives_up_on_a_server_that_never_answers(loopback):
    """#22 kept row: a server that never answers is cannot judge after the timeout"""
    responder, release, url = loopback
    responder.append(lambda handler: release.wait(30))
    with pytest.raises(CannotJudge, match="did not answer"):
        sonar.fetch_json(url, "Basic x", timeout=0.25)


def test_the_real_transport_reports_an_unreachable_server():
    """#22 kept row: an unreachable server is cannot judge"""
    with pytest.raises(CannotJudge, match="could not be reached"):
        sonar.fetch_json("http://127.0.0.1:1/x", "Basic x")


CLEAN_PROPERTIES = (
    "sonar.organization=overnightworks\nsonar.projectKey=overnightworks_quality\nsonar.sources=src\nsonar.tests=tests\n"
)


def run_preflight(monkeypatch, capsys, build_repo, properties: str | None = CLEAN_PROPERTIES, environment=PULL_REQUEST_ENVIRONMENT, extra_files=None):
    set_environment(monkeypatch, environment)
    files = dict(extra_files or {})
    if properties is not None:
        files["sonar-project.properties"] = properties
    code = cli.main(["sonar", "preflight", str(build_repo(files))])
    captured = capsys.readouterr()
    assert TOKEN not in captured.out + captured.err
    return code, captured.out, captured.err


def test_preflight_passes_the_callers_own_clean_properties(monkeypatch, capsys, build_repo):
    """#26 rule 3: the caller's own organization, key and allowlisted settings pass"""
    assert run_preflight(monkeypatch, capsys, build_repo)[0] == 0


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
    ],
    ids=[
        "typo beside a valid sibling", "glob that matches", "glob that matches nothing", "trailing dir/** over a file below",
        "space after the comma", "directory instead of a file", "empty value", "missing lcov report", "present lcov report",
        "absolute entry", "entry with a parent segment",
    ],
)
def test_preflight_requires_every_named_coverage_report_to_exist(monkeypatch, capsys, build_repo, key, value, files, code, named):
    """#34 rule 2: each entry of a coverage report key is judged on its own; a missing report is exit 2 naming key and entry"""
    properties = CLEAN_PROPERTIES + f"{key}={value}\n"
    result = run_preflight(monkeypatch, capsys, build_repo, properties, extra_files={name: "<coverage/>" for name in files})
    assert result[0] == code
    if named:
        assert key in result[2] and named in result[2]


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
def test_preflight_refuses_what_could_steer_the_token_naming_it(monkeypatch, capsys, build_repo, properties, named):
    """#26 rule 3: a foreign key, endpoint family or ${...} substitution in any decoded value is exit 2, naming the key"""
    code, _, err = run_preflight(monkeypatch, capsys, build_repo, CLEAN_PROPERTIES + properties + "\n")
    assert code == 2
    assert named in err


def test_preflight_drops_a_final_unpaired_backslash_like_java_properties(monkeypatch, capsys, build_repo):
    """#26 rule 3: a last line ending in a backslash without a newline is read as java.util.Properties reads it"""
    properties = "sonar.projectKey=overnightworks_quality\nsonar.organization=overnightworks\\"
    assert run_preflight(monkeypatch, capsys, build_repo, properties)[0] == 0


def test_preflight_refuses_a_properties_file_without_the_callers_key(monkeypatch, capsys, build_repo):
    """#26 rule 3: a file that does not name the caller's project key is exit 2"""
    code, _, err = run_preflight(monkeypatch, capsys, build_repo, "sonar.organization=overnightworks\n")
    assert (code, "sonar.projectKey" in err) == (2, True)


def test_preflight_lets_a_lone_dollar_a_brace_and_comments_pass(monkeypatch, capsys, build_repo):
    """#26 rule 3: a lone dollar or a brace without a dollar is not a substitution; comments are skipped"""
    properties = CLEAN_PROPERTIES + "# sonar.host.url=https://evil.example\n! sonar.region=us\nsonar.projectName=cost $ {x}\n"
    assert run_preflight(monkeypatch, capsys, build_repo, properties)[0] == 0


@pytest.mark.parametrize(
    "environment",
    [{**PULL_REQUEST_ENVIRONMENT, "SONAR_TOKEN": ""}, {k: v for k, v in PULL_REQUEST_ENVIRONMENT.items() if k != "SONAR_TOKEN"}],
    ids=["empty token", "unset token"],
)
def test_preflight_refuses_a_missing_token_and_never_skips(monkeypatch, capsys, build_repo, environment):
    """#26 rule 2: an empty or unset token is exit 2 naming SONAR_TOKEN"""
    code, _, err = run_preflight(monkeypatch, capsys, build_repo, environment=environment)
    assert (code, "SONAR_TOKEN is empty" in err) == (2, True)


def test_preflight_refuses_a_tracked_scanner_work_directory(monkeypatch, capsys, build_repo):
    """#26 rule 3: a committed .scannerwork/report-task.txt is exit 2"""
    files = {".scannerwork/report-task.txt": "ceTaskId=x\n"}
    code, _, err = run_preflight(monkeypatch, capsys, build_repo, extra_files=files)
    assert (code, ".scannerwork" in err) == (2, True)


def test_preflight_refuses_a_repository_without_properties(monkeypatch, capsys, build_repo):
    """#26 rule 3: no sonar-project.properties is exit 2"""
    code, _, err = run_preflight(monkeypatch, capsys, build_repo, properties=None)
    assert (code, "sonar-project.properties" in err) == (2, True)
