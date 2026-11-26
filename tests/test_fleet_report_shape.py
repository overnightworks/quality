"""`quality report`'s streams: the fleet line, a row per repository and the closing line or the pair on stderr, the
tables alone on stdout and in the step summary."""

import urllib.error
from email.message import Message

import pytest

from quality import web
from tests.support.fleet import (
    RERUN_NEXT,
    Answer,
    Listing,
    listed,
    report_in,
    ticks,
    write_organizations,
)


def http_error(status: int) -> Answer:
    return Answer(failure=urllib.error.HTTPError("https://api.github.com", status, "", Message(), None))


SHAPE_NEXT = 'next: write it in quality/fleet.toml like this:\n    organizations = ["overnightworks"]\n'


@pytest.mark.parametrize(
    ("answer", "fact", "steps"),
    [
        (http_error(404), "quality/fleet.toml organizations[0] is no GitHub organization", SHAPE_NEXT),
        (http_error(503), "GitHub did not list o · HTTP 503", RERUN_NEXT),
        (Answer(link='<https://api.github.com/next>; rel="next"'), "o has more than 100 public repositories · quality reads one page", RERUN_NEXT),
        (Answer(failure=TimeoutError()), f"GitHub did not answer the listing of o within {web.REQUEST_TIMEOUT_SECONDS}s", RERUN_NEXT),
        (Answer(failure=urllib.error.URLError(ConnectionRefusedError("secret-host"))), "GitHub could not be reached for the listing of o", RERUN_NEXT),
        (Answer(body=b"not json"), "GitHub's listing of o is not a list of repositories", RERUN_NEXT),
        (Answer(body=b"[" * 100_000 + b"]" * 100_000), "GitHub's listing of o is not a list of repositories", RERUN_NEXT),
        (Answer(body=b'{"full_name": "o/a"}'), "GitHub's listing of o is not a list of repositories", RERUN_NEXT),
        (listed(("--upload-pack=x/y",)), "GitHub's listing of o is not a list of repositories", RERUN_NEXT),
        (listed(("o/..",)), "GitHub's listing of o is not a list of repositories", RERUN_NEXT),
        (Answer(body=b'[{"full_name": "o/a", "private": "no", "archived": false, "fork": false}]'),
         "GitHub's listing of o is not a list of repositories", RERUN_NEXT),
    ],
    ids=["404", "503", "more pages", "timeout", "unreachable", "not JSON", "deeply nested", "not a list", "option", "dots", "flag not boolean"],
)
def test_a_listing_that_cannot_be_used_is_exit_2_before_any_clone(capsys, monkeypatch, tmp_path, public, answer, fact, steps):
    """FLEET-60, FLEET-61, FLEET-62, FLEET-63, FLEET-64, FLEET-65, FLEET-70, FLEET-71: a listing that cannot be used is
    one pair with nothing measured, the rerun under `next:` unless the file is at fault, and no text of the answer in
    it."""
    printed = report_in(
        write_organizations(tmp_path / "fleet", ["o"]), capsys, monkeypatch, ticks(0), fetch_listing=Listing({"o": answer})
    )
    assert (printed.code, printed.out) == (2, "")
    assert printed.err == f"error: {fact} · nothing measured\n{steps}"
    assert not any(word in printed.err for word in ("upload-pack", "secret-host", "fleet  "))


def test_every_organization_problem_stands_in_one_pair_with_the_shape_before_the_rerun(capsys, monkeypatch, tmp_path, public):
    """FLEET-61, FLEET-62, FLEET-63, FLEET-70: the problems of two organizations stand in one pair, the shape before the
    rerun."""
    listing = Listing({"a": http_error(404), "b": http_error(503)})
    printed = report_in(write_organizations(tmp_path / "fleet", ["a", "b"]), capsys, monkeypatch, ticks(0), fetch_listing=listing)
    assert (printed.code, printed.out, printed.err) == (
        2,
        "",
        ("error: quality/fleet.toml organizations[0] is no GitHub organization · GitHub did not list b · HTTP 503"
        " · nothing measured\n"
        "next:\n"
        "    write it in quality/fleet.toml like this:\n"
        '        organizations = ["overnightworks"]\n'
        "    fix what the error names, then run quality report again\n"),
    )


def test_an_organization_name_that_is_no_name_sends_no_request(capsys, monkeypatch, tmp_path, public):
    """FLEET-56, FLEET-57, FLEET-59: an organization name that is no name is named by its place and sends no request."""
    listing = Listing({})
    printed = report_in(write_organizations(tmp_path / "fleet", ["a/b"]), capsys, monkeypatch, ticks(0), fetch_listing=listing)
    assert (printed.code, listing.requests) == (2, [])
    assert printed.err == (
        "error: quality/fleet.toml organizations[0] is no organization name\n"
        "next: write it in quality/fleet.toml like this:\n"
        '    organizations = ["overnightworks"]\n'
    )


def test_a_named_repository_that_the_organization_already_lists_is_refused_by_its_place(capsys, monkeypatch, tmp_path, public):
    """FLEET-67: a named repository the organization already lists, in any case, is refused by its place."""
    listing = Listing({"o": listed(("o/alpha",))})
    printed = report_in(write_organizations(tmp_path / "fleet", ["o"], ["O/Alpha"]), capsys, monkeypatch, ticks(0), fetch_listing=listing)
    assert (printed.code, printed.out) == (2, "")
    assert printed.err == f"error: quality/fleet.toml repositories[0] is already in o · nothing measured\n{SHAPE_NEXT}"


@pytest.mark.parametrize(
    ("answer", "exclude"),
    [(listed(("o/old", "archived")), []), (listed(("o/alpha",)), ["o/alpha"])],
    ids=["all archived", "all excluded"],
)
def test_organizations_that_select_nothing_are_refused(capsys, monkeypatch, tmp_path, public, answer, exclude):
    """FLEET-66: organizations whose listing leaves nothing to measure are refused after the listing."""
    listing = Listing({"o": answer})
    printed = report_in(write_organizations(tmp_path / "fleet", ["o"], exclude=exclude), capsys, monkeypatch, ticks(0), fetch_listing=listing)
    assert (printed.code, printed.out) == (2, "")
    assert printed.err == f"error: quality/fleet.toml selects no repository · nothing measured\n{SHAPE_NEXT}"


@pytest.mark.parametrize(
    "answers",
    [
        {"o": listed(("o/alpha",)), "p": listed(("p/other",))},
        {"o": listed(("o/alpha",)), "p": listed(("p/hidden-name", "private"))},
        {"o": listed(("o/alpha",)), "p": listed(("p/hidden-name", "archived"))},
        {"o": listed(("o/alpha",)), "p": listed(("p/hidden-name", "fork"))},
        {"o": listed(("o/alpha",)), "p": listed(("o/other",))},
    ],
    ids=["not listed", "private", "archived", "fork", "another owner"],
)
def test_an_exclude_entry_that_matches_no_found_repository_is_one_pair_whatever_github_holds(capsys, monkeypatch, tmp_path, public, answers):
    """FLEET-68: an `exclude` entry that matches no found repository is one pair, whether GitHub does not list it or it
    is private, archived or a fork."""
    organizations = ["o", "p"] if "p" in answers else ["o"]
    printed = report_in(
        write_organizations(tmp_path / "fleet", organizations, exclude=["p/hidden-name"]), capsys, monkeypatch, ticks(0),
        fetch_listing=Listing(answers),
    )
    assert (printed.code, printed.out) == (2, "")
    assert printed.err == f"error: quality/fleet.toml exclude[0] matches no repository found · nothing measured\n{SHAPE_NEXT}"


@pytest.mark.parametrize(
    ("repositories", "exclude", "fact"),
    [(["o/a", "o/b"], ["O/B"], "exclude[0] repeats repositories[1]"), (None, ["o/b", "O/B"], "exclude[1] repeats exclude[0]")],
    ids=["in repositories too", "twice in exclude"],
)
def test_an_exclude_entry_that_repeats_is_refused_by_its_place_before_any_request(capsys, monkeypatch, tmp_path, public, repositories, exclude, fact):
    """FLEET-54, FLEET-57, FLEET-59: an entry that repeats another, in any case or across the two lists, is refused by
    its place before any request."""
    listing = Listing({})
    printed = report_in(
        write_organizations(tmp_path / "fleet", ["o"], repositories, exclude), capsys, monkeypatch, ticks(0), fetch_listing=listing
    )
    assert (printed.code, printed.out, listing.requests) == (2, "", [])
    assert printed.err == f"error: quality/fleet.toml {fact}\nnext: write it in quality/fleet.toml like this:\n    organizations = [\"overnightworks\"]\n"


def test_an_exclude_entry_of_an_organization_github_did_not_list_waits_for_the_rerun(capsys, monkeypatch, tmp_path, public):
    """FLEET-69, FLEET-70: an `exclude` entry of an organization whose listing failed waits for the rerun, which the
    pair adds after the shape."""
    listing = Listing({"a": listed(("a/x",)), "b": http_error(503)})
    printed = report_in(
        write_organizations(tmp_path / "fleet", ["a", "b"], exclude=["b/y", "a/z"]), capsys, monkeypatch, ticks(0), fetch_listing=listing
    )
    assert (printed.code, printed.out) == (2, "")
    assert printed.err == (
        "error: GitHub did not list b · HTTP 503 · quality/fleet.toml exclude[1] matches no repository found · nothing measured\n"
        "next:\n"
        "    write it in quality/fleet.toml like this:\n"
        '        organizations = ["overnightworks"]\n'
        "    fix what the error names, then run quality report again\n"
    )
