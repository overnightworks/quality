"""The operator's emergency door: switch a repository's gate off, merge one pull request, switch it on again."""

import json
import re
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime

from quality.hygiene import CannotJudge
from quality.report import render
from quality.sonar import (
    Analysis,
    Project,
    Scope,
    Transport,
    basic_authorization,
    default_branch_analysed,
    open_findings,
    open_total,
    require_resolved,
)

EXIT_MERGED = 0
EXIT_REFUSED = 1
EXIT_NOT_RESTORED = 2

REPOSITORY_NAME = re.compile(r"[\w.-]+/[\w.-]+")
REQUIRED_SCOPE = "admin:org"
GATE_PROPERTY = "quality_gate"
CONFIRMATION_WORD = "ja"
LEGACY_CHECKS = frozenset({"quality / sonar", "quality / verdict"})
GREEN_CONCLUSIONS = frozenset({"success", "skipped", "neutral"})
COMPLETED = "completed"

GhRunner = Callable[[Sequence[str], str | None], str]
Ask = Callable[[str], str]


class GhFailed(Exception):
    pass


class Refused(Exception):
    pass


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
        return self.status == COMPLETED and self.conclusion in GREEN_CONCLUSIONS

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
        return f"checks other than {' and '.join(sorted(LEGACY_CHECKS))} are red: {', '.join(other_red)}"
    if all(check.green for check in checks):
        return "no check is red: the door is not the only way, land the pull request normally"
    return None


def read_checks(gh: GhRunner, repository: str, head_sha: str) -> list[Check]:
    listed = gh(
        ["api", "--paginate", f"repos/{repository}/commits/{head_sha}/check-runs?per_page=100", "--jq", ".check_runs[]"], None
    )
    return [Check(run["name"], run["status"], run["conclusion"]) for run in map(json.loads, listed.splitlines())]


def require_admin_scope(gh: GhRunner) -> None:
    headers = gh(["api", "--include", "user"], None).partition("\n\n")[0]
    scopes = [
        value.strip()
        for line in headers.splitlines()
        for name, _, value in [line.partition(":")]
        if name.lower() == "x-oauth-scopes"
    ]
    if REQUIRED_SCOPE not in ",".join(scopes).replace(" ", "").split(","):
        raise CannotJudge(f"the gh login lacks the {REQUIRED_SCOPE} scope that switching {GATE_PROPERTY} needs")


def judge_sonar(repository: str, pull_request: int, default_branch: str, transport: Transport, sonar_token: str) -> int:
    """The legacy count, once the pull request's own scope is proven free of findings."""
    owner, _, name = repository.partition("/")
    authorization = basic_authorization(sonar_token)
    pull_scope = Scope("pullRequest", str(pull_request))
    analysis = Analysis(Project(owner, f"{owner}_{name}"), pull_scope, "", authorization, default_branch)
    require_resolved(analysis, transport)
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


def switch_off_merge_switch_on(gh: GhRunner, door: Door) -> tuple[bool, str | None]:
    """Returns (merged, error of the merge); a restore that fails raises GhFailed after the merge was tried."""
    merged, error = False, None
    try:
        set_gate(gh, door.repository, False)
        gh(
            ["pr", "merge", str(door.pull_request), "--repo", door.repository, "--merge", "--match-head-commit", door.head_sha],
            None,
        )
        merged = True
    except GhFailed as failure:
        error = str(failure)
    except KeyboardInterrupt:
        error = "interrupted; check whether the pull request was merged"
    finally:
        set_gate(gh, door.repository, True)
    return merged, error


def log_comment(login: str, moment: datetime, door: Door, merged: bool) -> str:
    outcome = "merge" if merged else "merge failed"
    return (
        f"Emergency door used by @{login} at {moment.isoformat(timespec='seconds')}: {door.legacy} legacy finding(s); "
        f"{GATE_PROPERTY} off → {outcome} → on."
    )


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
        require_admin_scope(gh)
        pull = json.loads(gh(["api", f"repos/{repository}/pulls/{pull_request}"], None))
        if pull["state"] != "open":
            raise Refused(f"the pull request is {pull['state']}")
        head_sha = pull["head"]["sha"]
        refusal = refusal_of_checks(read_checks(gh, repository, head_sha))
        if refusal:
            raise Refused(refusal)
        default_branch = json.loads(gh(["api", f"repos/{repository}"], None))["default_branch"]
        legacy = judge_sonar(repository, pull_request, default_branch, transport, environment.get("SONAR_TOKEN", ""))
        login = gh(["api", "user", "--jq", ".login"], None).strip()
    except Refused as refusal_error:
        report(f"emergency-merge: refused - {refusal_error}")
        return EXIT_REFUSED
    except (CannotJudge, GhFailed, KeyError, ValueError) as error:
        report(f"emergency-merge: cannot judge - {error}; nothing was changed")
        return EXIT_NOT_RESTORED
    door = Door(repository, pull_request, head_sha, legacy)
    report(f"{door.repository} #{door.pull_request} head {door.head_sha}: {door.legacy} legacy finding(s)")
    if ask(f"Switch {GATE_PROPERTY} off, merge, switch it on? Type '{CONFIRMATION_WORD}': ").strip() != CONFIRMATION_WORD:
        report("emergency-merge: not confirmed, nothing was changed")
        return EXIT_REFUSED
    try:
        merged, merge_error = switch_off_merge_switch_on(gh, door)
    except GhFailed as error:
        report(f"emergency-merge: {GATE_PROPERTY} MAY STILL BE OFF for {repository} - set it to true by hand now ({error})")
        return EXIT_NOT_RESTORED
    try:
        gh(["api", "-X", "POST", f"repos/{repository}/issues/{pull_request}/comments", "-f", f"body={log_comment(login, now(), door, merged)}"], None)
    except GhFailed as error:
        report(f"emergency-merge: the log comment on #{pull_request} was not posted, add it by hand ({error})")
    if not merged:
        report(f"emergency-merge: not merged - {merge_error}; {GATE_PROPERTY} is on again")
        return EXIT_REFUSED
    report(f"merged. For the head: aco release <n> --merged {pull_request}")
    return EXIT_MERGED
