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


def run_cli(
    monkeypatch, capsys, fake: FakeSonar, environment=PULL_REQUEST_ENVIRONMENT, task_text: str | None = None, tmp_path=None
):
    monkeypatch.setattr("os.environ", dict(environment))
    task_file = TASK_FIXTURE
    if task_text is not None:
        task_file = tmp_path / "report-task.txt"
        task_file.write_text(task_text)
    sleeps: list[float] = []
    code = cli.main(["sonar", "findings", "--task-file", str(task_file)], transport=fake, sleep=sleeps.append)
    captured = capsys.readouterr()
    assert TOKEN not in captured.out + captured.err
    return code, captured.out, captured.err, sleeps


def test_known_pass_fixture_is_clean_and_prints_the_proof(monkeypatch, capsys):
    code, out, _, _ = run_cli(monkeypatch, capsys, sonar_answering())
    assert (code, out) == (0, f"sonar: clean for '{KEY}' (pullRequest=7): 0 open finding(s)\n")


def test_known_fail_fixture_lists_every_open_finding_and_exits_one(monkeypatch, capsys):
    code, out, err, _ = run_cli(monkeypatch, capsys, sonar_answering(issues=issues_page(2, 1, 2)))
    assert code == 1
    assert "sonar src/a1.py:1 - python:S1: m" in out and "sonar src/a2.py:2 - python:S1: m" in out
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
    fake = sonar_answering()
    fake.answers["issues/search"] = pages
    code, _, err, _ = run_cli(monkeypatch, capsys, fake)
    assert (code, len(fake.urls_to("issues/search"))) == (1, expected_requests)
    assert f"{expected_count} open finding(s)" in err


def test_asks_for_statuses_a_person_may_have_accepted_and_encodes_the_scope(monkeypatch, capsys):
    fake = sonar_answering(
        task={**FINISHED_FOR_PR_7, "pullRequest": None, "branch": "feat/a&b#c%d"}, issues=issues_page(0)
    )
    environment = {**PUSH_ENVIRONMENT, "GITHUB_REF_NAME": "feat/a&b#c%d"}
    code, *_ = run_cli(monkeypatch, capsys, fake, environment)
    query = parse_qs(urlparse(fake.urls_to("issues/search")[0]).query)
    assert code == 0
    assert query["issueStatuses"] == ["OPEN,CONFIRMED,ACCEPTED,FALSE_POSITIVE"]
    assert query["branch"] == ["feat/a&b#c%d"] and "statuses" not in query
    assert query["componentKeys"] == [KEY]


def test_sends_the_token_in_the_header_only(monkeypatch, capsys):
    fake = sonar_answering()
    run_cli(monkeypatch, capsys, fake)
    assert {authorization for _, authorization in fake.requests} == {sonar.basic_authorization(TOKEN)}
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
    ],
    ids=["scope unresolved", "measures unreachable", "no total", "component without file", "wrong shape", "bad total"],
)
def test_an_unreadable_answer_is_exit_two_and_never_clean(monkeypatch, capsys, fake):
    code, out, err, _ = run_cli(monkeypatch, capsys, fake)
    assert (code, out) == (2, "")
    assert err.startswith("sonar: cannot judge - ")


def test_does_not_read_findings_when_the_scope_does_not_resolve(monkeypatch, capsys):
    fake = sonar_answering()
    fake.answers["measures/component"] = [sonar.SonarHttpError(404, "u")]
    code, *_ = run_cli(monkeypatch, capsys, fake)
    assert (code, fake.urls_to("issues/search")) == (2, [])


@pytest.mark.parametrize(
    ("edit", "reason"),
    [
        (("serverUrl=https://sonarcloud.io", "serverUrl=https://evil.example"), "evil.example"),
        (("projectKey=overnightworks_quality", "projectKey=other_project"), "other_project"),
        (("projectKey=overnightworks_quality\n", ""), "None"),
        (("ceTaskId=AXtask-1\n", ""), "ceTaskId"),
    ],
    ids=["foreign server", "foreign project", "missing project", "missing task id"],
)
def test_a_task_file_naming_something_foreign_is_refused_before_any_request(
    monkeypatch, capsys, tmp_path, edit, reason
):
    fake = sonar_answering()
    text = TASK_FIXTURE.read_text().replace(*edit)
    code, _, err, _ = run_cli(monkeypatch, capsys, fake, task_text=text, tmp_path=tmp_path)
    assert (code, fake.requests) == (2, [])
    assert reason in err


def test_a_task_file_that_cannot_be_read_is_refused_before_any_request(monkeypatch, capsys, tmp_path):
    fake = sonar_answering()
    monkeypatch.setattr("os.environ", dict(PULL_REQUEST_ENVIRONMENT))
    code = cli.main(["sonar", "findings", "--task-file", str(tmp_path / "absent.txt")], transport=fake)
    assert (code, fake.requests) == (2, [])
    assert "absent.txt" in capsys.readouterr().err


def test_never_requests_the_urls_the_task_file_names(monkeypatch, capsys, tmp_path):
    fake = sonar_answering()
    text = TASK_FIXTURE.read_text().replace("https://sonarcloud.io/api/ce", "https://evil.example/api/ce")
    run_cli(monkeypatch, capsys, fake, task_text=text, tmp_path=tmp_path)
    assert all(url.startswith("https://sonarcloud.io/api/") for url, _ in fake.requests)


def test_a_task_id_with_an_ampersand_stays_one_query_value(monkeypatch, capsys, tmp_path):
    fake = sonar_answering()
    text = TASK_FIXTURE.read_text().replace("ceTaskId=AXtask-1", "ceTaskId=a&b=c")
    run_cli(monkeypatch, capsys, fake, task_text=text, tmp_path=tmp_path)
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
        for name, value in {"Content-Length": str(len(body)), **headers}.items():
            handler.send_header(name.replace("_", "-"), value)
        handler.end_headers()
        handler.wfile.write(body)

    return respond


def test_the_real_transport_returns_json(loopback):
    responder, _, url = loopback
    responder.append(reply(200, b'{"a": 1}'))
    assert sonar.fetch_json(url, "Basic x") == {"a": 1}


@pytest.mark.parametrize(
    ("responder", "message"),
    [
        (reply(200, b"<html>"), "not JSON"),
        (reply(302, b"", Location="http://127.0.0.1:1/stolen"), "HTTP 302"),
        (reply(404, b"{}"), "HTTP 404"),
    ],
    ids=["non-JSON", "redirect refused", "http error"],
)
def test_the_real_transport_refuses_what_it_cannot_trust(loopback, responder, message):
    set_responder, _, url = loopback
    set_responder.append(responder)
    with pytest.raises(CannotJudge, match=message):
        sonar.fetch_json(url, "Basic x")


def test_the_real_transport_gives_up_on_a_server_that_never_answers(loopback, monkeypatch):
    responder, release, url = loopback
    responder.append(lambda handler: release.wait(30))
    monkeypatch.setattr(sonar, "REQUEST_TIMEOUT_SECONDS", 0.25)
    with pytest.raises(CannotJudge, match="did not answer"):
        sonar.fetch_json(url, "Basic x")


def test_the_real_transport_reports_an_unreachable_server():
    with pytest.raises(CannotJudge, match="could not be reached"):
        sonar.fetch_json("http://127.0.0.1:1/x", "Basic x")
