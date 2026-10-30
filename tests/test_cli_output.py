import contextlib
import io
import json
import os
import pty
import re
import shlex
import signal
import subprocess
import sys
import unicodedata
from pathlib import Path

import pytest

from quality import check, cli, ratchet, scanners
from quality.ratchet import BASELINE_PATH
from quality.report import DETAIL_MARKER, CannotJudge, Command
from quality.rules import Area, Rule
from tests.support.architecture import FORBIDDEN, SHOP, SHOP_DB_IMPORTS_WEB, toml
from tests.support.check import assert_every_line_starts_with_the_door
from tests.support.processes import process_is_gone
from tests.support.scanners import fake_scanner, lacking
from tests.support.secrets import FAKE_KEY, allow_config

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
    *actions_lines, actions_verdict = actions.stdout.split("\n", 1)[1].splitlines()
    *local_lines, local_verdict = local.stdout.splitlines()
    assert actions_lines == local_lines  # the verdict line ends in each run's own duration
    assert all(re.fullmatch(r"✓ clean · 7 areas · debt measured, not enforced · (<1s|\d+s)", line) for line in (actions_verdict, local_verdict))
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
    assert (done.returncode, "error: 1 of 7 areas could not check" in done.stderr) == (2, True)


def test_a_closed_stderr_keeps_the_stop_on_stdout_and_the_exit(broken_compose_repo):
    """#89 rule 1, cell 5: fd 2 closed."""
    local = run_door("hygiene", ".", cwd=broken_compose_repo, actions=None, shell=CLOSED_STDERR)
    done = run_door("hygiene", ".", cwd=broken_compose_repo, shell=CLOSED_STDERR)
    stop_token(done.stdout)
    assert done.stdout.split("\n", 1)[1] == local.stdout
    assert done.returncode == local.returncode == 2


@pytest.mark.parametrize(
    "arguments",
    [("baseline", "write", "."), ("sonar", "properties")],
    ids=["baseline write", "sonar properties"],
)
def test_other_doors_start_with_the_stop_too(clean_repo, arguments):
    """#89 rule 1, cell 6."""
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
    (".env.\u202eelpmaxe\u202c", ".env.\\u202eelpmaxe\\u202c"),
    ("ls\u2028x.env", "ls\\u2028x.env"),
]


@pytest.mark.parametrize(("name", "escaped"), ENV_NAME_CASES, ids=[escaped for _, escaped in ENV_NAME_CASES])
def test_an_env_name_with_a_control_format_or_separator_character_prints_as_one_escaped_line(build_repo, capsys, name, escaped):
    """#97 rule 2, cell 7 (E4) and #228 rule 7: the finding is one line with the name escaped, and the fix is #86's hint."""
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
        ({"a\n::error::x.compose.yaml": "services: [\n"}, "  ✗ files         not checked    cannot read a\\x0a::error::x.compose.yaml: line 2, "),
        (
            {**SHOP_DB_IMPORTS_WEB, ".importlinter": "[importlinter]\nroot_packages =\n    shop\n\n[importlinter:contract:a\n::error::c]\nname = x\n"},
            "  ✗ imports       not checked    ",
        ),
    ],
    ids=["compose file name", "importlinter section header that echoes"],
)
def test_a_refusal_is_one_escaped_line(build_repo, capsys, files, refusal_start):
    """#97 rule 2, cell 9 (E5): a refusal naming repository text is one line and exit 2."""
    code, lines = door_lines(capsys, "hygiene", str(build_repo(files)))
    refusals = [line for line in lines if line.startswith(refusal_start)]
    assert (code, len(refusals)) == (2, 1), lines
    assert_every_line_starts_with_the_door(lines)
    if ".importlinter" in files:
        assert "\\x0a\\x09[line " in refusals[0]
        assert "'::error::c]" in refusals[0]


def test_a_broken_contract_named_like_a_command_keeps_its_chain_on_its_own_line(build_repo, capsys):
    """#97 rule 2, cell 10 (E6): the name is printed on the finding's line, the import chain stays a line of its own."""
    contract = {**FORBIDDEN, "name": "guarded ::error::c"}
    repo = build_repo({**SHOP_DB_IMPORTS_WEB, "pyproject.toml": toml(contract)})
    code, lines = door_lines(capsys, "hygiene", str(repo))
    assert code == 1
    assert "import-contract pyproject.toml - contract guarded ::error::c is broken:" in lines
    assert f"{DETAIL_MARKER}-   shop.db -> shop.web (l.1)" in lines
    assert_every_line_starts_with_the_door(lines)


FORGED_MATCHER_PAIR = ['  File "a", line 1, in b', "  raise E('forged')"]
INDEPENDENT_SHOP = {"type": "independence", "modules": ["shop.web", "shop.db"]}
EXHAUSTIVE_LAYERS = {"type": "layers", "layers": ["web", "db"], "containers": ["shop"], "exhaustive": True}
WRAPPED_MODULE_NAME = "q" * 70 + ' File "ab", line 1, in b ' + "r" * 52 + " raise E('forged')"


def broken_contract_lines(capsys, files: dict[str, str], build_repo) -> list[str]:
    code, lines = door_lines(capsys, "hygiene", str(build_repo(files)))
    assert code == 1, lines
    assert_every_line_starts_with_the_door(lines)
    return lines


def test_a_long_module_name_import_linter_would_wrap_cannot_forge_a_matcher_pair(build_repo, capsys):
    """#97 rule 2, cell 18 (Opus D1): rich's soft wrap would put `File "ab", ...` and `raise ...` at the line start."""
    files = {**SHOP, f"src/shop/{WRAPPED_MODULE_NAME}.py": "", "pyproject.toml": toml(EXHAUSTIVE_LAYERS)}
    lines = broken_contract_lines(capsys, files, build_repo)
    assert f"{DETAIL_MARKER}- shop.{WRAPPED_MODULE_NAME}" in lines


@pytest.mark.parametrize("guidance", [FORGED_MATCHER_PAIR, "\n".join(FORGED_MATCHER_PAIR)], ids=["list", "multi-line text"])
def test_contract_guidance_with_several_lines_is_judged_and_printed_behind_the_marker(build_repo, capsys, guidance):
    """#97 rule 2, cell 18 (Codex F1, Opus D2): legitimate multi-line guidance is judged as before: exit 1."""
    contract = {**INDEPENDENT_SHOP, "broken_contract_guidance": guidance}
    lines = broken_contract_lines(capsys, {**SHOP_DB_IMPORTS_WEB, "pyproject.toml": toml(contract)}, build_repo)
    assert all(f"{DETAIL_MARKER}{line}" in lines for line in FORGED_MATCHER_PAIR)


@pytest.mark.parametrize(
    ("name", "text_after_the_line_feed"),
    [("a\n::error::x", "::error::x"), ("a\n  File \"ab\", line 1, in b\n  raise E('forged')\nx", "  raise E('forged')")],
    ids=["workflow command", "File and raise lines"],
)
def test_a_module_name_with_a_line_feed_is_judged_and_its_second_line_stays_behind_the_marker(
    build_repo, capsys, name, text_after_the_line_feed
):
    """#97 rule 2, cell 18 and cell 12: a tracked source name no longer stops the judgement, and forges no line."""
    files = {**SHOP_DB_IMPORTS_WEB, f"src/shop/{name}.py": "import shop.web\n",
             "pyproject.toml": toml({**FORBIDDEN, "source_modules": ["shop.*"]})}
    lines = broken_contract_lines(capsys, files, build_repo)
    assert any(line.startswith(f"{DETAIL_MARKER}{text_after_the_line_feed}") for line in lines), lines


def test_a_source_directory_with_a_line_feed_is_judged_without_a_forged_line(build_repo, capsys):
    """#97 rule 2, cell 18: a package directory name is judged like a file name."""
    files = {**SHOP_DB_IMPORTS_WEB, "src/shop/a\n::error::x/__init__.py": "", "pyproject.toml": toml(FORBIDDEN)}
    broken_contract_lines(capsys, files, build_repo)


@pytest.mark.parametrize("name", ["guarded\n::error::c", "guarded\x1b[31m"], ids=["line feed", "escape"])
def test_a_contract_name_with_a_control_character_is_judged_and_prints_escaped(build_repo, capsys, name):
    """#97 rule 2, cell 10a: the name prints escaped on the finding's line and the chain stays behind the marker."""
    lines = broken_contract_lines(capsys, {**SHOP_DB_IMPORTS_WEB, "pyproject.toml": toml({**FORBIDDEN, "name": name})}, build_repo)
    assert any(line.startswith("import-contract pyproject.toml - contract guarded\\x") for line in lines), lines


def test_a_source_name_that_is_not_utf8_is_still_refused_as_one_line(build_repo, capsys):
    """#97 rule 2, cell 12: invalid UTF-8 in a tracked source name is refused before import-linter renders it."""
    files = {**SHOP_DB_IMPORTS_WEB, "src/shop/lat\udce9.py": "import shop.web\n", "pyproject.toml": toml(FORBIDDEN)}
    code, lines = door_lines(capsys, "hygiene", str(build_repo(files)))
    refusals = [line for line in lines if line.startswith("  ✗ imports       not checked    ")]
    assert (code, len(refusals)) == (2, 1), lines
    assert_every_line_starts_with_the_door(lines)


def test_a_scanner_stderr_with_several_lines_prints_as_one_escaped_line(build_repo, served_scanners, capsys):
    """#97 rule 2, cell 14 (E5): the stderr tail of a failing scanner joins on `\\x0a`, never a line of its own."""
    fake_scanner(served_scanners, "gitleaks", on_scan="echo first >&2; echo '::error::second' >&2; exit 2")
    repo = build_repo({"README.md": "x\n"})
    code, lines = door_lines(capsys, "hygiene", str(repo))
    refusals = [line for line in lines if line.startswith("  ✗ secrets       not checked    ")]
    assert (code, len(refusals)) == (2, 1), lines
    assert "first\\x0a::error::second" in refusals[0]
    assert_every_line_starts_with_the_door(lines)


def test_an_unused_allowance_with_a_control_character_prints_escaped(build_repo, capsys):
    """#97 rule 2, cell 13 (E4): the unused allowance line names its file escaped."""
    config = allow_config(("aws-access-token", "no\\u001bthere.txt", FAKE_KEY, "stale entry"))
    repo = build_repo({"quality/config.toml": config})
    code, lines = door_lines(capsys, "hygiene", str(repo))
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


def test_a_baseline_refusal_for_a_file_that_is_not_utf8_prints_its_surrogate_escaped(build_repo, capsys):
    """#97 rule 2, cell 16: the invalid-UTF-8 branch is one escaped refusal line, exit 2."""
    repo = build_repo({"src/lat\udce9.py": "x = 1\n" * 810, BASELINE_PATH: "[file_lines]\n\"src/gone.py\" = 900\n"})
    code, lines = door_lines(capsys, "baseline", "write", str(repo))
    assert code == 2
    assert lines[0] == "baseline: not written - src/lat\\udce9.py is not valid UTF-8, so quality/baseline.toml cannot hold it"
    assert_every_line_starts_with_the_door(lines)


def test_ctrl_c_in_the_door_ends_the_run_as_interrupted_through_the_injected_sender(capsys):
    """#228 rule 2: an interrupt ends the run with 130 and prints nothing."""
    sent = []

    def interrupted_door() -> int:
        raise KeyboardInterrupt

    saved = signal.getsignal(signal.SIGINT)
    try:
        with pytest.raises(SystemExit) as exited:
            cli.run(door=interrupted_door, environment={}, send_signal=lambda pid, number: sent.append((pid, number)))
    finally:
        signal.signal(signal.SIGINT, saved)
    assert sent == [(os.getpid(), signal.SIGINT)]
    assert exited.value.code == 128 + signal.SIGINT == 130
    captured = capsys.readouterr()
    assert (captured.out, captured.err) == ("", "")


@pytest.mark.integration
@pytest.mark.parametrize("under_shell", [True, False], ids=["under a shell", "direct"])
def test_ctrl_c_during_a_scan_ends_the_run_as_interrupted_without_a_traceback(clean_repo, served_scanners, tmp_path, under_shell):
    """#228 rule 2: a shell reads 130 (the process dies by SIGINT), the scanner is gone and the scratch directory removed."""
    scanner_pid = tmp_path / "scanner.pid"
    scratch_parent = tmp_path / "scratch"
    scratch_parent.mkdir()
    fake_scanner(
        served_scanners, "gitleaks",
        on_scan='echo $$ > "$SCANNER_PID"; head -c 1048576 /dev/zero; kill -INT $PPID; exec sleep 60',
    )
    door = [*DOOR, "hygiene", str(clean_repo)]
    command = ["sh", "-c", '"$@"; echo "exit $?" >&2', "sh", *door] if under_shell else door
    environment = os.environ | {"SCANNER_PID": str(scanner_pid), "TMPDIR": str(scratch_parent)}
    interrupted = subprocess.run(command, env=environment, capture_output=True, text=True, timeout=60, check=False)
    if under_shell:
        assert interrupted.stderr.rstrip().endswith("exit 130")
    else:
        assert interrupted.returncode == -signal.SIGINT
    assert "Traceback" not in interrupted.stdout + interrupted.stderr
    assert "KeyboardInterrupt" not in interrupted.stdout + interrupted.stderr
    assert process_is_gone(int(scanner_pid.read_text()))
    assert list(scratch_parent.iterdir()) == []


# Decision 16 and the frame: one header row per area, the findings under it, the verdict line or the error and next pair.

CLEAN_FILES = {"README.md": "x\n"}
CLONES_BASELINE = {BASELINE_PATH: "[clones]\n"}
PRINTING = {"src/a.py": 'print("x")\n'}
ZERO_SECONDS = "<1s"


class Terminal(io.StringIO):
    def isatty(self) -> bool:
        return True


def frame_run(capsys, path: Path, **keywords) -> tuple[int, list[str], list[str]]:
    code = cli.main(["hygiene", str(path)], clock=lambda: 0.0, **keywords)
    captured = capsys.readouterr()
    return code, captured.out.splitlines(), captured.err.splitlines()


def header(area: Area, glyph: str, event: str, fact: str = "") -> str:
    return f"  {glyph} {area.value:<12}  {event:<14} {fact}".rstrip()


def headers(lines: list[str]) -> list[str]:
    return [line for line in lines if re.match(rf"  [✓!✗·] ({'|'.join(Area)}) ", line)]


def test_a_clean_run_has_seven_clean_rows_in_area_order_and_the_verdict(build_repo, capsys):
    code, out, err = frame_run(capsys, build_repo({**CLEAN_FILES, **CLONES_BASELINE}))
    assert (code, headers(out), out[-1], err) == (0, [header(area, "✓", "clean") for area in Area], f"✓ clean · 7 areas · {ZERO_SECONDS}", [])


def test_a_run_without_a_baseline_notes_the_debt_as_not_recorded_and_says_it_is_not_enforced(build_repo, capsys):
    code, out, err = frame_run(capsys, build_repo(CLEAN_FILES))
    assert (code, err) == (0, [])
    assert header(Area.DEBT, "·", "not recorded", "measured only") in out
    assert out[-1] == f"✓ clean · 7 areas · debt measured, not enforced · {ZERO_SECONDS}"


def test_a_baseline_without_clones_is_partly_measured_only(build_repo, capsys):
    code, out, err = frame_run(capsys, build_repo({**CLEAN_FILES, BASELINE_PATH: ""}))
    assert (code, err) == (0, [])
    assert header(Area.DEBT, "·", "not recorded", "partly measured only") in out
    assert out[-1] == f"✓ clean · 7 areas · debt partly measured, not enforced · {ZERO_SECONDS}"


def test_findings_in_two_areas_follow_their_rows_and_end_in_the_counted_verdict(build_repo, capsys):
    code, out, err = frame_run(capsys, build_repo({**CLONES_BASELINE, **PRINTING, ".env": "A=1\n"}))
    files_row, code_row = out.index(header(Area.FILES, "!", "2 findings")), out.index(header(Area.CODE, "!", "1 finding"))
    assert (code, err) == (1, [])
    assert out[files_row + 1].startswith("env-file .env - ")
    assert out[code_row + 1].startswith("python-print src/a.py:1 - ")
    assert out[-1] == f"! 3 findings in 2 areas · 5 areas clean · {ZERO_SECONDS}"


def test_an_area_that_cannot_check_ends_the_run_with_the_pair_and_no_verdict(build_repo, use_mise, served_scanners, capsys):
    use_mise(lacking(served_scanners, scanners.GITLEAKS))
    code, out, err = frame_run(capsys, build_repo({**CLONES_BASELINE, **PRINTING}))
    unchecked = [line for line in out if line.startswith("  ✗ secrets  ")]
    assert (code, len(unchecked), [line for line in out if "areas clean" in line or " · 7 areas" in line]) == (2, 1, [])
    assert unchecked[0].endswith(f"not checked    mise could not locate {scanners.GITLEAKS} {scanners.pinned_tool(scanners.GITLEAKS).version}")
    assert header(Area.CODE, "!", "1 finding") in out
    assert err == ["error: 1 of 7 areas could not check · 1 finding in the others", f"next: {scanners.install_command()}"]
    assert scanners.install_command() not in "\n".join(out)


def test_findings_in_the_area_that_cannot_check_stay_under_its_row_and_are_counted(build_repo, capsys):
    code, out, err = frame_run(capsys, build_repo({".env": "A=1\n", "compose.yaml": "services: [\n"}))
    row = next(number for number, line in enumerate(out) if line.startswith("  ✗ files  "))
    assert code == 2
    assert [line for line in headers(out) if "files" in line and "!" in line] == []
    assert any(line.startswith("env-file .env - ") for line in out[row + 1 :])
    assert re.fullmatch(r"error: 1 of 7 areas could not check · \d+ findings?, \d+ of them in those areas", err[0]), err


def test_two_distinct_reasons_in_one_area_stand_as_sub_lines_in_run_order(build_repo, capsys):
    code, out, _ = frame_run(capsys, build_repo({"compose.yaml": "services: [\n", "pyproject.toml": '[project]\nrequires-python = "banana"\n'}))
    row = next(number for number, line in enumerate(out) if line.startswith("  ✗ files  "))
    assert code == 2
    assert out[row].endswith("not checked    2 reasons")
    assert [bool(re.match(r"    ✗ \S", line)) for line in out[row + 1 : row + 3]] == [True, True]
    assert "cannot read compose.yaml" in out[row + 1]
    assert "pyproject.toml" in out[row + 2]


def test_one_reason_refusing_rules_in_several_areas_stands_once_in_each(build_repo, capsys):
    unknown_table = {"quality/config.toml": "[bogus]\nx = 1\n"}
    code, out, err = frame_run(capsys, build_repo({**CLEAN_FILES, **unknown_table}))
    unchecked = [line for line in out if re.match(r"  ✗ ", line)]
    assert code == 2
    assert len(unchecked) >= 2
    assert err[0] == f"error: {len(unchecked)} of 7 areas could not check"
    assert all("not checked" in line and "bogus" in line for line in unchecked)


@pytest.mark.integration
def test_a_folder_outside_any_repository_prints_only_the_location_pair(tmp_path, capsys):
    code, out, err = frame_run(capsys, tmp_path)
    assert (code, out, err) == (2, [], [f"error: not inside a git work tree · {tmp_path}", "next: run quality hygiene inside a repository"])


@pytest.mark.integration
def test_a_missing_path_gets_the_git_step(tmp_path, capsys):
    code, out, err = frame_run(capsys, tmp_path / "missing")
    assert (code, out) == (2, [])
    assert err[0].startswith("error: ")
    assert "not inside a git work tree" not in err[0]
    assert shlex.split(err[1].removeprefix("next: ")) == ["git", "-C", str(tmp_path / "missing"), "status"]
    assert len(err) == 2


def test_a_bare_repository_and_the_inside_of_git_are_outside_a_work_tree(tmp_path, build_repo, capsys):
    bare = tmp_path / "bare.git"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
    inside = build_repo(CLEAN_FILES) / ".git"
    for path in (bare, inside):
        code, out, err = frame_run(capsys, path)
        assert (code, out, err) == (2, [], [f"error: not inside a git work tree · {path}", "next: run quality hygiene inside a repository"])


@pytest.mark.integration
def test_a_folder_outside_any_repository_is_outside_in_every_locale(tmp_path, fake_git, monkeypatch, capsys):
    """A git that speaks German unless it is asked in the C locale, so the test needs no installed catalogue."""
    fake_git(
        'if [ "$LC_ALL" = C ]; then echo "fatal: not a git repository (or any of the parent directories): .git" >&2; '
        'else echo "fatal: Kein Git-Repository (oder irgendein Elternverzeichnis): .git" >&2; fi; exit 128'
    )
    monkeypatch.setenv("LC_ALL", "de_DE.UTF-8")
    code, out, err = frame_run(capsys, tmp_path)
    assert (code, out, err[0]) == (2, [], f"error: not inside a git work tree · {tmp_path}")


def test_a_repository_git_refuses_gets_the_git_step(build_repo, monkeypatch, capsys):
    repo = build_repo(CLEAN_FILES)
    monkeypatch.setenv("GIT_TEST_ASSUME_DIFFERENT_OWNER", "1")
    code, out, err = frame_run(capsys, repo)
    assert (code, out) == (2, [])
    assert err[0].startswith("error: ")
    assert "not inside a git work tree" not in err[0]
    assert shlex.split(err[1].removeprefix("next: "))[:3] == ["git", "-C", str(repo)]


def exits_and_rows(build_repo, capsys, use_mise, served_scanners, case: str):
    arranged = {
        "clean": ({**CLEAN_FILES, **CLONES_BASELINE}, False),
        "measured only": (CLEAN_FILES, False),
        "findings": ({**CLONES_BASELINE, **PRINTING}, False),
        "refusal": ({**CLONES_BASELINE}, True),
        "refusal with findings": ({**CLONES_BASELINE, **PRINTING}, True),
    }
    files, refuses = arranged[case]
    if refuses:
        use_mise(lacking(served_scanners, scanners.GITLEAKS))
    return frame_run(capsys, build_repo(files))


@pytest.mark.parametrize("case", ["clean", "measured only", "findings", "refusal", "refusal with findings"])
def test_a_refusal_row_appears_exactly_when_the_exit_is_two_and_a_findings_row_when_an_area_has_findings(
    case, build_repo, capsys, use_mise, served_scanners
):
    code, out, _ = exits_and_rows(build_repo, capsys, use_mise, served_scanners, case)
    rows = headers(out)
    assert any(row.startswith("  ✗") for row in rows) is (code == 2)
    assert any(row.startswith("  !") for row in rows) is (case in ("findings", "refusal with findings"))
    assert len(rows) == len(Area)


def terminal_streams(monkeypatch) -> tuple[Terminal, Terminal]:
    """Both streams terminals; set inside the test, because pytest's capture puts its own back when the test starts."""
    monkeypatch.setenv("TERM", "xterm")
    monkeypatch.delenv("NO_COLOR", raising=False)
    stdout, stderr = Terminal(), Terminal()
    monkeypatch.setattr(sys, "stdout", stdout)
    monkeypatch.setattr(sys, "stderr", stderr)
    return stdout, stderr


def test_at_a_terminal_a_refusal_from_repository_text_prints_as_escaped_text_between_quality_sgr_only(build_repo, monkeypatch):
    repo = build_repo({"a\x1b[35m\n\u202e.compose.yaml": "services: [\n"})
    stdout, stderr = terminal_streams(monkeypatch)
    code = cli.main(["hygiene", str(repo)], clock=lambda: 0.0)
    stripped = re.sub(r"\x1b\[[0-9;]*m", "", stdout.getvalue() + stderr.getvalue())
    assert code == 2
    assert "\x1b[" in stdout.getvalue(), stderr.getvalue()
    assert "a\\x1b[35m\\x0a\\u202e.compose.yaml" in stripped
    assert [c for c in stripped if unicodedata.category(c) in ("Cc", "Cf", "Zl", "Zp") and c != "\n"] == []


@pytest.mark.integration
@pytest.mark.parametrize(("environment", "coloured"), [({"TERM": "xterm"}, True), ({"TERM": "xterm", "NO_COLOR": "1"}, False)], ids=["terminal", "NO_COLOR"])
def test_the_real_door_colours_a_pseudo_terminal_only_without_no_color(clean_repo, environment, coloured):
    parent, child = pty.openpty()
    base = {key: value for key, value in os.environ.items() if key not in ("NO_COLOR", "GITHUB_ACTIONS")}
    done = subprocess.run([*DOOR, "hygiene", "."], cwd=clean_repo, env={**base, **environment}, stdout=child, stderr=subprocess.PIPE, check=False)
    os.close(child)
    chunks = []
    with contextlib.suppress(OSError):
        while chunk := os.read(parent, 4096):
            chunks.append(chunk)
    os.close(parent)
    text = b"".join(chunks).decode()
    assert (done.returncode, "\x1b[" in text, "✓" in text) == (0, coloured, True), text


def test_a_refusal_git_did_not_run_for_gets_the_general_instruction(build_repo, tmp_path, monkeypatch, capsys):
    repo = build_repo(CLEAN_FILES)
    nowhere = tmp_path / "empty-bin"
    nowhere.mkdir()
    monkeypatch.setenv("PATH", str(nowhere))
    code, out, err = frame_run(capsys, repo)
    assert (code, out, err[1:]) == (2, [], [f"next: {cli.START_FAILURE_NEXT.text}"])


def test_a_malformed_baseline_beside_a_missing_scanner_names_the_baseline_step_then_the_install_command(
    build_repo, use_mise, served_scanners, capsys
):
    use_mise(lacking(served_scanners, scanners.GITLEAKS))
    code, out, err = frame_run(capsys, build_repo({BASELINE_PATH: "x = [\n"}))
    assert code == 2
    assert err[1:] == ["next:", f"    {ratchet.WRITE_COMMAND}", f"    {scanners.install_command()}"]
    assert ratchet.WRITE_COMMAND not in "\n".join(out)


def areas_refusing(*reasons_by_area: tuple[CannotJudge, ...]) -> tuple[check.AreaVerdict, ...]:
    rules = iter(Rule)
    return tuple(
        check.AreaVerdict(area, tuple(check.RuleVerdict(next(rules), refusal=refusal) for refusal in refusals))
        for area, refusals in zip(Area, reasons_by_area, strict=False)
    )


ONE, TWO = Command("one"), Command("two")


@pytest.mark.parametrize(
    ("areas", "expected"),
    [
        (((CannotJudge("a"),),), (cli.REFUSED_AREAS_NEXT,)),
        (((CannotJudge("a", (ONE,)),), (CannotJudge("b", (ONE,)),)), (ONE,)),
        (((CannotJudge("a", (ONE,)),), (CannotJudge("b"),)), (ONE, cli.REFUSED_AREAS_NEXT)),
        (((CannotJudge("a", (TWO,)),), (CannotJudge("b", (ONE,)),)), (TWO, ONE)),
        (((CannotJudge("a", (TWO, ONE)),),), (TWO, ONE)),
    ],
    ids=["none carries a step", "one step shared", "one step beside a refusal without one", "two steps in row order", "one reason with two steps"],
)
def test_next_names_each_distinct_step_once_in_row_order_and_the_general_instruction_last_where_a_refusal_has_none(areas, expected):
    assert cli.next_steps(areas_refusing(*areas)) == expected


def test_an_area_names_one_fact_once_for_two_reasons_that_differ_only_in_steps():
    area = areas_refusing((CannotJudge("same", (ONE,)), CannotJudge("same", (TWO,))))[0]
    row = cli.area_row(area)
    assert (row.fact, row.sub_lines) == ("same", ())
