"""`quality report`: every fleet repository's size in one markdown table, and under it the release matrix: the
gate and CodeQL release each repository's workflows call. The door prints the fleet line and a row per repository on
stderr as each is measured, then the tables alone on stdout and in the step summary, then the closing line or the pair.

Lines and the production/test split are the `size` rule's own (`ratchet.summarise`), measured on a shallow clone of
the default branch tip. The fleet is every public, non-archived, non-fork repository GitHub lists for the organizations
`quality/fleet.toml` names, less the single `owner/name`s it excludes, plus the single `owner/name`s it adds. Only `https://github.com/<owner>/<name>` is cloned, and git runs
anonymously: no credential helper, no askpass, no terminal prompt, no system, global or environment config and an
empty HOME, from a temporary directory, so no checkout's own config reaches it either. A repository that is not
anonymously readable is not measured: the table is published in a public run summary.
"""

import json
import re
import subprocess
import urllib.error
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date
from email.message import Message
from enum import Enum, StrEnum
from functools import partial
from pathlib import Path

from quality import (
    __version__,
    architecture,
    baseline_file,
    catalog_rules,
    checkout,
    clones,
    config,
    layout,
    output,
    process,
    ratchet,
    report,
    repository,
    web,
)
from quality.report import CannotJudge, Command, Example, Instruction, Step
from quality.rules import Rule, RuleRelease, Switch


def repository_url(name: str) -> str:
    return f"{checkout.GITHUB}{name}"


FLEET_PATH = "quality/fleet.toml"
FLEET_EXAMPLE = 'organizations = ["overnightworks"]'
OWNER = "[A-Za-z0-9][A-Za-z0-9-]*"
ENTRY = re.compile(rf"{OWNER}/(?!\.+\Z)[A-Za-z0-9._-]+")
ORGANIZATION = re.compile(OWNER)


@dataclass(frozen=True)
class FleetKey:
    name: str
    valid: re.Pattern[str]
    noun: str


FLEET_KEYS = (
    FleetKey("organizations", ORGANIZATION, "organization name"),
    FleetKey("repositories", ENTRY, "owner/name"),
    FleetKey("exclude", ENTRY, "owner/name"),
)
LISTING_URL = "https://api.github.com/orgs/{organization}/repos?type=public&per_page=100"
NEXT_PAGE = re.compile(r'rel="next"')
LISTING_CREDENTIAL_VARIABLE = "GITHUB_TOKEN"
NOTHING_MEASURED = "nothing measured"
FUNCTION_SCOPE = "functions and CCN: Python only"
CLONE_TIMEOUT_SECONDS = 300
LS_REMOTE_TIMEOUT_SECONDS = 60
SHORT_COMMIT = 7
DASH = "—"
SELF = "self"
CALLER_SEPARATOR = " · "
ANONYMOUS_GIT = ("git", "-c", "credential.helper=", "-c", "core.askPass=")
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
DEBT_COLUMNS = (baseline_file.Metric.FILE_LINES, baseline_file.Metric.FUNCTION_LINES, baseline_file.Metric.CCN)

ENFORCED = "enforced"
REPORT_ONLY = "report-only"
NO_CONFIG = "no config"
NO_JUDGED_PROJECT = "no judged project"
NO_CATALOGS_DECLARATION = "no catalogs declaration"
RELEASE_UNKNOWN = "release unknown"
NOT_RELEASED = "not released"
IMPORT_LINTER_CONFIG = "import-linter config"
REPORT_COMMAND = "quality report"
FLEET_SUBJECT = "fleet"
RELEASE_TAGS_SUBJECT = "release tags"
EXIT_MEASURED = 0
EXIT_NOT_MEASURED = 2
# The characters CommonMark and GFM tables read as syntax inside a line: escapes, code, emphasis, strikethrough, links
# and images, HTML and entities, cell borders.
MARKDOWN_SYNTAX = frozenset("\\`*_~[]<>&|")
FIX_THEN_RERUN = Instruction(f"fix what the error names, then run {REPORT_COMMAND} again")
FLEET_SHAPE = Example(f"write it in {FLEET_PATH} like this", (FLEET_EXAMPLE,))


class FleetEvent(StrEnum):
    """What a row of the report says."""

    FOUND = "found"
    MEASURED = "measured"
    PARTLY_READ = "partly read"
    NOT_MEASURED = "not measured"
    NOT_READ = "not read"


ListingFetch = Callable[[str, Mapping[str, str], Callable[[Exception], CannotJudge]], tuple[bytes, Message]]


def fleet_refusal(fact: str, *, shape: bool = True, rerun: bool = False) -> CannotJudge:
    """A fleet that cannot be used: the fact, and under `next:` the file's shape to copy when the file or an entry is
    at fault, then the rerun when GitHub failed."""
    steps: tuple[Step, ...] = ((FLEET_SHAPE,) if shape else ()) + ((FIX_THEN_RERUN,) if rerun else ())
    return CannotJudge(fact, steps)


class ListingFault(CannotJudge):
    """GitHub's listing of an organization cannot be used; `in_file` when the fault is the file's entry, not GitHub's."""

    def __init__(self, fact: str, in_file: bool = False) -> None:
        super().__init__(fact)
        self.in_file = in_file


RELEASES = tuple(rule.release for rule in Rule)
OLDEST_RELEASE = min((rule.first_release for rule in RELEASES if rule.first_release), key=checkout.release_order)
EVERY_RELEASE_ENFORCES = tuple(
    rule for rule in RELEASES if rule.switch is Switch.ALWAYS and rule.first_release == OLDEST_RELEASE
)
SWITCHED_RULES = tuple(rule for rule in RELEASES if rule not in EVERY_RELEASE_ENFORCES)
MATRIX_COLUMNS = (*FIXED_MATRIX_COLUMNS, *(rule.rule for rule in SWITCHED_RULES))


TagReader = Callable[[], checkout.ReleaseTags]


class ReleaseTagsError(Exception):
    """The gate repository's release tags cannot be read."""


@dataclass(frozen=True)
class Declared:
    """What a clone's own files switch on: the gate settings, a size baseline, a clone table, a literal-text table, an
    import-linter config, a project whose tests the layout rules judge, a catalogs declaration, and the files among them
    that could not be read."""

    settings: config.GateSettings | None
    has_baseline: bool
    has_clone_table: bool
    has_literal_table: bool
    has_import_config: bool
    has_judged_project: bool
    declares_catalogs: bool
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
    workflows: checkout.Workflows
    declared: Declared


@dataclass(frozen=True)
class Unjudged:
    name: str
    reason: str


Row = Measured | Unjudged


@dataclass(frozen=True)
class Fleet:
    """What `quality/fleet.toml` selects: organizations, single `owner/name`s added and single `owner/name`s left out of
    what the organizations list, each in file order."""

    organizations: tuple[str, ...]
    repositories: tuple[str, ...]
    exclude: tuple[str, ...]


def read_fleet(root: Path) -> Fleet:
    try:
        text = repository.read_text(root, FLEET_PATH)
    except repository.RepositoryFileError as error:
        raise fleet_refusal(str(error)) from error
    if text is None:
        raise fleet_refusal(f"{FLEET_PATH} is missing")
    return parse_fleet(text)


def parse_fleet(text: str) -> Fleet:
    try:
        document = repository.parse_toml(text)
    except repository.TomlError as error:
        raise fleet_refusal(f"{FLEET_PATH} is not valid TOML ({error})") from error
    unknown = sorted(set(document) - {key.name for key in FLEET_KEYS})
    if unknown:
        keys = words_joined([key.name for key in FLEET_KEYS])
        raise fleet_refusal(f"{FLEET_PATH} has a key other than {keys}: {', '.join(unknown)}")
    lists = {key.name: document.get(key.name, []) for key in FLEET_KEYS}
    for key, entries in lists.items():
        if not isinstance(entries, list) or not all(isinstance(entry, str) for entry in entries):
            raise fleet_refusal(f"{FLEET_PATH} needs {key} as a list of strings")
    if not lists["organizations"] and not lists["repositories"]:
        raise fleet_refusal(f"{FLEET_PATH} selects no repository")
    problems = _entry_problems(lists)
    if problems:
        raise fleet_refusal(f"{FLEET_PATH} {output.SEPARATOR.join(problems)}")
    return Fleet(*(tuple(lists[key.name]) for key in FLEET_KEYS))


def _entry_problems(lists: Mapping[str, list[str]]) -> list[str]:
    """Each problem named by its place, never its text: an entry may be a pasted URL with a password."""
    first_place: dict[str, str] = {}
    problems = []
    for key in FLEET_KEYS:
        for index, entry in enumerate(lists[key.name]):
            place = f"{key.name}[{index}]"
            if not key.valid.fullmatch(entry):
                problems.append(f"{place} is no {key.noun}")
            elif entry.lower() in first_place:
                problems.append(f"{place} repeats {first_place[entry.lower()]}")
            else:
                first_place[entry.lower()] = place
    return problems


def listing_failure(index: int, organization: str, error: Exception) -> ListingFault:
    """What went wrong reaching GitHub's listing, in words that carry no text of the exception."""
    if isinstance(error, urllib.error.HTTPError):
        if error.code == 404:
            return ListingFault(f"{FLEET_PATH} organizations[{index}] is no GitHub organization", in_file=True)
        return ListingFault(f"GitHub did not list {organization}{output.SEPARATOR}HTTP {error.code}")
    if isinstance(error, OSError) and web.timed_out(error):
        return ListingFault(f"GitHub did not answer the listing of {organization} within {web.REQUEST_TIMEOUT_SECONDS}s")
    return ListingFault(f"GitHub could not be reached for the listing of {organization}")


def list_organization(index: int, organization: str, token: str, fetch_listing: ListingFetch) -> tuple[str, ...]:
    """The public, non-archived, non-fork repositories GitHub lists for the organization, as `owner/name`, from one
    page. The token goes to GitHub's API only, never to git; a local run lists anonymously."""
    headers = {"Accept": "application/vnd.github+json", **({"Authorization": f"Bearer {token}"} if token else {})}
    body, answer = fetch_listing(LISTING_URL.format(organization=organization), headers, partial(listing_failure, index, organization))
    if NEXT_PAGE.search(answer.get("Link", "")):
        raise ListingFault(f"{organization} has more than 100 public repositories{output.SEPARATOR}quality reads one page")
    malformed = ListingFault(f"GitHub's listing of {organization} is not a list of repositories")
    try:
        listed = json.loads(body)
    except (ValueError, RecursionError) as error:
        raise malformed from error
    if not isinstance(listed, list) or not all(_is_listed_repository(entry) for entry in listed):
        raise malformed
    return tuple(entry["full_name"] for entry in listed if not (entry["private"] or entry["archived"] or entry["fork"]))


def _is_listed_repository(entry: object) -> bool:
    return (
        isinstance(entry, dict)
        and isinstance(entry.get("full_name"), str)
        and ENTRY.fullmatch(entry["full_name"]) is not None
        and all(isinstance(entry.get(flag), bool) for flag in ("private", "archived", "fork"))
    )


def exclude_found(found: Mapping[str, str], exclude: tuple[str, ...], unlisted: set[str]) -> tuple[dict[str, str], list[str]]:
    """The found names (by lower-cased name) less the excluded ones, and a fact per `exclude` entry that matches none of
    them, named by its place. `found` holds only what `list_organization` kept, so an entry naming a private repository
    reads exactly as a name GitHub does not list, and no fact repeats an entry. An entry whose organization's listing
    failed waits for the rerun."""
    facts = [
        f"{FLEET_PATH} exclude[{index}] matches no repository found"
        for index, entry in enumerate(exclude)
        if entry.lower() not in found and entry.partition("/")[0].lower() not in unlisted
    ]
    excluded = {entry.lower() for entry in exclude}
    return {key: name for key, name in found.items() if key not in excluded}, facts


def select_repositories(fleet: Fleet, token: str, fetch_listing: ListingFetch) -> tuple[str, ...]:
    """The found repositories of every organization less the excluded ones, plus the named ones, sorted by lower-cased
    name; every problem of every organization stands in one refusal before any clone."""
    found: dict[str, tuple[str, str]] = {}
    unlisted: set[str] = set()
    facts: list[str] = []
    shape_wrong = github_failed = False
    for index, organization in enumerate(fleet.organizations):
        try:
            names = list_organization(index, organization, token, fetch_listing)
        except ListingFault as fault:
            unlisted.add(organization.lower())
            github_failed |= not fault.in_file
            shape_wrong |= fault.in_file
            facts.append(str(fault))
        else:
            found.update({name.lower(): (organization, name) for name in names})
    for index, entry in enumerate(fleet.repositories):
        if entry.lower() in found:
            facts.append(f"{FLEET_PATH} repositories[{index}] is already in {found[entry.lower()][0]}")
            shape_wrong = True
    kept, exclude_facts = exclude_found({key: name for key, (_, name) in found.items()}, fleet.exclude, unlisted)
    shape_wrong |= bool(exclude_facts)
    facts.extend(exclude_facts)
    selected = {**kept, **{entry.lower(): entry for entry in fleet.repositories}}
    if not facts and not selected:
        facts.append(f"{FLEET_PATH} selects no repository")
        shape_wrong = True
    if facts:
        raise fleet_refusal(output.SEPARATOR.join((*facts, NOTHING_MEASURED)), shape=shape_wrong, rerun=github_failed)
    return tuple(selected[key] for key in sorted(selected))


def anonymous_environment(scratch: Path) -> dict[str, str]:
    """No configuration git could authenticate from: the process boundary's isolation drops every GIT_* variable and the system
    and global config, and points HOME at an empty directory, so no ~/.netrc either."""
    isolated = process.attribute_free_git_environment(scratch / "home")
    # git falls back to SSH_ASKPASS for an http password too.
    # The job's token is for GitHub's API alone; no git that reaches the network may inherit it.
    return {**{name: value for name, value in isolated.items() if name not in {"SSH_ASKPASS", LISTING_CREDENTIAL_VARIABLE}}, "GIT_TERMINAL_PROMPT": "0"}


def release_tags_or_none(tags: checkout.ReleaseTags | ReleaseTagsError) -> checkout.ReleaseTags | None:
    return None if isinstance(tags, ReleaseTagsError) else tags


def read_release_tags() -> checkout.ReleaseTags:
    """Release tag -> commit of the gate repository, the annotated ones peeled, by one anonymous `git ls-remote`."""
    with process.scratch_directory() as scratch:
        try:
            listed = git(["ls-remote", "--tags", "--", checkout.GATE_URL], scratch, anonymous_environment(scratch),
                         LS_REMOTE_TIMEOUT_SECONDS)
        except process.ProcessError as error:
            raise ReleaseTagsError(str(error)) from error
    if listed.returncode != 0:
        raise ReleaseTagsError(f"git exited {listed.returncode}")
    tags = checkout.release_tags_listed(listed.stdout)
    if not tags:
        raise ReleaseTagsError("no release tag")
    return tags


def measure_repository(name: str) -> Row:
    with process.scratch_directory() as scratch:
        environment = anonymous_environment(scratch)
        clone = scratch / "clone"
        try:
            cloned = git(["clone", "--depth", "1", "--no-tags", "--quiet", "--", repository_url(name), str(clone)], scratch, environment,
                         CLONE_TIMEOUT_SECONDS)
            # git's own words name the local clone directory, so only its exit code is reported.
            if cloned.returncode != 0:
                return Unjudged(name, f"cannot clone (git exited {cloned.returncode})")
            head = git(["-C", str(clone), "rev-parse", "HEAD"], scratch, environment, CLONE_TIMEOUT_SECONDS)
        except process.ProcessError as error:
            return Unjudged(name, f"cannot clone ({error})")
        if head.returncode != 0:
            return Unjudged(name, f"cannot read the cloned commit (git exited {head.returncode})")
        try:
            tracked = checkout.tracked_files(clone)
            sources = ratchet.read_sources(clone, tracked)
        except report.CannotJudge as error:
            return Unjudged(name, f"cannot measure ({str(error).replace(str(clone), name)})")
        workflows = checkout.read_workflows(clone, tracked)
        declared = read_declared(clone, tracked)
    tree = ratchet.measure(sources)
    pairs = clones.summarise_pairs(clones.find_pairs(sources).pairs)
    commit = head.stdout.strip()[:SHORT_COMMIT]
    languages, areas = ratchet.language_lines(tree), ratchet.summarise(tree)
    return Measured(name, commit, languages, areas, len(tree.unparsable), pairs, workflows, declared)


def read_declared(clone: Path, tracked: list[str]) -> Declared:
    """The clone's gate settings, size baseline, clone table, literal-text table and import-linter config; a file that cannot be read is named."""
    unreadable: set[str] = set()
    settings, has_baseline, has_clone_table, has_literal_table, has_import_config = None, False, False, False, False
    try:
        settings = config.read_gate_settings(clone) if config.CONFIG_PATH in tracked else None
    except config.ConfigError:
        unreadable.add(config.CONFIG_PATH)
    try:
        baseline = baseline_file.read_baseline(clone)
        has_baseline = baseline is not None
        has_clone_table = baseline is not None and baseline.clones is not None
        has_literal_table = baseline is not None and baseline.literal_texts is not None
    except (report.CannotJudge, baseline_file.SizeError):
        unreadable.add(baseline_file.BASELINE_PATH)
    try:
        has_import_config = find_import_config(clone, tracked)
    except (repository.RepositoryFileError, architecture.ContractError):
        unreadable.add(IMPORT_LINTER_CONFIG)
    try:
        declares_catalogs = catalog_rules.declares_catalogs(clone, tracked)
    except report.CannotJudge:
        declares_catalogs = False
        unreadable.add(catalog_rules.PYPROJECT)
    has_judged_project = any(project.packages for project in layout.python_projects(tracked)) or bool(
        layout.frontend_projects(tracked)
    )
    return Declared(
        settings, has_baseline, has_clone_table, has_literal_table, has_import_config, has_judged_project, declares_catalogs,
        frozenset(unreadable),
    )


def find_import_config(clone: Path, tracked: list[str]) -> bool:
    texts = {name: repository.read_text(clone, name) for name in tracked if name in architecture.CONFIG_FILES}
    return architecture.find_config({name: text for name, text in texts.items() if text is not None}) is not None


def git(
    arguments: list[str], scratch: Path, environment: dict[str, str], timeout: float
) -> subprocess.CompletedProcess[str]:
    return process.run([*ANONYMOUS_GIT, *arguments], cwd=scratch, env=environment, timeout=timeout)


def render(rows: list[Row], today: date, tags: checkout.ReleaseTags | ReleaseTagsError) -> tuple[str, ...]:
    heading = f"**Fleet report, {today.isoformat()}, quality {__version__} — {FUNCTION_SCOPE}**"
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


def run_report(
    release_tags: TagReader, fetch_listing: ListingFetch, clock: Callable[[], float], today: date, channels: output.Channels, environment: Mapping[str, str]
) -> int:
    """Measures the fleet `quality/fleet.toml` selects where the door runs: the fleet line and a row on stderr as each
    repository is measured, then the tables on stdout and in the step summary, then the closing line or the pair."""
    started = clock()
    try:
        fleet = read_fleet(Path("."))
        names = select_repositories(fleet, environment.get(LISTING_CREDENTIAL_VARIABLE, ""), fetch_listing)
    except CannotJudge as refusal:
        output.write_pair(refusal.reason.fact, refusal.reason.next_steps, channels.err)
        return EXIT_NOT_MEASURED
    width = max(output.SUBJECT_WIDTH, *map(len, names))
    show = partial(output.write_rows, channel=channels.err, subject_width=width)
    sources = output.SEPARATOR.join((*fleet.organizations, FLEET_PATH))
    show((output.Row(output.Glyph.NOTE, FLEET_SUBJECT, FleetEvent.FOUND, sources, count=len(names), counts_plural=False),))
    tags = _read_tags(release_tags, show)
    pins = release_tags_or_none(tags)
    rows: list[Row] = []
    for name in names:
        began = clock()
        measured = measure_repository(name)
        show((report_row(measured, pins, clock() - began),))
        rows.append(measured)
    tables = render(rows, today, tags)
    output.write_lines(tables, channels.out)
    try:
        output.append_step_summary(tables, environment)
    except OSError as error:
        output.write_pair(f"{output.STEP_SUMMARY_VARIABLE} is not writable{output.SEPARATOR}{error}", (FIX_THEN_RERUN,), channels.err)
        return EXIT_NOT_MEASURED
    problems = report_problems(rows, pins, tags)
    if problems:
        output.write_pair(output.SEPARATOR.join((*problems, "the tables say so")), (Command(REPORT_COMMAND),), channels.err)
        return EXIT_NOT_MEASURED
    whole = f"{len(rows)} of {output.counted(len(rows), 'repository', 'repositories')} measured"
    output.write_closing_line(output.Glyph.CLEAN, (whole, output.format_duration(clock() - started)), channels.err)
    return EXIT_MEASURED


def _read_tags(release_tags: TagReader, show: Callable[[tuple[output.Row, ...]], None]) -> checkout.ReleaseTags | ReleaseTagsError:
    """The gate's release tags, read before any clone so each row's glyph is final when it prints; a failed read is its row."""
    try:
        return release_tags()
    except ReleaseTagsError as error:
        show((output.Row(output.Glyph.COULD_NOT_CHECK, RELEASE_TAGS_SUBJECT, FleetEvent.NOT_READ, str(error), descriptive=True),))
        return error


def unread_files(row: Measured, pins: checkout.ReleaseTags | None) -> tuple[str, ...]:
    """The files the row's matrix cells could not read, each once, in cell order."""
    cells = matrix_cells(row, pins)
    return tuple(dict.fromkeys(file for cell in cells if isinstance(cell, CannotRead) for file in cell.files))


def report_row(row: Row, pins: checkout.ReleaseTags | None, seconds: float) -> output.Row:
    if isinstance(row, Unjudged):
        return output.Row(output.Glyph.COULD_NOT_CHECK, row.name, FleetEvent.NOT_MEASURED, row.reason, descriptive=True)
    unread = unread_files(row, pins)
    if unread:
        return output.Row(output.Glyph.COULD_NOT_CHECK, row.name, FleetEvent.PARTLY_READ, str(CannotRead(unread)), descriptive=True)
    return output.Row(output.Glyph.CLEAN, row.name, FleetEvent.MEASURED, f"{row.commit}{output.SEPARATOR}{output.format_duration(seconds)}")


def report_problems(rows: list[Row], pins: checkout.ReleaseTags | None, tags: checkout.ReleaseTags | ReleaseTagsError) -> list[str]:
    """What keeps the report from being whole, in the pair's order; empty when every repository was measured."""
    not_measured = sum(isinstance(row, Unjudged) for row in rows)
    partly_read = sum(isinstance(row, Measured) and bool(unread_files(row, pins)) for row in rows)
    problems = []
    if not_measured:
        problems.append(f"{not_measured} of {output.counted(len(rows), 'repository', 'repositories')} not measured")
    if partly_read:
        problems.append(f"{partly_read} partly read")
    if isinstance(tags, ReleaseTagsError):
        problems.append("the release tags not read")
    return problems


def words_joined(words: list[str]) -> str:
    return f"{', '.join(words[:-1])} and {words[-1]}" if len(words) > 1 else "".join(words)


def newest_release_words(tags: checkout.ReleaseTags | ReleaseTagsError) -> str:
    if isinstance(tags, ReleaseTagsError):
        return f"newest release unreadable ({tags}), pins shown as commits"
    return f"newest release {newest_release(tags)}"


def newest_release(tags: checkout.ReleaseTags) -> str:
    return max(tags, key=checkout.release_order)


def matrix_cells(row: Row, tags: checkout.ReleaseTags | None) -> tuple[Cell, ...]:
    if not isinstance(row, Measured):
        return (row.name, status_label(row), *("",) * (len(MATRIX_COLUMNS) - 2))
    if row.workflows.unreadable:
        return (row.name, *(CannotRead(row.workflows.unreadable),) * (len(MATRIX_COLUMNS) - 1))
    is_gate = row.name == checkout.GATE_REPOSITORY
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


def gate_release(calls: tuple[checkout.Call, ...], is_gate_repository: bool, tags: checkout.ReleaseTags | None) -> Gate | str:
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
    return min(tagged, key=checkout.release_order) if tagged else Gate.MAIN


def pin_release(ref: str, tags: checkout.ReleaseTags | None) -> Gate | str:
    commit = checkout.commit_of(ref)
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
        if checkout.release_order(release) < checkout.release_order(rule.first_release):
            return f"not in {release}"
    return switch_words(switch_at(rule, release), declared)


def switch_at(rule: RuleRelease, release: Gate | str) -> Switch:
    """The rule's later switch for main and for a tag at or above the change, else its first switch."""
    change = rule.switched
    if change is None:
        return rule.switch
    if release is Gate.MAIN:
        after_change = True
    else:
        after_change = (
            isinstance(release, str) and change.release is not None and checkout.release_order(release) >= checkout.release_order(change.release)
        )
    return change.switch if after_change else rule.switch


def switch_words(switch: Switch, declared: Declared) -> Cell:
    if switch is Switch.ALWAYS:
        return ENFORCED
    if switch is Switch.REPORT_ONLY:
        return REPORT_ONLY
    if switch is Switch.JUDGED_PROJECT:
        return ENFORCED if declared.has_judged_project else NO_JUDGED_PROJECT
    if switch is Switch.CATALOG_DECLARATION:
        if catalog_rules.PYPROJECT in declared.unreadable:
            return CannotRead((catalog_rules.PYPROJECT,))
        return ENFORCED if declared.declares_catalogs else NO_CATALOGS_DECLARATION
    file, present = {
        Switch.SIZE_BASELINE: (baseline_file.BASELINE_PATH, declared.has_baseline),
        Switch.CLONE_TABLE: (baseline_file.BASELINE_PATH, declared.has_clone_table),
        Switch.LITERAL_TABLE: (baseline_file.BASELINE_PATH, declared.has_literal_table),
        Switch.IMPORT_LINTER_CONFIG: (IMPORT_LINTER_CONFIG, declared.has_import_config),
    }[switch]
    if file in declared.unreadable:
        return CannotRead((file,))
    return ENFORCED if present else REPORT_ONLY


def pin_cell(calls: tuple[checkout.Call, ...], absent: str, is_gate_repository: bool, tags: checkout.ReleaseTags | None) -> str:
    """The repository's own file counts only in the gate's repository; a remote ref is named by its release tag."""
    own = [SELF for call in calls if call.ref is None and is_gate_repository]
    pins = [pin_words(call.ref, tags) for call in calls if call.ref is not None]
    return CALLER_SEPARATOR.join(sorted({*own, *pins})) or absent


def pin_words(ref: str, tags: checkout.ReleaseTags | None) -> str:
    commit = checkout.commit_of(ref)
    if commit is None:
        return f"{ref} (not a commit)"
    short = commit[:SHORT_COMMIT]
    if tags is None:
        return short
    tag = released_tag(commit, tags)
    if tag is None:
        return f"{short} (no release)"
    return tag if tag == newest_release(tags) else f"{tag} (behind)"


def released_tag(commit: str, tags: checkout.ReleaseTags) -> str | None:
    """The newest release tag that points at the commit."""
    return max((tag for tag, tagged in tags.items() if tagged == commit), key=checkout.release_order, default=None)


def table_line(values: tuple[Cell, ...]) -> str:
    return "| " + " | ".join(markdown_text(str(value)) for value in values) + " |"


def markdown_text(text: str) -> str:
    """Text a cell shows as written: a backslash before each character markdown reads as syntax, so repository text
    opens no HTML comment, link, emphasis or new cell in the published summary."""
    return "".join(f"\\{character}" if character in MARKDOWN_SYNTAX else character for character in text)


def cells(row: Row) -> tuple[str, ...]:
    if isinstance(row, Measured):
        return measured_cells(row)
    return (row.name, DASH, status_label(row), *("",) * (len(COLUMNS) - 3))


def status_label(row: Unjudged) -> str:
    return f"cannot judge: {row.reason}"


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
