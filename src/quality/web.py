"""HTTP reads that send a credential: never follow a redirect, never wait without bound; and the injected wait between polls."""

import http.client
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from email.message import Message

from quality.report import CannotJudge

# A server that accepts the connection and then stops answering must not hang the job.
REQUEST_TIMEOUT_SECONDS = 30
Sleep = Callable[[float], None]


class RedirectRefusingHandler(urllib.request.HTTPRedirectHandler):
    # A redirect would forward the Authorization header to another host; refuse it instead.
    def redirect_request(self, *args, **kwargs) -> None:
        return None


def fetch(
    url: str, headers: Mapping[str, str], unreadable: Callable[[Exception], CannotJudge],
    timeout: float = REQUEST_TIMEOUT_SECONDS,
) -> tuple[bytes, Message]:
    """The body and headers answered at `url`, never following a redirect; any other answer is `unreadable(error)`."""
    opener = urllib.request.build_opener(RedirectRefusingHandler())
    try:
        with opener.open(urllib.request.Request(url, headers=dict(headers)), timeout=timeout) as response:  # noqa: S310 - urls are SonarCloud and GitHub API endpoints built by quality plus the next page url from GitHub's Link header (trusted as GitHub's https answer; no scheme check)
            return response.read(), response.headers
    except (OSError, http.client.HTTPException) as error:  # a truncated body is an HTTPException
        raise unreadable(error) from error


def timed_out(error: OSError) -> bool:
    # A read that runs out of time raises TimeoutError itself; a connect that does arrives in URLError.
    return isinstance(error, TimeoutError) or (
        isinstance(error, urllib.error.URLError) and isinstance(error.reason, TimeoutError)
    )
