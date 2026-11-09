import contextlib
import io
import os
import select
import signal
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from quality import check, cli, output, pin, ratchet, scanners, sonar
from quality.output import Glyph
from quality.rules import Area
from tests.support.advisories import uv_lock
from tests.support.check import area_row, refusal_pair
from tests.support.door import CHECK_WORD, MODULE_DOOR, QUALITY_MODULE_DOOR, check_door, door_environment, door_in
from tests.support.processes import process_is_gone
from tests.support.python_lint import fake_ruff
from tests.support.scanners import CLEAN_SCANS, fake_scanner, fake_zizmor
from tests.support.terminal import screen


def printed_before(printed: str, area: Area) -> str:
    """The lines of `printed` above the row of `area`, each with its line feed."""
    lines = printed.splitlines(keepends=True)
    return "".join(lines[: next(number for number, line in enumerate(lines) if line.split()[1:2] == [area.value])])


def kill_recorded(pid_file: Path) -> None:
    """Ends the process whose id the fake scanner recorded, where it still lives."""
    with contextlib.suppress(FileNotFoundError, ProcessLookupError, ValueError):
        os.kill(int(pid_file.read_text()), signal.SIGKILL)


def test_each_area_starts_after_the_rows_of_every_area_above_it(build_repo, served_scanners, tmp_path, monkeypatch):
    """STREAM-1, STREAM-6: a judged run prints each area's lines to stdout as its last rule ends, none to stderr."""
    repo = build_repo({".github/workflows/w.yml": "on: push\n", "uv.lock": uv_lock(), "src/.env": "A=1\n", "src/bad.py": "def broken(:\n"})
    rows, seen = tmp_path / "rows", tmp_path / "seen"
    seen.mkdir()
    monkeypatch.setenv("ROWS", str(rows))
    monkeypatch.setenv("SEEN", str(seen))

    def copying(name: str, answer: str) -> str:
        return f'cp "$ROWS" "$SEEN/{name}"\n{answer}'

    for name, answer in CLEAN_SCANS.items():
        fake_scanner(served_scanners, name, copying(name, answer))
    locate_zizmor = fake_zizmor(tmp_path / "zizmor", copying(scanners.ZIZMOR, "echo '[]'"))
    locate_ruff = str(fake_ruff(tmp_path, copying(scanners.RUFF, "echo '[]'")))

    def entering(owner, function_name: str, label: str) -> None:
        real = getattr(owner, function_name)

        def observed(*arguments, **keywords):
            (seen / label).write_text(rows.read_text(encoding="utf-8"), encoding="utf-8")
            return real(*arguments, **keywords)

        monkeypatch.setattr(owner, function_name, observed)

    entering(ratchet, "read_sources", "debt")
    entering(check, "check_import_contract", "imports")
    stderr = io.StringIO()
    with open(rows, "w", buffering=1, encoding="utf-8") as stdout, contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        code = check_door(repo, locate_ruff=lambda: locate_ruff, locate_zizmor=locate_zizmor, clock=lambda: 0.0, environment={})
    finished = rows.read_text(encoding="utf-8")
    expected = {
        "debt": printed_before(finished, Area.DEBT),
        "imports": printed_before(finished, Area.IMPORTS),
        scanners.GITLEAKS: printed_before(finished, Area.SECRETS),
        scanners.ACTIONLINT: printed_before(finished, Area.WORKFLOWS),
        scanners.ZIZMOR: printed_before(finished, Area.WORKFLOWS),
        scanners.OSV_SCANNER: printed_before(finished, Area.DEPENDENCIES),
        scanners.RUFF: printed_before(finished, Area.CODE),
    }
    assert {name: (seen / name).read_text(encoding="utf-8") for name in expected} == expected
    assert (code, stderr.getvalue()) == (1, "")


def test_a_refused_area_stands_before_the_next_scanner_and_the_pair_stays_on_stderr(build_repo, served_scanners, tmp_path):
    """STREAM-2, STREAM-6: a refused area's row stands before the next scanner starts and the pair stays on stderr."""
    repo = build_repo({"compose.yaml": "services: [\n"})
    rows, seen = tmp_path / "rows", tmp_path / "seen"
    fake_scanner(served_scanners, scanners.GITLEAKS, f'cp "$ROWS" "{seen}"\n{CLEAN_SCANS[scanners.GITLEAKS]}')
    with open(rows, "w", encoding="utf-8") as stdout:
        done = subprocess.run([*MODULE_DOOR, CHECK_WORD], cwd=repo, env=door_environment(ROWS=str(rows)), stdout=stdout, stderr=subprocess.PIPE, text=True, timeout=60, check=False)
    finished = rows.read_text(encoding="utf-8")
    assert seen.read_text(encoding="utf-8") == printed_before(finished, Area.SECRETS)
    assert area_row(seen.read_text(encoding="utf-8"), Area.FILES).glyph == Glyph.COULD_NOT_CHECK
    assert (done.returncode, done.stderr) == (2, refusal_pair(repo))


def interrupting_gitleaks(served_scanners: Path, name: str) -> None:
    """A gitleaks that records its id, pushes a megabyte through the capture pipe, which is drained only inside
    `communicate`, so the signal never meets the start of the process, then signals the door and waits."""
    fake_scanner(served_scanners, scanners.GITLEAKS, f'echo $$ > "$SCANNER_PID"; head -c 1048576 /dev/zero; kill -{name} $PPID; exec sleep 60')


@pytest.mark.parametrize(
    ("sent", "under_shell"),
    [
        pytest.param(signal.SIGINT, True, id="Ctrl-C under a shell"),
        pytest.param(signal.SIGINT, False, id="Ctrl-C direct"),
        pytest.param(signal.SIGTERM, False, id="SIGTERM direct"),
    ],
)
def test_an_interrupt_during_a_scan_leaves_the_rows_of_the_finished_areas_whole(build_repo, served_scanners, tmp_path, sent, under_shell):
    """STREAM-6, STREAM-7: an interrupt during a scan leaves the finished areas' lines whole, with neither verdict
    nor pair."""
    repo = build_repo({"README.md": "x\n"})
    complete = subprocess.run([*MODULE_DOOR, CHECK_WORD], cwd=repo, env=door_environment(), capture_output=True, text=True, timeout=60, check=False)
    pid_file, scratch_parent = tmp_path / "scanner.pid", tmp_path / "scratch"
    scratch_parent.mkdir()
    interrupting_gitleaks(served_scanners, signal.Signals(sent).name.removeprefix("SIG"))
    door = [*MODULE_DOOR, CHECK_WORD]
    command = ["sh", "-c", '"$@"; echo "exit $?" >&2', "sh", *door] if under_shell else door
    try:
        done = subprocess.run(command, cwd=repo, env=door_environment(SCANNER_PID=str(pid_file), TMPDIR=str(scratch_parent)), capture_output=True, text=True, timeout=60, check=False)
        assert done.stdout == printed_before(complete.stdout, Area.SECRETS)
        if under_shell:
            assert done.stderr == f"exit {128 + signal.SIGINT}\n"
        else:
            assert (done.returncode, done.stderr) == (-signal.SIGINT if sent == signal.SIGINT else 128 + signal.SIGTERM, "")
        assert list(scratch_parent.iterdir()) == []
        assert process_is_gone(int(pid_file.read_text()))
    finally:
        kill_recorded(pid_file)


@pytest.mark.parametrize(
    ("sent", "code"),
    [pytest.param(signal.SIGINT, -signal.SIGINT, id="Ctrl-C"), pytest.param(signal.SIGTERM, 128 + signal.SIGTERM, id="SIGTERM")],
)
def test_an_interrupt_at_a_terminal_leaves_whole_lines(build_repo, served_scanners, tmp_path, window, sent, code):
    """COLOUR-11, COLOUR-12, STREAM-6, STREAM-7: an interrupt at a terminal, Ctrl-C or SIGTERM, erases the live line
    and leaves the finished areas' lines whole, with neither verdict nor pair."""
    repo = build_repo({"README.md": "x\n"})
    complete = subprocess.run([*MODULE_DOOR, CHECK_WORD], cwd=repo, env=door_environment(), capture_output=True, text=True, timeout=60, check=False)
    pid_file = tmp_path / "scanner.pid"
    interrupting_gitleaks(served_scanners, signal.Signals(sent).name.removeprefix("SIG"))
    terminal = window(200)
    try:
        done = subprocess.run(
            [*MODULE_DOOR, CHECK_WORD], cwd=repo, env=door_environment(TERM="xterm", SCANNER_PID=str(pid_file)), stdout=terminal.stream, stderr=terminal.stream, timeout=60, check=False,
        )
        printed = terminal.read()
        assert "  ⠋ secrets       gitleaks" in printed
        assert screen(printed) == printed_before(complete.stdout, Area.SECRETS)
        assert done.returncode == code
        assert process_is_gone(int(pid_file.read_text()))
    finally:
        kill_recorded(pid_file)


@pytest.mark.parametrize(
    ("files", "one_pipe"),
    [
        pytest.param({"README.md": "x\n"}, False, id="clean"),
        pytest.param({"src/a.py": 'eval("1")\n'}, False, id="finding"),
        pytest.param({"compose.yaml": "services: [\n"}, False, id="refused"),
        pytest.param({"compose.yaml": "services: [\n"}, True, id="refused, both streams in one pipe"),
    ],
)
def test_a_reader_that_left_after_the_first_line_changes_neither_the_judging_nor_the_exit(build_repo, served_scanners, tmp_path, files, one_pipe):
    """STREAM-5, STREAM-8: a first line stands in a pipe while the first scanner runs, and a reader that left after it
    changes neither the judging nor the exit."""
    repo = build_repo(files)
    complete = subprocess.run([*MODULE_DOOR, CHECK_WORD], cwd=repo, env=door_environment(), capture_output=True, text=True, timeout=60, check=False)
    expected_stderr = refusal_pair(repo) if complete.returncode == 2 else ""
    pid_file, release, stderr_file = tmp_path / "scanner.pid", tmp_path / "release", tmp_path / "stderr"
    os.mkfifo(release)
    release_end = os.open(release, os.O_RDWR)
    fake_scanner(served_scanners, scanners.GITLEAKS, f'echo $$ > "$SCANNER_PID"\nread -r _ < "$RELEASE"\n{CLEAN_SCANS[scanners.GITLEAKS]}')
    door = None
    try:
        with open(stderr_file, "w", encoding="utf-8") as errors:
            door = subprocess.Popen(
                [*MODULE_DOOR, CHECK_WORD], cwd=repo, env=door_environment(SCANNER_PID=str(pid_file), RELEASE=str(release)),
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT if one_pipe else errors,
            )
            printed = b""
            while b"\n" not in printed:
                readable, _, _ = select.select([door.stdout], [], [], 30)
                assert readable, "the door printed no whole line while its first scanner was still running"
                chunk = os.read(door.stdout.fileno(), 4096)
                assert chunk, "the door closed its output before it printed a whole line"
                printed += chunk
            first_line = printed.split(b"\n", 1)[0].decode("utf-8")
            door.stdout.close()
            os.write(release_end, b"\n")
            door.wait(timeout=60)
        assert (door.returncode, first_line) == (complete.returncode, complete.stdout.splitlines()[0])
        if not one_pipe:
            assert stderr_file.read_text(encoding="utf-8") == expected_stderr
    finally:
        if door is not None and door.poll() is None:
            door.kill()
            door.wait(timeout=60)
        kill_recorded(pid_file)
        os.close(release_end)


@pytest.mark.parametrize(("files", "code"), [pytest.param({"README.md": "x\n"}, 0, id="clean"), pytest.param({"src/.env": "A=1\n"}, 1, id="finding")])
def test_a_terminal_that_hung_up_during_a_scan_leaves_the_run_s_own_exit(build_repo, served_scanners, tmp_path, window, files, code):
    """STREAM-8: a terminal that hung up during a scan leaves the run's own exit."""
    repo = build_repo(files)
    pid_file, release, up = tmp_path / "scanner.pid", tmp_path / "release", tmp_path / "up"
    os.mkfifo(release)
    os.mkfifo(up)
    release_end, up_end = os.open(release, os.O_RDWR), os.open(up, os.O_RDWR)
    fake_scanner(served_scanners, scanners.GITLEAKS, f'echo $$ > "$SCANNER_PID"\necho up > "$UP"\nread -r _ < "$RELEASE"\n{CLEAN_SCANS[scanners.GITLEAKS]}')
    terminal = window(80)
    door = None
    try:
        door = subprocess.Popen(
            [*MODULE_DOOR, CHECK_WORD], cwd=repo, env=door_environment(TERM="xterm", SCANNER_PID=str(pid_file), RELEASE=str(release), UP=str(up)),
            stdout=subprocess.DEVNULL, stderr=terminal.stream,
        )
        readable, _, _ = select.select([up_end], [], [], 30)
        assert readable, "the scanner never started under the live line"
        terminal.hang_up()
        os.write(release_end, b"\n")
        door.wait(timeout=60)
        assert door.returncode == code
    finally:
        if door is not None and door.poll() is None:
            door.kill()
            door.wait(timeout=60)
        kill_recorded(pid_file)
        os.close(release_end)
        os.close(up_end)


def test_a_step_s_pipes_carry_the_stop_line_and_no_live_line(build_repo):
    """COLOUR-7, MASK-14: a step's stderr holds the stop line and no other line, the live line included."""
    repo = build_repo({"README.md": "x\n"})
    done = subprocess.run([*MODULE_DOOR, CHECK_WORD], cwd=repo, env=door_environment(TERM="xterm", GITHUB_ACTIONS="true"), capture_output=True, text=True, timeout=60, check=False)
    lines = done.stderr.splitlines()
    assert (len(lines), lines[0].startswith("::stop-commands::")) == (1, True)


@pytest.mark.integration
def test_a_start_failure_with_stderr_closed_prints_nothing_on_stdout(elsewhere, tmp_path):
    """STREAM-9: with stderr closed a run that cannot start prints nothing on stdout."""
    command = ["sh", "-c", 'exec "$@" 2>&-', "sh", *QUALITY_MODULE_DOOR, CHECK_WORD]
    with open(tmp_path / "stdout", "w", encoding="utf-8") as stdout:
        done = subprocess.run(command, cwd=elsewhere, env=door_environment(), stdout=stdout, timeout=60, check=False)
    assert (done.returncode, (tmp_path / "stdout").read_text(encoding="utf-8")) == (2, "")


ENV_ROW = f"{Glyph.NEEDS_YOU} .env "
FIX_LINE = f"{output.FIX_LABEL} "
CONSOLE_SCRIPT_DOOR = [str(Path(sys.executable).parent / "quality")]


def quality_door(*arguments: str, cwd: Path, door: list[str] = MODULE_DOOR, **streams) -> subprocess.CompletedProcess[str]:
    return subprocess.run([*door, *arguments], cwd=cwd, text=True, check=False, **streams)


@pytest.fixture
def env_and_broken_compose(build_repo) -> Path:
    return build_repo({".env": "A=1\n", "compose.yaml": "services: [\n"})


@pytest.mark.parametrize("door", [QUALITY_MODULE_DOOR, CONSOLE_SCRIPT_DOOR], ids=["module", "console script"])
def test_a_merged_pipe_of_the_real_entry_points_keeps_the_pair_in_order(env_and_broken_compose, door):
    """Both entry points write the pair in its order; a fixture without a gate pin is refused before any row."""
    done = quality_door(CHECK_WORD, cwd=env_and_broken_compose, door=door, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    assert (done.returncode, done.stdout) == (
        2, "error: no workflow here calls quality's gate.yml, so no release is pinned\nnext: add the gate as quality's docs/gate.md describes\n",
    )


def test_a_merged_pipe_shows_the_check_lines_in_run_order(env_and_broken_compose):
    """STREAM-3: a merged pipe holds the release row, the area rows with their findings (`.env` as env-file's and
    root-layout's rows, each with its fix), then the pair."""
    done = quality_door(CHECK_WORD, cwd=env_and_broken_compose, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    prefixes = [
        "  · release", "  ✗ files", ENV_ROW, FIX_LINE, ENV_ROW, FIX_LINE,
        "  · debt", FIX_LINE, "  · imports", "  ✓ secrets", "  ✓ workflows", "  ✓ dependencies", "  ✓ code", "error: ", "next: ",
    ]
    lines = done.stdout.splitlines()
    indented = (ENV_ROW, FIX_LINE)
    assert all((line.lstrip() if prefix in indented else line).startswith(prefix) for line, prefix in zip(lines, prefixes, strict=True)), done.stdout


def test_discarding_stderr_hides_exactly_the_pair(env_and_broken_compose):
    """STREAM-2, STREAM-3, STREAM-4: no line changes its stream: discarding stderr hides exactly the pair, which
    follows the rows in a merged pipe; the exit stays 2."""
    merged = quality_door(CHECK_WORD, cwd=env_and_broken_compose, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    done = quality_door(CHECK_WORD, cwd=env_and_broken_compose, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    assert (done.returncode, merged.stdout.startswith(done.stdout), merged.stdout[len(done.stdout):]) == (2, True, refusal_pair(env_and_broken_compose))


def test_discarding_stdout_leaves_exactly_the_pair(env_and_broken_compose):
    """STREAM-1, STREAM-2: the rows are stdout's and the pair is stderr's: discarding stdout leaves exactly the pair."""
    done = quality_door(CHECK_WORD, cwd=env_and_broken_compose, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    assert (done.returncode, done.stderr) == (2, refusal_pair(env_and_broken_compose))


SONAR_FINDINGS_SCRIPT = """
import sys
sys.path.insert(0, {repository_root!r})
from tests.support.sonar import issues_page, sonar_answering
from quality import cli
cli.run(lambda: cli.main(["sonar", "findings"], transport=sonar_answering(issues=issues_page(2, 1, 2)), sleep=lambda seconds: None))
"""


@pytest.mark.integration
def test_a_merged_pipe_lists_the_sonar_findings_before_the_closing_line(tmp_path):
    """STREAM-3: the findings precede the closing line through `2>&1`."""
    from tests.support.sonar import PULL_REQUEST_ENVIRONMENT, TASK_FIXTURE, legacy_config_absent

    (tmp_path / "quality").mkdir()
    (tmp_path / "quality" / "config.toml").write_text(legacy_config_absent)
    (tmp_path / sonar.TASK_FILE).parent.mkdir()
    (tmp_path / sonar.TASK_FILE).write_text(TASK_FIXTURE.read_text())
    script = tmp_path / "findings.py"
    script.write_text(textwrap.dedent(SONAR_FINDINGS_SCRIPT).format(repository_root=str(Path(__file__).parents[1])))
    done = subprocess.run(
        [sys.executable, str(script)], cwd=tmp_path, text=True, check=False, env={**os.environ, **PULL_REQUEST_ENVIRONMENT},
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )
    prefixes = ["  · analysis", "  ! new", "    ! src/a1.py:1", "    ! src/a2.py:2", "      fix:", "  · legacy", "  ✓ quality gate", "! red"]
    lines = done.stdout.splitlines()
    assert (done.returncode, all(line.startswith(prefix) for line, prefix in zip(lines, prefixes, strict=True))) == (1, True), done.stdout


def test_a_replaced_stdout_still_prints_the_help():
    """HELP-8: `main` leaves the process's streams alone, so a captured stdout takes the help."""
    captured = io.StringIO()
    with contextlib.redirect_stdout(captured), pytest.raises(SystemExit) as stopped:
        cli.main(["--help"])
    assert (stopped.value.code, "check" in captured.getvalue()) == (0, True)


@pytest.mark.integration
def test_a_closed_stdout_object_still_prints_the_pair_on_stderr(tmp_path, capsys, monkeypatch, own_home):
    """STREAM-2, STREAM-9: a closed stdout object never crashes `main`; the pair reaches stderr."""
    monkeypatch.setenv("NO_COLOR", "1")  # Python 3.14's argparse otherwise probes the closed stdout for colour
    closed = io.TextIOWrapper(io.BytesIO())
    closed.close()
    with contextlib.redirect_stdout(closed):
        code = door_in(tmp_path, CHECK_WORD, judging=pin.judge_here)
    assert (code, capsys.readouterr().err) == (2, f"error: not inside a git work tree · {tmp_path}\nnext: run quality check inside a repository\n")


@pytest.mark.integration
def test_a_closed_stdout_fd_still_prints_the_pair_and_never_crashes(tmp_path, own_home):
    """STREAM-2, STREAM-9: with fd 1 closed the door still exits 2 with its cannot-judge pair on stderr."""
    done = subprocess.run(
        ["bash", "-c", '"$PY" -m quality.cli check >&-'],
        env={**os.environ, "PY": sys.executable},
        cwd=tmp_path, text=True, check=False, stderr=subprocess.PIPE, timeout=60,
    )
    assert (done.returncode, done.stderr) == (2, f"error: not inside a git work tree · {tmp_path}\nnext: run quality check inside a repository\n")


def test_a_closed_stdout_takes_the_rows_and_leaves_the_pair_on_stderr(env_and_broken_compose, served_scanners):
    """STREAM-2, STREAM-9: with no stdout the rows go nowhere and never to stderr, which holds the pair alone."""
    err = io.StringIO()
    with contextlib.redirect_stdout(None), contextlib.redirect_stderr(err):
        code = check_door(env_and_broken_compose, clock=lambda: 0.0)
    assert (code, err.getvalue()) == (2, refusal_pair(env_and_broken_compose))
