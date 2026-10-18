"""`quality scoreboard`: every fleet repository's Python size in one markdown table.

Lines and the production/test split are the `size` rule's own (`ratchet.summarise`), measured on a shallow clone of
the default branch tip. git runs anonymously: only an http, https or file URL without credentials, no credential
helper, no askpass, no terminal prompt, no system, global or environment config and an empty HOME, from a temporary
directory, so no checkout's own config reaches it either. A repository declared private is never cloned: the table
is published in a public run summary.
"""

import subprocess
from dataclasses import dataclass
from datetime import date
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

from quality import __version__, engines, hygiene, ratchet

LANGUAGES = "Python only"
CLONE_TIMEOUT_SECONDS = 300
LS_REMOTE_TIMEOUT_SECONDS = 60
SHORT_COMMIT = 7
NO_COMMIT = "—"
UNNAMED = "unnamed URL"
ANONYMOUS_GIT = ("git", "-c", "credential.helper=", "-c", "core.askPass=")
# ssh authenticates with the user's keys and agent whatever HOME says, so only these transports are anonymous.
ANONYMOUS_SCHEMES = frozenset({"file", "http", "https"})
COLUMNS = (
    "Repo",
    "Commit",
    "Lines prod / test",
    "test:prod",
    f"Files ≥ {ratchet.FILE_LINES_FROM} prod / test",
    f"Functions ≥ {ratchet.FUNCTION_LINES_FROM} prod / test",
    f"CCN > {ratchet.CCN_ABOVE} prod / test",
    "Note",
)
DEBT_COLUMNS = (ratchet.Metric.FILE_LINES, ratchet.Metric.FUNCTION_LINES, ratchet.Metric.CCN)


@dataclass(frozen=True)
class Measured:
    name: str
    commit: str
    areas: dict[ratchet.Area, ratchet.AreaSummary]
    unparsable: int


@dataclass(frozen=True)
class Private:
    """Declared private and not readable anonymously: nothing about it is published."""

    name: str


@dataclass(frozen=True)
class Unjudged:
    name: str
    reason: str


Row = Measured | Private | Unjudged


def measure_fleet(public: list[str], private: list[str]) -> list[Row]:
    """One row per URL, public ones first, each in the order given; one row that fails never stops the others."""
    return [*(measure_public(url) for url in public), *(judge_private(url) for url in private)]


def anonymous_environment(scratch: Path) -> dict[str, str]:
    """No configuration git could authenticate from: the engines' isolation drops every GIT_* variable and the system
    and global config, and points HOME at an empty directory, so no ~/.netrc either."""
    isolated = engines.attribute_free_git_environment(scratch / "home")
    # git falls back to SSH_ASKPASS for an http password too.
    return {**{name: value for name, value in isolated.items() if name != "SSH_ASKPASS"}, "GIT_TERMINAL_PROMPT": "0"}


class Refused(Exception):
    """Why git must not be pointed at a URL."""


def require_anonymous(url: str) -> None:
    """Refuses a URL git could read as an option or authenticate with; any other is anonymous."""
    if url.startswith("-"):
        raise Refused("reads as a git option")
    try:
        parts = urlsplit(url)
    except ValueError:
        raise Refused("not a valid URL") from None
    if parts.scheme not in ANONYMOUS_SCHEMES:
        raise Refused("not an http, https or file URL")
    if parts.username is not None or parts.password is not None:
        raise Refused("the URL carries credentials")


def cannot_judge(rows: list[Row]) -> bool:
    return any(isinstance(row, Unjudged) for row in rows)


def repository_name(url: str) -> str:
    """The URL's last path segment, else its host: never the URL itself, which may carry credentials."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return UNNAMED
    return PurePosixPath(parts.path).name.removesuffix(".git") or parts.hostname or UNNAMED


def measure_public(url: str) -> Row:
    name = repository_name(url)
    try:
        require_anonymous(url)
    except Refused as refused:
        return Unjudged(name, f"cannot clone ({refused})")
    with engines.scratch_directory() as scratch:
        environment = anonymous_environment(scratch)
        clone = scratch / "clone"
        try:
            cloned = git(["clone", "--depth", "1", "--no-tags", "--quiet", "--", url, str(clone)], scratch, environment,
                         CLONE_TIMEOUT_SECONDS)
            # git's own words name the local clone directory, so only its exit code is reported.
            if cloned.returncode != 0:
                return Unjudged(name, f"cannot clone (git exited {cloned.returncode})")
            head = git(["-C", str(clone), "rev-parse", "HEAD"], scratch, environment, CLONE_TIMEOUT_SECONDS)
        except engines.EngineError as error:
            return Unjudged(name, f"cannot clone ({error})")
        if head.returncode != 0:
            return Unjudged(name, f"cannot read the cloned commit (git exited {head.returncode})")
        try:
            tree = hygiene.measure_size(clone, hygiene.tracked_files(clone))
        except hygiene.CannotJudge as error:
            return Unjudged(name, f"cannot measure ({str(error).replace(str(clone), name)})")
    return Measured(name, head.stdout.strip()[:SHORT_COMMIT], ratchet.summarise(tree), len(tree.unparsable))


def judge_private(url: str) -> Row:
    name = repository_name(url)
    try:
        require_anonymous(url)
    except Refused as refused:
        return Unjudged(name, f"cannot check that it is private ({refused})")
    with engines.scratch_directory() as scratch:
        try:
            listed = git(["ls-remote", "--", url], scratch, anonymous_environment(scratch), LS_REMOTE_TIMEOUT_SECONDS)
        except engines.EngineError as error:
            return Unjudged(name, f"cannot check that it is private ({error})")
    if listed.returncode == 0:
        return Unjudged(name, "declared private but readable")
    return Private(name)


def git(
    arguments: list[str], scratch: Path, environment: dict[str, str], timeout: float
) -> subprocess.CompletedProcess[str]:
    return engines.run([*ANONYMOUS_GIT, *arguments], cwd=scratch, env=environment, timeout=timeout)


def render(rows: list[Row], today: date) -> str:
    heading = f"**Fleet scoreboard, {today.isoformat()}, quality {__version__} — {LANGUAGES}**"
    table = [table_line(COLUMNS), "|---" * len(COLUMNS) + "|", *(table_line(cells(row)) for row in rows)]
    return "\n".join((heading, "", *table))


def table_line(values: tuple[str, ...]) -> str:
    return "| " + " | ".join(values) + " |"


def cells(row: Row) -> tuple[str, ...]:
    if isinstance(row, Measured):
        return measured_cells(row)
    label = "not measured: private" if isinstance(row, Private) else f"cannot judge: {row.reason}"
    return (row.name, NO_COMMIT, label, *("",) * (len(COLUMNS) - 3))


def measured_cells(row: Measured) -> tuple[str, ...]:
    production, tests = row.areas[ratchet.Area.PRODUCTION], row.areas[ratchet.Area.TESTS]
    ratio = f"{tests.lines / production.lines:.2f}" if production.lines else "—"
    debts = (f"{production.debt[metric]} / {tests.debt[metric]}" for metric in DEBT_COLUMNS)
    return (row.name, row.commit, f"{production.lines:,} / {tests.lines:,}", ratio, *debts, unparsable_note(row))


def unparsable_note(row: Measured) -> str:
    if not row.unparsable:
        return ""
    return f"{row.unparsable} {'file' if row.unparsable == 1 else 'files'} not valid Python"
