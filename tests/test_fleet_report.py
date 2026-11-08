"""`quality report`'s streams: the fleet line, a row per repository and the closing line or the pair on stderr, the
tables alone on stdout and in the step summary."""

import json
import urllib.error
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.message import Message
from pathlib import Path

import pytest

from quality import __version__, cli, web
from quality.fleet import ListingFetch, ReleaseTagsError, TagReader
from quality.report import CannotJudge
from tests.support.fleet import NEWEST, RELEASES, row, write_fleet
from tests.support.git_repositories import head_commit


@dataclass(frozen=True)
class Printed:
    code: int
    out: str
    err: str


def ticks(*seconds: float) -> Callable[[], float]:
    """A clock that reads each of `seconds` once; one read too many is a StopIteration."""
    return iter(seconds).__next__


def report_in(
    folder: Path,
    capsys,
    monkeypatch,
    clock: Callable[[], float],
    words: tuple[str, ...] = (),
    tags: TagReader = lambda: RELEASES,
    environment: Mapping[str, str] | None = None,
    fetch_listing: ListingFetch | None = None,
) -> Printed:
    monkeypatch.chdir(folder)
    code = cli.main(
        ["report", *words],
        release_tags=tags,
        fetch_listing=fetch_listing or Listing({}),
        clock=clock,
        now=lambda: datetime(2026, 11, 6, 12, tzinfo=UTC),
        environment=environment or {},
    )
    printed = capsys.readouterr()
    return Printed(code, printed.out, printed.err)


@dataclass(frozen=True)
class Answer:
    """What the fake GitHub says to one organization's listing: a body and a Link header, or a failure of the request."""

    body: bytes = b"[]"
    link: str | None = None
    failure: Exception | None = None


@dataclass
class Listing:
    """A fake listing fetch: one answer per organization, and the url and headers of every request it got."""

    answers: dict[str, Answer]
    requests: list[tuple[str, dict[str, str]]] = field(default_factory=list)

    def __call__(self, url: str, headers: Mapping[str, str], unreadable: Callable[[Exception], CannotJudge]) -> tuple[bytes, Message]:
        self.requests.append((url, dict(headers)))
        answer = self.answers[url.split("/orgs/")[1].split("/")[0]]
        if answer.failure is not None:
            raise unreadable(answer.failure)
        headers_answered = Message()
        if answer.link is not None:
            headers_answered["Link"] = answer.link
        return answer.body, headers_answered


def listed(*entries: tuple[str, ...]) -> Answer:
    """The listing of `entries` as GitHub answers it; each entry is `(full_name, *flags set)`."""
    return Answer(json.dumps([
        {"full_name": name, "private": "private" in set_flags, "archived": "archived" in set_flags, "fork": "fork" in set_flags}
        for name, *set_flags in entries
    ]).encode())


def http_error(status: int) -> Answer:
    return Answer(failure=urllib.error.HTTPError("https://api.github.com", status, "", Message(), None))


SHAPE_NEXT = 'next: write it in quality/fleet.toml like this:\n    organizations = ["overnightworks"]\n'
RERUN_NEXT = "next: fix what the error names, then run quality report again\n"


def write_organizations(
    directory: Path, organizations: list[str], repositories: list[str] | None = None, exclude: list[str] | None = None
) -> Path:
    (directory / "quality").mkdir(parents=True)
    lines = [
        f"organizations = {json.dumps(organizations)}",
        *([f"repositories = {json.dumps(repositories)}"] if repositories else []),
        *([f"exclude = {json.dumps(exclude)}"] if exclude else []),
    ]
    (directory / "quality" / "fleet.toml").write_text("\n".join(lines) + "\n")
    return directory


def gate_caller(ref: str) -> dict[str, str]:
    return {".github/workflows/ci.yml": f"jobs:\n  gate:\n    uses: overnightworks/quality/.github/workflows/gate.yml@{ref}\n"}


def test_a_measured_fleet_prints_its_rows_on_stderr_and_only_the_tables_on_stdout(capsys, monkeypatch, tmp_path, public, remotes):
    """FLEET-1, FLEET-7, FLEET-33, FLEET-34, FLEET-35, FLEET-39, FLEET-40, FLEET-41, FLEET-42, FLEET-45: the rows stand
    on stderr, the two tables alone on stdout, and the heading names the newest release by version."""
    alpha = public("alpha", {"a.py": "a = 1\n"})
    beta = public("beta-repository", {"b.py": "b = 1\n"})
    folder = write_fleet(tmp_path / "fleet", [beta, alpha])
    printed = report_in(folder, capsys, monkeypatch, ticks(5, 6, 9, 9, 14, 77))
    alpha_commit, beta_commit = head_commit(remotes / alpha)[:7], head_commit(remotes / beta)[:7]
    assert (printed.code, printed.err) == (
        0,
        ("  · fleet              2 found        quality/fleet.toml\n"
        f"  ✓ o/alpha            measured       {alpha_commit} · 3s\n"
        f"  ✓ o/beta-repository  measured       {beta_commit} · 5s\n"
        "✓ 2 of 2 repositories measured · 1m12s\n"),
    )
    lines = printed.out.splitlines()
    assert lines[:6] == [
        f"**Fleet report, 2026-11-06, quality {__version__} — functions and CCN: Python only**",
        "",
        ("| Repo | Commit | Languages | Lines prod / test | test:prod | Files ≥ 800 prod / test | Functions ≥ 60 prod / test "
        "| CCN \\> 15 prod / test | Clone pairs prod / test | Note |"),
        "|---" * 10 + "|",
        f"| o/alpha | {alpha_commit} | Python 1 | 1 / 0 | 0.00 | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 |  |",
        f"| o/beta-repository | {beta_commit} | Python 1 | 1 / 0 | 0.00 | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 |  |",
    ]
    assert lines[6:9] == ["", "**Repo × rule, newest release v0.10.0**", ""]
    assert [cells[:3] for cells in (row(printed.out, alpha, 1), row(printed.out, beta, 1))] == [
        ["o/alpha", "no gate", "none"],
        ["o/beta-repository", "no gate", "none"],
    ]
    assert lines[9].startswith("Every gate release enforces ")
    assert lines[11].startswith("| Repo | Gate |")
    assert (len(lines), [line.split(" |")[0] for line in lines[-2:]]) == (15, ["| o/alpha", "| o/beta-repository"])


def test_a_repository_that_cannot_be_cloned_is_its_row_and_the_pair_after_the_tables(capsys, monkeypatch, tmp_path, public, missing, remotes):
    """FLEET-26, FLEET-36, FLEET-46: a repository that cannot be cloned is its row, and the closing pair counts it."""
    alpha = public("alpha", {"a.py": "a = 1\n"})
    printed = report_in(write_fleet(tmp_path / "fleet", [alpha, missing]), capsys, monkeypatch, ticks(0, 1, 2, 3, 4))
    assert (printed.code, printed.err) == (
        2,
        ("  · fleet         2 found        quality/fleet.toml\n"
        f"  ✓ o/alpha       measured       {head_commit(remotes / alpha)[:7]} · 1s\n"
        "  ✗ o/gone        not measured   cannot clone (git exited 128)\n"
        "error: 1 of 2 repositories not measured · the tables say so\n"
        "next: quality report\n"),
    )
    assert row(printed.out, missing)[2] == "cannot judge: cannot clone (git exited 128)"


def test_unread_release_tags_stand_under_the_fleet_line_and_in_the_pair(capsys, monkeypatch, tmp_path, public, remotes):
    """FLEET-26, FLEET-38, FLEET-46: unread release tags stand under the fleet line and in the closing pair."""
    alpha = public("alpha", {"a.py": "a = 1\n"})

    def unreadable() -> dict[str, str]:
        raise ReleaseTagsError("git exited 128")

    printed = report_in(write_fleet(tmp_path / "fleet", [alpha]), capsys, monkeypatch, ticks(0, 1, 2), tags=unreadable)
    assert (printed.code, printed.err) == (
        2,
        ("  · fleet         1 found        quality/fleet.toml\n"
        "  ✗ release tags  not read       git exited 128\n"
        f"  ✓ o/alpha       measured       {head_commit(remotes / alpha)[:7]} · 1s\n"
        "error: the release tags not read · the tables say so\n"
        "next: quality report\n"),
    )


def test_a_cell_that_cannot_read_a_file_makes_the_row_partly_read(capsys, monkeypatch, tmp_path, public):
    """FLEET-26, FLEET-37, FLEET-46: a cell that cannot read a file makes its row partly read."""
    alpha = public("alpha", {**gate_caller(NEWEST), "quality/config.toml": "[gate\n"})
    printed = report_in(write_fleet(tmp_path / "fleet", [alpha]), capsys, monkeypatch, ticks(0, 1, 2))
    assert (printed.code, printed.err) == (
        2,
        ("  · fleet         1 found        quality/fleet.toml\n"
        "  ✗ o/alpha       partly read    cannot read quality/config.toml\n"
        "error: 1 partly read · the tables say so\n"
        "next: quality report\n"),
    )


def test_an_unwritable_step_summary_is_the_pair_after_the_tables(capsys, monkeypatch, tmp_path, public):
    """FLEET-47: an unwritable step summary is the closing pair, after the tables."""
    alpha = public("alpha", {"a.py": "a = 1\n"})
    summary = tmp_path / "a-directory"
    summary.mkdir()
    printed = report_in(
        write_fleet(tmp_path / "fleet", [alpha]), capsys, monkeypatch, ticks(0, 1, 2), environment={"GITHUB_STEP_SUMMARY": str(summary)}
    )
    assert (printed.code, printed.out.splitlines()[0].startswith("**Fleet report, "), "| o/alpha |" in printed.out) == (2, True, True)
    assert printed.err.endswith(
        f"error: GITHUB_STEP_SUMMARY is not writable · [Errno 21] Is a directory: '{summary}'\n"
        "next: fix what the error names, then run quality report again\n"
    )


@pytest.mark.parametrize(
    ("ref", "cell"),
    [
        ("<!--x", "\\<!--x (not a commit)"),
        ("[a](b)", "\\[a\\](b) (not a commit)"),
        ("a\\b", "a\\\\b (not a commit)"),
        ("a|b", "a\\|b (not a commit)"),
        ("*a*_b_", "\\*a\\*\\_b\\_ (not a commit)"),
    ],
    ids=["comment", "link", "backslash", "cell border", "emphasis"],
)
def test_markdown_in_repository_text_stays_text_in_the_tables(capsys, monkeypatch, tmp_path, public, ref, cell):
    """FLEET-43, FLEET-44: repository text stays text in the tables, in stdout and in the step summary."""
    hostile = public("hostile", gate_caller(ref))
    summary = tmp_path / "summary.md"
    printed = report_in(
        write_fleet(tmp_path / "fleet", [hostile]), capsys, monkeypatch, ticks(0, 1, 2, 3), environment={"GITHUB_STEP_SUMMARY": str(summary)}
    )
    assert (row(printed.out, hostile, 1)[1], row(summary.read_text(), hostile, 1)[1]) == (cell, cell)


def test_report_takes_v_and_prints_the_same(capsys, monkeypatch, tmp_path, public):
    """FLEET-48: -v prints what the run prints without it."""
    folder = write_fleet(tmp_path / "fleet", [public("alpha", {"a.py": "a = 1\n"})])
    plain = report_in(folder, capsys, monkeypatch, ticks(0, 1, 2, 3))
    verbose = report_in(folder, capsys, monkeypatch, ticks(0, 1, 2, 3), words=("-v",))
    assert verbose == plain


def test_an_organizations_public_repositories_are_measured_and_nothing_else(capsys, monkeypatch, tmp_path, public):
    """FLEET-32, FLEET-33, FLEET-34: an organization's public, non-archived, non-fork repositories are measured in
    name order, and nothing else."""
    public("alpha", {"a.py": "a = 1\n"})
    public("Beta", {"b.py": "b = 1\n"})
    listing = Listing({"o": listed(("o/Beta",), ("o/alpha",), ("o/old", "archived"), ("o/copy", "fork"))})
    printed = report_in(
        write_organizations(tmp_path / "fleet", ["o"]), capsys, monkeypatch, ticks(0, 1, 2, 3, 4, 5), fetch_listing=listing
    )
    assert printed.code == 0, printed.err
    assert printed.err.splitlines()[0].split() == ["·", "fleet", "2", "found", "o", "·", "quality/fleet.toml"]
    assert [line.split()[1] for line in printed.err.splitlines()[1:3]] == ["o/alpha", "o/Beta"]
    assert "o/old" not in printed.err + printed.out
    assert "o/copy" not in printed.err + printed.out


def test_a_private_entry_of_the_listing_is_in_no_line(capsys, monkeypatch, tmp_path, public):
    """FLEET-32, FLEET-72: a private entry of the listing is measured nowhere and named in no line, the step summary
    included."""
    public("alpha", {"a.py": "a = 1\n"})
    summary = tmp_path / "summary.md"
    listing = Listing({"o": listed(("o/alpha",), ("o/hidden-name", "private"))})
    printed = report_in(
        write_organizations(tmp_path / "fleet", ["o"]), capsys, monkeypatch, ticks(0, 1, 2, 3),
        environment={"GITHUB_STEP_SUMMARY": str(summary)}, fetch_listing=listing,
    )
    assert printed.code == 0, printed.err
    assert "hidden-name" not in printed.err + printed.out + summary.read_text()


def test_a_private_repository_named_in_the_file_never_reads_as_found(capsys, monkeypatch, tmp_path, public, missing):
    """FLEET-67: a private repository the listing names does not make a `repositories` entry already listed."""
    listing = Listing({"o": listed(("o/gone", "private"))})
    printed = report_in(
        write_organizations(tmp_path / "fleet", ["o"], [missing]), capsys, monkeypatch, ticks(0, 1, 2, 3), fetch_listing=listing
    )
    assert printed.code == 2
    assert "already in" not in printed.err


@pytest.mark.parametrize(
    ("environment", "authorization"),
    [({"GITHUB_TOKEN": "dummy-token"}, "Bearer dummy-token"), ({}, None), ({"GITHUB_TOKEN": ""}, None)],
    ids=["token", "no token", "empty token"],
)
def test_the_listing_carries_the_job_token_only_when_there_is_one(capsys, monkeypatch, tmp_path, public, environment, authorization):
    """FLEET-50, FLEET-60: the listing is asked once and carries the job token as a bearer header only when there is
    one, and no output holds it."""
    public("alpha", {"a.py": "a = 1\n"})
    listing = Listing({"o": listed(("o/alpha",))})
    printed = report_in(
        write_organizations(tmp_path / "fleet", ["o"]), capsys, monkeypatch, ticks(0, 1, 2, 3),
        environment=environment, fetch_listing=listing,
    )
    ((url, headers),) = listing.requests
    assert (url, headers.get("Authorization")) == ("https://api.github.com/orgs/o/repos?type=public&per_page=100", authorization)
    assert "dummy-token" not in printed.err + printed.out


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


def test_a_fork_named_in_the_file_is_measured(capsys, monkeypatch, tmp_path, public):
    """FLEET-30: a fork named in the file is measured."""
    public("alpha", {"a.py": "a = 1\n"})
    public("copy", {"c.py": "c = 1\n"})
    listing = Listing({"o": listed(("o/alpha",), ("o/copy", "fork"))})
    printed = report_in(
        write_organizations(tmp_path / "fleet", ["o"], ["o/copy"]), capsys, monkeypatch, ticks(0, 1, 2, 3, 4, 5), fetch_listing=listing
    )
    assert printed.code == 0, printed.err
    assert [cells[0] for cells in (row(printed.out, "o/alpha", 1), row(printed.out, "o/copy", 1))] == ["o/alpha", "o/copy"]


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


def test_an_excluded_repository_is_left_out_and_the_fleet_line_counts_the_rest(capsys, monkeypatch, tmp_path, public):
    """FLEET-31, FLEET-33, FLEET-45: an excluded repository is left out, the fleet line counts the rest, and a fleet of
    one closes on `1 of 1 repository`."""
    public("alpha", {"a.py": "a = 1\n"})
    listing = Listing({"o": listed(("o/alpha",), ("o/Beta",))})
    printed = report_in(
        write_organizations(tmp_path / "fleet", ["o"], exclude=["o/beta"]), capsys, monkeypatch, ticks(0, 1, 2, 3), fetch_listing=listing
    )
    assert printed.code == 0, printed.err
    assert "  · fleet         1 found        o · quality/fleet.toml\n" in printed.err
    assert "Beta" not in printed.err + printed.out
    assert printed.err.endswith("✓ 1 of 1 repository measured · 3s\n")


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
