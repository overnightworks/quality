import errno
import http.client
import socket
import ssl
import threading
import time
import urllib.error
from http import HTTPStatus

import pytest

from quality import web
from quality.deadline import Deadline
from quality.report import CannotJudge


def within(seconds: float) -> Deadline:
    return Deadline(time.monotonic() + seconds, time.monotonic)


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
    """A timeout is told apart from other transport failures, also when it arrives wrapped in a URLError."""
    assert web.timed_out(error) is expected


@pytest.mark.integration
def test_a_redirect_is_refused_and_its_target_hears_nothing(stub_server):
    """The credential `fetch` sends must never follow a redirect to another host."""
    target, target_heard = stub_server(lambda handler: (handler.send_response(200), handler.end_headers()))

    def redirect(handler):
        handler.send_response(302)
        handler.send_header("Location", f"{target}/elsewhere")
        handler.end_headers()

    origin, origin_heard = stub_server(redirect)
    refused = CannotJudge("refused")
    with pytest.raises(CannotJudge) as raised:
        web.fetch(f"{origin}/listing", {"Authorization": "Bearer dummy-token"}, lambda error: refused)
    assert (raised.value, len(origin_heard), target_heard) == (refused, 1, [])


DEADLINE_SECONDS = 1.0
MARGIN_SECONDS = 0.7  # what scheduling of the worker threads may add to a deadline
DRIP_SECONDS = 0.9  # shorter than the deadline, so no single socket operation of `timeout` runs out


def drips_the_body(handler, release) -> None:
    handler.send_response(200)
    handler.send_header("Content-Length", "100")
    handler.end_headers()
    for _ in range(10):
        if release.wait(DRIP_SECONDS):
            return
        handler.wfile.write(b"x")
        handler.wfile.flush()


def drips_the_headers(handler, release) -> None:
    handler.wfile.write(b"HTTP/1.0 200 OK\r\n")
    handler.wfile.flush()
    for _ in range(4):
        if release.wait(DRIP_SECONDS):
            return
        handler.wfile.write(b"X-Drip: 1\r\n")
        handler.wfile.flush()
    handler.wfile.write(b"\r\n")


@pytest.mark.integration
@pytest.mark.parametrize("respond", [drips_the_body, drips_the_headers], ids=["body", "headers"])
def test_a_server_that_drips_ends_at_the_deadline_though_every_socket_operation_is_in_time(stub_server, respond):
    """SONAR-115: the deadline ends the read however the bytes arrive; the operation timeout is far longer than it"""
    release = threading.Event()
    origin, _ = stub_server(lambda handler: respond(handler, release))
    started = time.monotonic()
    try:
        with pytest.raises(web.CutAtDeadline):
            web.fetch_within(f"{origin}/zip", {}, Deadline(started + DEADLINE_SECONDS, time.monotonic), timeout=15)
    finally:
        release.set()
    assert time.monotonic() - started < DEADLINE_SECONDS + MARGIN_SECONDS


@pytest.mark.integration
def test_a_body_that_arrives_whole_within_the_deadline_is_returned_as_sent(stub_server):
    """SONAR-115: the body is returned as the server sent it"""
    body = b"abc" * (1 << 20)

    def answer(handler):
        handler.send_response(200)
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)

    origin, _ = stub_server(answer)
    assert web.fetch_within(f"{origin}/zip", {}, within(30))[0] == body


@pytest.mark.integration
def test_an_error_answer_is_raised_as_its_status(stub_server):
    """SONAR-115: a failure of the request is raised to the caller, not an error of the thread that made it"""
    origin, _ = stub_server(lambda handler: (handler.send_response(HTTPStatus.NOT_FOUND), handler.end_headers()))
    with pytest.raises(urllib.error.HTTPError) as raised:
        web.fetch_within(f"{origin}/zip", {}, within(30))
    assert raised.value.code == HTTPStatus.NOT_FOUND


def drips_forever(handler, release, client_gone) -> None:
    handler.send_response(200)
    handler.send_header("Content-Length", "100000")
    handler.end_headers()
    try:
        while not release.wait(0.05):
            handler.wfile.write(b"x")
            handler.wfile.flush()
    except OSError:
        client_gone.set()


def drips_a_chunk_size(handler, release, client_gone) -> None:
    handler.send_response(200)
    handler.send_header("Transfer-Encoding", "chunked")
    handler.end_headers()
    try:
        handler.wfile.write(b"1;")
        while not release.wait(0.05):
            handler.wfile.write(b"x")
            handler.wfile.flush()
    except OSError:
        client_gone.set()


@pytest.mark.integration
@pytest.mark.parametrize("respond", [drips_forever, drips_a_chunk_size], ids=["length", "chunked"])
def test_the_thread_of_a_read_that_ran_past_the_deadline_closes_its_connection_by_itself(stub_server, respond):
    """SONAR-115: every read of the thread is bounded by the time left, so the abandoned thread ends without the caller's help"""
    release, client_gone = threading.Event(), threading.Event()
    origin, _ = stub_server(lambda handler: respond(handler, release, client_gone))
    try:
        with pytest.raises(web.CutAtDeadline):
            web.fetch_within(f"{origin}/zip", {}, within(DEADLINE_SECONDS), timeout=15)
        ended_by_itself = client_gone.wait(MARGIN_SECONDS)
    finally:
        release.set()
    assert ended_by_itself


@pytest.mark.integration
def test_a_body_that_ends_short_of_its_length_is_refused(stub_server):
    """SONAR-115: a truncated body is raised, never a shorter body returned as whole"""

    def answer(handler):
        handler.send_response(200)
        handler.send_header("Content-Length", "100")
        handler.end_headers()
        handler.wfile.write(b"x" * 10)

    origin, _ = stub_server(answer)
    with pytest.raises(http.client.IncompleteRead):
        web.fetch_within(f"{origin}/zip", {}, within(30))


def test_a_read_that_starts_after_the_deadline_asks_nobody():
    """SONAR-115: a spent deadline ends the read before a request goes out"""
    with pytest.raises(web.CutAtDeadline):
        web.fetch_within("http://unreachable.invalid/zip", {}, Deadline(10.0, lambda: 10.0))


def test_a_thread_that_dies_without_an_outcome_is_stopped_unexpectedly_at_once(monkeypatch):
    """SONAR-115, ADVISORY-9: a failure nobody anticipated ends the read with its own error long before the deadline"""
    reported = []
    monkeypatch.setattr(threading, "excepthook", lambda arguments: reported.append(arguments.exc_type))
    started = time.monotonic()
    with pytest.raises(web.StoppedUnexpectedly):
        web.fetch_within("no-scheme", {}, within(30))
    assert (reported, time.monotonic() - started < MARGIN_SECONDS) == ([ValueError], True)


def status(code: int, expected: bool, label: str):
    return pytest.param(urllib.error.HTTPError("u", code, label, {}, None), expected, id=f"{code} {label}")


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        status(HTTPStatus.TOO_MANY_REQUESTS, True, "busy"),
        status(HTTPStatus.GATEWAY_TIMEOUT, True, "late"),
        status(599, True, "last server error"),
        status(HTTPStatus.NOT_FOUND, False, "missing"),
        status(HTTPStatus.FOUND, False, "moved"),
        pytest.param(urllib.error.URLError(ConnectionRefusedError()), True, id="refused connection"),
        pytest.param(ConnectionResetError(), True, id="reset connection"),
        pytest.param(TimeoutError(), True, id="timeout"),
        pytest.param(urllib.error.URLError(socket.gaierror()), True, id="name that does not resolve"),
        pytest.param(http.client.IncompleteRead(b""), True, id="body cut short"),
        pytest.param(urllib.error.URLError(OSError(errno.ENETUNREACH, "unreachable")), True, id="unreachable network"),
        pytest.param(OSError(errno.EHOSTUNREACH, "no route"), True, id="unreachable host"),
        pytest.param(urllib.error.URLError(ssl.SSLCertVerificationError()), False, id="certificate failure"),
        pytest.param(urllib.error.URLError("unknown url type"), False, id="url of no known type"),
        pytest.param(OSError(), False, id="other os error"),
        pytest.param(http.client.BadStatusLine("x"), False, id="other http failure"),
        pytest.param(web.StoppedUnexpectedly(), False, id="thread that died"),
    ],
)
def test_which_transport_failures_may_not_come_again(error, expected):
    """ADVISORY-9: transient failures are retried, a certificate failure, a missing file and a redirect are not"""
    assert web.is_transient(error) is expected
