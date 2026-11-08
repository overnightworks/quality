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
    """#22 kept row: a transport failure is told apart as a timeout"""
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
