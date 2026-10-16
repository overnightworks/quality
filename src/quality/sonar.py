"""The SonarCloud zero-findings rule: wait for this run's own analysis, then count its open findings."""

import base64
import http.client
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from quality.hygiene import CannotJudge, tracked_files
from quality.report import Finding

SONAR_ORIGIN = "https://sonarcloud.io"
SONAR_API_ROOT = f"{SONAR_ORIGIN}/api"
RULE = "sonar"

# A SonarCloud that accepts the connection and then stops answering must not hang the job.
REQUEST_TIMEOUT_SECONDS = 30
# 5 s x 120 stays below the job timeout the workflow gives this step.
TASK_POLL_INTERVAL_SECONDS = 5
TASK_POLL_ATTEMPTS = 120
FINISHED_TASK_STATUS = "SUCCESS"
ABANDONED_TASK_STATUSES = ("FAILED", "CANCELED")

FINDINGS_PAGE_SIZE = 100
# `issues/search` refuses a page past its ten-thousandth issue, so paging is bounded by that limit
# and never by the total an answer claims.
FINDINGS_SEARCH_LIMIT = 10_000
MAXIMUM_FINDINGS_PAGES = FINDINGS_SEARCH_LIMIT // FINDINGS_PAGE_SIZE
# `issueStatuses`, never the legacy `statuses`, whose vocabulary drops an issue a person accepted
# or marked false positive in the SonarCloud interface.
OPEN_ISSUE_STATUSES = "OPEN,CONFIRMED,ACCEPTED,FALSE_POSITIVE"

PULL_REQUEST_EVENT = "pull_request"
TOKEN_VARIABLE = "SONAR_TOKEN"

PROPERTIES_FILE = "sonar-project.properties"
SCANNER_WORK_DIRECTORY = ".scannerwork"
# The scanner writes it here, relative to the working directory; no caller chooses another path.
TASK_FILE = Path(SCANNER_WORK_DIRECTORY, "report-task.txt")
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
        "sonar.python.coverage.reportPaths",
        "sonar.javascript.lcov.reportPaths",
    }
)
# The scanner expands ${env.NAME} and ${sonar.token} in any value.
SUBSTITUTION_MARKER = "${"
PROPERTY_WHITESPACE = " \t\f"
# Not str.splitlines(): java.util.Properties breaks lines on these three alone.
PROPERTY_LINE_BREAK = re.compile(r"\r\n|\r|\n")
PROPERTY_ESCAPES = {"t": "\t", "n": "\n", "r": "\r", "f": "\f"}

Transport = Callable[[str, str], object]
Sleep = Callable[[float], None]


class SonarHttpError(CannotJudge):
    def __init__(self, status: int, url: str) -> None:
        super().__init__(f"SonarCloud answered HTTP {status} for {url.partition('?')[0]}")
        self.status = status


class RedirectRefusingHandler(urllib.request.HTTPRedirectHandler):
    # A redirect would forward the Authorization header to another host; refuse it instead.
    def redirect_request(self, *args, **kwargs) -> None:
        return None


def fetch_json(url: str, authorization: str, timeout: float = REQUEST_TIMEOUT_SECONDS) -> object:
    opener = urllib.request.build_opener(RedirectRefusingHandler())
    request = urllib.request.Request(url, headers={"Authorization": authorization})
    where = url.partition("?")[0]
    try:
        with opener.open(request, timeout=timeout) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        raise SonarHttpError(error.code, url) from error
    except OSError as error:
        if timed_out(error):
            raise CannotJudge(f"SonarCloud did not answer {where} within {timeout}s") from error
        raise CannotJudge(f"SonarCloud could not be reached at {where}: {error}") from error
    except (http.client.HTTPException, ValueError) as error:
        # JSONDecodeError and UnicodeDecodeError are ValueErrors; a truncated body is an HTTPException.
        raise CannotJudge(f"SonarCloud answered {where} with a body that is not JSON") from error


def timed_out(error: OSError) -> bool:
    # A read that runs out of time raises TimeoutError itself; a connect that does arrives in URLError.
    return isinstance(error, TimeoutError) or (
        isinstance(error, urllib.error.URLError) and isinstance(error.reason, TimeoutError)
    )


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


@dataclass(frozen=True)
class Project:
    owner: str
    key: str


@dataclass(frozen=True)
class Analysis:
    project: Project
    scope: Scope
    task_id: str
    authorization: str


@dataclass(frozen=True)
class OpenFindings:
    total: int
    listed: tuple[Finding, ...]

    @property
    def count(self) -> int:
        # A total the same answer contradicts by listing findings is not a zero.
        return max(self.total, len(self.listed))


@dataclass(frozen=True)
class SonarResult:
    scope: Scope
    project: Project
    findings: OpenFindings

    @property
    def fails_the_run(self) -> bool:
        return self.scope.is_pull_request and self.findings.count > 0

    def summary(self) -> str:
        count = self.findings.count
        if count == 0:
            return f"{RULE}: clean for {self.project.key!r} ({self.scope.label}): 0 open finding(s)"
        if self.scope.is_pull_request:
            return f"{RULE}: {count} open finding(s) in {self.scope.label}"
        return f"{RULE}: {count} open on {self.scope.value} (report only until baseline)"


def preflight(root: Path, environment: Mapping[str, str]) -> None:
    """Bind the scan to the caller's own project before the token is handed to the scanner."""
    require_token(environment)
    project = project_of(environment)
    if any(Path(name).parts[0] == SCANNER_WORK_DIRECTORY for name in tracked_files(root)):
        raise CannotJudge(f"{SCANNER_WORK_DIRECTORY}/ is tracked: a committed {TASK_FILE.name} would stand in for this run's analysis")
    properties = read_properties(root / PROPERTIES_FILE)
    for key, value in properties:
        if key not in ALLOWED_PROPERTIES:
            raise CannotJudge(f"{PROPERTIES_FILE} sets {key!r}, which is not allowed")
        if SUBSTITUTION_MARKER in value:
            raise CannotJudge(f"{PROPERTIES_FILE} sets {key!r} to a value with a {SUBSTITUTION_MARKER}...}} substitution")
    chosen = dict(properties)
    for key, expected in (("sonar.organization", project.owner), ("sonar.projectKey", project.key)):
        if chosen.get(key) != expected:
            raise CannotJudge(f"{PROPERTIES_FILE} sets {key!r} to {chosen.get(key)!r}, not {expected!r}")


def require_token(environment: Mapping[str, str]) -> None:
    if not environment.get(TOKEN_VARIABLE):
        raise CannotJudge(f"{TOKEN_VARIABLE} is empty: an anonymous reader is told zero findings")


def read_properties(path: Path) -> list[tuple[str, str]]:
    """The java.util.Properties grammar: separators, continuations and escapes in keys and values."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise CannotJudge(f"cannot read {path.name}: {error.__class__.__name__}") from error
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
    environment: Mapping[str, str], transport: Transport = fetch_json, sleep: Sleep = time.sleep
) -> SonarResult:
    """Every check that can refuse runs before the token is sent anywhere."""
    analysis = read_analysis(environment)
    wait_for_own_task(analysis, transport, sleep)
    require_resolved(analysis, transport)
    return SonarResult(analysis.scope, analysis.project, open_findings(analysis, transport))


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
    return Analysis(project, scope_of(environment), settings["ceTaskId"], basic_authorization(environment[TOKEN_VARIABLE]))


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


def wait_for_own_task(analysis: Analysis, transport: Transport, sleep: Sleep) -> None:
    url = api_url("ce/task", id=analysis.task_id)
    status = "unknown"
    for attempt in range(TASK_POLL_ATTEMPTS):
        if attempt:
            sleep(TASK_POLL_INTERVAL_SECONDS)
        task = answer_object(transport(url, analysis.authorization), "task", url)
        status = str(task.get("status"))
        if status == FINISHED_TASK_STATUS:
            require_task_binds(task, analysis)
            return
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
    url = api_url("measures/component", component=analysis.project.key, metricKeys="ncloc", **scope_query(analysis))
    try:
        transport(url, analysis.authorization)
    except SonarHttpError as error:
        raise CannotJudge(f"SonarCloud did not resolve {analysis.project.key} in {analysis.scope.label} (HTTP {error.status})") from error


def scope_query(analysis: Analysis) -> dict[str, str]:
    return {analysis.scope.parameter: analysis.scope.value}


def open_findings(analysis: Analysis, transport: Transport) -> OpenFindings:
    listed: list[Finding] = []
    total = 0
    for page in range(1, MAXIMUM_FINDINGS_PAGES + 1):
        url = api_url(
            "issues/search",
            componentKeys=analysis.project.key,
            issueStatuses=OPEN_ISSUE_STATUSES,
            ps=FINDINGS_PAGE_SIZE,
            p=page,
            **scope_query(analysis),
        )
        report = answer_object(transport(url, analysis.authorization), None, url)
        try:
            total = int(report["total"])
            issues = report["issues"]
            if not isinstance(issues, list):
                raise CannotJudge(f"SonarCloud answered {url.partition('?')[0]} with issues that are not a list")
            listed.extend(finding_from_issue(issue) for issue in issues)
        except (KeyError, TypeError, ValueError) as error:
            raise CannotJudge(f"SonarCloud answered {url.partition('?')[0]} with an unreadable body") from error
        if not issues or len(listed) >= total:
            break
    return OpenFindings(total, tuple(listed))


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
