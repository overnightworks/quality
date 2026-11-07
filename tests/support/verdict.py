import json
from pathlib import Path

from quality import verdict
from quality.verdict import Job
from tests.support.door import door_in

GREEN_GATE = {"check": "success"}


def job(name="checks", attempt=1, status="completed", conclusion="success", leg=1):
    """One entry of the run's jobs; GitHub gives each leg of a name, in each attempt, an id of its own."""
    return Job(name, attempt, status, conclusion, int.from_bytes(f"{name}/{attempt}/{leg}".encode()))


CHECKS_GREEN = json.dumps(
    {"total_count": 1, "jobs": [{"id": 1, "name": "checks", "run_attempt": 1, "status": "completed", "conclusion": "success"}]}
)


def jobs_page(*jobs: tuple[str, str, str | None]) -> str:
    """One page of a run's jobs, each `(name, status, conclusion)` in attempt 1."""
    listed = [
        {"id": number, "name": name, "run_attempt": 1, "status": status, "conclusion": conclusion}
        for number, (name, status, conclusion) in enumerate(jobs, start=1)
    ]
    return json.dumps({"total_count": len(listed), "jobs": listed})


def set_gate_variables(monkeypatch) -> None:
    """The variables gate.yml's verdict step hands the verdict, outside a pull request: no base commit. The pull
    request's head is a sha of its own, never a commit of the fixture."""
    for name, value in {
        "GITHUB_REPOSITORY": "o/r",
        "GITHUB_RUN_ID": "1",
        "GITHUB_RUN_ATTEMPT": "1",
        "GITHUB_TOKEN": "t",
        "GATE_NEEDS": json.dumps({"check": {"result": "success"}}),
        "GITHUB_BASE_REF": "main",
        "GATE_PULL_REQUEST": "7",
        "GATE_HEAD_SHA": "5d1e0b7" + "c" * 33,
    }.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("GATE_BASE_SHA", raising=False)


def verdict_streams(repo: Path, capsys) -> tuple[int, str, str]:
    code = door_in(repo, "verdict", fetcher_for=lambda token: lambda url: (CHECKS_GREEN, None), clock=lambda: 0.0)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def verdict_of(repo: Path, capsys) -> tuple[int, str]:
    code, out, err = verdict_streams(repo, capsys)
    return code, out + err


def viewed(repo: Path, capsys) -> tuple[int, list[str]]:
    """The verdict's exit and what its stdout says about the loosenings (`verdict_view`)."""
    code, out, _ = verdict_streams(repo, capsys)
    return code, verdict_view(out)


def fetch_jobs(origin: str) -> str:
    body, _ = verdict.github_fetcher("t")(origin + "/jobs")
    return body


SONGMAKER = "overnightworks/songmaker#1345"


GREEN = "✓ green"
RED = "! red"
ONLY_DECLARED = "! red · only declared loosenings · only the operator merges it"


def declared(what: str, ruling: str = f"{SONGMAKER} of 2026-10-20") -> str:
    """A declaration's line under the verdict's `loosenings` or `declarations` row."""
    return f"    ! {what} · {ruling}"


def verdict_view(out: str) -> list[str]:
    """What a verdict's stdout says about its loosenings: each line under a row that needs you, then the closing
    line's verdict, the only-declared one whole and any other up to its first part."""
    lines = out.splitlines()
    closing = lines[-1] if lines else ""
    word = ONLY_DECLARED if closing.startswith(ONLY_DECLARED) else closing.split(" · ")[0]
    return [*(line for line in lines if line.startswith("    ! ")), word]


def declaration(what: str, item: str = SONGMAKER, date: str = "2026-10-20") -> str:
    """A [[gate.loosened]] entry; JSON's string escapes are TOML's, so `what` is written as the TOML escape."""
    return f'[[gate.loosened]]\nwhat = {json.dumps(what)}\nitem = "{item}"\ndate = {date}\nreason = "ruled"\n'


def gate_config(*jobs: str, legacy: str = "report", declared: tuple[str, ...] = ()) -> str:
    return f"[gate]\nmandatory_jobs = {json.dumps(jobs)}\nlegacy = \"{legacy}\"\n" + "".join(declared)
