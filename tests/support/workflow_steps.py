import json
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

WORKFLOWS = Path(__file__).parents[2] / ".github" / "workflows"
FORMAT_STEP = "Refuse a malformed version"
GITHUB_SHELL = ["bash", "--noprofile", "--norc", "-eo", "pipefail", "-c"]
FAKE_GH = """\
#!{python}
import json, subprocess, sys
from pathlib import Path

home = Path(__file__).parent
arguments = sys.argv[1:]
filter_ = None
if "--jq" in arguments:
    position = arguments.index("--jq")
    filter_ = arguments[position + 1]
    del arguments[position : position + 2]
with (home / "calls.jsonl").open("a") as calls:
    calls.write(json.dumps(arguments) + "\\n")
answer = json.loads((home / "answers.json").read_text()).get(json.dumps(arguments))
if answer is None:
    print("gh: Not Found (HTTP 404)", file=sys.stderr)
    sys.exit(1)
if answer["exit_code"]:
    sys.exit(answer["exit_code"])
body = json.dumps(answer["body"])
if filter_ is None:
    print(body)
else:
    sys.exit(subprocess.run(["jq", "-r", filter_], input=body, text=True).returncode)
"""


@dataclass(frozen=True)
class Answer:
    """What the fake `gh` prints for one request: the JSON body, or a failing exit code."""

    body: object = None
    exit_code: int = 0


@dataclass(frozen=True)
class StepRun:
    returncode: int
    stdout: str
    stderr: str
    calls: list[list[str]]
    outputs: str


def workflow(name: str) -> dict:
    return yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))


def step(loaded: dict, job: str, name: str) -> dict:
    return next(candidate for candidate in loaded["jobs"][job]["steps"] if candidate["name"] == name)


def step_script(loaded: dict, job: str, name: str) -> str:
    return step(loaded, job, name)["run"]


def step_names(loaded: dict, job: str) -> list[str]:
    return [candidate["name"] for candidate in loaded["jobs"][job]["steps"]]


def step_environment(loaded: dict, job: str, name: str, **scenario: str) -> dict[str, str]:
    """The job's and the step's `env` from the workflow, overlaid with the scenario's values: a step never runs with
    a variable its job does not define, and a value still holding an expression is a scenario that forgot it."""
    environment = {**loaded["jobs"][job].get("env", {}), **step(loaded, job, name).get("env", {}), **scenario}
    unresolved = [key for key, value in environment.items() if "${{" in str(value)]
    assert not unresolved, f"the scenario leaves {unresolved} as workflow expressions"
    return {key: str(value) for key, value in environment.items()}


def write_executable(path: Path, source: str) -> None:
    path.write_text(source)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)


class FakeGh:
    """An executable `gh` on a step's PATH: it answers exactly the requests registered (its arguments without
    `--jq`, `--paginate` included), filters a body through `jq -r` in the caller's environment, and records each call."""

    def __init__(self, directory: Path, answers: dict[tuple[str, ...], Answer]) -> None:
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True)
        registered = {json.dumps(list(request)): {"body": answer.body, "exit_code": answer.exit_code} for request, answer in answers.items()}
        (directory / "answers.json").write_text(json.dumps(registered))
        write_executable(directory / "gh", FAKE_GH.format(python=sys.executable))

    def calls(self) -> list[list[str]]:
        return recorded_calls(self.directory / "calls.jsonl")


def recorded_calls(record: Path) -> list[list[str]]:
    return [json.loads(line) for line in record.read_text().splitlines()] if record.exists() else []


def run_step(script: str, cwd: Path, environment: dict[str, str], tools: Path) -> StepRun:
    """Runs `script` as GitHub's default shell does, with the fake tools first on PATH and a fresh `GITHUB_OUTPUT`."""
    output = cwd / "github_output"
    output.write_text("")
    completed = subprocess.run(
        [*GITHUB_SHELL, script],
        cwd=cwd,
        env={"PATH": f"{tools}:/usr/bin:/bin", "HOME": str(cwd), "GITHUB_OUTPUT": str(output), **environment},
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    return StepRun(completed.returncode, completed.stdout, completed.stderr, recorded_calls(tools / "calls.jsonl"), output.read_text())

