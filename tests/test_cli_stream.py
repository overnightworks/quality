import contextlib
import io
import os
import select
import signal
import subprocess
import sys
from pathlib import Path

import pytest

from quality import check, cli, ratchet, scanners
from quality.output import Glyph
from quality.rules import Area
from tests.support.advisories import uv_lock
from tests.support.check import area_row, refusal_pair
from tests.support.git_repositories import GIT_IDENTITY
from tests.support.processes import process_is_gone
from tests.support.python_lint import fake_ruff
from tests.support.scanners import CLEAN_SCANS, OUTPUTS, fake_gitleaks, fake_scanner, fake_zizmor
from tests.support.terminal import screen


def door_argv(command: str, target: Path) -> list[str]:
    return [sys.executable, "-m", "quality.cli", command, str(target)]


def door_environment(**extra: str) -> dict[str, str]:
    """The process environment without the variables that change how the door paints or stops commands."""
    kept = {key: value for key, value in os.environ.items() if key not in ("NO_COLOR", "GITHUB_ACTIONS", "TERM")}
    return kept | extra


def printed_before(printed: str, area: Area) -> str:
    """The lines of `printed` above the row of `area`, each with its line feed."""
    lines = printed.splitlines(keepends=True)
    return "".join(lines[: next(number for number, line in enumerate(lines) if line.split()[1:2] == [area.value])])


def kill_recorded(pid_file: Path) -> None:
    """Ends the process whose id the fake scanner recorded, where it still lives."""
    with contextlib.suppress(FileNotFoundError, ProcessLookupError, ValueError):
        os.kill(int(pid_file.read_text()), signal.SIGKILL)


def test_each_area_starts_after_the_rows_of_every_area_above_it(build_repo, served_scanners, tmp_path, monkeypatch):
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
        code = cli.main(["hygiene", str(repo)], locate_ruff=lambda: locate_ruff, locate_zizmor=locate_zizmor, clock=lambda: 0.0, environment={})
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
    repo = build_repo({"compose.yaml": "services: [\n"})
    rows, seen = tmp_path / "rows", tmp_path / "seen"
    fake_scanner(served_scanners, scanners.GITLEAKS, f'cp "$ROWS" "{seen}"\n{CLEAN_SCANS[scanners.GITLEAKS]}')
    with open(rows, "w", encoding="utf-8") as stdout:
        done = subprocess.run(door_argv("hygiene", repo), env=door_environment(ROWS=str(rows)), stdout=stdout, stderr=subprocess.PIPE, text=True, timeout=60, check=False)
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
    repo = build_repo({"README.md": "x\n"})
    complete = subprocess.run(door_argv("hygiene", repo), env=door_environment(), capture_output=True, text=True, timeout=60, check=False)
    pid_file, scratch_parent = tmp_path / "scanner.pid", tmp_path / "scratch"
    scratch_parent.mkdir()
    interrupting_gitleaks(served_scanners, signal.Signals(sent).name.removeprefix("SIG"))
    door = door_argv("hygiene", repo)
    command = ["sh", "-c", '"$@"; echo "exit $?" >&2', "sh", *door] if under_shell else door
    try:
        done = subprocess.run(command, env=door_environment(SCANNER_PID=str(pid_file), TMPDIR=str(scratch_parent)), capture_output=True, text=True, timeout=60, check=False)
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
    repo = build_repo({"README.md": "x\n"})
    complete = subprocess.run(door_argv("hygiene", repo), env=door_environment(), capture_output=True, text=True, timeout=60, check=False)
    pid_file = tmp_path / "scanner.pid"
    interrupting_gitleaks(served_scanners, signal.Signals(sent).name.removeprefix("SIG"))
    terminal = window(200)
    try:
        done = subprocess.run(
            door_argv("hygiene", repo), env=door_environment(TERM="xterm", SCANNER_PID=str(pid_file)), stdout=terminal.stream, stderr=terminal.stream, timeout=60, check=False,
        )
        assert screen(terminal.read()) == printed_before(complete.stdout, Area.SECRETS)
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
    repo = build_repo(files)
    complete = subprocess.run(door_argv("hygiene", repo), env=door_environment(), capture_output=True, text=True, timeout=60, check=False)
    expected_stderr = refusal_pair(repo) if complete.returncode == 2 else ""
    pid_file, release, stderr_file = tmp_path / "scanner.pid", tmp_path / "release", tmp_path / "stderr"
    os.mkfifo(release)
    release_end = os.open(release, os.O_RDWR)
    fake_scanner(served_scanners, scanners.GITLEAKS, f'echo $$ > "$SCANNER_PID"\nread -r _ < "$RELEASE"\n{CLEAN_SCANS[scanners.GITLEAKS]}')
    door = None
    try:
        with open(stderr_file, "w", encoding="utf-8") as errors:
            door = subprocess.Popen(
                door_argv("hygiene", repo), env=door_environment(SCANNER_PID=str(pid_file), RELEASE=str(release)),
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


@pytest.mark.integration
def test_a_start_failure_printed_on_stdout_because_stderr_is_closed_survives_a_reader_that_left(elsewhere, tmp_path):
    command = ["sh", "-c", 'exec "$@" 2>&-', "sh", *door_argv("hygiene", elsewhere)]
    with open(tmp_path / "stdout", "w", encoding="utf-8") as stdout:
        complete = subprocess.run(command, env=door_environment(), stdout=stdout, timeout=60, check=False)
    read_end, write_end = os.pipe()
    os.close(read_end)
    try:
        done = subprocess.run(command, env=door_environment(), stdout=write_end, timeout=60, check=False)
    finally:
        os.close(write_end)
    assert done.returncode == complete.returncode


def test_a_reader_that_left_before_the_staged_findings_silences_them_and_the_closing_line(build_repo, served_scanners, tmp_path):
    repo = build_repo({"README.md": "x\n"})
    (repo / "staged.txt").write_text("x\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), *GIT_IDENTITY, "add", "staged.txt"], check=True, timeout=60)
    fake_gitleaks(served_scanners, on_scan=f'cp "{OUTPUTS / "gitleaks-finding.json"}" "$report"; exit 7')
    read_end, write_end = os.pipe()
    os.close(read_end)
    try:
        with open(tmp_path / "stderr", "w", encoding="utf-8") as errors:
            done = subprocess.run(door_argv("scan-staged", repo), env=door_environment(), stdout=write_end, stderr=errors, timeout=60, check=False)
    finally:
        os.close(write_end)
    assert (done.returncode, (tmp_path / "stderr").read_text(encoding="utf-8")) == (1, "")
