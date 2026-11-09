"""The SonarCloud job's step after the scan, `quality sonar findings`, and what it shares with the step before it
(`quality sonar properties`, in `sonar_properties`): the token, the project, the rows' events and the refusal pair; each
step prints its rows and one closing line, or the pair."""

import base64
import json
import urllib.error
import urllib.parse
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from quality import output
from quality.config import ConfigError, LegacyMode, read_gate_settings
from quality.report import (
    FIX_THEN_RERUN,
    PUSH_THE_FIX,
    CannotJudge,
    Finding,
    FindingEvent,
    Instruction,
    Note,
    NoteEvent,
    Reason,
    config_refusal,
)
from quality.web import REQUEST_TIMEOUT_SECONDS, Sleep, fetch, timed_out

SONAR_ORIGIN = "https://sonarcloud.io"
SONAR_API_ROOT = f"{SONAR_ORIGIN}/api"
RULE = "sonar"
EXIT_CLEAN, EXIT_FINDINGS, EXIT_CANNOT_JUDGE = 0, 1, 2

# 5 s x 120 stays below the job timeout the workflow gives this step.
TASK_POLL_INTERVAL_SECONDS = 5
TASK_POLL_ATTEMPTS = 120
FINISHED_TASK_STATUS = "SUCCESS"
ABANDONED_TASK_STATUSES = ("FAILED", "CANCELED")
GATE_PASSED = "OK"
GATE_FAILED = "ERROR"
# SonarCloud's comparator names for a failed condition, and how the gate's demand reads.
COMPARATOR_DEMANDS = {"LT": "at least", "GT": "at most"}
IGNORED_CONDITIONS = "coverage and duplication not judged: fewer than 20 new lines"

FINDINGS_PAGE_SIZE = 100
# `issues/search` refuses a page past its ten-thousandth issue, so paging is bounded by that limit
# and never by the total an answer claims.
FINDINGS_SEARCH_LIMIT = 10_000
MAXIMUM_FINDINGS_PAGES = FINDINGS_SEARCH_LIMIT // FINDINGS_PAGE_SIZE
# `issueStatuses`, never the legacy `statuses`, whose vocabulary drops an issue a person accepted
# or marked false positive in the SonarCloud interface.
OPEN_ISSUE_STATUSES = "OPEN,CONFIRMED,ACCEPTED,FALSE_POSITIVE"

PULL_REQUEST_EVENT = "pull_request"
TOKEN_VARIABLE = "SONAR_TOKEN"  # noqa: S105 - the name of an environment variable and not a secret
TOKEN_STEP = Instruction(f"set the {TOKEN_VARIABLE} secret and pass it to gate.yml, then run the gate again")
# gate.yml passes github.event.repository.default_branch; an absent expression renders as an empty string.
DEFAULT_BRANCH_VARIABLE = "DEFAULT_BRANCH"

SCANNER_WORK_DIRECTORY = ".scannerwork"
# The scanner writes it here, relative to the working directory; no caller chooses another path.
TASK_FILE = Path(SCANNER_WORK_DIRECTORY, "report-task.txt")

# The second argument is the Authorization header value; None sends the request anonymously.
Transport = Callable[[str, str | None], object]
TOKEN_REFUSALS = frozenset({401, 403})


class SonarEvent(StrEnum):
    """What a row of the two Sonar steps says."""

    CLEAN = "clean"
    EXCLUDES = "excludes"
    OFF = "off"
    NONE = "none"
    FINDING = "finding"
    OPEN = "open"
    UNKNOWN = "unknown"
    PASSED = "passed"
    FAILED = "failed"


class SonarHttpError(CannotJudge):
    def __init__(self, status: int, url: str) -> None:
        super().__init__(f"SonarCloud answered HTTP {status} for {url.partition('?')[0]}")
        self.status = status


def fetch_json(url: str, authorization: str | None, timeout: float = REQUEST_TIMEOUT_SECONDS) -> object:
    def unreadable(error: Exception) -> CannotJudge:
        where = url.partition("?")[0]
        if isinstance(error, urllib.error.HTTPError):
            return SonarHttpError(error.code, url)
        if isinstance(error, OSError):
            if timed_out(error):
                return CannotJudge(f"SonarCloud did not answer {where} within {timeout}s")
            return CannotJudge(f"SonarCloud could not be reached at {where}: {error}")
        return CannotJudge(f"SonarCloud answered {where} with a body that is not JSON")

    headers = {} if authorization is None else {"Authorization": authorization}
    body, _ = fetch(url, headers, unreadable, timeout)
    try:
        return json.loads(body)
    except ValueError as error:  # JSONDecodeError and UnicodeDecodeError
        raise unreadable(error) from error


@dataclass(frozen=True)
class Scope:
    """The single branch or pull request analysis this run asks about."""

    parameter: str
    value: str

    @property
    def label(self) -> str:
        return f"{self.parameter}={self.value}"

    @property
    def is_pull_request(self) -> bool:
        return self.parameter == "pullRequest"

    def is_branch(self, name: str) -> bool:
        return self.parameter == "branch" and self.value == name


@dataclass(frozen=True)
class Project:
    owner: str
    key: str


class RunKind(StrEnum):
    """What the run is: the one answer to which findings fail it and whether legacy can be judged."""

    PULL_REQUEST = "pull request"
    DEFAULT_BRANCH = "default branch"
    OTHER_BRANCH = "other branch"

    @property
    def judges_legacy(self) -> bool:
        return self is not RunKind.OTHER_BRANCH


@dataclass(frozen=True)
class Analysis:
    project: Project
    scope: Scope
    task_id: str
    authorization: str | None
    default_branch: str

    @property
    def kind(self) -> RunKind:
        if self.scope.is_pull_request:
            return RunKind.PULL_REQUEST
        return RunKind.DEFAULT_BRANCH if self.scope.is_branch(self.default_branch) else RunKind.OTHER_BRANCH


@dataclass(frozen=True)
class OpenFindings:
    total: int
    listed: tuple[Finding, ...]

    @property
    def count(self) -> int:
        # A total the same answer contradicts by listing findings is not a zero.
        return max(self.total, len(self.listed))


def legacy_unknown(default_branch: str) -> str:
    return f"legacy unknown ({default_branch} not analysed yet)"


@dataclass(frozen=True)
class GateCondition:
    metric: str
    comparator: str
    actual: str
    threshold: str

    @property
    def sentence(self) -> str:
        return f"{self.metric} is {self.actual}, the gate needs {COMPARATOR_DEMANDS[self.comparator]} {self.threshold}"


@dataclass(frozen=True)
class GateStatus:
    """What SonarCloud's quality gate answered for the run's own analysis; only `OK` and `ERROR` are a judgement."""

    failed: tuple[GateCondition, ...]
    conditions_ignored: bool

    @property
    def passed(self) -> bool:
        return not self.failed


@dataclass(frozen=True)
class SonarResult:
    """`legacy` is the default branch's open count, asked for on pull requests only."""

    analysis: Analysis
    findings: OpenFindings
    legacy_mode: LegacyMode
    legacy_unknown: bool
    legacy: int | None
    gate: GateStatus | None = None

    @property
    def fails_the_run(self) -> bool:
        if self.analysis.kind is RunKind.PULL_REQUEST:
            return (
                self.findings.count > 0
                or (self.legacy_mode is LegacyMode.FAIL and bool(self.legacy))
                or (self.gate is not None and not self.gate.passed)
            )
        return self.analysis.kind is RunKind.DEFAULT_BRANCH and self.findings.count > 0


def require_token(environment: Mapping[str, str]) -> None:
    if not environment.get(TOKEN_VARIABLE):
        raise CannotJudge(f"{TOKEN_VARIABLE} is empty: an anonymous reader is told zero findings", (TOKEN_STEP,))


def analyse(
    environment: Mapping[str, str],
    legacy_mode: LegacyMode,
    transport: Transport,
    sleep: Sleep,
) -> SonarResult:
    """The local configuration and destination checks run before the token is sent anywhere."""
    analysis = read_analysis(environment)
    analysis_id = wait_for_own_task(analysis, transport, sleep)
    require_resolved(analysis, transport)
    kind = analysis.kind
    gate = quality_gate_status(analysis, analysis_id, transport) if kind is RunKind.PULL_REQUEST else None
    default_analysed = kind.judges_legacy and default_branch_analysed(analysis, transport)
    unknown = kind.judges_legacy and not default_analysed
    if unknown and legacy_mode is LegacyMode.FAIL:
        raise CannotJudge(legacy_unknown(analysis.default_branch))
    findings = open_findings(analysis, transport)
    legacy = None
    if kind is RunKind.PULL_REQUEST and default_analysed:
        legacy = legacy_count(analysis, transport)
    return SonarResult(analysis, findings, legacy_mode, unknown, legacy, gate)


def run_findings(
    environment: Mapping[str, str], transport: Transport, sleep: Sleep, clock: Callable[[], float], channels: output.Channels
) -> int:
    """Runs after the scan: this run's own analysis, its findings, the legacy count and the quality gate as rows, then
    the closing line; a refusal is the pair."""
    started = clock()
    try:
        result = analyse(environment, _legacy_mode(), transport, sleep)
    except CannotJudge as refusal:
        return refused(refusal.reason, FIX_THEN_RERUN, channels)
    write_result(result, channels.out)
    glyph, parts = closing(result)
    output.write_closing_line(glyph, (*parts, output.format_duration(clock() - started)), channels.out)
    return EXIT_FINDINGS if result.fails_the_run else EXIT_CLEAN


def refused(reason: Reason, general: Instruction, channels: output.Channels) -> int:
    """The pair; a refusal that names no step of its own gets its door's general one."""
    output.write_pair(reason.fact, reason.next_steps or (general,), channels.err)
    return EXIT_CANNOT_JUDGE


def _legacy_mode() -> LegacyMode:
    try:
        return read_gate_settings(Path(".")).legacy
    except ConfigError as error:
        raise config_refusal(error, PUSH_THE_FIX) from error


def write_result(result: SonarResult, channel: output.Channel) -> None:
    """The analysis row and the findings row with what it lists, then the legacy and quality gate rows that apply."""
    output.write_rows((_analysis_row(result.analysis), _findings_row(result)), channel)
    listed = result.findings.listed
    if result.analysis.kind is RunKind.OTHER_BRANCH:
        output.write_findings_and_notes((), [Note(finding.path, NoteEvent.OPEN, finding.fact, finding.line) for finding in listed], channel)
    else:
        output.write_findings_and_notes(listed, (), channel)
    output.write_rows((*_legacy_rows(result), *_gate_rows(result.gate)), channel)


def _analysis_row(analysis: Analysis) -> output.Row:
    scope = analysis.scope
    where = f"#{scope.value}" if scope.is_pull_request else scope.value
    return output.Row(output.Glyph.NOTE, "analysis", analysis.kind, f"{where}{output.SEPARATOR}{analysis.project.key}")


def _findings_row(result: SonarResult) -> output.Row:
    """`new` on a pull request, `open` on a branch; another branch's findings are reported, never asked of anyone."""
    kind, count = result.analysis.kind, result.findings.count
    subject = "new" if kind is RunKind.PULL_REQUEST else "open"
    if not count:
        return output.Row(output.Glyph.CLEAN, subject, SonarEvent.NONE)
    if kind is RunKind.OTHER_BRANCH:
        return output.Row(output.Glyph.NOTE, subject, SonarEvent.FINDING, "report only", count=count)
    return output.Row(output.Glyph.NEEDS_YOU, subject, SonarEvent.FINDING, count=count)


def _legacy_rows(result: SonarResult) -> list[output.Row]:
    default_branch = result.analysis.default_branch
    if result.legacy_unknown:
        return [output.Row(output.Glyph.NOTE, "legacy", SonarEvent.UNKNOWN, f"{default_branch} not analysed yet")]
    if result.legacy is None:
        return []
    fails = result.legacy_mode is LegacyMode.FAIL and result.legacy > 0
    setting = f'{output.SEPARATOR}[gate] legacy = "{LegacyMode.FAIL.value}"' if fails else ""
    glyph = output.Glyph.NEEDS_YOU if fails else output.Glyph.NOTE
    return [output.Row(glyph, "legacy", SonarEvent.OPEN, f"on {default_branch}{setting}", count=result.legacy, counts_plural=False)]


def _gate_rows(gate: GateStatus | None) -> list[output.Row]:
    if gate is None:
        return []
    ignored = (IGNORED_CONDITIONS,) if gate.conditions_ignored else ()
    if gate.passed:
        return [output.Row(output.Glyph.CLEAN, "quality gate", SonarEvent.PASSED, output.SEPARATOR.join(ignored))]
    conditions = tuple(output.SubLine(output.Glyph.NEEDS_YOU, condition.sentence, whole=True) for condition in gate.failed)
    fact = output.SEPARATOR.join((output.counted(len(gate.failed), "condition"), *ignored))
    return [output.Row(output.Glyph.NEEDS_YOU, "quality gate", SonarEvent.FAILED, fact, sub_lines=conditions)]


def closing(result: SonarResult) -> tuple[output.Glyph, tuple[str, ...]]:
    """The closing line's glyph and its parts before the duration."""
    kind, count, branch = result.analysis.kind, result.findings.count, result.analysis.scope.value
    if kind is RunKind.OTHER_BRANCH:
        return output.Glyph.CLEAN, ("report only", f"{count} open on {branch}")
    glyph, word = (output.Glyph.NEEDS_YOU, "red") if result.fails_the_run else (output.Glyph.CLEAN, "clean")
    unknown = ("legacy unknown",) if result.legacy_unknown else ()
    if kind is RunKind.DEFAULT_BRANCH:
        return glyph, (word, f"{count} open on {branch}", *unknown)
    gate_failed = ("quality gate failed",) if result.gate is not None and not result.gate.passed else ()
    return glyph, (word, f"new {count}", *(unknown or (f"legacy {result.legacy}",)), *gate_failed)


def read_analysis(environment: Mapping[str, str]) -> Analysis:
    project = project_of(environment)
    settings = read_task_file()
    if settings.get("projectKey") != project.key:
        raise CannotJudge(f"{TASK_FILE.name} names project {settings.get('projectKey')!r}, not {project.key!r}")
    if settings.get("organization") != project.owner:
        raise CannotJudge(f"{TASK_FILE.name} names organization {settings.get('organization')!r}, not {project.owner!r}")
    if settings.get("serverUrl") != SONAR_ORIGIN:
        raise CannotJudge(f"{TASK_FILE.name} names server {settings.get('serverUrl')!r}, not {SONAR_ORIGIN}")
    if not settings.get("ceTaskId"):
        raise CannotJudge(f"{TASK_FILE.name} names no ceTaskId")
    require_token(environment)
    default_branch = environment.get(DEFAULT_BRANCH_VARIABLE, "")
    if not default_branch:
        raise CannotJudge(f"{DEFAULT_BRANCH_VARIABLE} is empty: the gate passes github.event.repository.default_branch")
    return Analysis(
        project, scope_of(environment), settings["ceTaskId"], basic_authorization(environment[TOKEN_VARIABLE]), default_branch
    )


def project_of(environment: Mapping[str, str]) -> Project:
    owner, separator, repository = environment.get("GITHUB_REPOSITORY", "").partition("/")
    if not (owner and separator and repository):
        raise CannotJudge("GITHUB_REPOSITORY is not <owner>/<repo>")
    return Project(owner, f"{owner}_{repository}")


def scope_of(environment: Mapping[str, str]) -> Scope:
    try:
        if environment["GITHUB_EVENT_NAME"] == PULL_REQUEST_EVENT:
            return Scope("pullRequest", environment["PULL_REQUEST_NUMBER"])
        return Scope("branch", environment["GITHUB_REF_NAME"])
    except KeyError as error:
        raise CannotJudge(f"{error.args[0]} is not set") from error


def read_task_file() -> dict[str, str]:
    try:
        lines = TASK_FILE.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as error:
        raise CannotJudge(f"cannot read {TASK_FILE}: {error.__class__.__name__}") from error
    pairs = (line.partition("=") for line in lines)
    return {name.strip(): value.strip() for name, separator, value in pairs if separator}


def basic_authorization(token: str) -> str:
    # The token is the basic-auth user name with an empty password, and it travels in a header so no
    # command line, process listing or URL can carry it.
    return "Basic " + base64.b64encode(f"{token}:".encode()).decode()


def api_url(endpoint: str, **parameters: object) -> str:
    # A ref name may legally contain characters a query string reads differently.
    return f"{SONAR_API_ROOT}/{endpoint}?{urllib.parse.urlencode(parameters)}"


def wait_for_own_task(analysis: Analysis, transport: Transport, sleep: Sleep) -> object:
    """The finished task's `analysisId`, as the task answered it."""
    url = api_url("ce/task", id=analysis.task_id)
    status = "unknown"
    for attempt in range(TASK_POLL_ATTEMPTS):
        if attempt:
            sleep(TASK_POLL_INTERVAL_SECONDS)
        task = answer_object(transport(url, analysis.authorization), "task", url)
        status = str(task.get("status"))
        if status == FINISHED_TASK_STATUS:
            require_task_binds(task, analysis)
            return task.get("analysisId")
        if status in ABANDONED_TASK_STATUSES:
            raise CannotJudge(f"SonarCloud analysis task ended {status}")
    raise CannotJudge(f"SonarCloud analysis task still {status} after {TASK_POLL_ATTEMPTS} checks")


def require_task_binds(task: dict, analysis: Analysis) -> None:
    scope = analysis.scope
    bound = {
        "componentKey": analysis.project.key,
        "organization": analysis.project.owner,
        scope.parameter: scope.value,
    }
    for field, expected in bound.items():
        actual = task.get(field)
        if actual is None or str(actual) != expected:
            raise CannotJudge(f"SonarCloud task belongs to another analysis: {field} is {actual!r}, expected {expected!r}")


def require_resolved(analysis: Analysis, transport: Transport) -> None:
    # An unauthenticated, misspelled or never-analysed scope answers the issue search with zero
    # findings and no error, so read the measures of this exact scope first: it is 404 when the
    # credential, the key or the scope does not resolve.
    url = api_url("measures/component", component=analysis.project.key, metricKeys="ncloc", **scope_query(analysis.scope))
    try:
        transport(url, analysis.authorization)
    except SonarHttpError as error:
        where = f"{analysis.project.key} in {analysis.scope.label}"
        if analysis.authorization is None:
            raise CannotJudge(
                f"SonarCloud did not resolve {where} anonymously (HTTP {error.status}); a private project needs {TOKEN_VARIABLE}"
            ) from error
        if error.status in TOKEN_REFUSALS:
            raise CannotJudge(f"SonarCloud rejected the token for {where} (HTTP {error.status})") from error
        raise CannotJudge(f"SonarCloud did not resolve {where} (HTTP {error.status})") from error


def scope_query(scope: Scope) -> dict[str, str]:
    return {scope.parameter: scope.value}


def default_branch_analysed(analysis: Analysis, transport: Transport) -> bool:
    """False when the default branch was never analysed; refuses one that SonarCloud does not treat as its main branch."""
    url = api_url("project_branches/list", project=analysis.project.key)
    answer = answer_object(transport(url, analysis.authorization), None, url)
    try:
        branches = [branch for branch in answer["branches"] if branch["name"] == analysis.default_branch]
    except (KeyError, TypeError) as error:
        raise CannotJudge(f"SonarCloud answered {url.partition('?')[0]} with an unreadable body") from error
    if not branches:
        return False
    if branches[0].get("isMain") is not True:
        # A non-main branch counts only its changed code, so a legacy count would be silently too low.
        raise CannotJudge(f"{analysis.default_branch} is not SonarCloud's main branch: set SonarCloud's main branch to {analysis.default_branch}")
    return bool(branches[0].get("analysisDate"))


def issues_search_url(analysis: Analysis, scope: Scope, page: int, page_size: int) -> str:
    return api_url(
        "issues/search",
        componentKeys=analysis.project.key,
        issueStatuses=OPEN_ISSUE_STATUSES,
        ps=page_size,
        p=page,
        **scope_query(scope),
    )


def legacy_count(analysis: Analysis, transport: Transport) -> int:
    """The default branch's open total."""
    return open_total(analysis, Scope("branch", analysis.default_branch), transport)


def open_total(analysis: Analysis, scope: Scope, transport: Transport) -> int:
    """The answer's total alone: findings counted, never listed."""
    url = issues_search_url(analysis, scope, 1, 1)
    unreadable = CannotJudge(f"SonarCloud answered {url.partition('?')[0]} with an unreadable body")
    try:
        total = answer_object(transport(url, analysis.authorization), None, url)["total"]
    except KeyError as error:
        raise unreadable from error
    if type(total) is not int or total < 0:
        raise unreadable
    return total


def open_findings(analysis: Analysis, transport: Transport) -> OpenFindings:
    listed: list[Finding] = []
    fetched = 0
    total = 0
    for page in range(1, MAXIMUM_FINDINGS_PAGES + 1):
        url = issues_search_url(analysis, analysis.scope, page, FINDINGS_PAGE_SIZE)
        report = answer_object(transport(url, analysis.authorization), None, url)
        try:
            total = int(report["total"])
            issues = report["issues"]
            if not isinstance(issues, list):
                raise CannotJudge(f"SonarCloud answered {url.partition('?')[0]} with issues that are not a list")
            listed.extend(map(finding_from_issue, issues))
        except (KeyError, TypeError, ValueError) as error:
            raise CannotJudge(f"SonarCloud answered {url.partition('?')[0]} with an unreadable body") from error
        fetched += len(issues)
        if not issues or fetched >= total:
            break
    return OpenFindings(max(0, total), tuple(listed))


def analysed_pull_request_commit(analysis: Analysis, transport: Transport) -> str | None:
    """The commit of the latest analysis SonarCloud holds for the analysis's pull request, None when it has none."""
    url = api_url("project_pull_requests/list", project=analysis.project.key)
    answer = answer_object(transport(url, analysis.authorization), None, url)
    try:
        for pull_request in answer["pullRequests"]:
            if str(pull_request["key"]) == analysis.scope.value:
                return pull_request["commit"]["sha"]
    except (KeyError, TypeError) as error:
        raise CannotJudge(f"SonarCloud answered {url.partition('?')[0]} with an unreadable body") from error
    return None


def quality_gate_status(analysis: Analysis, analysis_id: object, transport: Transport) -> GateStatus:
    """The quality gate of this run's own analysis; the threshold arrives with SonarCloud's answer."""
    if not isinstance(analysis_id, str) or not analysis_id:
        raise CannotJudge(f"SonarCloud task {analysis.task_id} names no analysisId, so its quality gate cannot be read")
    url = api_url("qualitygates/project_status", analysisId=analysis_id)
    status = answer_object(transport(url, analysis.authorization), "projectStatus", url)
    try:
        word = status["status"]
        if word == GATE_PASSED:
            return GateStatus((), status.get("ignoredConditions") is True)
        if word != GATE_FAILED:
            raise CannotJudge(f"SonarCloud's quality gate is {word!r}, which is neither {GATE_PASSED} nor {GATE_FAILED}")
        failed = tuple(failed_condition(condition) for condition in status["conditions"] if condition["status"] == GATE_FAILED)
    except (KeyError, TypeError) as error:
        raise CannotJudge(f"SonarCloud answered {url.partition('?')[0]} with an unreadable body") from error
    if not failed:
        raise CannotJudge(f"SonarCloud's quality gate is {GATE_FAILED} without a failed condition")
    return GateStatus(failed, status.get("ignoredConditions") is True)


def failed_condition(condition: dict) -> GateCondition:
    if condition["comparator"] not in COMPARATOR_DEMANDS:
        raise CannotJudge(f"SonarCloud's quality gate failed {condition['metricKey']} with the comparator {condition['comparator']!r}")
    return GateCondition(condition["metricKey"], condition["comparator"], condition["actualValue"], condition["errorThreshold"])


def answer_object(answer: object, key: str | None, url: str) -> dict:
    inner = answer.get(key) if key and isinstance(answer, dict) else answer
    if not isinstance(inner, dict):
        raise CannotJudge(f"SonarCloud answered {url.partition('?')[0]} with an unexpected shape")
    return inner


def finding_from_issue(issue: dict) -> Finding:
    if not isinstance(issue["component"], str):
        raise CannotJudge(f"SonarCloud reported a finding whose component {issue['component']!r} is not a string")
    _, separator, file_path = issue["component"].partition(":")
    if not separator:
        raise CannotJudge(f"SonarCloud reported a finding whose component {issue['component']!r} carries no file path")
    return Finding(
        rule=RULE,
        path=file_path,
        line=issue.get("line"),
        fact=f"{issue['rule']}: {issue['message']}",
        fix="fix it in the code; an accepted or false-positive mark in SonarCloud still counts",
        event=FindingEvent.OPEN,
    )
