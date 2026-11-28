"""The one HTTP transport: it never follows a redirect and never waits without bound; and the injected wait between polls."""

import errno
import http.client
import socket
import threading
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator, Mapping
from concurrent.futures import Future
from contextlib import contextmanager
from email.message import Message
from http import HTTPStatus

from quality.deadline import Deadline
from quality.report import CannotJudge

# A server that accepts the connection and then stops answering must not hang the job.
REQUEST_TIMEOUT_SECONDS = 30
READ_CHUNK_BYTES = 1 << 16
DEADLINE_SPENT = "the deadline is spent"
_TRANSIENT_ERRNOS = frozenset({errno.ENETUNREACH, errno.EHOSTUNREACH})
_TRANSIENT_CONNECTION_ERRORS = (
    ConnectionRefusedError, ConnectionResetError, TimeoutError, socket.gaierror, http.client.IncompleteRead,
)
Sleep = Callable[[float], None]


class CutAtDeadline(TimeoutError):
    """The request did not finish before its deadline."""


class StoppedUnexpectedly(Exception):
    """The thread that made the request ended without an outcome; its traceback is on stderr."""


FETCH_FAILURES = (OSError, http.client.HTTPException, StoppedUnexpectedly)
type BoundedFetch = Callable[[str, Mapping[str, str], Deadline, float], tuple[bytes, Message]]


class RedirectRefusingHandler(urllib.request.HTTPRedirectHandler):
    # A redirect would forward the Authorization header to another host; refuse it instead.
    def redirect_request(self, *args, **kwargs) -> None:
        return None


@contextmanager
def _opened(url: str, headers: Mapping[str, str], timeout: float) -> Iterator[http.client.HTTPResponse]:
    """The open response at `url`, a redirect refused: its status comes as an `HTTPError`."""
    opener = urllib.request.build_opener(RedirectRefusingHandler())
    with opener.open(urllib.request.Request(url, headers=dict(headers)), timeout=timeout) as response:  # noqa: S310 - urls are SonarCloud and GitHub API endpoints built by quality plus the next page url from GitHub's Link header (trusted as GitHub's https answer; no scheme check) and the SonarSource binaries url and the advisory database url built by quality
        yield response


def fetch(
    url: str, headers: Mapping[str, str], unreadable: Callable[[Exception], CannotJudge],
    timeout: float = REQUEST_TIMEOUT_SECONDS,
) -> tuple[bytes, Message]:
    """The body and headers answered at `url`, never following a redirect; any other answer is `unreadable(error)`."""
    try:
        with _opened(url, headers, timeout) as response:
            return response.read(), response.headers
    except (OSError, http.client.HTTPException) as error:  # a truncated body is an HTTPException
        raise unreadable(error) from error


class _SocketWithinDeadline:
    """A socket whose every receive is armed with the smaller of `timeout` and the time left; a spent deadline is `CutAtDeadline`."""

    def __init__(self, sock: socket.socket, deadline: Deadline, timeout: float) -> None:
        self._sock, self._deadline, self._timeout = sock, deadline, timeout

    def recv_into(self, buffer: bytearray | memoryview) -> int:
        left = self._deadline.left()
        if left <= 0:
            raise CutAtDeadline(DEADLINE_SPENT)
        self._sock.settimeout(min(self._timeout, left))
        return self._sock.recv_into(buffer)

    def __getattr__(self, name: str) -> object:
        return getattr(self._sock, name)


def _read_within(response: http.client.HTTPResponse, deadline: Deadline, timeout: float) -> bytes:
    """The rest of `response`, every socket receive of it (chunk framing included) bounded by `timeout` and the time left.
    `http.client` has no per-read timeout, so the socket under the response's file is replaced by one that arms itself."""
    raw = response.fp.raw
    raw._sock = _SocketWithinDeadline(raw._sock, deadline, timeout)  # noqa: SLF001 - the only way to bound the reads of a response that is already open
    chunks: list[bytes] = []
    while not response.isclosed():  # the response closes itself with its last byte
        chunks.append(response.read1(READ_CHUNK_BYTES))
    if response.length:  # the connection ended before the announced length
        raise http.client.IncompleteRead(b"".join(chunks), response.length)
    return b"".join(chunks)


def fetch_within(
    url: str, headers: Mapping[str, str], deadline: Deadline, timeout: float = REQUEST_TIMEOUT_SECONDS
) -> tuple[bytes, Message]:
    """The body and headers answered at `url`, never following a redirect, or `CutAtDeadline` when `deadline` is reached
    first. Any other answer raises a `FETCH_FAILURES` error: the transport's own, a status as `HTTPError`, or
    `StoppedUnexpectedly` where the thread that made the request died.
    The request runs on a thread this call stops waiting for at the deadline, so a name lookup, a connect to several
    addresses, a header, a chunk size or a byte that arrives slowly cannot hold it past the deadline; `timeout` and the
    time left bound each socket operation of that thread, and each receive of the body is armed again, which lets an
    abandoned thread end by itself at the deadline once it reads the body."""
    left = deadline.left()
    if left <= 0:
        raise CutAtDeadline(DEADLINE_SPENT)
    answer: Future[tuple[bytes, Message]] = Future()

    def read() -> None:
        try:
            with _opened(url, headers, min(timeout, left)) as response:
                answer.set_result((_read_within(response, deadline, timeout), response.headers))
        except (OSError, http.client.HTTPException) as error:
            answer.set_exception(error)

    thread = threading.Thread(target=read, daemon=True)
    thread.start()
    thread.join(left)
    if answer.done():
        try:
            return answer.result()
        except OSError as error:
            if timed_out(error) and deadline.left() <= 0:  # a socket wait bounded by the time left ends at the deadline
                raise CutAtDeadline(DEADLINE_SPENT) from error
            raise
    if thread.is_alive():
        raise CutAtDeadline(DEADLINE_SPENT)
    raise StoppedUnexpectedly("the request stopped on an unexpected error")


def is_transient(error: BaseException) -> bool:
    """Whether a failure of the transport may not come again: a status of 429 or 500 to 599, a refused or reset
    connection, a timeout, a name that does not resolve, an unreachable network or host, a body cut short."""
    if isinstance(error, urllib.error.HTTPError):
        return error.code == HTTPStatus.TOO_MANY_REQUESTS or 500 <= error.code <= 599
    if isinstance(error, urllib.error.URLError):
        return isinstance(error.reason, BaseException) and is_transient(error.reason)
    return isinstance(error, _TRANSIENT_CONNECTION_ERRORS) or (
        isinstance(error, OSError) and error.errno in _TRANSIENT_ERRNOS
    )


def timed_out(error: OSError) -> bool:
    # A read that runs out of time raises TimeoutError itself; a connect that does arrives in URLError.
    return isinstance(error, TimeoutError) or (
        isinstance(error, urllib.error.URLError) and isinstance(error.reason, TimeoutError)
    )
