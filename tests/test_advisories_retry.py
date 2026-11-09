import datetime
import errno
import http.server
import io
import threading
import urllib.error
import urllib.request
import urllib.response
from types import SimpleNamespace

import pytest

from quality import advisories
from quality.scanners import ScannerError
from tests.support.advisories import uv_lock, zipped

DATABASE = zipped({"GHSA-aaaa-bbbb-cccc.json": "{}"})
TODAY = datetime.date(2026, 11, 9)
GATEWAY_TIMEOUT = 504
TRUNCATED = "truncated"
SERVED = "served"
NOT_A_ZIP = "not a zip"


@pytest.fixture
def scripted_server(monkeypatch):
    """A local HTTP server that answers request n with script[n] (the last entry repeats): an HTTP status, `SERVED`
    for the database, or `TRUNCATED` for a body cut short. It refuses connections until `.open()`, and each request
    is recorded as it arrives; it is stopped at teardown."""
    served = SimpleNamespace(script=[SERVED], requested=[], on_request=lambda: None)

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            step = served.script[min(len(served.requested), len(served.script) - 1)]
            served.requested.append(self.path)
            served.on_request()
            if step in (SERVED, NOT_A_ZIP):
                body = DATABASE if step == SERVED else step.encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif step == TRUNCATED:
                self.send_response(200)
                self.send_header("Content-Length", str(len(DATABASE) + 100))
                self.end_headers()
                self.wfile.write(DATABASE)
                self.close_connection = True
            else:
                self.send_error(step)

        def log_message(self, *arguments):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler, bind_and_activate=False)
    server.daemon_threads = True
    server.server_bind()
    serving = threading.Thread(target=server.serve_forever)

    def open_for_connections() -> None:
        server.server_activate()
        serving.start()

    served.open = open_for_connections
    monkeypatch.setattr(advisories, "DATABASE_ROOT", f"http://127.0.0.1:{server.server_address[1]}")
    yield served
    if serving.is_alive():
        server.shutdown()
        serving.join()
    server.server_close()


@pytest.fixture
def flaky_network(monkeypatch):
    """Makes the first `flaky://` open raise `unreachable` as urllib reports it; every later one serves the database."""
    opened = SimpleNamespace(count=0)

    class Flaky(urllib.request.BaseHandler):
        def flaky_open(self, request):
            opened.count += 1
            if opened.count == 1:
                raise urllib.error.URLError(OSError(errno.ENETUNREACH, "Network is unreachable"))
            return urllib.response.addinfourl(io.BytesIO(DATABASE), {}, request.full_url, 200)

    urllib.request.install_opener(urllib.request.build_opener(Flaky()))
    monkeypatch.setattr(advisories, "DATABASE_ROOT", "flaky://database")
    yield opened
    urllib.request.install_opener(None)


@pytest.fixture
def scan(build_repo):
    """Scans a repository with a uv.lock; returns (the result or the refusal, the seconds slept) with a clock that
    moves only by sleeping."""
    repo = build_repo({"uv.lock": uv_lock()})
    state = SimpleNamespace(now=0.0, slept=[], on_sleep=lambda: None)

    def sleep(seconds: float) -> None:
        state.on_sleep()
        state.slept.append(seconds)
        state.now += seconds

    def run():
        try:
            return advisories.scan({"uv.lock": repo / "uv.lock"}, repo, [], TODAY, lambda: state.now, sleep=sleep)
        except ScannerError as refusal:
            return refusal

    run.state = state
    return run


def test_a_gateway_timeout_is_tried_again_after_three_seconds_and_then_used(scan, scripted_server):
    """ADVISORY-9: a transient failure is retried; the first delay is 3 seconds."""
    scripted_server.script = [GATEWAY_TIMEOUT, SERVED]
    scripted_server.open()
    result = scan()
    assert (isinstance(result, ScannerError), len(scripted_server.requested), scan.state.slept) == (False, 2, [3])


@pytest.mark.parametrize("status", [GATEWAY_TIMEOUT, 429])
def test_a_database_that_never_arrives_is_refused_after_four_attempts_with_growing_delays(
    scan, scripted_server, status
):
    """ADVISORY-9, ADVISORY-42: 429 and 5xx are transient; four attempts wait 3, 9 and 27 seconds, then the refusal
    names the database, the last error and the attempts."""
    scripted_server.script = [status]
    scripted_server.open()
    refusal = scan()
    assert isinstance(refusal, ScannerError)
    message = str(refusal)
    observed = (message.startswith("cannot download the PyPI advisory database from "), message.endswith(" after 4 attempts"))
    assert observed == (True, True)
    assert (len(scripted_server.requested), scan.state.slept) == (4, [3, 9, 27])


def test_a_missing_database_is_not_tried_again(scan, scripted_server):
    """ADVISORY-42: another 4xx is permanent."""
    scripted_server.script = [404]
    scripted_server.open()
    refusal = scan()
    message = str(refusal)
    observed = (isinstance(refusal, ScannerError), "cannot download the PyPI advisory database" in message)
    assert (*observed, "attempts" in message) == (True, True, False)
    assert (len(scripted_server.requested), scan.state.slept) == (1, [])


def test_a_database_missing_from_a_local_root_is_not_tried_again(scan, database_root):
    """ADVISORY-42: a missing file is permanent."""
    (database_root / "PyPI" / "all.zip").unlink()
    refusal = scan()
    message = str(refusal)
    observed = (isinstance(refusal, ScannerError), "cannot download the PyPI advisory database" in message)
    assert (*observed, scan.state.slept) == (True, True, [])


def test_a_refused_connection_is_tried_again_until_the_server_listens(scan, scripted_server):
    """ADVISORY-9: a refused connection is transient."""
    scan.state.on_sleep = scripted_server.open
    result = scan()
    observed = (isinstance(result, ScannerError), scripted_server.requested, scan.state.slept)
    assert observed == (False, ["/PyPI/all.zip"], [3])


def test_an_unreachable_network_is_tried_again_and_then_used(scan, flaky_network):
    """ADVISORY-9: ENETUNREACH is transient."""
    result = scan()
    assert (isinstance(result, ScannerError), flaky_network.count, scan.state.slept) == (False, 2, [3])


def test_a_body_cut_short_is_tried_again(scan, scripted_server):
    """ADVISORY-9: a truncated body is transient."""
    scripted_server.script = [TRUNCATED, SERVED]
    scripted_server.open()
    result = scan()
    assert (isinstance(result, ScannerError), len(scripted_server.requested), scan.state.slept) == (False, 2, [3])


def test_a_body_that_arrived_whole_but_is_not_a_zip_is_not_tried_again(scan, scripted_server):
    """ADVISORY-42: every error after the body is whole is permanent."""
    scripted_server.script = [NOT_A_ZIP]
    scripted_server.open()
    refusal = scan()
    assert (isinstance(refusal, ScannerError), "is not a valid zip" in str(refusal)) == (True, True)
    assert (len(scripted_server.requested), scan.state.slept) == (1, [])


def test_the_last_wait_is_cut_to_the_time_left_and_the_deadline_is_the_refusal(scan, scripted_server):
    """ADVISORY-9, ADVISORY-34: the retries spend the existing deadline and add none."""
    scripted_server.script = [GATEWAY_TIMEOUT]
    scripted_server.on_request = lambda: setattr(scan.state, "now", advisories.DATABASE_DOWNLOAD_DEADLINE_SECONDS - 1)
    scripted_server.open()
    refusal = scan()
    assert isinstance(refusal, ScannerError)
    assert (str(refusal), scan.state.slept, len(scripted_server.requested)) == (
        "the PyPI advisory database download exceeded 300 seconds", [1], 1)
