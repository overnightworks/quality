"""HTTP reads that send a credential: never follow a redirect, never wait without bound; and the injected wait between polls."""

import http.client
import queue
import socket
import threading
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from email.message import Message

from quality.report import CannotJudge

# A server that accepts the connection and then stops answering must not hang the job.
REQUEST_TIMEOUT_SECONDS = 30
READ_CHUNK_BYTES = 1 << 16
DEADLINE_SPENT = "the deadline is spent"
Sleep = Callable[[float], None]


class RedirectRefusingHandler(urllib.request.HTTPRedirectHandler):
    # A redirect would forward the Authorization header to another host; refuse it instead.
    def redirect_request(self, *args, **kwargs) -> None:
        return None


@contextmanager
def _opened(
    url: str, headers: Mapping[str, str], unreadable: Callable[[Exception], CannotJudge], timeout: float
) -> Iterator[http.client.HTTPResponse]:
    """The open response at `url`, a redirect refused; a failure of the request or of the reads inside the block is `unreadable(error)`."""
    opener = urllib.request.build_opener(RedirectRefusingHandler())
    try:
        with opener.open(urllib.request.Request(url, headers=dict(headers)), timeout=timeout) as response:  # noqa: S310 - urls are SonarCloud and GitHub API endpoints built by quality plus the next page url from GitHub's Link header (trusted as GitHub's https answer; no scheme check) and the SonarSource binaries url built by quality
            yield response
    except (OSError, http.client.HTTPException) as error:  # a truncated body is an HTTPException
        raise unreadable(error) from error


def fetch(
    url: str, headers: Mapping[str, str], unreadable: Callable[[Exception], CannotJudge],
    timeout: float = REQUEST_TIMEOUT_SECONDS,
) -> tuple[bytes, Message]:
    """The body and headers answered at `url`, never following a redirect; any other answer is `unreadable(error)`."""
    with _opened(url, headers, unreadable, timeout) as response:
        return response.read(), response.headers


class _SocketWithinDeadline:
    """A socket whose every receive is armed with the smaller of `timeout` and the time left; a spent deadline is a TimeoutError."""

    def __init__(self, sock: socket.socket, expires_at: float, clock: Callable[[], float], timeout: float) -> None:
        self._sock, self._expires_at, self._clock, self._timeout = sock, expires_at, clock, timeout

    def recv_into(self, buffer: bytearray | memoryview) -> int:
        left = self._expires_at - self._clock()
        if left <= 0:
            raise TimeoutError(DEADLINE_SPENT)
        self._sock.settimeout(min(self._timeout, left))
        return self._sock.recv_into(buffer)

    def __getattr__(self, name: str) -> object:
        return getattr(self._sock, name)


def _read_within(response: http.client.HTTPResponse, expires_at: float, clock: Callable[[], float], timeout: float) -> bytes:
    """The rest of `response`, every socket receive of it (chunk framing included) bounded by `timeout` and the time left.
    `http.client` has no per-read timeout, so the socket under the response's file is replaced by one that arms itself."""
    raw = response.fp.raw
    raw._sock = _SocketWithinDeadline(raw._sock, expires_at, clock, timeout)  # noqa: SLF001 - the only way to bound the reads of a response that is already open
    chunks: list[bytes] = []
    while not response.isclosed():  # the response closes itself with its last byte
        chunks.append(response.read1(READ_CHUNK_BYTES))
    if response.length:  # the connection ended before the announced length
        raise http.client.IncompleteRead(b"".join(chunks), response.length)
    return b"".join(chunks)


def fetch_within(
    url: str, unreadable: Callable[[Exception], CannotJudge], expires_at: float, clock: Callable[[], float],
    timeout: float = REQUEST_TIMEOUT_SECONDS,
) -> bytes:
    """The body answered at `url`, or `unreadable(TimeoutError)` when `clock` (in seconds) reaches `expires_at` first.
    The request runs on a thread this call stops waiting for at the deadline, so a name lookup, a connect to several
    addresses, a header, a chunk size or a byte that arrives slowly cannot hold it past `expires_at`; `timeout` and the
    time left bound each socket operation of that thread, and each receive of the body is armed again, which lets an
    abandoned thread end by itself at the deadline once it reads the body."""
    left = expires_at - clock()
    if left <= 0:
        raise unreadable(TimeoutError(DEADLINE_SPENT))
    answers: queue.Queue[bytes | CannotJudge] = queue.Queue(maxsize=1)

    def read() -> None:
        try:
            with _opened(url, {}, unreadable, min(timeout, left)) as response:
                answers.put(_read_within(response, expires_at, clock, timeout))
        except CannotJudge as refusal:
            answers.put(refusal)

    threading.Thread(target=read, daemon=True).start()
    try:
        answer = answers.get(timeout=left)
    except queue.Empty:
        raise unreadable(TimeoutError(DEADLINE_SPENT)) from None
    if isinstance(answer, CannotJudge):
        raise answer
    return answer


def timed_out(error: OSError) -> bool:
    # A read that runs out of time raises TimeoutError itself; a connect that does arrives in URLError.
    return isinstance(error, TimeoutError) or (
        isinstance(error, urllib.error.URLError) and isinstance(error.reason, TimeoutError)
    )
