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
from enum import Enum
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

import yaml

from quality import __version__, advisories, architecture, clones, config, engines, hygiene, ratchet, repository

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
FIXED_MATRIX_COLUMNS = ("Repo", "Gate", "CodeQL", "Mandatory jobs", "Sonar legacy")
DEBT_COLUMNS = (ratchet.Metric.FILE_LINES, ratchet.Metric.FUNCTION_LINES, ratchet.Metric.CCN)

ENFORCED = "enforced"
REPORT_ONLY = "report-only"
NO_CONFIG = "no config"
RELEASE_UNKNOWN = "release unknown"
NOT_RELEASED = "not released"
IMPORT_LINTER_CONFIG = "import-linter config"
FIRST_RELEASE = "v0.1.0"
RATCHET_RELEASE = "v0.3.0"
CLONE_RELEASE = "v0.4.0"


def release_order(tag: str) -> tuple[int, ...]:
    match = RELEASE_TAG.fullmatch(tag)
    return tuple(int(number) for number in match.groups()) if match else ()


class Switch(Enum):
    """What decides whether a gate that knows a rule enforces it."""

    ALWAYS = "always"
    SIZE_BASELINE = "a size baseline"
    IMPORT_LINTER_CONFIG = "an import-linter config"
    REPORT_ONLY = "report only"


@dataclass(frozen=True)
class RuleRelease:
    """A rule of the door, the first gate release that judges it (None: main only) and what switches it on."""

    rule: str
    first_release: str | None
    switch: Switch


# In cell order. The tripwire test holds the ids equal to the rules `hygiene.run_hygiene` judges.
RULES = (
    RuleRelease(hygiene.ENV_FILE, FIRST_RELEASE, Switch.ALWAYS),
    RuleRelease(hygiene.COMPOSE_PORT, FIRST_RELEASE, Switch.ALWAYS),
    RuleRelease(engines.LEAK_RULE, FIRST_RELEASE, Switch.ALWAYS),
    RuleRelease(engines.WORKFLOW_LINT, FIRST_RELEASE, Switch.ALWAYS),
    RuleRelease(engines.WORKFLOW_SECURITY, FIRST_RELEASE, Switch.ALWAYS),
    RuleRelease(hygiene.CLAUDE_MD, RATCHET_RELEASE, Switch.ALWAYS),
    RuleRelease(advisories.RULE, RATCHET_RELEASE, Switch.ALWAYS),
    RuleRelease(ratchet.SIZE, RATCHET_RELEASE, Switch.SIZE_BASELINE),
    RuleRelease(architecture.RULE, RATCHET_RELEASE, Switch.IMPORT_LINTER_CONFIG),
    RuleRelease(clones.RULE, CLONE_RELEASE, Switch.REPORT_ONLY),
)
OLDEST_RELEASE = min((rule.first_release for rule in RULES if rule.first_release), key=release_order)
EVERY_RELEASE_ENFORCES = tuple(
    rule for rule in RULES if rule.switch is Switch.ALWAYS and rule.first_release == OLDEST_RELEASE
)
SWITCHED_RULES = tuple(rule for rule in RULES if rule not in EVERY_RELEASE_ENFORCES)
MATRIX_COLUMNS = (*FIXED_MATRIX_COLUMNS, *(rule.rule for rule in SWITCHED_RULES))


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
class Declared:
    """What a clone's own files switch on: the gate settings, a size baseline, an import-linter config, and the files
    among them that could not be read."""

    settings: config.GateSettings | None
    has_baseline: bool
    has_import_config: bool
    unreadable: frozenset[str]


@dataclass(frozen=True)
class CannotRead:
    """A matrix cell that depends on files that could not be read; the run cannot judge while one is printed."""

    files: tuple[str, ...]

    def __str__(self) -> str:
        return f"cannot read {', '.join(self.files)}"


Cell = str | CannotRead


class Gate(Enum):
    """The release a row's gate is, when it is not a release tag."""

    NONE = "no gate"
    MAIN = "main"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Measured:
    name: str
    commit: str
    languages: dict[ratchet.Language, int]
    areas: dict[ratchet.Area, ratchet.AreaSummary]
    unparsable: int
    pairs: dict[ratchet.Area, int]
    workflows: Workflows
    declared: Declared


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
    pins = release_tags_or_none(tags)
    unreadable = any(
        isinstance(cell, CannotRead)
        for row in rows
        if isinstance(row, Measured)
        for cell in matrix_cells(row, pins)
    )
    return isinstance(tags, ReleaseTagsError) or unreadable or any(isinstance(row, Unjudged) for row in rows)


def release_tags_or_none(tags: ReleaseTags | ReleaseTagsError) -> ReleaseTags | None:
    return None if isinstance(tags, ReleaseTagsError) else tags


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
        declared = read_declared(clone, tracked)
    tree = ratchet.measure(sources)
    pairs = clones.summarise_pairs(clones.find_pairs(sources))
    commit = head.stdout.strip()[:SHORT_COMMIT]
    languages, areas = ratchet.language_lines(tree), ratchet.summarise(tree)
    return Measured(name, commit, languages, areas, len(tree.unparsable), pairs, workflows, declared)


def read_declared(clone: Path, tracked: list[str]) -> Declared:
    """The clone's gate settings, size baseline and import-linter config; a file that cannot be read is named."""
    unreadable: set[str] = set()
    settings, has_baseline, has_import_config = None, False, False
    try:
        settings = config.read_gate_settings(clone) if config.CONFIG_PATH in tracked else None
    except config.ConfigError:
        unreadable.add(config.CONFIG_PATH)
    try:
        has_baseline = hygiene.read_baseline(clone) is not None
    except (hygiene.CannotJudge, ratchet.SizeError):
        unreadable.add(ratchet.BASELINE_PATH)
    try:
        has_import_config = find_import_config(clone, tracked)
    except (repository.RepositoryFileError, architecture.ContractError):
        unreadable.add(IMPORT_LINTER_CONFIG)
    return Declared(settings, has_baseline, has_import_config, frozenset(unreadable))


def find_import_config(clone: Path, tracked: list[str]) -> bool:
    texts = {name: repository.read_text(clone, name) for name in tracked if name in architecture.CONFIG_FILES}
    return architecture.find_config({name: text for name, text in texts.items() if text is not None}) is not None


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
            # BaseLoader keeps scalars strings, so no conversion can fail; ValueError is the scanner's chr() on a \U escape past U+10FFFF
            document = yaml.load(text, Loader=yaml.BaseLoader) if text is not None else None
        except (yaml.YAMLError, RecursionError, ValueError):
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
    pins = release_tags_or_none(tags)
    matrix_heading = f"**Repo × rule, {newest_release_words(tags)}**"
    every_release = f"Every gate release enforces {words_joined([rule.rule for rule in EVERY_RELEASE_ENFORCES])}."
    matrix = [
        table_line(MATRIX_COLUMNS),
        "|---" * len(MATRIX_COLUMNS) + "|",
        *(table_line(matrix_cells(row, pins)) for row in rows),
    ]
    return (heading, "", *table, "", matrix_heading, "", every_release, "", *matrix)


def words_joined(words: list[str]) -> str:
    return f"{', '.join(words[:-1])} and {words[-1]}" if len(words) > 1 else "".join(words)


def newest_release_words(tags: ReleaseTags | ReleaseTagsError) -> str:
    if isinstance(tags, ReleaseTagsError):
        return f"newest release unreadable ({tags}), pins shown as commits"
    return f"newest release {newest_release(tags)}"


def newest_release(tags: ReleaseTags) -> str:
    return max(tags, key=release_order)


def matrix_cells(row: Row, tags: ReleaseTags | None) -> tuple[Cell, ...]:
    if not isinstance(row, Measured):
        return (row.name, status_label(row), *("",) * (len(MATRIX_COLUMNS) - 2))
    if row.workflows.unreadable:
        return (row.name, *(CannotRead(row.workflows.unreadable),) * (len(MATRIX_COLUMNS) - 1))
    is_gate = row.name == GATE_REPOSITORY
    release = gate_release(row.workflows.gate, is_gate, tags)
    return (
        row.name,
        pin_cell(row.workflows.gate, "no gate", is_gate, tags),
        pin_cell(row.workflows.codeql, "none", is_gate, tags),
        *settings_cells(row.declared, release),
        *(rule_cell(rule, release, row.declared) for rule in SWITCHED_RULES),
    )


def settings_cells(declared: Declared, release: Gate | str) -> tuple[Cell, Cell]:
    if release is Gate.NONE:
        return (DASH, DASH)
    if config.CONFIG_PATH in declared.unreadable:
        return (CannotRead((config.CONFIG_PATH,)),) * 2
    if declared.settings is None:
        return (NO_CONFIG, NO_CONFIG)
    return (str(len(declared.settings.mandatory_jobs)), declared.settings.legacy.value)


def gate_release(calls: tuple[Call, ...], is_gate_repository: bool, tags: ReleaseTags | None) -> Gate | str:
    """The oldest release the gate calls are: a tag, main for the gate's own file, else why there is none."""
    pins = [
        pin_release(call.ref, tags) if call.ref is not None else Gate.MAIN
        for call in calls
        if call.ref is not None or is_gate_repository
    ]
    tagged = [pin for pin in pins if isinstance(pin, str)]
    if not pins:
        return Gate.NONE
    if Gate.UNKNOWN in pins:
        return Gate.UNKNOWN
    return min(tagged, key=release_order) if tagged else Gate.MAIN


def commit_of(ref: str) -> str | None:
    """The ref lowercased when it has the shape of a full commit, else None."""
    commit = ref.lower()
    return commit if COMMIT_SHA.fullmatch(commit) else None


def pin_release(ref: str, tags: ReleaseTags | None) -> Gate | str:
    commit = commit_of(ref)
    if tags is None or commit is None:
        return Gate.UNKNOWN
    return released_tag(commit, tags) or Gate.UNKNOWN


def rule_cell(rule: RuleRelease, release: Gate | str, declared: Declared) -> Cell:
    """What the pinned gate does with the rule: never more than the oldest release the repository calls."""
    if release is Gate.NONE:
        return DASH
    if release is Gate.UNKNOWN:
        return RELEASE_UNKNOWN
    if isinstance(release, str):
        if rule.first_release is None:
            return NOT_RELEASED
        if release_order(release) < release_order(rule.first_release):
            return f"not in {release}"
    return switch_words(rule.switch, declared)


def switch_words(switch: Switch, declared: Declared) -> Cell:
    if switch is Switch.ALWAYS:
        return ENFORCED
    if switch is Switch.REPORT_ONLY:
        return REPORT_ONLY
    file, present = (
        (ratchet.BASELINE_PATH, declared.has_baseline)
        if switch is Switch.SIZE_BASELINE
        else (IMPORT_LINTER_CONFIG, declared.has_import_config)
    )
    if file in declared.unreadable:
        return CannotRead((file,))
    return ENFORCED if present else REPORT_ONLY


def pin_cell(calls: tuple[Call, ...], absent: str, is_gate_repository: bool, tags: ReleaseTags | None) -> str:
    """The repository's own file counts only in the gate's repository; a remote ref is named by its release tag."""
    own = [SELF for call in calls if call.ref is None and is_gate_repository]
    pins = [pin_words(call.ref, tags) for call in calls if call.ref is not None]
    return CALLER_SEPARATOR.join(sorted({*own, *pins})) or absent


def pin_words(ref: str, tags: ReleaseTags | None) -> str:
    commit = commit_of(ref)
    if commit is None:
        return f"{ref} (not a commit)"
    short = commit[:SHORT_COMMIT]
    if tags is None:
        return short
    tag = released_tag(commit, tags)
    if tag is None:
        return f"{short} (no release)"
    return tag if tag == newest_release(tags) else f"{tag} (behind)"


def released_tag(commit: str, tags: ReleaseTags) -> str | None:
    """The newest release tag that points at the commit."""
    return max((tag for tag, tagged in tags.items() if tagged == commit), key=release_order, default=None)


def table_line(values: tuple[Cell, ...]) -> str:
    return "| " + " | ".join(str(value).replace("|", "\\|") for value in values) + " |"


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
