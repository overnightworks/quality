"""`quality report`'s streams: the fleet line, a row per repository and the closing line or the pair on stderr, the
tables alone on stdout and in the step summary."""

import json
import subprocess
import urllib.error
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.message import Message
from pathlib import Path

import pytest

from quality import __version__, cli, fleet_ci_time, web
from quality.fleet import ListingFetch, ReleaseTagsError, TagReader
from quality.report import CannotJudge
from tests.support.clones import function
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


JOB = {"name": "tests", "conclusion": "success", "created_at": "2026-11-05T10:00:00Z", "started_at": "2026-11-05T10:00:07Z", "completed_at": "2026-11-05T10:01:00Z"}
NO_RUNS = b'{"total_count": 0, "workflow_runs": []}'


def runs_of(*runs: tuple[str, str, str, str], attempt: int = 1) -> Answer:
    """GitHub's answer for the runs listing; each run is `(head_sha, created_at, updated_at, conclusion)` of a pull request at `run_attempt`."""
    entries = [
        {"id": number, "head_branch": "feature", "head_sha": sha, "event": "pull_request", "status": "completed", "conclusion": conclusion, "run_attempt": attempt, "created_at": created, "updated_at": updated}
        for number, (sha, created, updated, conclusion) in enumerate(runs, 1)
    ]
    return Answer(json.dumps({"total_count": len(entries), "workflow_runs": entries}).encode())


@dataclass
class Listing:
    """A fake listing fetch: one answer per organization, and the url and headers of every request it got."""

    answers: dict[str, Answer]
    runs: dict[str, Answer] = field(default_factory=dict)
    requests: list[tuple[str, dict[str, str]]] = field(default_factory=list)
    runs_requests: list[tuple[str, dict[str, str]]] = field(default_factory=list)

    def __call__(self, url: str, headers: Mapping[str, str], unreadable: Callable[[Exception], CannotJudge]) -> tuple[bytes, Message]:
        if "/jobs?" in url:
            return json.dumps({"total_count": 1, "jobs": [JOB]}).encode(), Message()
        if "/repos/" in url:
            self.runs_requests.append((url, dict(headers)))
            return self._runs_answer(url, unreadable)
        self.requests.append((url, dict(headers)))
        answer = self.answers[url.split("/orgs/")[1].split("/")[0]]
        if answer.failure is not None:
            raise unreadable(answer.failure)
        headers_answered = Message()
        if answer.link is not None:
            headers_answered["Link"] = answer.link
        return answer.body, headers_answered

    def _runs_answer(self, url: str, unreadable: Callable[[Exception], CannotJudge]) -> tuple[bytes, Message]:
        answer = self.runs.get(url.split("/repos/")[1].split("/actions")[0], Answer(NO_RUNS))
        if answer.failure is not None:
            raise unreadable(answer.failure)
        return answer.body, Message()


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
    on stderr, the tables alone on stdout, and the heading names the newest release by version."""
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
        f"**Fleet report, 2026-11-06, quality {__version__}**",
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
    assert lines[16].startswith("**CI time, last 7 days")
    assert lines[23:] == ["**Clone pairs between repositories · 0 pairs**", "", "| Repo | Function | Repo | Function | Kind |", "|---|---|---|---|---|"]


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


def test_a_repository_that_holds_no_commit_is_a_row_that_says_so(capsys, monkeypatch, tmp_path, public, remotes):
    """FLEET-36, FLEET-103: a served repository without a commit is not measured, and its row says it holds none."""
    alpha = public("alpha", {"a.py": "a = 1\n"})
    empty = remotes / "o" / "empty"
    empty.mkdir(parents=True)
    subprocess.run(["git", "init", "--quiet", str(empty)], check=True)
    printed = report_in(write_fleet(tmp_path / "fleet", [alpha, "o/empty"]), capsys, monkeypatch, ticks(0, 1, 2, 3, 4))
    assert (printed.code, printed.err.splitlines()[2:4]) == (
        2,
        ["  ✗ o/empty       not measured   holds no commit", "error: 1 of 2 repositories not measured · the tables say so"],
    )
    assert row(printed.out, "o/empty")[2] == "cannot judge: holds no commit"


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
    """FLEET-32, FLEET-33, FLEET-34, FLEET-92: an organization's public, non-archived, non-fork repositories are
    measured in name order, named as GitHub names them, and nothing else."""
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
    """FLEET-36, FLEET-67, FLEET-94: a private repository the listing names does not make a `repositories` entry
    already listed; the entry is measured and, as nobody serves it, is a row that cannot be cloned."""
    listing = Listing({"o": listed(("o/gone", "private"))})
    printed = report_in(
        write_organizations(tmp_path / "fleet", ["o"], [missing]), capsys, monkeypatch, ticks(0, 1, 2, 3), fetch_listing=listing
    )
    assert printed.code == 2
    assert [line.split() for line in printed.err.splitlines() if "o/gone" in line] == [
        ["✗", "o/gone", "not", "measured", "cannot", "clone", "(git", "exited", "128)"]
    ]


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
    ((_, runs_headers),) = listing.runs_requests
    assert runs_headers.get("Authorization") == authorization


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


def test_the_weekly_ci_time_is_a_third_table_and_leaves_the_exit_as_it_is(capsys, monkeypatch, tmp_path, public):
    """FLEET-40, FLEET-104: each repository's commits, median, p90 and red share of the last 7 days stand in a third table
    after the two others, in the step summary too, and a red week raises no finding."""
    public("alpha", {"a.py": "a = 1\n"})
    public("beta", {"b.py": "b = 1\n"})
    slow = [("x", "2026-11-05T10:00:00Z", "2026-11-05T10:03:00Z", "failure"), ("y", "2026-11-05T11:00:00Z", "2026-11-05T11:01:00Z", "success")]
    listing = Listing({"o": listed(("o/alpha",), ("o/beta",))}, runs={"o/alpha": runs_of(*slow)})
    summary = tmp_path / "summary.md"
    printed = report_in(
        write_organizations(tmp_path / "fleet", ["o"]), capsys, monkeypatch, ticks(*range(8)),
        environment={"GITHUB_STEP_SUMMARY": str(summary)}, fetch_listing=listing,
    )
    assert printed.code == 0, printed.err
    third = printed.out.split("**CI time, last 7 days")[1].splitlines()
    assert third[0] == " · 4 API calls**"
    assert third[2:6] == [
        "| Repo | Commits | Median | p90 | Red | Red on main | Re-runs | Green on re-run | Jobs sampled | Queue p90 | Over 12 min |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
        "| o/alpha | 2 | 1m00s | 3m00s | 50 % (1 of 2) | — | 0 | 0 | 2 | 7s | none |",
        "| o/beta | 0 | — | — | — | — | — | — | — | — | — |",
    ]
    assert "| o/alpha | 2 | 1m00s |" in summary.read_text()
    assert "created=%3E%3D2026-10-30" in listing.runs_requests[0][0]


def test_re_runs_and_those_green_on_re_run_print_in_the_ci_time_table_and_leave_the_exit_as_it_is(capsys, monkeypatch, tmp_path, public):
    """FLEET-112: a run re-run to a green latest attempt counts in both cells, and no finding or exit follows."""
    public("alpha", {"a.py": "a = 1\n"})
    listing = Listing({"o": listed(("o/alpha",))}, runs={"o/alpha": runs_of(("x", "2026-11-05T10:00:00Z", "2026-11-05T10:01:00Z", "success"), attempt=2)})
    printed = report_in(write_organizations(tmp_path / "fleet", ["o"]), capsys, monkeypatch, ticks(*range(8)), fetch_listing=listing)
    assert printed.code == 0, printed.err
    assert "| o/alpha | 1 | 1m00s | 1m00s | 0 % (0 of 1) | — | 1 | 1 | 1 | 7s | none |" in printed.out


def test_a_repository_whose_runs_cannot_be_read_is_a_partly_read_row_and_the_exit_stays(capsys, monkeypatch, tmp_path, public):
    """FLEET-37, FLEET-105: unreadable runs are a stderr row and a partly read table row, the other repository keeps its
    numbers, and the closing line and exit are those of a report without the table."""
    public("alpha", {"a.py": "a = 1\n"})
    public("beta", {"b.py": "b = 1\n"})
    listing = Listing(
        {"o": listed(("o/alpha",), ("o/beta",))},
        runs={"o/alpha": Answer(failure=OSError("down")), "o/beta": runs_of(("x", "2026-11-05T10:00:00Z", "2026-11-05T10:00:30Z", "success"))},
    )
    printed = report_in(write_organizations(tmp_path / "fleet", ["o"]), capsys, monkeypatch, ticks(*range(8)), fetch_listing=listing)
    assert printed.code == 0, printed.err
    assert "  ✗ o/alpha  " in printed.err
    assert "partly read    cannot read CI time" in printed.err
    assert printed.err.splitlines()[-1].startswith("✓ 2 of 2 repositories measured")
    assert "| o/alpha | partly read · cannot read CI time | — | — | — | — | — | — | — | — | — |" in printed.out
    assert "| o/beta | 1 | 30s | 30s | 0 % (0 of 1) | — | 0 | 0 | 1 | 7s | none |" in printed.out


def slow_week() -> Answer:
    return runs_of(("x", "2026-11-05T10:00:00Z", "2026-11-05T10:11:00Z", "success"))


def fleet_of_two(tmp_path, public):
    """A fleet of a repository with a week of 11-minute CI and one without runs, and the listing that serves it."""
    public("alpha", {"a.py": "a = 1\n"})
    public("beta", {"b.py": "b = 1\n"})
    listing = Listing({"o": listed(("o/alpha",), ("o/beta",))}, runs={"o/alpha": slow_week()})
    return write_organizations(tmp_path / "fleet", ["o"]), listing


def test_the_switch_writes_the_weekly_document_and_leaves_stdout_and_stderr_as_they_are(capsys, monkeypatch, tmp_path, public):
    """FLEET-106, FLEET-107, FLEET-40: `--ci-time-json` creates the missing folder, writes the document and prints nothing
    of its own; without it the same report writes no file."""
    folder, listing = fleet_of_two(tmp_path, public)
    without = report_in(folder, capsys, monkeypatch, ticks(*range(8)), fetch_listing=listing)
    assert sorted(path.name for path in folder.iterdir()) == ["quality"]
    with_switch = report_in(folder, capsys, monkeypatch, ticks(*range(8)), words=("--ci-time-json",), fetch_listing=listing)
    assert with_switch == without
    written = json.loads((folder / fleet_ci_time.DOCUMENT_PATH).read_text(encoding="utf-8"))
    assert (written["generated_at"], written["window_start"], written["complete"]) == ("2026-11-06T12:00:00Z", "2026-10-30T00:00:00Z", True)
    assert written["repositories"] == [
        {"name": "o/alpha", "median_seconds": 660, "commits_sampled": 1, "measured": True, "breach": True},
        {"name": "o/beta", "median_seconds": 0, "commits_sampled": 0, "measured": False, "breach": False},
    ]


@pytest.mark.parametrize("blocker", ["the folder is a file", "the file is a directory"])
def test_a_document_that_cannot_be_written_is_the_pair_after_the_tables(capsys, monkeypatch, tmp_path, public, blocker):
    """FLEET-109: a folder or a file in the way of the document is `error: <path> is not writable · <reason>` with the
    rerun step, exit 2, after the tables."""
    folder, listing = fleet_of_two(tmp_path, public)
    if blocker == "the folder is a file":
        (folder / fleet_ci_time.DOCUMENT_PATH.parent).write_text("")
    else:
        (folder / fleet_ci_time.DOCUMENT_PATH).mkdir(parents=True)
    printed = report_in(folder, capsys, monkeypatch, ticks(*range(8)), words=("--ci-time-json",), fetch_listing=listing)
    assert printed.code == 2
    assert "| o/alpha |" in printed.out
    assert printed.err.endswith(RERUN_NEXT)
    assert printed.err.splitlines()[-2].startswith(f"error: {fleet_ci_time.DOCUMENT_PATH} is not writable · [Errno ")


def test_an_unwritable_step_summary_leaves_no_document(capsys, monkeypatch, tmp_path, public):
    """FLEET-47, FLEET-110: an unwritable step summary ends the report before the document is written."""
    folder, listing = fleet_of_two(tmp_path, public)
    summary = tmp_path / "a-directory"
    summary.mkdir()
    printed = report_in(
        folder, capsys, monkeypatch, ticks(*range(8)), words=("--ci-time-json",),
        environment={"GITHUB_STEP_SUMMARY": str(summary)}, fetch_listing=listing,
    )
    assert printed.code == 2
    assert not (folder / fleet_ci_time.DOCUMENT_PATH).exists()


def test_a_red_report_still_writes_the_document_and_a_repository_not_measured_by_size_keeps_its_ci_time(capsys, monkeypatch, tmp_path, public, missing):
    """FLEET-110: a repository that cannot be cloned makes the report exit 2 yet the document is written, its row
    following its CI-time reading."""
    alpha = public("alpha", {"a.py": "a = 1\n"})
    listing = Listing({}, runs={missing: slow_week()})
    folder = write_fleet(tmp_path / "fleet", [alpha, missing])
    printed = report_in(folder, capsys, monkeypatch, ticks(*range(8)), words=("--ci-time-json",), fetch_listing=listing)
    assert printed.code == 2
    rows = json.loads((folder / fleet_ci_time.DOCUMENT_PATH).read_text(encoding="utf-8"))["repositories"]
    assert [(entry["name"], entry["measured"], entry["breach"]) for entry in rows] == [(alpha, False, False), (missing, True, True)]


def test_a_private_repository_is_in_no_row_of_the_document(capsys, monkeypatch, tmp_path, public):
    """FLEET-110: the public document names no private repository."""
    public("alpha", {"a.py": "a = 1\n"})
    listing = Listing({"o": listed(("o/alpha",), ("o/hidden-name", "private"))})
    folder = write_organizations(tmp_path / "fleet", ["o"])
    printed = report_in(folder, capsys, monkeypatch, ticks(*range(8)), words=("--ci-time-json",), fetch_listing=listing)
    assert printed.code == 0, printed.err
    assert [entry["name"] for entry in json.loads((folder / fleet_ci_time.DOCUMENT_PATH).read_text(encoding="utf-8"))["repositories"]] == ["o/alpha"]


def clone_pair_table(printed: Printed) -> list[str]:
    """The lines of the table under `Clone pairs between repositories`, from the rest of its heading."""
    return printed.out.split("**Clone pairs between repositories")[1].splitlines()


LOOPING = (
    "def moved(items):\n    for item in items:\n        if item:\n            items.append(item)\n        else:\n"
    "            items.remove(item)\n    while items:\n        items.pop()\n        items.sort()\n"
    "    return [entry for entry in items if entry]\n"
)
COPIED = {
    "same path": ("src/shared.py", "src/shared.py", "prod"),
    "tests": ("tests/test_a.py", "tests/test_b.py", "test"),
    "test copying production": ("src/shared.py", "tests/test_b.py", "prod"),
}


@pytest.mark.parametrize(("first", "second", "kind"), COPIED.values(), ids=COPIED.keys())
def test_a_function_copied_between_two_repositories_is_one_row_naming_both_with_its_kind(
    capsys, monkeypatch, tmp_path, public, first, second, kind
):
    """FLEET-113, FLEET-115, FLEET-117: one row names both repositories and paths, sides sorted; `Kind` is test only when both sides are tests."""
    alpha = public("alpha", {first: function("copied", "value", "local")})
    beta = public("beta", {second: function("copied", "other", "kept")})
    printed = report_in(write_fleet(tmp_path / "fleet", [beta, alpha]), capsys, monkeypatch, ticks(*range(8)))
    assert printed.code == 0, printed.err
    assert clone_pair_table(printed) == [
        " · 1 pair**",
        "",
        "| Repo | Function | Repo | Function | Kind |",
        "|---|---|---|---|---|",
        f"| {alpha} | {first.replace('_', chr(92) + '_')} copied | {beta} | {second.replace('_', chr(92) + '_')} copied | {kind} |",
    ]


def test_production_pairs_stand_before_test_pairs(capsys, monkeypatch, tmp_path, public):
    """FLEET-115: `prod` rows first, then by sides."""
    files = {"tests/test_a.py": function("copied", "value", "local"), "src/b.py": LOOPING}
    alpha, beta = public("alpha", files), public("beta", files)
    printed = report_in(write_fleet(tmp_path / "fleet", [alpha, beta]), capsys, monkeypatch, ticks(*range(8)))
    assert [line.rsplit(" | ", 1)[1] for line in clone_pair_table(printed)[4:]] == ["prod |", "test |"]


def test_a_copy_inside_one_repository_stays_out_of_the_table_and_in_its_own_cell(capsys, monkeypatch, tmp_path, public):
    """FLEET-114: the pair of one repository counts in its `Clone pairs prod / test` cell only."""
    alpha = public("alpha", {"a.py": function("copied", "value", "local"), "b.py": function("copied", "value", "local")})
    printed = report_in(write_fleet(tmp_path / "fleet", [alpha]), capsys, monkeypatch, ticks(*range(8)))
    assert row(printed.out, alpha)[-2] == "1 / 0"
    assert clone_pair_table(printed)[0] == " · 0 pairs**"
    assert len(clone_pair_table(printed)) == 4


def test_a_repository_without_the_copy_or_not_measured_leaves_no_row(capsys, monkeypatch, tmp_path, public, missing):
    """FLEET-116: a drifted copy and a repository that cannot be cloned add no pair; the heading reads `0 pairs`."""
    alpha = public("alpha", {"a.py": function("copied", "value", "local")})
    beta = public("beta", {"a.py": function("copied", "value", "local", assignments=1, passes=20)})
    printed = report_in(write_fleet(tmp_path / "fleet", [alpha, beta, missing]), capsys, monkeypatch, ticks(*range(10)))
    assert printed.code == 2
    assert clone_pair_table(printed) == [" · 0 pairs**", "", "| Repo | Function | Repo | Function | Kind |", "|---|---|---|---|---|"]


def test_markdown_in_a_path_of_a_pair_stays_text(capsys, monkeypatch, tmp_path, public):
    """FLEET-44: a path with markdown syntax is escaped in the pair table."""
    alpha = public("alpha", {"<!--x.py": function("copied", "value", "local")})
    beta = public("beta", {"<!--x.py": function("copied", "value", "local")})
    printed = report_in(write_fleet(tmp_path / "fleet", [alpha, beta]), capsys, monkeypatch, ticks(*range(8)))
    assert clone_pair_table(printed)[4].startswith(f"| {alpha} | \\<!--x.py copied | {beta} |")
