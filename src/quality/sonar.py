"""The SonarCloud zero-findings rule: wait for this run's own analysis, then count its open findings."""

import base64
import json
import re
import urllib.error
import urllib.parse
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path

from quality import repository
from quality.checkout import tracked_files
from quality.config import LegacyMode
from quality.report import CannotJudge, Finding
from quality.web import REQUEST_TIMEOUT_SECONDS, Sleep, fetch, timed_out

SONAR_ORIGIN = "https://sonarcloud.io"
SONAR_API_ROOT = f"{SONAR_ORIGIN}/api"
RULE = "sonar"

# 5 s x 120 stays below the job timeout the workflow gives this step.
TASK_POLL_INTERVAL_SECONDS = 5
TASK_POLL_ATTEMPTS = 120
FINISHED_TASK_STATUS = "SUCCESS"
ABANDONED_TASK_STATUSES = ("FAILED", "CANCELED")
GATE_PASSED = "OK"
GATE_FAILED = "ERROR"
# SonarCloud's comparator names for a failed condition, and how the gate's demand reads.
COMPARATOR_DEMANDS = {"LT": "at least", "GT": "at most"}
IGNORED_CONDITIONS_LINE = (
    f"{RULE}: quality gate ignored its coverage and duplication conditions on a small change (fewer than 20 new lines)"
)

FINDINGS_PAGE_SIZE = 100
# `issues/search` refuses a page past its ten-thousandth issue, so paging is bounded by that limit
# and never by the total an answer claims.
FINDINGS_SEARCH_LIMIT = 10_000
MAXIMUM_FINDINGS_PAGES = FINDINGS_SEARCH_LIMIT // FINDINGS_PAGE_SIZE
# `issueStatuses`, never the legacy `statuses`, whose vocabulary drops an issue a person accepted
# or marked false positive in the SonarCloud interface.
OPEN_ISSUE_STATUSES = "OPEN,CONFIRMED,ACCEPTED,FALSE_POSITIVE"

# Sonar itself drops this rule on a branch analysis when the bot config exists, by existence alone; its pull-request
# analysis cannot see an unchanged file and keeps the finding, so the gate applies the same test everywhere.
DEPENDABOT_CONFIG = ".github/dependabot.yml"
DEPENDABOT_EXCEPTION_RULE = "docker:S8431"
SET_ASIDE_REASON = f" (does not count: {DEPENDABOT_CONFIG} exists, Sonar's own exception, which its pull-request analysis misses)"

PULL_REQUEST_EVENT = "pull_request"
TOKEN_VARIABLE = "SONAR_TOKEN"  # noqa: S105 - the name of an environment variable and not a secret
# gate.yml passes github.event.repository.default_branch; an absent expression renders as an empty string.
DEFAULT_BRANCH_VARIABLE = "DEFAULT_BRANCH"

PROPERTIES_FILE = "sonar-project.properties"
SCANNER_WORK_DIRECTORY = ".scannerwork"
# The scanner writes it here, relative to the working directory; no caller chooses another path.
TASK_FILE = Path(SCANNER_WORK_DIRECTORY, "report-task.txt")
COVERAGE_REPORT_KEYS = ("sonar.python.coverage.reportPaths", "sonar.javascript.lcov.reportPaths")
GLOB_CHARACTERS = "*?"
# Ant-style `dir/**` means the files beneath; pathlib's `dir/**` yields directories only.
ANT_TRAILING_ANY = "**"
# Grows only through a quality release. Anything that can steer the scanner's endpoint, proxy, working
# directory or issue handling stays out, because the token travels with every authenticated request.
ALLOWED_PROPERTIES = frozenset(
    {
        "sonar.organization",
        "sonar.projectKey",
        "sonar.projectName",
        "sonar.sources",
        "sonar.tests",
        "sonar.inclusions",
        "sonar.exclusions",
        "sonar.test.inclusions",
        "sonar.test.exclusions",
        "sonar.coverage.exclusions",
        "sonar.cpd.exclusions",
        "sonar.sourceEncoding",
        "sonar.python.version",
        *COVERAGE_REPORT_KEYS,
    }
)
# The scanner expands ${env.NAME} and ${sonar.token} in any value.
SUBSTITUTION_MARKER = "${"
PROPERTY_WHITESPACE = " \t\f"
# Not str.splitlines(): java.util.Properties breaks lines on these three alone.
PROPERTY_LINE_BREAK = re.compile(r"\r\n|\r|\n")
PROPERTY_ESCAPES = {"t": "\t", "n": "\n", "r": "\r", "f": "\f"}

# The second argument is the Authorization header value; None sends the request anonymously.
Transport = Callable[[str, str | None], object]
TOKEN_REFUSALS = frozenset({401, 403})


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


class RunKind(Enum):
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
    set_aside: tuple[Finding, ...] = ()

    @property
    def count(self) -> int:
        # A total the same answer contradicts by listing findings is not a zero.
        return max(self.total, len(self.listed))


def uncounted_rules(dependabot_config_exists: bool) -> frozenset[str]:
    """The rules whose findings the gate prints but does not count; a rule's file path is no input."""
    return frozenset({DEPENDABOT_EXCEPTION_RULE}) if dependabot_config_exists else frozenset()


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
        return f"{RULE}: quality gate {GATE_FAILED} - {self.metric} is {self.actual}, the gate needs {COMPARATOR_DEMANDS[self.comparator]} {self.threshold}"


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
    """`legacy` is the default branch's open count less the rules its own run sets aside, asked for on pull requests only."""

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

    def gate_lines(self) -> tuple[tuple[str, ...], tuple[str, ...]]:
        """The failed conditions (for stderr) and the small-change disclosure (for stdout)."""
        if self.gate is None:
            return (), ()
        disclosure = (IGNORED_CONDITIONS_LINE,) if self.gate.conditions_ignored else ()
        return tuple(condition.sentence for condition in self.gate.failed), disclosure

    def summary(self) -> str:
        count = self.findings.count
        scope, key, default_branch = self.analysis.scope, self.analysis.project.key, self.analysis.default_branch
        if self.analysis.kind is RunKind.PULL_REQUEST:
            if self.legacy_unknown:
                return f"{RULE}: new {count} · {legacy_unknown(default_branch)}"
            return f"{RULE}: new {count} · legacy {self.legacy} ({key}, {default_branch})"
        if count == 0:
            line = f"{RULE}: clean for {key!r} ({scope.label}): 0 open finding(s)"
        elif self.fails_the_run:
            line = f"{RULE}: {count} open on {scope.value}"
        else:
            line = f"{RULE}: {count} open on {scope.value} (report only)"
        return f"{line} · {legacy_unknown(default_branch)}" if self.legacy_unknown else line


def check_properties(root: Path, environment: Mapping[str, str]) -> None:
    """Bind the scan to the caller's own project before the token is handed to the scanner."""
    require_token(environment)
    project = project_of(environment)
    if any(Path(name).parts[0] == SCANNER_WORK_DIRECTORY for name in tracked_files(root)):
        raise CannotJudge(f"{SCANNER_WORK_DIRECTORY}/ is tracked: a committed {TASK_FILE.name} would stand in for this run's analysis")
    properties = read_properties(root)
    for key, value in properties:
        if key not in ALLOWED_PROPERTIES:
            raise CannotJudge(f"{PROPERTIES_FILE} sets {key!r}, which is not allowed")
        if SUBSTITUTION_MARKER in value:
            raise CannotJudge(f"{PROPERTIES_FILE} sets {key!r} to a value with a {SUBSTITUTION_MARKER}...}} substitution")
    chosen = dict(properties)
    for key, expected in (("sonar.organization", project.owner), ("sonar.projectKey", project.key)):
        if chosen.get(key) != expected:
            raise CannotJudge(f"{PROPERTIES_FILE} sets {key!r} to {chosen.get(key)!r}, not {expected!r}")
    for key in COVERAGE_REPORT_KEYS:
        for entry in report_entries(chosen.get(key, "")):
            require_report_exists(root, key, entry)


def report_entries(value: str) -> list[str]:
    return [entry for entry in (part.strip() for part in value.split(",")) if entry]


def require_report_exists(root: Path, key: str, entry: str) -> None:
    """A named report that is absent when the scan starts would be a silent 0.0% coverage."""
    path = Path(entry)
    if path.is_absolute() or ".." in path.parts:
        raise CannotJudge(f"{PROPERTIES_FILE} sets {key!r} to {entry!r}, which lies outside the checkout")
    if any(character in entry for character in GLOB_CHARACTERS):
        if any(ANT_TRAILING_ANY in part and part != ANT_TRAILING_ANY for part in path.parts):
            raise CannotJudge(f"{PROPERTIES_FILE} sets {key!r} to {entry!r}, where {ANT_TRAILING_ANY!r} is not a whole path segment")
        pattern = f"{entry}/*" if path.name == ANT_TRAILING_ANY else entry
        found = any(match.is_file() for match in root.glob(pattern))
    else:
        found = (root / path).is_file()
    if not found:
        raise CannotJudge(f"{PROPERTIES_FILE} sets {key!r} to {entry!r}, but no such report file exists (did the tests job upload it, and does the caller pass coverage-artifact?)")


def require_token(environment: Mapping[str, str]) -> None:
    if not environment.get(TOKEN_VARIABLE):
        raise CannotJudge(f"{TOKEN_VARIABLE} is empty: an anonymous reader is told zero findings")


def read_properties(root: Path) -> list[tuple[str, str]]:
    """The java.util.Properties grammar: separators, continuations and escapes in keys and values."""
    try:
        text = repository.read_text(root, PROPERTIES_FILE)
    except repository.RepositoryFileError as error:
        raise CannotJudge(str(error)) from error
    if text is None:
        raise CannotJudge(f"{PROPERTIES_FILE} is missing")
    return [parse_property(line) for line in logical_property_lines(text)]


def logical_property_lines(text: str) -> list[str]:
    logical: list[str] = []
    pending: str | None = None
    for physical in PROPERTY_LINE_BREAK.split(text):
        line = physical.lstrip(PROPERTY_WHITESPACE)
        if pending is None and (not line or line[0] in "#!"):
            continue
        joined = (pending or "") + line
        trailing_backslashes = len(joined) - len(joined.rstrip("\\"))
        pending = joined[:-1] if trailing_backslashes % 2 else None
        if pending is None:
            logical.append(joined)
    if pending is not None:
        logical.append(pending)  # java.util.Properties drops a final unpaired backslash
    return logical


def parse_property(line: str) -> tuple[str, str]:
    key, position = decode_property_text(line, 0, stop_at=PROPERTY_WHITESPACE + "=:")
    position = skip_property_whitespace(line, position)
    if line[position : position + 1] in ("=", ":"):
        position = skip_property_whitespace(line, position + 1)
    value, _ = decode_property_text(line, position)
    return key, value


def skip_property_whitespace(line: str, position: int) -> int:
    while line[position : position + 1] and line[position] in PROPERTY_WHITESPACE:
        position += 1
    return position


def decode_property_text(line: str, position: int, stop_at: str = "") -> tuple[str, int]:
    decoded: list[str] = []
    while position < len(line) and line[position] not in stop_at:
        character = line[position]
        position += 1
        if character != "\\":
            decoded.append(character)
            continue
        escaped = line[position]
        position += 1
        if escaped == "u":
            digits = line[position : position + 4]
            if len(digits) < 4 or any(digit not in "0123456789abcdefABCDEF" for digit in digits):
                raise CannotJudge(f"{PROPERTIES_FILE} has a malformed unicode escape in {line!r}")
            decoded.append(chr(int(digits, 16)))
            position += 4
        else:
            decoded.append(PROPERTY_ESCAPES.get(escaped, escaped))
    return "".join(decoded), position


def analyse(
    environment: Mapping[str, str],
    legacy_mode: LegacyMode,
    uncounted: frozenset[str],
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
    findings = open_findings(analysis, transport, uncounted)
    legacy = None
    if kind is RunKind.PULL_REQUEST and default_analysed:
        legacy = legacy_count(analysis, uncounted, transport)
    return SonarResult(analysis, findings, legacy_mode, unknown, legacy, gate)


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


def issues_search_url(analysis: Analysis, scope: Scope, page: int, page_size: int, rule: str | None = None) -> str:
    rule_filter = {} if rule is None else {"rules": rule}
    return api_url(
        "issues/search",
        componentKeys=analysis.project.key,
        issueStatuses=OPEN_ISSUE_STATUSES,
        ps=page_size,
        p=page,
        **rule_filter,
        **scope_query(scope),
    )


def legacy_count(analysis: Analysis, uncounted: frozenset[str], transport: Transport) -> int:
    """The default branch's open total less the findings of the rules its own run sets aside."""
    scope = Scope("branch", analysis.default_branch)
    total = open_total(analysis, scope, transport)
    for rule in sorted(uncounted):
        set_aside = open_total(analysis, scope, transport, rule)
        if set_aside > total:
            raise CannotJudge(f"SonarCloud counts {set_aside} open {rule} findings on {analysis.default_branch} but only {total} open findings in all")
        total -= set_aside
    return total


def open_total(analysis: Analysis, scope: Scope, transport: Transport, rule: str | None = None) -> int:
    """The answer's total alone: findings counted, never listed."""
    url = issues_search_url(analysis, scope, 1, 1, rule)
    unreadable = CannotJudge(f"SonarCloud answered {url.partition('?')[0]} with an unreadable body")
    try:
        total = answer_object(transport(url, analysis.authorization), None, url)["total"]
    except (KeyError, TypeError) as error:
        raise unreadable from error
    if type(total) is not int or total < 0:
        raise unreadable
    return total


def open_findings(analysis: Analysis, transport: Transport, uncounted: frozenset[str] = frozenset()) -> OpenFindings:
    listed: list[Finding] = []
    set_aside: list[Finding] = []
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
            for issue in issues:
                finding = finding_from_issue(issue)
                if issue["rule"] in uncounted:
                    set_aside.append(replace(finding, reason=finding.reason + SET_ASIDE_REASON))
                else:
                    listed.append(finding)
        except (KeyError, TypeError, ValueError) as error:
            raise CannotJudge(f"SonarCloud answered {url.partition('?')[0]} with an unreadable body") from error
        fetched += len(issues)
        if not issues or fetched >= total:
            break
    return OpenFindings(max(0, total - len(set_aside)), tuple(listed), tuple(set_aside))


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
        reason=f"{issue['rule']}: {issue['message']}",
        fix="Fix it in the code; an accepted or false-positive mark in SonarCloud still counts",
    )
