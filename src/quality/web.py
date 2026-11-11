"""HTTP reads that send a credential: never follow a redirect, never wait without bound; and the injected wait between polls."""

import http.client
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from email.message import Message

from quality.report import CannotJudge

# A server that accepts the connection and then stops answering must not hang the job.
REQUEST_TIMEOUT_SECONDS = 30
READ_CHUNK_BYTES = 1 << 20
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


def fetch_within(
    url: str, unreadable: Callable[[Exception], CannotJudge], expires_at: float, clock: Callable[[], float],
    timeout: float = REQUEST_TIMEOUT_SECONDS,
) -> bytes:
    """The body answered at `url`, read in chunks so that `clock` passing `expires_at` ends the read with
    `unreadable(TimeoutError)` however slowly the server sends. A request or read waits at most `timeout` and at
    most what is left of the deadline when the request opens."""
    left = expires_at - clock()
    if left <= 0:
        raise unreadable(TimeoutError("the deadline is spent"))
    chunks: list[bytes] = []
    with _opened(url, {}, unreadable, min(timeout, left)) as response:
        while chunk := response.read1(READ_CHUNK_BYTES):
            if clock() >= expires_at:
                raise unreadable(TimeoutError("the deadline is spent"))
            chunks.append(chunk)
    return b"".join(chunks)


def timed_out(error: OSError) -> bool:
    # A read that runs out of time raises TimeoutError itself; a connect that does arrives in URLError.
    return isinstance(error, TimeoutError) or (
        isinstance(error, urllib.error.URLError) and isinstance(error.reason, TimeoutError)
    )
