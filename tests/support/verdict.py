import json
from pathlib import Path

from quality import cli, verdict
from quality.verdict import Job

GREEN_GATE = {"hygiene": "success"}


def job(name="checks", attempt=1, status="completed", conclusion="success", leg=1):
    """One entry of the run's jobs; GitHub gives each leg of a name, in each attempt, an id of its own."""
    return Job(name, attempt, status, conclusion, int.from_bytes(f"{name}/{attempt}/{leg}".encode()))


CHECKS_GREEN = json.dumps(
    {"total_count": 1, "jobs": [{"id": 1, "name": "checks", "run_attempt": 1, "status": "completed", "conclusion": "success"}]}
)


def verdict_of(repo: Path, capsys) -> tuple[int, str]:
    code = cli.main(["verdict", str(repo)], fetcher_for=lambda token: lambda url: (CHECKS_GREEN, None))
    captured = capsys.readouterr()
    return code, captured.out + captured.err


def fetch_jobs(origin: str) -> str:
    body, _ = verdict.github_fetcher("t")(origin + "/jobs")
    return body


SONGMAKER = "overnightworks/songmaker#1345"


DECLARED = f"loosened (declared, {SONGMAKER} of 2026-10-20): "


ONLY_DECLARED = "verdict: red - only declared loosenings: only the operator's quality emergency-merge lands this pull request"


def declaration(what: str, item: str = SONGMAKER, date: str = "2026-10-20") -> str:
    """A [[gate.loosened]] entry; JSON's string escapes are TOML's, so `what` is written as the TOML escape."""
    return f'[[gate.loosened]]\nwhat = {json.dumps(what)}\nitem = "{item}"\ndate = {date}\nreason = "ruled"\n'


def gate_config(*jobs: str, legacy: str = "report", declared: tuple[str, ...] = ()) -> str:
    return f"[gate]\nmandatory_jobs = {json.dumps(jobs)}\nlegacy = \"{legacy}\"\n" + "".join(declared)
