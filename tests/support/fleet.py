import json
import re
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.message import Message
from pathlib import Path

from quality import cli
from quality.fleet import ListingFetch, TagReader
from quality.report import CannotJudge
from quality.rules import Rule

TEST_OWNER = "o"
GITHUB_BASE_VARIABLE = "FAKE_GITHUB_BASE"
# git maps https://github.com/ onto the base before it looks anything up; -c, because the anonymous environment sets
# GIT_CONFIG_COUNT itself.
GITHUB_REDIRECT = f'set -- -c "url.${GITHUB_BASE_VARIABLE}.insteadOf=https://github.com/" "$@"'
NEWEST, OLDER = "b" * 40, "a" * 40


RELEASES = {"v0.9.0": OLDER, "v0.10.0": NEWEST}  # the newest by version, not by text


MATRIX_HEADING = "**Repo × rule, newest release v0.10.0**"


def write_fleet(directory: Path, repositories: Sequence[str]) -> Path:
    """A repository whose quality/fleet.toml lists these entries; json.dumps writes a TOML array of basic strings."""
    (directory / "quality").mkdir(parents=True)
    (directory / "quality" / "fleet.toml").write_text(f"repositories = {json.dumps(repositories)}\n")
    return directory


def row(output: str, name: str, table: int = 0) -> list[str]:
    """The cells of repository `name` in the size table (0) or the matrix (1)."""
    lines = [line for line in output.splitlines() if line.startswith(f"| {name} |")]
    return [cell.strip() for cell in re.split(r"(?<!\\)\|", lines[table].strip("|"))]


UNRELEASED = "c" * 40


FIRST_RELEASES = {"v0.2.1": OLDER, "v0.3.1": NEWEST}


FIRST_RELEASE_COMMIT = "f" * 40


LITERAL_TEXT = Rule.LITERAL_TEXT.release


@dataclass(frozen=True)
class Printed:
    code: int
    out: str
    err: str


def ticks(*seconds: float) -> Callable[[], float]:
    """A clock that reads each of `seconds` once on the test's thread, one read too many a StopIteration; the CI-time
    worker's reads see the latest value and use none."""
    readings, owner, latest = iter(seconds), threading.get_ident(), [0.0]

    def read() -> float:
        if threading.get_ident() == owner:
            latest[0] = next(readings)
        return latest[0]

    return read


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
