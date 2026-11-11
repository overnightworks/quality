import threading
import time
import urllib.error

import pytest

from quality import web
from quality.report import CannotJudge


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


@pytest.mark.parametrize("respond", [drips_the_body, drips_the_headers], ids=["body", "headers"])
def test_a_server_that_drips_ends_at_the_deadline_though_every_socket_operation_is_in_time(stub_server, respond):
    """SONAR-115: the deadline ends the read however the bytes arrive; the operation timeout is far longer than it"""
    release = threading.Event()
    origin, _ = stub_server(lambda handler: respond(handler, release))
    started = time.monotonic()
    try:
        with pytest.raises(CannotJudge):
            web.fetch_within(f"{origin}/zip", lambda error: CannotJudge(str(error)), started + DEADLINE_SECONDS, time.monotonic, timeout=15)
    finally:
        release.set()
    assert time.monotonic() - started < DEADLINE_SECONDS + 0.4


def test_a_body_that_arrives_whole_within_the_deadline_is_returned_as_sent(stub_server):
    """SONAR-115: the body is returned as the server sent it"""
    body = b"abc" * (1 << 20)

    def answer(handler):
        handler.send_response(200)
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)

    origin, _ = stub_server(answer)
    assert web.fetch_within(f"{origin}/zip", lambda error: CannotJudge(str(error)), time.monotonic() + 30, time.monotonic) == body


def test_an_error_answer_ends_the_read_with_the_callers_refusal(stub_server):
    """SONAR-115: a failure of the request is `unreadable(error)`, not an error of the thread that made it"""
    refusal = CannotJudge("refused")
    origin, _ = stub_server(lambda handler: (handler.send_response(404), handler.end_headers()))
    expires_at = time.monotonic() + 30
    with pytest.raises(CannotJudge) as raised:
        web.fetch_within(f"{origin}/zip", lambda error: refusal, expires_at, time.monotonic)
    assert raised.value is refusal
