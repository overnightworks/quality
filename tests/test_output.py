import io
import json
import os
import re
import subprocess
import sys
import unicodedata
from pathlib import Path

import pytest
from conftest import FAKE_KEY, allow_config
from test_architecture import FORBIDDEN, SHOP_DB_IMPORTS_WEB, toml

from quality import cli
from quality.ratchet import BASELINE_PATH
from quality.report import printable, shell_word

DOOR = [sys.executable, "-m", "quality.cli"]
STOP_LINE = re.compile(r"::stop-commands::([0-9a-f]{32})")
CLOSED_STDOUT = 'exec "$@" >&-'
CLOSED_STDERR = 'exec "$@" 2>&-'


def run_door(*arguments: str, cwd: Path, actions: str | None = "true", shell: str | None = None):
    environment = {key: value for key, value in os.environ.items() if key != "GITHUB_ACTIONS"}
    if actions is not None:
        environment["GITHUB_ACTIONS"] = actions
    command = [*DOOR, *arguments] if shell is None else ["sh", "-c", shell, "sh", *DOOR, *arguments]
    return subprocess.run(command, cwd=cwd, env=environment, capture_output=True, text=True, check=False)


@pytest.fixture
def clean_repo(build_repo) -> Path:
    return build_repo({"README.md": "x\n"})


@pytest.fixture
def broken_compose_repo(build_repo) -> Path:
    return build_repo({"compose.yaml": "services: [\n"})


def stop_token(stream: str) -> str:
    first_line = stream.splitlines()[0]
    match = STOP_LINE.fullmatch(first_line)
    assert match, first_line
    return match.group(1)


def test_both_streams_start_with_the_same_stop_and_nothing_resumes_it(clean_repo):
    """#89 rule 1, cell 1."""
    local = run_door("hygiene", ".", cwd=clean_repo, actions=None)
    actions = run_door("hygiene", ".", cwd=clean_repo)
    token = stop_token(actions.stdout)
    assert stop_token(actions.stderr) == token
    assert actions.stdout.split("\n", 1)[1] == local.stdout
    assert actions.stderr.split("\n", 1)[1] == local.stderr
    assert f"::{token}::" not in actions.stdout + actions.stderr
    assert actions.returncode == local.returncode


def test_every_run_draws_a_new_token(clean_repo):
    """#89 rule 1, cell 2 (E8)."""
    tokens = {stop_token(run_door("hygiene", ".", cwd=clean_repo).stdout) for _ in range(2)}
    assert len(tokens) == 2


@pytest.mark.parametrize("actions", [None, "false", "TRUE", ""], ids=["unset", "false", "upper case", "empty"])
def test_outside_actions_the_door_prints_no_stop(clean_repo, actions):
    """#89 rule 1, cell 3 (E2)."""
    done = run_door("hygiene", ".", cwd=clean_repo, actions=actions)
    assert "stop-commands" not in done.stdout + done.stderr


def test_main_alone_prints_no_stop(clean_repo, monkeypatch, capsys):
    """#89 rule 1, cell 4: the stop belongs to the process entry."""
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    cli.main(["hygiene", str(clean_repo)])
    captured = capsys.readouterr()
    assert "stop-commands" not in captured.out + captured.err


def test_a_closed_stdout_leaves_the_stop_and_the_verdict_on_stderr(broken_compose_repo):
    """#89 rule 1, cell 5: fd 1 closed."""
    done = run_door("hygiene", ".", cwd=broken_compose_repo, shell=CLOSED_STDOUT)
    stop_token(done.stderr)
    assert (done.returncode, "compose-port cannot judge" in done.stderr) == (2, True)


def test_a_closed_stderr_keeps_the_stop_on_stdout_and_the_exit(broken_compose_repo):
    """#89 rule 1, cell 5: fd 2 closed."""
    local = run_door("hygiene", ".", cwd=broken_compose_repo, actions=None, shell=CLOSED_STDERR)
    done = run_door("hygiene", ".", cwd=broken_compose_repo, shell=CLOSED_STDERR)
    stop_token(done.stdout)
    assert done.stdout.split("\n", 1)[1] == local.stdout
    assert done.returncode == local.returncode == 2


@pytest.mark.parametrize(
    "arguments",
    [("baseline", "write", "."), ("engines", "verify", "engines"), ("sonar", "preflight", ".")],
    ids=["baseline write", "engines verify", "sonar preflight"],
)
def test_other_doors_start_with_the_stop_too(clean_repo, arguments):
    """#89 rule 1, cell 6."""
    (clean_repo / "engines").mkdir()
    done = run_door(*arguments, cwd=clean_repo)
    assert stop_token(done.stdout) == stop_token(done.stderr)


def run_in_process(environment: dict[str, str], exit_code: int = 3) -> int:
    with pytest.raises(SystemExit) as exited:
        cli.run(door=lambda: exit_code, environment=environment)
    return exited.value.code


def test_run_stops_the_commands_on_both_streams_and_keeps_the_door_exit(capsys):
    """#89 rule 1: in Actions, in process."""
    assert run_in_process({"GITHUB_ACTIONS": "true"}) == 3
    captured = capsys.readouterr()
    assert stop_token(captured.out) == stop_token(captured.err)
    assert len(captured.out.splitlines()) == len(captured.err.splitlines()) == 1


def test_run_prints_nothing_outside_actions_and_keeps_the_door_exit(capsys):
    """#89 rule 1: not in Actions, in process."""
    assert run_in_process({}) == 3
    captured = capsys.readouterr()
    assert (captured.out, captured.err) == ("", "")


def test_run_skips_a_closed_stream_in_process(capsys, monkeypatch):
    """#89 rule 1: a stream that is None is skipped."""
    monkeypatch.setattr(sys, "stderr", None)
    assert run_in_process({"GITHUB_ACTIONS": "true"}) == 3
    stdout = capsys.readouterr().out
    stop_token(stdout)
    assert len(stdout.splitlines()) == 1


class SharedLogStream(io.StringIO):
    def __init__(self, name: str, log: list[tuple[str, str]]):
        super().__init__()
        self.name, self.log = name, log

    def write(self, text: str) -> int:
        self.log.append((self.name, text))
        return len(text)

    def flush(self) -> None:
        self.log.append((self.name, "flush"))

    def reconfigure(self, **_options) -> None:
        pass


def test_run_writes_and_flushes_the_stop_on_stderr_then_stdout_before_the_door_is_entered(monkeypatch):
    """#89 rule 1: the order of the stops."""
    log: list[tuple[str, str]] = []
    monkeypatch.setattr(sys, "stderr", SharedLogStream("stderr", log))
    monkeypatch.setattr(sys, "stdout", SharedLogStream("stdout", log))

    def door() -> int:
        log.append(("door", "entered"))
        return 0

    with pytest.raises(SystemExit):
        cli.run(door=door, environment={"GITHUB_ACTIONS": "true"})
    events = [event for event in log if event[1] != "\n"]
    stderr_stop, stdout_stop = events[0][1], events[2][1]
    assert events == [
        ("stderr", stderr_stop),
        ("stderr", "flush"),
        ("stdout", stdout_stop),
        ("stdout", "flush"),
        ("door", "entered"),
    ]
    assert STOP_LINE.fullmatch(stderr_stop)
    assert stdout_stop == stderr_stop


def door_lines(capsys, *arguments: str) -> tuple[int, list[str]]:
    """The exit code and every line the door printed to either stream, split on the line feed only."""
    code = cli.main(list(arguments))
    captured = capsys.readouterr()
    return code, (captured.out + captured.err).split("\n")


def assert_every_line_starts_with_the_door(lines: list[str]) -> None:
    """#97 rule 2, cell 18: no line repository text could forge, and no raw control character between the lines."""
    for line in lines:
        assert not line.lstrip().startswith(("::", "File ", "raise ")), line
        assert all(unicodedata.category(character) != "Cc" for character in line), line


def compose_services(*services: tuple[str, str]) -> dict[str, str]:
    body = "".join(f"  {json.dumps(name)}:\n    image: x\n    ports: [{json.dumps(port)}]\n" for name, port in services)
    return {"compose.yaml": "services:\n" + body}


ENV_NAME_CASES = [
    ("x\n::warning::injected.env", "x\\x0a::warning::injected.env"),
    ("cr\rfake.env", "cr\\x0dfake.env"),
    ("esc\x1b[31mred.env", "esc\\x1b[31mred.env"),
    ("tab\tx.env", "tab\\x09x.env"),
    ("del\x7fx.env", "del\\x7fx.env"),
    ("nel\x85x.env", "nel\\x85x.env"),
]


@pytest.mark.parametrize(("name", "escaped"), ENV_NAME_CASES, ids=[escaped for _, escaped in ENV_NAME_CASES])
def test_an_env_name_with_a_control_character_prints_as_one_escaped_line(build_repo, capsys, name, escaped):
    """#97 rule 2, cell 7 (E4): the finding is one line with the name escaped, and the fix is #86's hint."""
    code, lines = door_lines(capsys, "hygiene", str(build_repo({name: "A=1\n"})))
    assert code == 1
    assert any(line.startswith(f"env-file {escaped} - ") for line in lines), lines
    assert any(line.startswith("  Fix: remove it from the index by its bytes") for line in lines)
    assert_every_line_starts_with_the_door(lines)


FILE_AND_RAISE = "web\n  File \"a.py\", line 1, in b\n  raise E('forged')\nx"


@pytest.mark.parametrize(
    ("services", "line_start"),
    [
        ([("web\n::error::svc", "8080:80")], "compose-port compose.yaml - service web\\x0a::error::svc publishes port 8080:80"),
        ([("svc", "5432:5432\n::error::port")], "compose-port compose.yaml - service svc publishes port 5432:5432\\x0a::error::port"),
        (
            [(FILE_AND_RAISE, "0.0.0.0:80:80")],
            "compose-port compose.yaml - service web\\x0a  File \"a.py\", line 1, in b\\x0a  raise E('forged')\\x0ax",
        ),
    ],
    ids=["service name", "port text", "File and raise lines"],
)
def test_a_compose_finding_with_a_control_character_is_one_escaped_line(build_repo, capsys, services, line_start):
    """#97 rule 2, cell 8 (E4): the service and the port text print escaped on the finding's own line."""
    code, lines = door_lines(capsys, "hygiene", str(build_repo(compose_services(*services))))
    assert code == 1
    assert any(line.startswith(line_start) for line in lines), lines
    assert_every_line_starts_with_the_door(lines)


@pytest.mark.parametrize(
    ("files", "refusal_start"),
    [
        ({"a\n::error::x.compose.yaml": "services: [\n"}, "hygiene: compose-port cannot judge - cannot read a\\x0a::error::x.compose.yaml: line 2, "),
        (
            {**SHOP_DB_IMPORTS_WEB, ".importlinter": "[importlinter\n::error::c]\nroot_packages = shop\n"},
            "hygiene: import-contract cannot judge - ",
        ),
    ],
    ids=["compose file name", "importlinter section header"],
)
def test_a_refusal_is_one_escaped_line(build_repo, capsys, files, refusal_start):
    """#97 rule 2, cell 9 (E5): a refusal naming repository text is one line and exit 2."""
    code, lines = door_lines(capsys, "hygiene", str(build_repo(files)))
    refusals = [line for line in lines if line.startswith(refusal_start)]
    assert (code, len(refusals)) == (2, 1), lines
    assert_every_line_starts_with_the_door(lines)


def test_a_broken_contract_with_a_line_feed_in_its_name_keeps_its_chain_on_its_own_line(build_repo, capsys):
    """#97 rule 2, cell 10 (E6): the name is escaped, the import chain stays a line of its own."""
    contract = {**FORBIDDEN, "name": "guarded\n::error::c"}
    repo = build_repo({**SHOP_DB_IMPORTS_WEB, "pyproject.toml": toml(contract)})
    code, lines = door_lines(capsys, "hygiene", str(repo))
    assert code == 1
    assert "import-contract pyproject.toml - contract guarded\\x0a::error::c is broken:" in lines
    assert any(line.strip("- ") == "shop.db -> shop.web (l.1)" for line in lines), lines
    assert_every_line_starts_with_the_door(lines)


def test_an_unused_allowance_with_a_control_character_prints_escaped(build_repo, clean_engines, capsys):
    """#97 rule 2, cell 13 (E4): the unused allowance line names its file escaped."""
    config = allow_config(("aws-access-token", "no\\u001bthere.txt", FAKE_KEY, "stale entry"))
    repo = build_repo({"quality/config.toml": config})
    code, lines = door_lines(capsys, "hygiene", "--engines", str(clean_engines), str(repo))
    assert code == 0
    assert any(line.startswith("secret: unused allowance aws-access-token in no\\x1bthere.txt (sha256 ") for line in lines)
    assert_every_line_starts_with_the_door(lines)


def test_a_config_error_example_is_one_stderr_line(build_repo, monkeypatch, capsys):
    """#97 rule 2, cell 14 (E5): the multi-line `like:` example of a config error joins with `\\x0a`."""
    monkeypatch.chdir(build_repo({"quality/config.toml": "[gate]\nmandatory_jobs = 3\n"}))
    code, lines = door_lines(capsys, "sonar", "findings")
    cannot_judge = [line for line in lines if line.startswith("sonar: cannot judge - ")]
    assert (code, len(cannot_judge)) == (2, 1), lines
    assert "like:" in cannot_judge[0]
    assert "\\x0a" in cannot_judge[0]
    assert_every_line_starts_with_the_door(lines)


def test_a_raised_baseline_entry_for_a_file_with_a_control_character_prints_escaped(build_repo, capsys):
    """#97 rule 2, cell 16 (E4): the raise line names the file escaped."""
    long_file = "x = 1\n" * 810
    repo = build_repo({"src/esc\x1bape.py": long_file, BASELINE_PATH: "[file_lines]\n\"src/gone.py\" = 900\n"})
    code, lines = door_lines(capsys, "baseline", "write", str(repo))
    assert code == 0
    assert "baseline: adds file_lines src/esc\\x1bape.py 810" in lines
    assert_every_line_starts_with_the_door(lines)


@pytest.mark.parametrize(
    "name",
    ["plain.env", "umlaut-\u00e9.env", "-dash.env", "a:b.env", "*.env", "50%.env", "back\\slash.env", "=equals.env"],
)
def test_a_name_printable_unchanged_is_a_shell_word_and_printable_is_idempotent(name):
    """#97 rule 1, cell 17: a word exactly when `printable(x) == x`."""
    assert printable(name) == name
    assert shell_word(name) is not None


@pytest.mark.parametrize(
    "name", ["lat\udce9.env", "a\nb", "esc\x1b", "c1\x85", "del\x7f", "tab\t", "cr\r", "nul\0"]
)
def test_a_name_printable_changes_has_no_shell_word_and_printing_it_twice_changes_nothing(name):
    """#97 rule 1, cell 17: no word where `printable(x) != x`; the escape is idempotent."""
    assert printable(name) != name
    assert shell_word(name) is None
    assert printable(printable(name)) == printable(name)
