"""The operator's emergency door: switch a repository's gate off, merge one pull request, switch it on again."""

import json
import re
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import Enum, StrEnum
from functools import partial
from pathlib import Path
from urllib.parse import quote

from quality import architecture, process
from quality.config import CONFIG_PATH, ITEM_REFERENCE, REPOSITORY_NAME, Declaration, GateSettings, LegacyMode
from quality.output import SEPARATOR, Channels, Glyph, Row, SubLine, write_pair, write_rows
from quality.ratchet import BASELINE_PATH, NO_RENAMES, Renames
from quality.report import CannotJudge, Command, Instruction, Step, headline, printable
from quality.sonar import (
    Analysis,
    Scope,
    Transport,
    analysed_pull_request_commit,
    basic_authorization,
    default_branch_analysed,
    legacy_count,
    legacy_unknown,
    open_findings,
    project_of,
    require_resolved,
)
from quality.verdict import SUCCESS, Job, Side, Verdict, compares_baselines, judge, read_renames, side_of

EXIT_MERGED = 0
EXIT_REFUSED = 1
EXIT_NOT_RESTORED = 2

GATE_PROPERTY = "quality_gate"
CONFIRMATION_WORD = "ja"
SONAR_CHECK = "quality / sonar"
VERDICT_CHECK = "quality / verdict"
LEGACY_CHECKS = (SONAR_CHECK, VERDICT_CHECK)
RED = "failure"
COMPLETED = "completed"
PENDING_STATUS = "pending"
NOT_FOUND = "404"
CREDENTIAL_HELPER = "!gh auth git-credential"
FETCH_TIMEOUT_SECONDS = 120

GhRunner = Callable[[Sequence[str], str | None], str]
Ask = Callable[[str], str]
RemoteFor = Callable[[str], str]
Say = Callable[[str], None]


class GhFailed(Exception):
    pass


class Refused(Exception):
    """A step refuses: the first of `lines` is the fact, each further line stands whole below the step's row."""

    def __init__(self, *lines: str) -> None:
        super().__init__(*lines)
        self.lines = lines


class MergeMethod(Enum):
    """The methods the door lands with, in its order of preference; the values are the pull request rule's method names
    and the `gh pr merge` flag. A merge commit keeps the judged head as a parent and a squash lands the judged merge
    ref's tree; a rebase replays commits, so the door never uses it."""

    MERGE = "merge"
    SQUASH = "squash"

    @property
    def settings_key(self) -> str:
        return {"merge": "allow_merge_commit", "squash": "allow_squash_merge"}[self.value]

    @property
    def description(self) -> str:
        return {"merge": "merges with a merge commit", "squash": "merges with a squash"}[self.value]

    @property
    def flag(self) -> str:
        return f"--{self.value}"

    @property
    def event(self) -> DoorEvent:
        return {"merge": DoorEvent.MERGE_COMMIT, "squash": DoorEvent.SQUASH}[self.value]


RULE_METHOD_NAMES = ("merge", "squash", "rebase")


class DoorEvent(StrEnum):
    """What a row of the operator's door says."""

    OPEN = "open"
    DRAFT = "draft"
    CLOSED = "closed"
    OK = "ok"
    RED = "red"
    RUNNING = "running"
    REFUSED = "refused"
    DECLARED = "declared"
    NONE = "none"
    LEGACY_FINDING = "legacy finding"
    MERGE_COMMIT = "merge commit"
    SQUASH = "squash"
    NOT_JUDGED = "not judged"


NOTHING_CHANGED = "nothing changed"
NOT_JUDGED_ERROR = f"could not judge{SEPARATOR}{NOTHING_CHANGED}"
REFUSED_ERROR = (
    f"not merged{SEPARATOR}the operator's merge lands only declared loosenings and legacy findings{SEPARATOR}{NOTHING_CHANGED}"
)
LANDING = "landing"

type Pair = tuple[str, tuple[Step, ...]]
type Judged[T] = tuple[T, tuple[Row, ...]]
Show = Callable[[tuple[Row, ...]], None]


class Stopped(Exception):
    """The door stops at `row` with the pair after it; a `✗` row could not judge (exit 2), any other refused (exit 1)."""

    def __init__(self, row: Row, error: str, next_steps: tuple[Step, ...]) -> None:
        super().__init__(error)
        self.row, self.error, self.next_steps = row, error, next_steps

    @property
    def exit_code(self) -> int:
        return EXIT_NOT_RESTORED if self.row.glyph is Glyph.COULD_NOT_CHECK else EXIT_REFUSED


class Outcome(Enum):
    NOT_STARTED = "no merge"
    MERGED = "merge"
    NOT_MERGED = "merge failed"
    UNKNOWN = "merge state unknown"


@dataclass(frozen=True)
class Attempt:
    outcome: Outcome
    detail: str | None
    restore_error: str | None


def run_gh(arguments: Sequence[str], stdin: str | None = None) -> str:
    try:
        done = subprocess.run(["gh", *arguments], input=stdin, capture_output=True, text=True, check=False)  # noqa: S603, S607 - argv is the fixed gh executable plus arguments and never a shell
    except OSError as error:
        raise GhFailed(f"cannot run gh: {error}") from error
    if done.returncode != 0:
        raise GhFailed(f"gh {' '.join(arguments[:3])} failed: {done.stderr.strip()}")
    return done.stdout


@dataclass(frozen=True)
class Check:
    name: str
    status: str
    conclusion: str | None

    @property
    def green(self) -> bool:
        return self.status == COMPLETED and self.conclusion == SUCCESS

    @property
    def pending(self) -> bool:
        return self.status != COMPLETED


def described(declaration: Declaration) -> str:
    return f"declared loosening ({declaration.ruling()}): {declaration.what} - reason: {declaration.reason}"


@dataclass(frozen=True)
class Door:
    """What the door settled before the word: the pull request, its declared loosenings, the legacy count where Sonar
    failed, the method and the landing."""

    repository: str
    pull_request: int
    head_sha: str
    declared: tuple[Declaration, ...]
    legacy: int | None
    merge_method: MergeMethod
    landing: Landing

    @property
    def legacy_count(self) -> list[str]:
        return [] if self.legacy is None else [f"{self.legacy} legacy finding(s)"]

    def reasons(self) -> list[str]:
        """Why the door merges, as the log comment names it."""
        return [*self.legacy_count, *map(described, self.declared)]


def sonar_failed(checks: Sequence[Check]) -> bool:
    """True when a quality / sonar run failed, False when every run is green; a missing or other run shuts the door."""
    runs = [check for check in checks if check.name == SONAR_CHECK]
    if not runs:
        raise Refused(f"{SONAR_CHECK} did not run: the door needs it green or failed")
    neither = [check.conclusion for check in runs if not check.green and check.conclusion != RED]
    if neither:
        raise Refused(f"{SONAR_CHECK} is {neither[0]}: the door needs it green or failed")
    return any(check.conclusion == RED for check in runs)


def github_remote(repository: str) -> str:
    return f"https://github.com/{repository}.git"


def gh_configuration_directory(environment: Mapping[str, str]) -> str:
    """Where gh looks for its login: `GH_CONFIG_DIR`, else `$XDG_CONFIG_HOME/gh`, else `~/.config/gh`."""
    if environment.get("GH_CONFIG_DIR"):
        return environment["GH_CONFIG_DIR"]
    if environment.get("XDG_CONFIG_HOME"):
        return str(Path(environment["XDG_CONFIG_HOME"]) / "gh")
    return str(Path(environment.get("HOME", "~")).expanduser() / ".config" / "gh")


def fetch_environment(environment: Mapping[str, str], empty_home: Path) -> dict[str, str]:
    """The environment of git around the fetch: no inherited git routing or configuration, an empty home so that
    `.netrc` and every other file git reads there is absent, and no way to prompt for a password. gh is the only source
    of credentials, and it keeps its login by being told where the real configuration directory is."""
    isolated = process.attribute_free_git_environment(empty_home)
    kept = {name: value for name, value in isolated.items() if name != "SSH_ASKPASS"}
    return kept | {"GIT_TERMINAL_PROMPT": "0", "GH_CONFIG_DIR": gh_configuration_directory(environment)}


def read_renames_of_pull(remote: str, base_sha: str, merge_sha: str, environment: Mapping[str, str]) -> Renames:
    """The renames the verdict reads at the checkout, from the pull request's two commits fetched into a bare repository
    that is removed afterwards. Credentials stay in gh's credential protocol; git's own words never reach the operator."""
    with process.scratch_directory() as scratch:
        isolated = fetch_environment(environment, scratch / "home")
        repository = scratch / "repository"
        commands = (
            ["git", "init", "--bare", "--quiet", str(repository)],
            [
                "git", "-c", "credential.helper=", "-c", f"credential.helper={CREDENTIAL_HELPER}", "-c", "core.askPass=",
                "-C", str(repository), "fetch", "--quiet", "--depth=1", "--no-tags", "--", remote, base_sha, merge_sha,
            ],
        )
        for command in commands:
            try:
                done = process.run_bounded_in_own_group(command, FETCH_TIMEOUT_SECONDS, isolated)
            except process.ProcessError as error:
                raise CannotJudge(f"cannot fetch the pull request's commits to compare renamed files ({error})") from None
            if done.returncode != 0:
                raise CannotJudge(
                    f"cannot fetch the pull request's commits to compare renamed files (git exited {done.returncode})"
                )
        try:
            return read_renames(repository, base_sha, merge_sha, isolated, bound=FETCH_TIMEOUT_SECONDS)
        except CannotJudge:
            raise CannotJudge("cannot list the files git renamed between the pull request's commits") from None


def rederived_verdict(head: Side, base: Side, check_runs: Sequence[Check], renames: Renames) -> Verdict:
    """The verdict's own decider at the merge commit, with the Sonar job taken as green: the door judges Sonar itself."""
    jobs = [Job(run.name, 1, run.status, SUCCESS if run.name == SONAR_CHECK else run.conclusion) for run in check_runs]
    return judge(head, base, jobs, {"sonar": SUCCESS}, 1, renames)


def refusal_of_legacy_mode(settings: GateSettings) -> str | None:
    if settings.legacy is not LegacyMode.FAIL:
        return f"legacy = {settings.legacy.value} at the pull request's current merge commit: {SONAR_CHECK} cannot be red on legacy findings"
    return None


def read_check_runs(gh: GhRunner, repository: str, head_sha: str) -> list[Check]:
    listed = gh(
        ["api", "--paginate", f"repos/{repository}/commits/{head_sha}/check-runs?per_page=100", "--jq", ".check_runs[]"], None
    )
    return [Check(run["name"], run["status"], run["conclusion"]) for run in map(json.loads, listed.splitlines())]


def read_statuses(gh: GhRunner, repository: str, head_sha: str) -> list[Check]:
    listed = gh(["api", "--paginate", f"repos/{repository}/commits/{head_sha}/status?per_page=100", "--jq", ".statuses[]"], None)
    statuses = map(json.loads, listed.splitlines())
    return [Check(status["context"], "queued" if status["state"] == PENDING_STATUS else COMPLETED, status["state"]) for status in statuses]


def read_side_at(gh: GhRunner, repository: str, sha: str) -> Side:
    """The gate's settings, baseline and import-linter configs at one commit, as the verdict reads them; a 404 reads as absent."""
    config = _read_file_at(gh, repository, CONFIG_PATH, sha)
    baseline = _read_file_at(gh, repository, BASELINE_PATH, sha)
    import_configs = {name: _read_file_at(gh, repository, name, sha) for name in architecture.CONFIG_FILES}
    return side_of(config, baseline, sha, import_configs)


def _read_file_at(gh: GhRunner, repository: str, path: str, sha: str) -> str | None:
    try:
        return gh(["api", "-H", "Accept: application/vnd.github.raw", f"repos/{repository}/contents/{path}?ref={sha}"], None)
    except GhFailed as error:
        if NOT_FOUND in str(error):
            return None
        raise


def require_admin_permission(repository_facts: Mapping[str, object], repository: str) -> None:
    permissions = repository_facts.get("permissions")
    if not (isinstance(permissions, Mapping) and permissions.get("admin") is True):
        raise CannotJudge(f"the gh login has no admin permission on {repository}, which switching {GATE_PROPERTY} needs")


def judge_sonar(
    repository: str,
    pull_request: int,
    head_sha: str,
    default_branch: str,
    transport: Transport,
    sonar_token: str,
) -> int:
    """The legacy count, once the pull request's own scope at this head is proven free of findings."""
    project = project_of({"GITHUB_REPOSITORY": repository})
    pull_scope = Scope("pullRequest", str(pull_request))
    analysis = Analysis(project, pull_scope, "", basic_authorization(sonar_token) if sonar_token else None, default_branch)
    require_resolved(analysis, transport)
    analysed = analysed_pull_request_commit(analysis, transport)
    if analysed != head_sha:
        raise Refused(f"Sonar analysed {analysed or 'nothing'}, head is {head_sha}")
    findings = open_findings(analysis, transport)
    if findings.count:
        raise Refused("the pull request adds findings:", *map(headline, findings.listed))
    if not default_branch_analysed(analysis, transport):
        raise Refused(legacy_unknown(default_branch))
    legacy = legacy_count(analysis, transport)
    if not legacy:
        raise Refused(f"{default_branch} has no legacy finding: the red checks are not legacy findings")
    return legacy


def judge_legacy(settings: GateSettings, count: Callable[[], int]) -> int:
    """The legacy count behind a red Sonar check; under legacy = report Sonar cannot be red on legacy findings, though
    a finding the pull request adds is named first."""
    legacy_refusal = refusal_of_legacy_mode(settings)
    try:
        legacy = count()
    except CannotJudge:
        if legacy_refusal:
            raise Refused(legacy_refusal) from None
        raise
    if legacy_refusal:
        raise Refused(legacy_refusal)
    return legacy


def methods_the_settings_allow(repository_facts: Mapping[str, object]) -> frozenset[MergeMethod]:
    allowed = {method: repository_facts.get(method.settings_key) for method in MergeMethod}
    if not all(isinstance(value, bool) for value in allowed.values()):
        raise CannotJudge("the repository settings withhold or malform which merge methods they allow")
    return frozenset(method for method, value in allowed.items() if value)


def methods_the_rule_allows(rule: object, base: str) -> frozenset[MergeMethod] | None:
    """The methods one branch rule leaves open, None where it restricts none."""
    malformed = CannotJudge(f"the branch rules of {base} are malformed")
    if not isinstance(rule, Mapping) or not isinstance(rule.get("type"), str):
        raise malformed
    if "parameters" in rule and not isinstance(rule["parameters"], Mapping):
        raise malformed
    if rule["type"] == "required_linear_history":
        return frozenset(MergeMethod) - {MergeMethod.MERGE}
    if rule["type"] != "pull_request" or "parameters" not in rule:
        return None
    parameters = rule["parameters"]
    if "allowed_merge_methods" not in parameters:
        return None
    named = parameters["allowed_merge_methods"]
    if not isinstance(named, list) or not all(isinstance(name, str) and name in {*RULE_METHOD_NAMES} for name in named):
        raise malformed
    return frozenset(method for method in MergeMethod if method.value in named)


def read_branch_rules(gh: GhRunner, repository: str, base: str) -> list[object]:
    malformed = CannotJudge(f"the branch rules of {base} are malformed")
    try:
        listed = gh(["api", "--paginate", "--slurp", f"repos/{repository}/rules/branches/{quote(base, safe='/')}?per_page=100"], None)
    except GhFailed as error:
        raise CannotJudge(f"cannot read the branch rules of {base}: {error}") from error
    try:
        pages = json.loads(listed)
    except ValueError as error:
        raise malformed from error
    if not isinstance(pages, list) or not all(isinstance(page, list) for page in pages):
        raise malformed
    return [rule for page in pages for rule in page]


def choose_merge_method(gh: GhRunner, repository: str, repository_facts: Mapping[str, object], base: str) -> MergeMethod:
    """The door's method: a merge commit, else a squash, among those the settings and every rule on `base` allow."""
    allowed = methods_the_settings_allow(repository_facts)
    for rule in read_branch_rules(gh, repository, base):
        left_open = methods_the_rule_allows(rule, base)
        allowed = allowed if left_open is None else allowed & left_open
    chosen = next((method for method in MergeMethod if method in allowed), None)
    if chosen is None:
        raise CannotJudge("the repository and its branch rules allow no merge method the door uses (a merge commit or a squash)")
    return chosen


def set_gate(gh: GhRunner, repository: str, enabled: bool) -> None:
    body = {"properties": [{"property_name": GATE_PROPERTY, "value": str(enabled).lower()}]}
    gh(["api", "-X", "PATCH", f"repos/{repository}/properties/values", "--input", "-"], json.dumps(body))


def merged_on_github(gh: GhRunner, door: Door) -> bool | None:
    """What GitHub says now; None when it cannot be read."""
    try:
        return json.loads(gh(["api", f"repos/{door.repository}/pulls/{door.pull_request}"], None))["merged"] is True
    except (GhFailed, KeyError, ValueError):
        return None


def restore_gate(gh: GhRunner, repository: str, say: Say) -> str | None:
    """The reason the gate could not be switched on, None when it is on."""
    try:
        set_gate(gh, repository, True)
    except GhFailed as error:
        return str(error)
    except KeyboardInterrupt:
        end_interrupted_line(say)
        return "interrupted while switching it on"
    return None


def not_restored_warning(door: Door, attempt: Attempt) -> str:
    """One line for both manual checks, the gate and the pull request, with the merge outcome as far as it is known."""
    return (
        f"emergency-merge: {GATE_PROPERTY} MAY STILL BE OFF for {door.repository} - set it to true by hand now "
        f"({attempt.restore_error}); {merge_outcome(attempt, door.pull_request)}"
    )


def end_interrupted_line(say: Say) -> None:
    """Ctrl-C leaves the cursor behind the terminal's `^C`, so the next line starts on a line of its own."""
    say("")


def switch_off_merge_switch_on(gh: GhRunner, door: Door, say: Say) -> Attempt:
    """A gate that stays off is said at once, before any further request, since gh has no timeout to end one."""
    outcome, detail, interrupted = Outcome.NOT_STARTED, None, False
    try:
        set_gate(gh, door.repository, False)
        outcome = Outcome.UNKNOWN
        gh(
            ["pr", "merge", str(door.pull_request), "--repo", door.repository, door.merge_method.flag, "--match-head-commit", door.head_sha, "--body-file", "-"],
            door.landing.message,
        )
        outcome = Outcome.MERGED
    except (GhFailed, KeyboardInterrupt) as stop:
        interrupted = isinstance(stop, KeyboardInterrupt)
        detail = "interrupted" if interrupted else str(stop)
        if interrupted:
            end_interrupted_line(say)
    finally:
        restore_error = restore_gate(gh, door.repository, say)
        if restore_error:
            say(not_restored_warning(door, Attempt(outcome, detail, restore_error)))
    if outcome is not Outcome.UNKNOWN:
        return Attempt(outcome, detail, restore_error)
    try:
        outcome = settled_by_github(gh, door, interrupted)
    except KeyboardInterrupt:
        end_interrupted_line(say)
        outcome, interrupted_reading_back = Outcome.UNKNOWN, True
    else:
        interrupted_reading_back = False
    attempt = Attempt(outcome, None if outcome is Outcome.MERGED else detail, restore_error)
    if restore_error and (outcome is not Outcome.UNKNOWN or interrupted_reading_back):
        say(not_restored_warning(door, attempt))
    return attempt


def settled_by_github(gh: GhRunner, door: Door, interrupted: bool) -> Outcome:
    """A lost answer or Ctrl-C leaves it open whether GitHub merged; after Ctrl-C a merge not yet shown stays open."""
    landed = merged_on_github(gh, door)
    if landed:
        return Outcome.MERGED
    return Outcome.UNKNOWN if landed is None or interrupted else Outcome.NOT_MERGED


def log_comment(login: str, moment: datetime, door: Door, attempt: Attempt) -> str:
    restored = "on" if attempt.restore_error is None else "NOT restored, it may still be off"
    switched = "not switched off" if attempt.outcome is Outcome.NOT_STARTED else "off"
    return printable(
        f"Emergency door used by @{login} at {moment.isoformat(timespec='seconds')}: {'; '.join(door.reasons())}; {door.merge_method.description}; "
        f"{GATE_PROPERTY} {switched} → {attempt.outcome.value} → {restored}."
    )


def confirmed(ask: Ask, say: Say) -> bool:
    try:
        return ask(f"Switch {GATE_PROPERTY} off, merge, switch it on? Type '{CONFIRMATION_WORD}': ").strip() == CONFIRMATION_WORD
    except (KeyboardInterrupt, EOFError):
        end_interrupted_line(say)
        return False


WORK_ITEM_KEY = "Work-Item"
NO_ITEM_KEY = "No-Item"
NO_ITEM_KINDS = frozenset({"docs", "fix", "deps"})
CLASSIFICATION_LINE = re.compile(rf"(?i)(?P<key>{WORK_ITEM_KEY}|{NO_ITEM_KEY}):(?P<value>[^\n]*)")
FENCE_OPENING = re.compile(r" {0,3}(?P<run>`{3,}|~{3,})")
FENCE_CLOSING = re.compile(r" {0,3}(?P<run>`{3,}|~{3,})[ \t]*")


@dataclass(frozen=True)
class Landing:
    """The merge commit's message: the body without its classification line, then that line as the last paragraph."""

    trailer: str
    message: str


def live_line_indexes(lines: Sequence[str]) -> list[int]:
    """The lines outside fenced code blocks: a fence closes on a run of its own character at least as long, nothing after it."""
    live: list[int] = []
    fence = ""
    for index, line in enumerate(lines):
        if not fence:
            opening = FENCE_OPENING.match(line)
            if opening:
                fence = opening["run"]
            else:
                live.append(index)
        elif (closing := FENCE_CLOSING.fullmatch(line)) and closing["run"][0] == fence[0] and len(closing["run"]) >= len(fence):
            fence = ""
    return live


def trunk_trailer(key: str, value: str, repository: str) -> str:
    """The classification in the trunk's trailer form; any other value keeps the door shut, since a trailer cannot be rewritten."""
    if key.lower() == NO_ITEM_KEY.lower():
        if value.lower() not in NO_ITEM_KINDS:
            raise Refused(f"the pull request body carries {NO_ITEM_KEY}: {value}; an issue-less pull request is {', '.join(sorted(NO_ITEM_KINDS))}")
        return f"{NO_ITEM_KEY}: {value.lower()}"
    if not ITEM_REFERENCE.fullmatch(value):
        raise Refused(f"the pull request body carries {WORK_ITEM_KEY}: {value}; a work item reads OWNER/REPO#n or #n")
    named, _, number = value.rpartition("#")
    if named not in ("", repository):
        raise Refused(f"the pull request body names work item {value} of another repository, not of {repository}")
    return f"{WORK_ITEM_KEY}: #{number}"


def landing_of(body: object, repository: str) -> Landing:
    """The landing of the body judged with the head: exactly one classification line outside code blocks. GitHub answers an empty body as null."""
    if body is None:
        body = ""
    if not isinstance(body, str):
        raise CannotJudge("GitHub's answer for the pull request holds no body text")
    lines = body.splitlines()
    found = [(index, match) for index in live_line_indexes(lines) if (match := CLASSIFICATION_LINE.fullmatch(lines[index]))]
    if len(found) != 1:
        raise Refused(
            f"the pull request body carries {len(found) or 'no'} {WORK_ITEM_KEY}: or {NO_ITEM_KEY}: lines outside code "
            "blocks; the merge commit ends with exactly one"
        )
    index, match = found[0]
    trailer = trunk_trailer(match["key"], match["value"].strip(" \t"), repository)
    kept = [*lines[:index], *lines[index + 1 :]]
    if 0 < index < len(kept) and not kept[index - 1].strip() and not kept[index].strip():
        del kept[index]
    rest = "\n".join(kept).strip()
    return Landing(trailer, f"{rest}\n\n{trailer}" if rest else trailer)


def gh_command(repository: str, pull_request: int, word: str) -> Command:
    return Command(f"gh pr {word} {pull_request} --repo {repository}")


def unanswered(error: GhFailed | KeyError | ValueError) -> str:
    return f"GitHub's answer holds no {error}" if isinstance(error, KeyError) else str(error)


def judged[T](subject: str, step: Callable[[], Judged[T]], refusal: Pair, again: Step, show: Show) -> T:
    """What `step` answers, its rows shown; a refusal stops the door at `subject`'s `!` row, a read not judged at its `✗` row."""
    try:
        value, rows = step()
    except Refused as refused:
        fact, *lines = refused.lines
        below = tuple(SubLine(Glyph.NEEDS_YOU, line, whole=True) for line in lines)
        raise Stopped(Row(Glyph.NEEDS_YOU, subject, DoorEvent.REFUSED, fact, sub_lines=below, descriptive=True), *refusal) from None
    except (CannotJudge, GhFailed, KeyError, ValueError) as error:
        fact = str(error) if isinstance(error, CannotJudge) else unanswered(error)
        raise Stopped(Row(Glyph.COULD_NOT_CHECK, subject, DoorEvent.NOT_JUDGED, fact, descriptive=True), NOT_JUDGED_ERROR, (again,)) from error
    show(rows)
    return value


def pull_request_row(repository: str, pull_request: int, state: object, draft: object, fact: str) -> Row:
    """A closed pull request, then a draft, stops the door before its body or a check is read."""
    subject = f"#{pull_request}"
    if state != "open":
        closed = f"not merged{SEPARATOR}{subject} is closed{SEPARATOR}{NOTHING_CHANGED}"
        raise Stopped(Row(Glyph.NEEDS_YOU, subject, DoorEvent.CLOSED, fact), closed, (gh_command(repository, pull_request, "view"),))
    if not isinstance(draft, bool):
        raise CannotJudge("GitHub's answer for the pull request holds no draft flag")
    if draft:
        drafted = f"not merged{SEPARATOR}a draft does not land{SEPARATOR}{NOTHING_CHANGED}"
        raise Stopped(Row(Glyph.NEEDS_YOU, subject, DoorEvent.DRAFT, fact), drafted, (gh_command(repository, pull_request, "ready"),))
    return Row(Glyph.NOTE, subject, DoorEvent.OPEN, fact)


def read_pull_request(gh: GhRunner, repository: str, pull_request: int) -> Judged[tuple[Mapping[str, object], Mapping[str, object]]]:
    facts = json.loads(gh(["api", f"repos/{repository}"], None))
    require_admin_permission(facts, repository)
    pull = json.loads(gh(["api", f"repos/{repository}/pulls/{pull_request}"], None))
    fact = f"head {pull['head']['sha'][:7]}{SEPARATOR}into {pull['base']['ref']}"
    return (facts, pull), (pull_request_row(repository, pull_request, pull["state"], pull.get("draft"), fact),)


def unsettled_row(checks: Sequence[Check]) -> Row | None:
    """The row of checks still running, else of checks other than the two the door judges that are red."""
    pending = [check.name for check in checks if check.pending]
    if pending:
        return Row(Glyph.NEEDS_YOU, "checks", DoorEvent.RUNNING, ", ".join(pending), descriptive=True)
    other_red = [f"{check.name} ({check.conclusion})" for check in checks if not check.green and check.name not in LEGACY_CHECKS]
    if other_red:
        return Row(Glyph.NEEDS_YOU, "checks", DoorEvent.RED, f"{', '.join(other_red)} beside {VERDICT_CHECK}", descriptive=True)
    return None


def checks_row(checks: Sequence[Check], refused: Pair) -> Row:
    unsettled = unsettled_row(checks)
    if unsettled:
        raise Stopped(unsettled, *refused)
    if all(check.green for check in checks):
        raise Refused("no check is red: the door is not the only way, land the pull request normally")
    red = [name for name in LEGACY_CHECKS if any(check.name == name and check.conclusion == RED for check in checks)]
    if VERDICT_CHECK not in red:
        raise Refused(f"{VERDICT_CHECK} must have failed for the door to be the only way")
    return Row(Glyph.CLEAN, "checks", DoorEvent.OK, f"only {' and '.join(red)} {'is' if len(red) == 1 else 'are'} red")


def read_checks(gh: GhRunner, repository: str, head_sha: str, refused: Pair) -> Judged[tuple[list[Check], bool]]:
    check_runs = read_check_runs(gh, repository, head_sha)
    checks = [*check_runs, *read_statuses(gh, repository, head_sha)]
    row = checks_row(checks, refused)
    return (check_runs, sonar_failed(checks)), (row,)


def declaration_line(declaration: Declaration) -> SubLine:
    return SubLine(Glyph.NEEDS_YOU, f"{declaration.what}{SEPARATOR}{declaration.ruling()}", whole=True, below=(f"reason: {declaration.reason}",))


def loosenings_row(verdict: Verdict, sonar_red: bool, refused: Pair) -> Row:
    """The door merges legacy findings and declared loosenings, nothing else, and with Sonar green it needs a declared one."""
    if verdict.blocking:
        blocking = tuple(SubLine(Glyph.NEEDS_YOU, line, whole=True) for line in verdict.blocking)
        raise Stopped(Row(Glyph.NEEDS_YOU, "loosenings", DoorEvent.RED, sub_lines=blocking), *refused)
    if not sonar_red and not verdict.declared:
        raise Refused(f"{VERDICT_CHECK} is red, but judged again at the merge commit it finds nothing: re-run the verdict")
    if not verdict.declared:
        return Row(Glyph.CLEAN, "loosenings", DoorEvent.NONE)
    lines = tuple(map(declaration_line, verdict.declared))
    return Row(Glyph.NEEDS_YOU, "loosenings", DoorEvent.DECLARED, count=len(lines), counts_plural=False, sub_lines=lines)


def read_loosenings(
    gh: GhRunner, repository: str, pull: Mapping[str, object], check_runs: Sequence[Check], sonar_red: bool,
    environment: Mapping[str, str], remote_for: RemoteFor, refused: Pair,
) -> Judged[tuple[Verdict, GateSettings]]:
    merge_commit_sha, base_sha = pull["merge_commit_sha"], pull["base"]["sha"]
    if not merge_commit_sha:
        raise CannotJudge("GitHub has no merge commit for the pull request yet, so the config the jobs ran on is unknown")
    head, base = read_side_at(gh, repository, merge_commit_sha), read_side_at(gh, repository, base_sha)
    renames = read_renames_of_pull(remote_for(repository), base_sha, merge_commit_sha, environment) if compares_baselines(base, head) else NO_RENAMES
    verdict = rederived_verdict(head, base, check_runs, renames)
    return (verdict, head.settings), (loosenings_row(verdict, sonar_red, refused),)


def read_legacy(count: Callable[[], int], settings: GateSettings, default_branch: str) -> Judged[int]:
    legacy = judge_legacy(settings, count)
    return legacy, (Row(Glyph.NEEDS_YOU, "sonar", DoorEvent.LEGACY_FINDING, f"on {default_branch}", count=legacy),)


def read_method(gh: GhRunner, repository: str, facts: Mapping[str, object], base: str) -> Judged[tuple[MergeMethod, str]]:
    method = choose_merge_method(gh, repository, facts, base)
    login = gh(["api", "user", "--jq", ".login"], None).strip()
    return (method, login), (Row(Glyph.NOTE, "merge", method.event, f"allowed on {base}"),)


def judge_pull_request(
    repository: str,
    pull_request: int,
    gh: GhRunner,
    transport: Transport,
    environment: Mapping[str, str],
    remote_for: RemoteFor,
    show: Show,
) -> tuple[Door, str]:
    """Each step in the door's order, its rows shown as it passes; the first that does not pass stops the door."""
    rerun = f"quality emergency-merge {repository} {pull_request}"
    refused: Pair = (REFUSED_ERROR, (gh_command(repository, pull_request, "checks"),))
    item_line = f"{WORK_ITEM_KEY}: or {NO_ITEM_KEY}: line"
    landing_refused: Pair = (
        f"not merged{SEPARATOR}the operator's merge ends the merge commit with the pull request body's one {item_line}{SEPARATOR}{NOTHING_CHANGED}",
        (Instruction(f"give the pull request body exactly one {item_line} outside code blocks, then run {rerun} again"),),
    )
    step = partial(judged, again=Instruction(f"fix what the ✗ row names, then run {rerun} again"), show=show)
    facts, pull = step(f"#{pull_request}", lambda: read_pull_request(gh, repository, pull_request), refused)
    landing = step(LANDING, lambda: (landing_of(pull["body"], repository), ()), landing_refused)
    check_runs, sonar_red = step("checks", lambda: read_checks(gh, repository, pull["head"]["sha"], refused), refused)
    verdict, settings = step("loosenings", lambda: read_loosenings(gh, repository, pull, check_runs, sonar_red, environment, remote_for, refused), refused)
    legacy = None
    if sonar_red:
        token = environment.get("SONAR_TOKEN", "")

        def sonar_step() -> Judged[int]:
            default_branch = facts["default_branch"]
            count = partial(judge_sonar, repository, pull_request, pull["head"]["sha"], default_branch, transport, token)
            return read_legacy(count, settings, default_branch)

        legacy = step("sonar", sonar_step, refused)
    merge_method, login = step("merge", lambda: read_method(gh, repository, facts, pull["base"]["ref"]), refused)
    return Door(repository, pull_request, pull["head"]["sha"], verdict.declared, legacy, merge_method, landing), login


def merge_outcome(attempt: Attempt, pull_request: int) -> str:
    if attempt.outcome is Outcome.UNKNOWN:
        return f"merge state unknown ({attempt.detail or 'no answer'}) - check #{pull_request} on GitHub"
    if attempt.outcome is Outcome.MERGED:
        return "merged."
    return f"not merged - {attempt.detail}"


def report_attempt(attempt: Attempt, door: Door, say: Say) -> int:
    if attempt.restore_error:
        # Its line was said the moment the restore failed, and again after a failed log comment.
        return EXIT_NOT_RESTORED
    outcome = merge_outcome(attempt, door.pull_request)
    if attempt.outcome is Outcome.MERGED:
        say(outcome)
        return EXIT_MERGED
    say(f"emergency-merge: {outcome}; {GATE_PROPERTY} is on again")
    return EXIT_NOT_RESTORED if attempt.outcome is Outcome.UNKNOWN else EXIT_REFUSED


def run_emergency_merge(
    repository: str,
    pull_request: int,
    *,
    gh: GhRunner,
    transport: Transport,
    ask: Ask,
    is_terminal: bool,
    now: Callable[[], datetime],
    environment: Mapping[str, str],
    say: Say,
    channels: Channels,
    remote_for: RemoteFor = github_remote,
) -> int:
    """Its rows and pairs before the prompt leave through `channels`, every later line through `say`; both escape it."""
    if not REPOSITORY_NAME.fullmatch(repository):
        write_pair(f"not merged{SEPARATOR}{repository!r} is not <owner>/<repo>{SEPARATOR}{NOTHING_CHANGED}", (Instruction("name the repository as <owner>/<repo>"),), channels.err)
        return EXIT_REFUSED
    if not is_terminal:
        write_pair(f"not merged{SEPARATOR}the operator's merge needs a terminal on stdin{SEPARATOR}{NOTHING_CHANGED}", (Instruction("the operator runs it in his own terminal"),), channels.err)
        return EXIT_REFUSED
    try:
        door, login = judge_pull_request(repository, pull_request, gh, transport, environment, remote_for, partial(write_rows, channel=channels.out))
    except Stopped as stop:
        write_rows((stop.row,), channels.out)
        write_pair(stop.error, stop.next_steps, channels.err)
        return stop.exit_code
    say(f"the merge commit ends with {door.landing.trailer}")
    if not confirmed(ask, say):
        say("emergency-merge: not confirmed, nothing was changed")
        return EXIT_REFUSED
    attempt = switch_off_merge_switch_on(gh, door, say)
    try:
        gh(["api", "-X", "POST", f"repos/{repository}/issues/{pull_request}/comments", "-f", f"body={log_comment(login, now(), door, attempt)}"], None)
    except (GhFailed, KeyboardInterrupt) as error:
        if isinstance(error, KeyboardInterrupt):
            end_interrupted_line(say)
        say(f"emergency-merge: the log comment on #{pull_request} was not posted, add it by hand ({error})")
        if attempt.restore_error:
            say(not_restored_warning(door, attempt))
    return report_attempt(attempt, door, say)
