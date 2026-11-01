import http.server
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from quality import cli, sonar

TASK_FIXTURE = Path(__file__).parents[1] / "fixtures" / "sonar" / "report-task.txt"


TOKEN = "stub-token-4711"


KEY = "overnightworks_quality"


PULL_REQUEST_ENVIRONMENT = {
    "GITHUB_REPOSITORY": "overnightworks/quality",
    "GITHUB_EVENT_NAME": "pull_request",
    "PULL_REQUEST_NUMBER": "7",
    "GITHUB_REF_NAME": "7/merge",
    "SONAR_TOKEN": TOKEN,
    "DEFAULT_BRANCH": "main",
}


PUSH_ENVIRONMENT = {**PULL_REQUEST_ENVIRONMENT, "GITHUB_EVENT_NAME": "push", "GITHUB_REF_NAME": "main"}


FINISHED_FOR_PR_7 = {
    "status": "SUCCESS",
    "componentKey": KEY,
    "organization": "overnightworks",
    "pullRequest": "7",
    "analysisId": "analysis-7",
}


FINISHED_FOR_MAIN = {**FINISHED_FOR_PR_7, "pullRequest": None, "branch": "main"}


FINISHED_FOR_FEATURE = {**FINISHED_FOR_MAIN, "branch": "feat/a"}


FEATURE_ENVIRONMENT = {**PUSH_ENVIRONMENT, "GITHUB_REF_NAME": "feat/a"}


LEGACY_COUNT = "issues/search (count only)"


def rule_count_endpoint(rule: str) -> str:
    return f"{LEGACY_COUNT} of {rule}"


ANALYSED_MAIN = {"name": "main", "isMain": True, "analysisDate": "2026-10-16T10:00:00+0000"}


def issue(number: int, component: str | None = None) -> dict:
    return {"rule": "python:S1", "component": component or f"{KEY}:src/a{number}.py", "line": number, "message": "m"}


def issues_page(total: int, *numbers: int) -> dict:
    return {"total": total, "issues": [issue(number) for number in numbers]}


@dataclass
class FakeSonar:
    """Answers per endpoint in order, the last one repeating; records every request."""

    answers: dict[str, list[object]]
    requests: list[tuple[str, str]] = field(default_factory=list)

    def __call__(self, url: str, authorization: str | None) -> object:
        self.requests.append((url, authorization))
        queue = self.answers[self.endpoint_of(url)]
        answer = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(answer, Exception):
            raise answer
        return answer

    @staticmethod
    def endpoint_of(url: str) -> str:
        endpoint = urlparse(url).path.removeprefix("/api/")
        query = parse_qs(urlparse(url).query)
        if endpoint != "issues/search" or query.get("ps") != ["1"]:
            return endpoint
        return rule_count_endpoint(query["rules"][0]) if "rules" in query else LEGACY_COUNT

    def urls_to(self, endpoint: str) -> list[str]:
        return [url for url, _ in self.requests if self.endpoint_of(url) == endpoint]


GATE_OK = {"projectStatus": {"status": "OK", "conditions": []}}


def gate_error(*conditions: dict, **extra) -> dict:
    return {"projectStatus": {"status": "ERROR", "conditions": list(conditions), **extra}}


def failed_condition(metric="new_coverage", comparator="LT", actual="70.0", threshold="80") -> dict:
    return {"status": "ERROR", "metricKey": metric, "comparator": comparator, "actualValue": actual, "errorThreshold": threshold}


def sonar_answering(task=FINISHED_FOR_PR_7, issues=None, measures=None, legacy=0, branches=(ANALYSED_MAIN,), legacy_by_rule=None, gate=GATE_OK) -> FakeSonar:
    set_aside_counts = {"docker:S8431": 0, **(legacy_by_rule or {})}
    return FakeSonar(
        {
            **{rule_count_endpoint(rule): [{"total": count, "issues": []}] for rule, count in set_aside_counts.items()},
            "ce/task": [{"task": task}],
            "qualitygates/project_status": [gate],
            "measures/component": [measures or {"component": {}}],
            "project_branches/list": [{"branches": list(branches)}],
            "issues/search": [issues or issues_page(0)],
            LEGACY_COUNT: [{"total": legacy, "issues": []}],
        }
    )


def set_environment(monkeypatch, environment) -> None:
    for name in PULL_REQUEST_ENVIRONMENT:
        monkeypatch.delenv(name, raising=False)
    for name, value in environment.items():
        monkeypatch.setenv(name, value)


CLEAN_PROPERTIES = (
    "sonar.organization=overnightworks\nsonar.projectKey=overnightworks_quality\nsonar.sources=src\nsonar.tests=tests\n"
)


def run_preflight(
    monkeypatch,
    capsys,
    build_repo,
    properties: str | None = CLEAN_PROPERTIES,
    environment=PULL_REQUEST_ENVIRONMENT,
    extra_files=None,
    arrange: Callable[[Path], None] | None = None,
    step_output: Path | None = None,
):
    """Drives the preflight door; `arrange` changes the checkout on disk after its commit, `step_output` is GITHUB_OUTPUT."""
    set_environment(monkeypatch, environment)
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    if step_output is not None:
        monkeypatch.setenv("GITHUB_OUTPUT", str(step_output))
    files = dict(extra_files or {})
    if properties is not None:
        files["sonar-project.properties"] = properties
    root = build_repo(files)
    if arrange is not None:
        arrange(root)
    code = cli.main(["sonar", "preflight", str(root)])
    captured = capsys.readouterr()
    assert TOKEN not in captured.out + captured.err
    return code, captured.out, captured.err


legacy_config_absent = '[gate]\nmandatory_jobs = ["quality"]\n'


def legacy_config(mode: str) -> str:
    return f'[gate]\nmandatory_jobs = ["quality"]\nlegacy = "{mode}"\n'


def run_cli(
    monkeypatch,
    capsys,
    fake: FakeSonar,
    environment=PULL_REQUEST_ENVIRONMENT,
    task_text: str | None = None,
    config_text: str | None = legacy_config_absent,
):
    set_environment(monkeypatch, environment)
    if config_text is None:
        Path("quality/config.toml").unlink()
    else:
        Path("quality/config.toml").write_text(config_text)
    sonar.TASK_FILE.parent.mkdir()
    sonar.TASK_FILE.write_text(TASK_FIXTURE.read_text() if task_text is None else task_text)
    sleeps: list[float] = []
    code = cli.main(["sonar", "findings"], transport=fake, sleep=sleeps.append)
    captured = capsys.readouterr()
    assert TOKEN not in captured.out + captured.err
    return code, captured.out, captured.err, sleeps


Responder = Callable[[http.server.BaseHTTPRequestHandler], None]


def reply(status: int, body: bytes, **headers: str) -> Responder:
    def respond(handler):
        handler.send_response(status)
        sent = {"Content-Length": str(len(body))} | {name.replace("_", "-"): value for name, value in headers.items()}
        for name, value in sent.items():
            handler.send_header(name, value)
        handler.end_headers()
        handler.wfile.write(body)

    return respond
