"""`quality scoreboard`: every fleet repository's size in one markdown table, and under it the release matrix: the
gate and CodeQL release each repository's workflows call.

Lines and the production/test split are the `size` rule's own (`ratchet.summarise`), measured on a shallow clone of
the default branch tip. git runs anonymously: only an http, https or file URL without credentials, no credential
helper, no askpass, no terminal prompt, no system, global or environment config and an empty HOME, from a temporary
directory, so no checkout's own config reaches it either. A repository declared private is never cloned: the table
is published in a public run summary.
"""

import re
import subprocess
import tomllib
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import date
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

import yaml

from quality import __version__, clones, engines, hygiene, ratchet, repository

FLEET_PATH = "quality/fleet.toml"
FLEET_EXAMPLE = 'public = ["https://github.com/o/open"]\nprivate = ["https://github.com/o/closed"]'
FUNCTION_SCOPE = "functions and CCN: Python only"
CLONE_TIMEOUT_SECONDS = 300
LS_REMOTE_TIMEOUT_SECONDS = 60
SHORT_COMMIT = 7
DASH = "—"
GATE_REPOSITORY = "overnightworks/quality"
GATE_URL = f"https://github.com/{GATE_REPOSITORY}"
GATE_FILE = "gate.yml"
CODEQL_FILE = "codeql.yml"
WORKFLOW_DIRECTORY = PurePosixPath(".github/workflows")
WORKFLOW_SUFFIXES = frozenset({".yml", ".yaml"})
LOCAL_PREFIXES = ("./", "$/")
RELEASE_TAG = re.compile(r"v(\d+)\.(\d+)\.(\d+)")
COMMIT_SHA = re.compile(r"[0-9a-f]{40}")
SELF = "self"
CALLER_SEPARATOR = " · "
UNNAMED = "unnamed URL"
ANONYMOUS_GIT = ("git", "-c", "credential.helper=", "-c", "core.askPass=")
# ssh authenticates with the user's keys and agent whatever HOME says, so only these transports are anonymous.
ANONYMOUS_SCHEMES = frozenset({"file", "http", "https"})
# scheme and "//" then the rest of the line (a space stays, git takes the URL as one argument); git reads "https:host/x"
# as scp-style ssh and an uppercase scheme as a missing remote helper
ANONYMOUS_URL = re.compile(rf"(?:{'|'.join(sorted(ANONYMOUS_SCHEMES))})://.*")
COLUMNS = (
    "Repo",
    "Commit",
    "Languages",
    "Lines prod / test",
    "test:prod",
    f"Files ≥ {ratchet.FILE_LINES_FROM} prod / test",
    f"Functions ≥ {ratchet.FUNCTION_LINES_FROM} prod / test",
    f"CCN > {ratchet.CCN_ABOVE} prod / test",
    "Clone pairs prod / test",
    "Note",
)
MATRIX_COLUMNS = ("Repo", "Gate", "CodeQL")
DEBT_COLUMNS = (ratchet.Metric.FILE_LINES, ratchet.Metric.FUNCTION_LINES, ratchet.Metric.CCN)


ReleaseTags = dict[str, str]
TagReader = Callable[[], ReleaseTags]


class ReleaseTagsError(Exception):
    """The gate repository's release tags cannot be read."""


@dataclass(frozen=True)
class Call:
    """One job-level `uses:` of a reusable workflow: the ref after `@`, None for the repository's own file."""

    ref: str | None


@dataclass(frozen=True)
class Workflows:
    """What a clone's workflow files say about the gate and CodeQL, read as data."""

    gate: tuple[Call, ...]
    codeql: tuple[Call, ...]
    unreadable: tuple[str, ...]


@dataclass(frozen=True)
class Measured:
    name: str
    commit: str
    languages: dict[ratchet.Language, int]
    areas: dict[ratchet.Area, ratchet.AreaSummary]
    unparsable: int
    pairs: dict[ratchet.Area, int]
    workflows: Workflows


@dataclass(frozen=True)
class Private:
    """Declared private and not readable anonymously: nothing about it is published."""

    name: str


@dataclass(frozen=True)
class Unjudged:
    name: str
    reason: str


Row = Measured | Private | Unjudged


@dataclass(frozen=True)
class Fleet:
    """The repositories to report: URLs in file order, each listed once."""

    public: tuple[str, ...]
    private: tuple[str, ...]


class FleetError(Exception):
    """The fleet file cannot be used; the message names it and shows the right shape."""


def read_fleet(root: Path) -> Fleet:
    try:
        text = repository.read_text(root, FLEET_PATH)
    except repository.RepositoryFileError as error:
        raise FleetError(str(error)) from error
    if text is None:
        raise FleetError(f"{FLEET_PATH} is missing - create it with:\n{FLEET_EXAMPLE}")
    return parse_fleet(text)


def parse_fleet(text: str) -> Fleet:
    try:
        document = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise FleetError(f"{FLEET_PATH} is not valid TOML ({error}) - write it like:\n{FLEET_EXAMPLE}") from error
    unknown = sorted(set(document) - {"public", "private"})
    if unknown:
        raise FleetError(f"{FLEET_PATH} has unknown key(s) {unknown} - only public and private exist, like:\n{FLEET_EXAMPLE}")
    fleet = Fleet(_url_list(document, "public"), _url_list(document, "private"))
    urls = [*fleet.public, *fleet.private]
    if not urls:
        raise FleetError(f"{FLEET_PATH} lists no URL - list at least one, like:\n{FLEET_EXAMPLE}")
    repeats = _repeats(fleet)
    if repeats:
        raise FleetError(
            f"{FLEET_PATH} lists a URL more than once ({'; '.join(repeats)}) - list each URL once, like:\n{FLEET_EXAMPLE}"
        )
    return fleet


def _repeats(fleet: Fleet) -> list[str]:
    """Each repeated entry named by its place, never its text: a URL may embed credentials."""
    first_place: dict[str, str] = {}
    repeats = []
    for list_name, urls in (("public", fleet.public), ("private", fleet.private)):
        for index, url in enumerate(urls):
            place = f"{list_name}[{index}]"
            if url in first_place:
                repeats.append(f"{place} repeats {first_place[url]}")
            else:
                first_place[url] = place
    return repeats


def _url_list(document: dict, key: str) -> tuple[str, ...]:
    urls = document.get(key)
    if not isinstance(urls, list) or not all(isinstance(url, str) for url in urls):
        raise FleetError(f"{FLEET_PATH} needs {key} as a list of strings, like:\n{FLEET_EXAMPLE}")
    return tuple(urls)


def measure_fleet(fleet: Fleet) -> list[Row]:
    """One row per URL, public ones first, each in file order; one row that fails never stops the others."""
    return [*(measure_public(url) for url in fleet.public), *(judge_private(url) for url in fleet.private)]


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
    if not ANONYMOUS_URL.fullmatch(url):
        raise Refused("not an http, https or file URL")


def cannot_judge(rows: list[Row], tags: ReleaseTags | ReleaseTagsError) -> bool:
    unreadable = any(isinstance(row, Measured) and row.workflows.unreadable for row in rows)
    return isinstance(tags, ReleaseTagsError) or unreadable or any(isinstance(row, Unjudged) for row in rows)


def repository_name(url: str) -> str:
    """`owner/name` for a non-file URL with a host and two path segments, else its last path segment, else its host: never the
    URL itself, which may carry credentials."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return UNNAMED
    segments = [segment for segment in PurePosixPath(parts.path).parts if segment != "/"]
    name = segments[-1].removesuffix(".git") if segments else ""
    if parts.scheme != "file" and parts.hostname and name and len(segments) > 1:
        return f"{segments[-2]}/{name}"
    return name or parts.hostname or UNNAMED


def read_release_tags(url: str = GATE_URL) -> ReleaseTags:
    """Release tag -> commit of the gate repository, the annotated ones peeled, by one anonymous `git ls-remote`."""
    with engines.scratch_directory() as scratch:
        try:
            listed = git(["ls-remote", "--tags", "--", url], scratch, anonymous_environment(scratch),
                         LS_REMOTE_TIMEOUT_SECONDS)
        except engines.EngineError as error:
            raise ReleaseTagsError(str(error)) from error
    if listed.returncode != 0:
        raise ReleaseTagsError(f"git exited {listed.returncode}")
    tags = release_tags_listed(listed.stdout)
    if not tags:
        raise ReleaseTagsError("no release tag")
    return tags


def release_tags_listed(listing: str) -> ReleaseTags:
    """A peeled line (`refs/tags/v1.0.0^{}`) names the commit and wins over the tag object's own line."""
    tags: ReleaseTags = {}
    peeled: ReleaseTags = {}
    for line in listing.splitlines():
        commit, _, reference = line.partition("\t")
        tag = reference.removeprefix("refs/tags/")
        target = peeled if tag.endswith("^{}") else tags
        target[tag.removesuffix("^{}")] = commit
    return {tag: commit for tag, commit in {**tags, **peeled}.items() if RELEASE_TAG.fullmatch(tag)}


def release_order(tag: str) -> tuple[int, ...]:
    match = RELEASE_TAG.fullmatch(tag)
    return tuple(int(number) for number in match.groups()) if match else ()


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
            tracked = hygiene.tracked_files(clone)
            sources = hygiene.read_sources(clone, tracked)
        except hygiene.CannotJudge as error:
            return Unjudged(name, f"cannot measure ({str(error).replace(str(clone), name)})")
        workflows = read_workflows(clone, tracked)
    tree = ratchet.measure(sources)
    pairs = clones.summarise_pairs(clones.find_pairs(sources))
    commit = head.stdout.strip()[:SHORT_COMMIT]
    languages, areas = ratchet.language_lines(tree), ratchet.summarise(tree)
    return Measured(name, commit, languages, areas, len(tree.unparsable), pairs, workflows)


def read_workflows(clone: Path, tracked: list[str]) -> Workflows:
    """Parses each tracked workflow file as data, never running anything, and keeps its job-level reusable calls."""
    gate: list[Call] = []
    codeql: list[Call] = []
    unreadable: list[str] = []
    for name in tracked:
        path = PurePosixPath(name)
        if path.parent != WORKFLOW_DIRECTORY or path.suffix not in WORKFLOW_SUFFIXES:
            continue
        try:
            text = repository.read_text(clone, name)
        except repository.RepositoryFileError:
            unreadable.append(name)
            continue
        try:
            document = yaml.safe_load(text) if text is not None else None
        # safe_load runs a third-party parser on another repository's untrusted file: any error it raises means the file cannot be read
        except Exception:  # noqa: BLE001
            unreadable.append(name)
            continue
        for uses in job_calls(document):
            gate.extend(calls_of(uses, GATE_FILE))
            codeql.extend(calls_of(uses, CODEQL_FILE))
    return Workflows(tuple(gate), tuple(codeql), tuple(unreadable))


def job_calls(document: object) -> Iterator[str]:
    jobs = document.get("jobs") if isinstance(document, dict) else None
    for job in jobs.values() if isinstance(jobs, dict) else ():
        uses = job.get("uses") if isinstance(job, dict) else None
        if isinstance(uses, str):
            yield uses


def calls_of(uses: str, workflow_file: str) -> list[Call]:
    path = f"{WORKFLOW_DIRECTORY}/{workflow_file}"
    if uses in (f"{prefix}{path}" for prefix in LOCAL_PREFIXES):
        return [Call(None)]
    file, _, ref = uses.partition("@")
    owner_and_repository, _, workflow = file.partition(f"/{WORKFLOW_DIRECTORY}/")
    matches = owner_and_repository.lower() == GATE_REPOSITORY and workflow == workflow_file
    return [Call(ref)] if matches and ref else []


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


def render(rows: list[Row], today: date, tags: ReleaseTags | ReleaseTagsError) -> tuple[str, ...]:
    heading = f"**Fleet scoreboard, {today.isoformat()}, quality {__version__} — {FUNCTION_SCOPE}**"
    table = [table_line(COLUMNS), "|---" * len(COLUMNS) + "|", *(table_line(cells(row)) for row in rows)]
    pins = None if isinstance(tags, ReleaseTagsError) else tags
    matrix_heading = f"**Repo × rule, {newest_release_words(tags)}**"
    matrix = [
        table_line(MATRIX_COLUMNS),
        "|---" * len(MATRIX_COLUMNS) + "|",
        *(table_line(matrix_cells(row, pins)) for row in rows),
    ]
    return (heading, "", *table, "", matrix_heading, "", *matrix)


def newest_release_words(tags: ReleaseTags | ReleaseTagsError) -> str:
    if isinstance(tags, ReleaseTagsError):
        return f"newest release unreadable ({tags}), pins shown as commits"
    return f"newest release {newest_release(tags)}"


def newest_release(tags: ReleaseTags) -> str:
    return max(tags, key=release_order)


def matrix_cells(row: Row, tags: ReleaseTags | None) -> tuple[str, ...]:
    if not isinstance(row, Measured):
        return (row.name, status_label(row), "")
    if row.workflows.unreadable:
        unreadable = f"cannot read {', '.join(row.workflows.unreadable)}"
        return (row.name, unreadable, unreadable)
    is_gate = row.name == GATE_REPOSITORY
    return (row.name, pin_cell(row.workflows.gate, "no gate", is_gate, tags),
            pin_cell(row.workflows.codeql, "none", is_gate, tags))


def pin_cell(calls: tuple[Call, ...], absent: str, is_gate_repository: bool, tags: ReleaseTags | None) -> str:
    """The repository's own file counts only in the gate's repository; a remote ref is named by its release tag."""
    own = [SELF for call in calls if call.ref is None and is_gate_repository]
    pins = [pin_words(call.ref, tags) for call in calls if call.ref is not None]
    return CALLER_SEPARATOR.join(sorted({*own, *pins})) or absent


def pin_words(ref: str, tags: ReleaseTags | None) -> str:
    ref = ref.lower() if COMMIT_SHA.fullmatch(ref.lower()) else ref
    if not COMMIT_SHA.fullmatch(ref):
        return f"{ref} (not a commit)"
    short = ref[:SHORT_COMMIT]
    if tags is None:
        return short
    released = [tag for tag, commit in tags.items() if commit == ref]
    if not released:
        return f"{short} (no release)"
    tag = max(released, key=release_order)
    return tag if tag == newest_release(tags) else f"{tag} (behind)"


def table_line(values: tuple[str, ...]) -> str:
    return "| " + " | ".join(value.replace("|", "\\|") for value in values) + " |"


def cells(row: Row) -> tuple[str, ...]:
    if isinstance(row, Measured):
        return measured_cells(row)
    return (row.name, DASH, status_label(row), *("",) * (len(COLUMNS) - 3))


def status_label(row: Private | Unjudged) -> str:
    return "not measured: private" if isinstance(row, Private) else f"cannot judge: {row.reason}"


def measured_cells(row: Measured) -> tuple[str, ...]:
    production, tests = row.areas[ratchet.Area.PRODUCTION], row.areas[ratchet.Area.TESTS]
    ratio = f"{tests.lines / production.lines:.2f}" if production.lines else DASH
    debts = (f"{production.debt[metric]} / {tests.debt[metric]}" for metric in DEBT_COLUMNS)
    lines = f"{production.lines:,} / {tests.lines:,}"
    return (row.name, row.commit, languages_cell(row), lines, ratio, *debts, clone_pairs_cell(row), unparsable_note(row))


def languages_cell(row: Measured) -> str:
    return " · ".join(f"{language.value} {lines:,}" for language, lines in row.languages.items()) or DASH


def clone_pairs_cell(row: Measured) -> str:
    return f"{row.pairs[ratchet.Area.PRODUCTION]} / {row.pairs[ratchet.Area.TESTS]}"


def unparsable_note(row: Measured) -> str:
    if not row.unparsable:
        return ""
    return f"{row.unparsable} {'file' if row.unparsable == 1 else 'files'} not valid Python"
