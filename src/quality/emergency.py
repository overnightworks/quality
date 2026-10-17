"""The operator's emergency door: switch a repository's gate off, merge one pull request, switch it on again."""

import json
import re
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from quality.config import CONFIG_PATH, ConfigError, GateSettings, load_gate_settings
from quality.hygiene import CannotJudge
from quality.report import render
from quality.sonar import (
    Analysis,
    Scope,
    Transport,
    analysed_pull_request_commit,
    basic_authorization,
    default_branch_analysed,
    open_findings,
    open_total,
    project_named,
    require_resolved,
)
from quality.verdict import NO_BASE_SETTINGS, SUCCESS, Job, judge

EXIT_MERGED = 0
EXIT_REFUSED = 1
EXIT_NOT_RESTORED = 2

REPOSITORY_NAME = re.compile(r"[\w.-]+/[\w.-]+")
GATE_PROPERTY = "quality_gate"
CONFIRMATION_WORD = "ja"
SONAR_CHECK = "quality / sonar"
VERDICT_CHECK = "quality / verdict"
LEGACY_CHECKS = (SONAR_CHECK, VERDICT_CHECK)
RED = "failure"
COMPLETED = "completed"
PENDING_STATUS = "pending"
NOT_FOUND = "404"

GhRunner = Callable[[Sequence[str], str | None], str]
Ask = Callable[[str], str]


class GhFailed(Exception):
    pass


class Refused(Exception):
    pass


class Outcome(Enum):
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
        done = subprocess.run(["gh", *arguments], input=stdin, capture_output=True, text=True, check=False)
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


@dataclass(frozen=True)
class Door:
    """What the operator is shown before typing the word."""

    repository: str
    pull_request: int
    head_sha: str
    legacy: int


def refusal_of_checks(checks: Sequence[Check]) -> str | None:
    pending = [check.name for check in checks if check.pending]
    if pending:
        return f"checks still running: {', '.join(pending)}"
    other_red = [f"{check.name} ({check.conclusion})" for check in checks if not check.green and check.name not in LEGACY_CHECKS]
    if other_red:
        return f"checks other than {' and '.join(LEGACY_CHECKS)} are red: {', '.join(other_red)}"
    if all(check.green for check in checks):
        return "no check is red: the door is not the only way, land the pull request normally"
    failed = {check.name for check in checks if check.conclusion == RED}
    missing = [name for name in LEGACY_CHECKS if name not in failed]
    if missing:
        return f"{' and '.join(missing)} must have failed for the door to be the only way"
    return None


def refusal_of_verdict(settings: GateSettings, base_settings: GateSettings, check_runs: Sequence[Check]) -> str | None:
    """Judged by the verdict's own decider: the verdict must be red for the Sonar gate alone."""
    jobs = [Job(run.name, 1, run.status, SUCCESS if run.name == SONAR_CHECK else run.conclusion) for run in check_runs]
    problems = judge(settings, base_settings, jobs, {"sonar": SUCCESS}, 1).problems
    if problems:
        return f"{VERDICT_CHECK} is red for more than legacy findings: {'; '.join(problems)}"
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


def read_gate_settings_at(gh: GhRunner, repository: str, sha: str) -> GateSettings:
    try:
        text = gh(["api", "-H", "Accept: application/vnd.github.raw", f"repos/{repository}/contents/{CONFIG_PATH}?ref={sha}"], None)
    except GhFailed as error:
        if NOT_FOUND in str(error):
            return NO_BASE_SETTINGS
        raise
    try:
        return load_gate_settings(text)
    except ConfigError as error:
        raise CannotJudge(f"{CONFIG_PATH} at {sha}: {error}") from error


def require_admin_permission(repository_facts: Mapping[str, object], repository: str) -> None:
    permissions = repository_facts.get("permissions")
    if not (isinstance(permissions, Mapping) and permissions.get("admin") is True):
        raise CannotJudge(f"the gh login has no admin permission on {repository}, which switching {GATE_PROPERTY} needs")


def judge_sonar(
    repository: str, pull_request: int, head_sha: str, default_branch: str, transport: Transport, sonar_token: str
) -> int:
    """The legacy count, once the pull request's own scope at this head is proven free of findings."""
    project = project_named(repository)
    pull_scope = Scope("pullRequest", str(pull_request))
    analysis = Analysis(project, pull_scope, "", basic_authorization(sonar_token), default_branch)
    require_resolved(analysis, transport)
    analysed = analysed_pull_request_commit(analysis, transport)
    if analysed != head_sha:
        raise Refused(f"Sonar analysed {analysed or 'nothing'}, head is {head_sha}")
    findings = open_findings(analysis, transport)
    if findings.count:
        raise Refused("the pull request adds findings:\n" + "\n".join(render(finding) for finding in findings.listed))
    if not default_branch_analysed(analysis, transport):
        raise Refused(f"legacy unknown ({default_branch} not analysed yet)")
    legacy = open_total(analysis, Scope("branch", default_branch), transport)
    if not legacy:
        raise Refused(f"{default_branch} has no open finding: the red checks are not legacy findings")
    return legacy


def set_gate(gh: GhRunner, repository: str, enabled: bool) -> None:
    body = {"properties": [{"property_name": GATE_PROPERTY, "value": str(enabled).lower()}]}
    gh(["api", "-X", "PATCH", f"repos/{repository}/properties/values", "--input", "-"], json.dumps(body))


def merged_on_github(gh: GhRunner, door: Door) -> bool | None:
    """What GitHub says now; None when it cannot be read."""
    try:
        return json.loads(gh(["api", f"repos/{door.repository}/pulls/{door.pull_request}"], None))["merged"] is True
    except (GhFailed, KeyError, ValueError):
        return None


def restore_gate(gh: GhRunner, repository: str) -> str | None:
    """The reason the gate could not be switched on, None when it is on."""
    try:
        set_gate(gh, repository, True)
    except GhFailed as error:
        return str(error)
    except KeyboardInterrupt:
        return "interrupted while switching it on"
    return None


def switch_off_merge_switch_on(gh: GhRunner, door: Door) -> Attempt:
    outcome, detail, merge_started = Outcome.MERGED, None, False
    try:
        set_gate(gh, door.repository, False)
        merge_started = True
        gh(
            ["pr", "merge", str(door.pull_request), "--repo", door.repository, "--merge", "--match-head-commit", door.head_sha],
            None,
        )
    except GhFailed as failure:
        outcome, detail = Outcome.NOT_MERGED, str(failure)
    except KeyboardInterrupt:
        outcome, detail = Outcome.NOT_MERGED, "interrupted"
    restore_error = restore_gate(gh, door.repository)
    if merge_started and outcome is not Outcome.MERGED:
        # A lost answer or Ctrl-C leaves it open whether GitHub merged.
        landed = merged_on_github(gh, door)
        if landed:
            outcome, detail = Outcome.MERGED, None
        elif landed is None or detail == "interrupted":
            outcome = Outcome.UNKNOWN
    return Attempt(outcome, detail, restore_error)


def log_comment(login: str, moment: datetime, door: Door, attempt: Attempt) -> str:
    restored = "on" if attempt.restore_error is None else "NOT restored, it may still be off"
    return (
        f"Emergency door used by @{login} at {moment.isoformat(timespec='seconds')}: {door.legacy} legacy finding(s); "
        f"{GATE_PROPERTY} off → {attempt.outcome.value} → {restored}."
    )


def confirmed(ask: Ask) -> bool:
    try:
        return ask(f"Switch {GATE_PROPERTY} off, merge, switch it on? Type '{CONFIRMATION_WORD}': ").strip() == CONFIRMATION_WORD
    except (KeyboardInterrupt, EOFError):
        return False


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
    report: Callable[[str], None] = print,
) -> int:
    if not REPOSITORY_NAME.fullmatch(repository):
        report(f"emergency-merge: {repository!r} is not <owner>/<repo>")
        return EXIT_REFUSED
    if not is_terminal:
        report("emergency-merge: refused - this is the operator's command, it needs a terminal on stdin")
        return EXIT_REFUSED
    try:
        repository_facts = json.loads(gh(["api", f"repos/{repository}"], None))
        require_admin_permission(repository_facts, repository)
        pull = json.loads(gh(["api", f"repos/{repository}/pulls/{pull_request}"], None))
        if pull["state"] != "open":
            raise Refused(f"the pull request is {pull['state']}")
        head_sha = pull["head"]["sha"]
        check_runs = read_check_runs(gh, repository, head_sha)
        refusal = refusal_of_checks([*check_runs, *read_statuses(gh, repository, head_sha)])
        if refusal:
            raise Refused(refusal)
        refusal = refusal_of_verdict(
            read_gate_settings_at(gh, repository, head_sha), read_gate_settings_at(gh, repository, pull["base"]["sha"]), check_runs
        )
        if refusal:
            raise Refused(refusal)
        legacy = judge_sonar(
            repository, pull_request, head_sha, repository_facts["default_branch"], transport, environment.get("SONAR_TOKEN", "")
        )
        login = gh(["api", "user", "--jq", ".login"], None).strip()
    except Refused as refusal_error:
        report(f"emergency-merge: refused - {refusal_error}")
        return EXIT_REFUSED
    except (CannotJudge, GhFailed, KeyError, ValueError) as error:
        report(f"emergency-merge: cannot judge - {error}; nothing was changed")
        return EXIT_NOT_RESTORED
    door = Door(repository, pull_request, head_sha, legacy)
    report(f"{door.repository} #{door.pull_request} head {door.head_sha}: {door.legacy} legacy finding(s)")
    if not confirmed(ask):
        report("emergency-merge: not confirmed, nothing was changed")
        return EXIT_REFUSED
    attempt = switch_off_merge_switch_on(gh, door)
    try:
        gh(["api", "-X", "POST", f"repos/{repository}/issues/{pull_request}/comments", "-f", f"body={log_comment(login, now(), door, attempt)}"], None)
    except GhFailed as error:
        report(f"emergency-merge: the log comment on #{pull_request} was not posted, add it by hand ({error})")
    if attempt.restore_error:
        report(f"emergency-merge: {GATE_PROPERTY} MAY STILL BE OFF for {repository} - set it to true by hand now ({attempt.restore_error})")
        return EXIT_NOT_RESTORED
    if attempt.outcome is Outcome.UNKNOWN:
        report(f"emergency-merge: merge state unknown ({attempt.detail or 'no answer'}) - check #{pull_request} on GitHub; {GATE_PROPERTY} is on again")
        return EXIT_NOT_RESTORED
    if attempt.outcome is Outcome.NOT_MERGED:
        report(f"emergency-merge: not merged - {attempt.detail}; {GATE_PROPERTY} is on again")
        return EXIT_REFUSED
    report(f"merged. For the head: aco release <n> --merged {pull_request}")
    return EXIT_MERGED
